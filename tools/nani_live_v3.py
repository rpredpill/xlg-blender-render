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

VERSION = "3.0"
PROVENANCE = "forward-live-v3"
JP_TARGET = 100
JP_BUCKET = 0.50
US_BUCKET = 0.50


def load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def bucket_weights(raw: dict[str, float], bucket: float) -> dict[str, float]:
    total = sum(v for v in raw.values() if math.isfinite(v) and v > 0)
    if total <= 0:
        raise RuntimeError("Nani bucket raw score sum <= 0")
    return {k: bucket * v / total for k, v in raw.items() if math.isfinite(v) and v > 0}


def build_target(L, V2, allocation_month, source_metas, prices, float_shares):
    signal_month = L.month_add(allocation_month, -1)
    early_month = L.month_add(signal_month, -5)
    recent_ts = L.month_end(signal_month)
    early_ts = L.month_end(early_month)
    fx = L.fx_at(prices, recent_ts)

    candidates = []
    missing = []
    retried = []
    for meta in source_metas:
        s = meta["ticker"]
        recent = L.last_at(prices, meta["yf"], "Close", recent_ts)
        early = L.last_at(prices, meta["yf"], "Close", early_ts)
        if not recent:
            recent = V2.direct_close(meta["yf"], recent_ts)
            if recent:
                retried.append({"ticker": s, "field": "recentPrice"})
        if not early:
            early = V2.direct_close(meta["yf"], early_ts)
            if early:
                retried.append({"ticker": s, "field": "earlyPrice"})
        fs = float_shares.get(s)
        if not recent or not fs:
            missing.append({"ticker": s, "country": meta["country"], "recentPrice": recent, "hasFloatShares": bool(fs)})
            continue
        gross = recent / early if early else None
        local_fc = float(fs * recent)
        fc_usd = local_fc / fx if meta["country"] == "JP" else local_fc
        candidates.append({
            **meta,
            "recentPrice": float(recent),
            "earlyPrice": float(early) if early else None,
            "floatSharesProxy": float(fs),
            "grossMomentum": float(gross) if gross and math.isfinite(gross) and gross > 0 else None,
            "floatCapLocal": local_fc,
            "floatCap": fc_usd,
            "fxJPYperUSD": fx if meta["country"] == "JP" else 1.0,
        })

    # The top-100 Japan selection depends on ranking the entire Nikkei-225 source.
    # Missing source data is therefore fatal rather than silently shrinking the universe.
    if missing or len(candidates) != len(source_metas):
        raise RuntimeError(f"Nani source coverage failed rows={len(candidates)}/{len(source_metas)} missing={missing}")

    us = [r for r in candidates if r["country"] == "US"]
    jp_all = sorted((r for r in candidates if r["country"] == "JP"), key=lambda r: r["floatCap"], reverse=True)
    if len(jp_all) != 225:
        raise RuntimeError(f"Nikkei source must have 225 priced rows, got {len(jp_all)}")
    jp = jp_all[:JP_TARGET]
    selected = us + jp

    gross_values = [r["grossMomentum"] for r in selected if r.get("grossMomentum")]
    if len(gross_values) < max(190, int(len(selected) * 0.95)):
        raise RuntimeError(f"Nani momentum coverage too low {len(gross_values)}/{len(selected)}")
    neutral = float(np.median(gross_values))

    raw_us: dict[str, float] = {}
    raw_jp: dict[str, float] = {}
    for r in selected:
        g = r["grossMomentum"] if r.get("grossMomentum") and r["grossMomentum"] > 0 else neutral
        r["momentumFallback"] = r.get("grossMomentum") is None
        r["grossMomentum"] = float(g)
        r["momentum"] = float(g - 1.0)
        r["relativeMomentum"] = float(g / neutral)
        r["rawWeightScore"] = float(r["floatCap"] * (r["relativeMomentum"] ** 3))
        (raw_jp if r["country"] == "JP" else raw_us)[r["ticker"]] = r["rawWeightScore"]

    weights = {**bucket_weights(raw_jp, JP_BUCKET), **bucket_weights(raw_us, US_BUCKET)}
    out = []
    for r in selected:
        r["weight"] = float(weights[r["ticker"]])
        out.append(r)
    out.sort(key=lambda x: x["weight"], reverse=True)
    for i, r in enumerate(out, 1):
        r["weightRank"] = i

    jp_weight = sum(r["weight"] for r in out if r["country"] == "JP")
    us_weight = sum(r["weight"] for r in out if r["country"] == "US")
    cutoff = jp[-1]["floatCap"] if jp else None
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
        "countryWeightJP": jp_weight,
        "countryWeightUS": us_weight,
        "maxWeight": max(r["weight"] for r in out),
        "top10Weight": sum(r["weight"] for r in out[:10]),
        "nikkei100Members": [r["ticker"] for r in jp],
        "nikkei100CutoffFloatCapUSD": cutoff,
    }


def compact(rows):
    keep = [
        "ticker", "yf", "country", "localTicker", "weight", "momentum", "grossMomentum",
        "relativeMomentum", "floatCap", "floatCapLocal", "floatSharesProxy", "rawWeightScore",
        "recentPrice", "earlyPrice", "fxJPYperUSD", "weightRank"
    ]
    return [{k: r[k] for k in keep if k in r} for r in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default=".")
    ap.add_argument("--legacy", default="/tmp/nani_live_legacy.py")
    ap.add_argument("--v2", default="/tmp/nani_live_v2.py")
    args = ap.parse_args()
    site = Path(args.site)
    L = load_module("nani_legacy", args.legacy)
    V2 = load_module("nani_v2_helpers", args.v2)

    quu = json.loads((site / "quu-latest.json").read_text(encoding="utf-8"))
    ququ = json.loads((site / "ququ-latest.json").read_text(encoding="utf-8"))
    ndx = list(dict.fromkeys(str(r.get("ticker", "")).upper().replace("-", ".") for r in (quu.get("rows") or []) if r.get("ticker")))
    if not 95 <= len(ndx) <= 110:
        raise RuntimeError(f"Nasdaq source count {len(ndx)}")

    jp_codes = L.get_nikkei_members()
    topix_date, topix_weights, topix_url = L.load_topix_weights()
    if len(jp_codes) != 225:
        raise RuntimeError(f"Nikkei source must contain 225 constituents, got {len(jp_codes)}")

    source_metas = [{"ticker": s, "yf": s.replace(".", "-"), "country": "US", "localTicker": s} for s in ndx]
    source_metas += [{"ticker": f"{c}.T", "yf": f"{c}.T", "country": "JP", "localTicker": c} for c in jp_codes]
    source_metas = list({m["ticker"]: m for m in source_metas}.values())

    current = L.now_month()
    start_month = L.month_add(current, -7)
    start = (pd.Period(start_month, freq="M").start_time - pd.Timedelta(days=7)).date().isoformat()
    end = (datetime.now(timezone.utc) + timedelta(days=4)).date().isoformat()
    prices = L.download_prices([m["yf"] for m in source_metas] + ["JPY=X"], start, end)
    float_shares, float_diag = L.build_float_share_proxies(ndx, jp_codes, prices, ququ, topix_date, topix_weights)
    target = build_target(L, V2, current, source_metas, prices, float_shares)
    selected_count = len(target["rows"])

    hist_path = site / "nani-history.json"
    try:
        old = json.loads(hist_path.read_text(encoding="utf-8")) if hist_path.exists() else {}
    except Exception:
        old = {}
    old_months = old.get("months") if isinstance(old.get("months"), dict) else {}

    # Strategy definition changed materially in v3. Preserve only v3 forward-live records.
    months = {m: r for m, r in old_months.items() if isinstance(r, dict) and r.get("provenance") == PROVENANCE}

    prev = L.month_add(current, -1)
    prev_rec = months.get(prev)
    if isinstance(prev_rec, dict) and prev_rec.get("eligibleForReturn") and not isinstance(prev_rec.get("portfolioReturn"), (int, float)):
        result = L.month_return(prev, prev_rec.get("rows") or [], prices)
        prev_rec["portfolioReturn"] = float(result["portfolioReturn"])
        prev_rec["lastDate"] = result["lastDate"]
        prev_rec["coverageRatio"] = result["coverageRatio"]
        prev_rec["missingReturnWeight"] = result["missingReturnWeight"]
        prev_rec["rows"] = result["rows"]

    now = datetime.now(timezone.utc)
    current_old = months.get(current)
    current_complete = isinstance(current_old, dict) and len(current_old.get("rows") or []) == selected_count
    if current not in months or (not current_complete and not current_old.get("eligibleForReturn", False)):
        months[current] = {
            "allocationMonth": current,
            "signalMonth": target["signalMonth"],
            "signalDate": target["signalDate"],
            "earlyDate": target["earlyDate"],
            "snapshotGeneratedAt": now.isoformat(),
            "provenance": PROVENANCE,
            "eligibleForReturn": now.day <= 2,
            "validatedFreeFloatAtSnapshot": True,
            "universeCount": selected_count,
            "countryWeightJP": target["countryWeightJP"],
            "countryWeightUS": target["countryWeightUS"],
            "rows": compact(target["rows"]),
        }

    universe_label = "Nikkei 100 ∪ Nasdaq-100"
    selection_rule = "Nikkei 100 = top 100 Nikkei 225 constituents by float-adjusted market cap at each snapshot"
    rules = {
        "universe": universe_label,
        "nikkei100Selection": selection_rule,
        "momentum": "6-1 gross momentum relative to the selected-union cross-sectional median",
        "coreWeighting": "Float-Adjusted Market Cap × Relative Momentum^3",
        "countryAllocation": "Japan 50% / US 50%",
        "countryNormalization": "Normalize raw scores separately inside JP and US, then scale each country bucket to 50%",
        "singleNameCap": None,
        "rebalanceCadence": None,
        "recordPolicy": "Only targets archived live at the time are eligible for later realized-return evaluation.",
    }

    hist_payload = {
        "strategy": "Nani",
        "version": VERSION,
        "universe": universe_label,
        "description": "Forward-only month-end record store. No retroactive reconstruction is admitted.",
        "rules": rules,
        "dataCaveat": "The live snapshot uses contemporaneous free-float proxies available at snapshot time. Pre-v3 records use a different strategy definition and are excluded.",
        "months": {k: months[k] for k in sorted(months)},
    }
    hist_path.write_text(json.dumps(hist_payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")

    rows = compact(target["rows"])
    latest = {
        "strategy": "Nani",
        "version": VERSION,
        "ready": True,
        "generatedAt": now.isoformat(),
        "universe": universe_label,
        "holdings": len(rows),
        "allocationMonth": current,
        "signalMonth": target["signalMonth"],
        "signalDate": target["signalDate"],
        "earlyDate": target["earlyDate"],
        "rules": rules,
        "sources": {
            "nikkei225SourceUniverse": L.NIKKEI_URL,
            "japanFreeFloatWeights": topix_url,
            "pricesAndAnchorFloatShares": "Yahoo Finance via yfinance with individual retry for bulk misses",
            "usFreeFloatProxy": "site ququ-latest.json floatCap divided by contemporaneous adjusted close",
        },
        "diagnostics": {
            **float_diag,
            "topixWeightDate": topix_date,
            "nasdaqCount": len(ndx),
            "nikkeiSourceCount": len(jp_codes),
            "nikkei100Count": JP_TARGET,
            "unionCount": len(rows),
            "weightedCount": len(rows),
            "sourceCoverageCount": len(source_metas),
            "momentumMedianGross": target["momentumMedianGross"],
            "fxJPYperUSD": target["fxJPYperUSD"],
            "countryWeightJP": target["countryWeightJP"],
            "countryWeightUS": target["countryWeightUS"],
            "nikkei100CutoffFloatCapUSD": target["nikkei100CutoffFloatCapUSD"],
            "nikkei100Members": target["nikkei100Members"],
            "priceRetries": target["priceRetries"],
        },
        "weightSum": target["weightSum"],
        "maxWeight": target["maxWeight"],
        "top10Weight": target["top10Weight"],
        "rows": rows,
    }

    jp_rows = [r for r in rows if r.get("country") == "JP"]
    us_rows = [r for r in rows if r.get("country") == "US"]
    if len(jp_rows) != JP_TARGET or len(us_rows) != len(ndx) or len(rows) != JP_TARGET + len(ndx):
        raise RuntimeError(f"latest universe validation failed JP={len(jp_rows)} US={len(us_rows)} NDX={len(ndx)}")
    if abs(latest["weightSum"] - 1.0) > 1e-8 or abs(target["countryWeightJP"] - 0.5) > 1e-8 or abs(target["countryWeightUS"] - 0.5) > 1e-8:
        raise RuntimeError(f"latest allocation validation failed total={latest['weightSum']} JP={target['countryWeightJP']} US={target['countryWeightUS']}")

    (site / "nani-latest.json").write_text(json.dumps(latest, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({
        "strategy": "Nani v3",
        "holdings": len(rows),
        "JP": len(jp_rows),
        "US": len(us_rows),
        "countryWeightJP": target["countryWeightJP"],
        "countryWeightUS": target["countryWeightUS"],
        "maxWeight": latest["maxWeight"],
        "top10Weight": latest["top10Weight"],
        "completedHistoryMonths": [m for m, r in months.items() if isinstance(r.get("portfolioReturn"), (int, float))],
        "pendingMonths": [m for m, r in months.items() if not isinstance(r.get("portfolioReturn"), (int, float))],
    }, indent=2))


if __name__ == "__main__":
    main()
