#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf


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


def direct_close(symbol: str, target: pd.Timestamp, retries: int = 4) -> float | None:
    """Individual no-thread retry for transient yfinance bulk-download misses.

    The bulk downloader can occasionally hit yfinance's local SQLite cache lock.
    Never silently drop an index constituent for that transient infrastructure
    error; retry the exact symbol after the bulk request has finished.
    """
    target = pd.Timestamp(target)
    if target.tzinfo is not None:
        target = target.tz_convert("UTC").tz_localize(None)
    start = (target - pd.Timedelta(days=12)).date().isoformat()
    end = (target + pd.Timedelta(days=3)).date().isoformat()
    for attempt in range(retries):
        try:
            d = yf.download(
                symbol,
                start=start,
                end=end,
                auto_adjust=True,
                actions=False,
                progress=False,
                threads=False,
                group_by="column",
            )
            if d is not None and not d.empty:
                if isinstance(d.columns, pd.MultiIndex):
                    s = None
                    for key in (("Close", symbol), (symbol, "Close")):
                        if key in d.columns:
                            s = pd.to_numeric(d[key], errors="coerce").dropna()
                            break
                    if s is None:
                        try:
                            s = pd.to_numeric(d.xs("Close", axis=1, level=0).iloc[:, 0], errors="coerce").dropna()
                        except Exception:
                            s = pd.Series(dtype=float)
                else:
                    s = pd.to_numeric(d.get("Close"), errors="coerce").dropna() if "Close" in d.columns else pd.Series(dtype=float)
                if not s.empty:
                    idx = pd.to_datetime(s.index, utc=True)
                    tgt = pd.Timestamp(target, tz="UTC")
                    vals = s[idx <= tgt]
                    if not vals.empty:
                        v = float(vals.iloc[-1])
                        if math.isfinite(v) and v > 0:
                            return v
        except Exception as e:
            print(f"WARN direct price retry {symbol} attempt {attempt+1}/{retries}: {e}", flush=True)
        time.sleep(0.8 * (attempt + 1))
    return None


def build_target(L, allocation_month, symbols, prices, float_shares):
    signal_month = L.month_add(allocation_month, -1)
    early_month = L.month_add(signal_month, -5)
    recent_ts = L.month_end(signal_month)
    early_ts = L.month_end(early_month)
    fx = L.fx_at(prices, recent_ts)

    rows = []
    gross_values = []
    missing = []
    retried = []
    for meta in symbols:
        s = meta["ticker"]
        recent = L.last_at(prices, meta["yf"], "Close", recent_ts)
        early = L.last_at(prices, meta["yf"], "Close", early_ts)
        if not recent:
            recent = direct_close(meta["yf"], recent_ts)
            if recent:
                retried.append({"ticker": s, "field": "recentPrice"})
        if not early:
            early = direct_close(meta["yf"], early_ts)
            if early:
                retried.append({"ticker": s, "field": "earlyPrice"})
        fs = float_shares.get(s)
        if not recent or not fs:
            missing.append({"ticker": s, "country": meta["country"], "recentPrice": recent, "hasFloatShares": bool(fs)})
            continue
        gross = recent / early if early else None
        if gross and math.isfinite(gross) and gross > 0:
            gross_values.append(gross)
        rows.append({**meta, "recentPrice": recent, "earlyPrice": early, "floatSharesProxy": fs, "grossMomentum": gross})

    # Nani's universe is the union itself. Do not silently shrink it because a
    # data vendor had a transient failure. Missing current price/float is fatal.
    if missing or len(rows) != len(symbols):
        raise RuntimeError(f"Nani full-universe coverage failed rows={len(rows)}/{len(symbols)} missing={missing}")
    if len(gross_values) < 300:
        raise RuntimeError(f"Nani momentum coverage too low {len(gross_values)}/{len(symbols)}")

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
        "priceRetries": retried,
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
    if len(jp_codes) != 225:
        raise RuntimeError(f"Nikkei source must contain 225 constituents, got {len(jp_codes)}")
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

    # Archive this month's target. If an earlier same-month snapshot was
    # incomplete because of a transient vendor failure, repair it before it can
    # ever become an eligible realized-return record.
    now = datetime.now(timezone.utc)
    current_old = months.get(current)
    current_complete = isinstance(current_old, dict) and len(current_old.get("rows") or []) == len(metas)
    if current not in months or (not current_complete and not current_old.get("eligibleForReturn", False)):
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
            "pricesAndAnchorFloatShares":"Yahoo Finance via yfinance with individual retry for bulk misses",
            "usFreeFloatProxy":"site ququ-latest.json floatCap divided by contemporaneous adjusted close"
        },
        "diagnostics":{**float_diag,"topixWeightDate":topix_date,"nasdaqCount":len(ndx),"nikkeiCount":len(jp_codes),"unionCount":len(metas),"weightedCount":len(rows),"momentumMedianGross":target["momentumMedianGross"],"fxJPYperUSD":target["fxJPYperUSD"],"priceRetries":target["priceRetries"]},
        "weightSum":target["weightSum"],
        "maxWeight":target["maxWeight"],
        "top10Weight":target["top10Weight"],
        "rows":rows
    }
    if len(rows) != len(metas) or abs(latest["weightSum"]-1.0)>1e-8:
        raise RuntimeError(f"latest full-universe validation failed n={len(rows)}/{len(metas)} sum={latest['weightSum']}")
    (site / "nani-latest.json").write_text(json.dumps(latest,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    print(json.dumps({"strategy":"Nani v2","holdings":len(rows),"universe":len(metas),"priceRetries":target["priceRetries"],"maxWeight":latest["maxWeight"],"top10Weight":latest["top10Weight"],"completedHistoryMonths":[m for m,r in months.items() if isinstance(r.get("portfolioReturn"),(int,float))],"pendingMonths":[m for m,r in months.items() if not isinstance(r.get("portfolioReturn"),(int,float))]},indent=2))

if __name__ == "__main__":
    main()
