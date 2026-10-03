#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd


def load_legacy(path: str):
    spec = importlib.util.spec_from_file_location("nani_legacy", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def normalize(raw: dict[str, float]) -> dict[str, float]:
    total = sum(v for v in raw.values() if math.isfinite(v) and v > 0)
    if total <= 0:
        raise RuntimeError("Nani raw score sum <= 0")
    return {k: v / total for k, v in raw.items() if math.isfinite(v) and v > 0}


def build_target(L, allocation_month, symbols, prices, float_shares):
    signal_month = L.month_add(allocation_month, -1)
    early_month = L.month_add(signal_month, -5)
    recent_ts = L.month_end(signal_month)
    early_ts = L.month_end(early_month)
    fx = L.fx_at(prices, recent_ts)

    rows = []
    gross_values = []
    for meta in symbols:
        s = meta["ticker"]
        recent = L.last_at(prices, meta["yf"], "Close", recent_ts)
        early = L.last_at(prices, meta["yf"], "Close", early_ts)
        fs = float_shares.get(s)
        if not recent or not fs:
            continue
        gross = recent / early if early else None
        if gross and math.isfinite(gross) and gross > 0:
            gross_values.append(gross)
        rows.append({**meta, "recentPrice": recent, "earlyPrice": early, "floatSharesProxy": fs, "grossMomentum": gross})

    if len(rows) < 315 or len(gross_values) < 300:
        raise RuntimeError(f"Nani coverage too low rows={len(rows)} momentum={len(gross_values)}")

    neutral = float(np.median(gross_values))
    raw = {}
    for r in rows:
        g = r["grossMomentum"] if r["grossMomentum"] and r["grossMomentum"] > 0 else neutral
        r["momentumFallback"] = r["grossMomentum"] is None
        r["grossMomentum"] = float(g)
        r["momentum"] = float(g - 1.0)
        r["relativeMomentum"] = float(g / neutral)
        local_fc = float(r["floatSharesProxy"] * r["recentPrice"])
        r["floatCapLocal"] = local_fc
        r["fxJPYperUSD"] = fx if r["country"] == "JP" else 1.0
        r["floatCap"] = local_fc / fx if r["country"] == "JP" else local_fc
        r["rawWeightScore"] = r["floatCap"] * (r["relativeMomentum"] ** 3)
        raw[r["ticker"]] = r["rawWeightScore"]

    weights = normalize(raw)
    out = []
    for r in rows:
        if r["ticker"] not in weights:
            continue
        r["weight"] = float(weights[r["ticker"]])
        out.append(r)
    out.sort(key=lambda x: x["weight"], reverse=True)
    for i, r in enumerate(out, 1):
        r["weightRank"] = i

    return {
        "allocationMonth": allocation_month,
        "signalMonth": signal_month,
        "signalDate": recent_ts.date().isoformat(),
        "earlyDate": early_ts.date().isoformat(),
        "momentumMedianGross": neutral,
        "fxJPYperUSD": fx,
        "rows": out,
        "weightSum": sum(r["weight"] for r in out),
        "maxWeight": max(r["weight"] for r in out),
        "top10Weight": sum(r["weight"] for r in out[:10]),
    }


def compact(rows):
    keep = ["ticker","yf","country","localTicker","weight","momentum","grossMomentum","relativeMomentum","floatCap","floatCapLocal","floatSharesProxy","rawWeightScore","recentPrice","earlyPrice","fxJPYperUSD","weightRank"]
    return [{k:r[k] for k in keep if k in r} for r in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default=".")
    ap.add_argument("--legacy", default="/tmp/nani_live_legacy.py")
    args = ap.parse_args()
    site = Path(args.site)
    L = load_legacy(args.legacy)

    quu = json.loads((site / "quu-latest.json").read_text(encoding="utf-8"))
    ququ = json.loads((site / "ququ-latest.json").read_text(encoding="utf-8"))
    ndx = list(dict.fromkeys(str(r.get("ticker", "")).upper().replace("-", ".") for r in (quu.get("rows") or []) if r.get("ticker")))
    if not 95 <= len(ndx) <= 110:
        raise RuntimeError(f"Nasdaq source count {len(ndx)}")

    jp_codes = L.get_nikkei_members()
    topix_date, topix_weights, topix_url = L.load_topix_weights()
    metas = [{"ticker":s,"yf":s.replace(".","-"),"country":"US","localTicker":s} for s in ndx]
    metas += [{"ticker":f"{c}.T","yf":f"{c}.T","country":"JP","localTicker":c} for c in jp_codes]
    metas = list({m["ticker"]:m for m in metas}.values())

    current = L.now_month()
    start_month = L.month_add(current, -7)
    start = (pd.Period(start_month, freq="M").start_time - pd.Timedelta(days=7)).date().isoformat()
    end = (datetime.now(timezone.utc) + timedelta(days=4)).date().isoformat()
    prices = L.download_prices([m["yf"] for m in metas] + ["JPY=X"], start, end)
    float_shares, float_diag = L.build_float_share_proxies(ndx, jp_codes, prices, ququ, topix_date, topix_weights)
    target = build_target(L, current, metas, prices, float_shares)

    hist_path = site / "nani-history.json"
    try:
        old = json.loads(hist_path.read_text(encoding="utf-8")) if hist_path.exists() else {}
    except Exception:
        old = {}
    old_months = old.get("months") if isinstance(old.get("months"), dict) else {}

    # Keep only forward-live v2 records. All retroactive v1 backfill is discarded.
    months = {m:r for m,r in old_months.items() if isinstance(r,dict) and r.get("provenance")=="forward-live-v2"}

    # Evaluate only an already-archived previous-month target. Never reconstruct it later.
    prev = L.month_add(current, -1)
    prev_rec = months.get(prev)
    if isinstance(prev_rec, dict) and prev_rec.get("eligibleForReturn") and not isinstance(prev_rec.get("portfolioReturn"),(int,float)):
        result = L.month_return(prev, prev_rec.get("rows") or [], prices)
        prev_rec["portfolioReturn"] = float(result["portfolioReturn"])
        prev_rec["lastDate"] = result["lastDate"]
        prev_rec["coverageRatio"] = result["coverageRatio"]
        prev_rec["missingReturnWeight"] = result["missingReturnWeight"]
        prev_rec["rows"] = result["rows"]

    # Archive this month's target once. It is not overwritten later, preserving point-in-time state.
    now = datetime.now(timezone.utc)
    if current not in months:
        months[current] = {
            "allocationMonth": current,
            "signalMonth": target["signalMonth"],
            "signalDate": target["signalDate"],
            "earlyDate": target["earlyDate"],
            "snapshotGeneratedAt": now.isoformat(),
            "provenance": "forward-live-v2",
            "eligibleForReturn": now.day <= 2,
            "validatedFreeFloatAtSnapshot": True,
            "universeCount": len(metas),
            "rows": compact(target["rows"]),
        }

    hist_payload = {
        "strategy":"Nani",
        "version":"2.0",
        "universe":"Nasdaq-100 ∪ Nikkei 225",
        "description":"Forward-only month-end record store. No retroactive reconstruction is admitted.",
        "rules":{
            "coreWeighting":"Float-Adjusted Market Cap × Relative Momentum^3",
            "singleNameCap":None,
            "rebalanceCadence":None,
            "recordPolicy":"Only targets archived live at the time are eligible for later realized-return evaluation."
        },
        "dataCaveat":"The live snapshot uses contemporaneous free-float proxies available at snapshot time. Historical v1 reconstructed months were removed.",
        "months":{k:months[k] for k in sorted(months)}
    }
    hist_path.write_text(json.dumps(hist_payload,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")

    rows = compact(target["rows"])
    latest = {
        "strategy":"Nani",
        "version":"2.0",
        "ready":True,
        "generatedAt":now.isoformat(),
        "universe":"Nasdaq-100 ∪ Nikkei 225",
        "holdings":len(rows),
        "allocationMonth":current,
        "signalMonth":target["signalMonth"],
        "signalDate":target["signalDate"],
        "earlyDate":target["earlyDate"],
        "rules":{
            "universe":"Nasdaq-100 ∪ Nikkei 225",
            "momentum":"6-1 gross momentum relative to the cross-sectional median",
            "weighting":"Float-Adjusted Market Cap × Relative Momentum^3",
            "normalization":"raw scores normalized to 100%",
            "baseCurrency":"USD",
            "singleNameCap":None,
            "rebalanceCadence":None
        },
        "sources":{
            "nikkei225":L.NIKKEI_URL,
            "japanFreeFloatWeights":topix_url,
            "pricesAndAnchorFloatShares":"Yahoo Finance via yfinance",
            "usFreeFloatProxy":"site ququ-latest.json floatCap divided by contemporaneous adjusted close"
        },
        "diagnostics":{**float_diag,"topixWeightDate":topix_date,"nasdaqCount":len(ndx),"nikkeiCount":len(jp_codes),"unionCount":len(metas),"momentumMedianGross":target["momentumMedianGross"],"fxJPYperUSD":target["fxJPYperUSD"]},
        "weightSum":target["weightSum"],
        "maxWeight":target["maxWeight"],
        "top10Weight":target["top10Weight"],
        "rows":rows
    }
    if len(rows)<315 or abs(latest["weightSum"]-1.0)>1e-8:
        raise RuntimeError(f"latest validation failed n={len(rows)} sum={latest['weightSum']}")
    (site / "nani-latest.json").write_text(json.dumps(latest,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    print(json.dumps({"strategy":"Nani v2","holdings":len(rows),"maxWeight":latest["maxWeight"],"top10Weight":latest["top10Weight"],"completedHistoryMonths":[m for m,r in months.items() if isinstance(r.get("portfolioReturn"),(int,float))],"pendingMonths":[m for m,r in months.items() if not isinstance(r.get("portfolioReturn"),(int,float))]},indent=2))

if __name__ == "__main__":
    main()
