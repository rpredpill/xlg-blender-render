#!/usr/bin/env python3
"""Build the production QUQU v2 target-weight snapshot.

The production signal intentionally reuses the same feature/selection/weighting
functions as the validated QUQU v2 ablation.  Current Yahoo OHLCV is used for
fresh month-end prices, while the bulk shares/float adapters are reused for
coverage and consistency with the research run.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import ququ_v2_ablation as core
import ququ_v2_bulk_data as bulk

# Keep current Yahoo price downloads so the live signal is not limited by the
# publication lag of the historical bulk price shards.  Reuse the bulk
# shares/float adapters that passed the v2 ablation coverage checks.
core.load_shares = bulk.load_shares_hf_plus_proxy
core.load_float_info = bulk.load_float_info_bulk


def month_key_now() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


def final_weights(rows: list[dict]):
    gross = [float(r["winsorGross"]) for r in rows if r.get("winsorGross") and float(r["winsorGross"]) > 0]
    if not gross:
        raise RuntimeError("no positive winsorized gross signals")
    signal_median = float(np.median(gross))
    raw = {}
    for r in rows:
        t = r["ticker"]
        capv = float(r["floatCap"])
        g = float(r["winsorGross"])
        if capv > 0 and g > 0:
            raw[t] = math.sqrt(capv) * ((g / signal_median) ** 3)
    if len(raw) < 100:
        raise RuntimeError(f"only {len(raw)} final weightable holdings")
    weights = core.cap_and_redistribute(raw)
    return weights, signal_median


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pit", default="nasdaq-composite-pit.json")
    ap.add_argument("--output", default="ququ-latest.json")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--fallback-months", type=int, default=4)
    args = ap.parse_args()

    pit = json.loads(Path(args.pit).read_text(encoding="utf-8"))
    pit_months = pit.get("months") or {}
    if not pit_months:
        raise RuntimeError("PIT dataset has no months")

    current_month = month_key_now()
    previous_month = core.month_add(current_month, -1)
    eligible_signal_months = sorted(
        [m for m in pit_months if pd.Period(m, freq="M") <= pd.Period(previous_month, freq="M")],
        reverse=True,
    )
    eligible_signal_months = eligible_signal_months[: max(1, args.fallback_months)]
    if not eligible_signal_months:
        raise RuntimeError(f"no PIT month <= {previous_month}")

    # Download one union so a stale/latest-month fallback does not trigger a
    # second multi-thousand-symbol price pass.
    symbols = sorted({
        core.norm_symbol(s)
        for m in eligible_signal_months
        for s in (pit_months[m].get("symbols") or [])
    })
    newest_allocation = core.month_add(eligible_signal_months[0], 1)
    oldest_allocation = core.month_add(eligible_signal_months[-1], 1)
    price_start = str((pd.Period(oldest_allocation, freq="M") - 8).start_time.date())
    price_end = str((datetime.now(timezone.utc) + timedelta(days=2)).date())

    print(
        f"live candidate signals={eligible_signal_months} union symbols={len(symbols)} "
        f"price window={price_start}..{price_end}",
        flush=True,
    )
    prices = core.download_prices(symbols, price_start, price_end)
    price_symbols = sorted(prices)
    print(f"live price coverage={len(price_symbols)}/{len(symbols)}", flush=True)
    if len(price_symbols) < 1000:
        raise RuntimeError("live price coverage implausibly low")

    shares = core.load_shares(price_symbols, price_start, price_end, args.workers)

    chosen = None
    chosen_panel = None
    rejected = []
    for signal_month in eligible_signal_months:
        allocation_month = core.month_add(signal_month, 1)
        panel = core.build_month_rows(allocation_month, pit_months[signal_month], prices, shares)
        enough = panel["pricedCapCount"] >= panel["targetK"]
        print(
            f"live panel signal={signal_month} allocation={allocation_month} "
            f"PIT={panel['pitEligibleCount']} capRows={panel['pricedCapCount']} k={panel['targetK']}",
            flush=True,
        )
        if enough:
            chosen = signal_month
            chosen_panel = panel
            break
        rejected.append({
            "signalMonth": signal_month,
            "reason": f"pricedCapCount {panel['pricedCapCount']} < targetK {panel['targetK']}",
        })

    if chosen is None or chosen_panel is None:
        raise RuntimeError(f"no viable live PIT month; rejected={rejected}")

    k = chosen_panel["targetK"]
    buffer_n = min(len(chosen_panel["rows"]), max(k, core.FLOAT_BUFFER_MULTIPLE * k))
    float_symbols = sorted({r["ticker"] for r in chosen_panel["rows"][:buffer_n]})
    float_info = core.load_float_info(float_symbols, shares, args.workers)

    flt, float_diag = core.with_float_caps(chosen_panel, float_info)
    if not float_diag.get("selectionProvenAgainstOutsideBuffer"):
        raise RuntimeError("float-cap top-decile selection is not proven against outside buffer")
    flt, momentum_median, momentum_fallbacks = core.fill_momentum(flt)
    vol, sigma_median, vol_fallbacks = core.apply_vol(flt)
    liq, removed_liq = core.apply_liquidity(vol)
    win, winsor_diag = core.apply_winsor(liq)
    weights, signal_median = final_weights(win)

    rows = []
    by_ticker = {r["ticker"]: r for r in win}
    for ticker, weight in weights.items():
        r = by_ticker[ticker]
        rows.append({
            "ticker": ticker,
            "weight": float(weight),
            "marketCap": float(r["marketCap"]),
            "floatRatio": float(r.get("floatRatio") or 1.0),
            "floatCap": float(r["floatCap"]),
            "capMethod": r.get("capMethod"),
            "floatMethod": r.get("floatMethod"),
            "momentum": float(r["grossMomentumUsed"]) - 1.0,
            "grossMomentum": float(r["grossMomentumUsed"]),
            "momentumFallback": bool(r.get("momentumFallback")),
            "sigma63": float(r["sigma63"]) if r.get("sigma63") is not None else None,
            "volScale": float(r.get("volScale") or 1.0),
            "volAdjustedExcess": float(r["volAdjustedExcess"]),
            "adv63": float(r["adv63"]) if r.get("adv63") is not None else None,
            "winsorZ": float(r["winsorZ"]),
            "winsorGross": float(r["winsorGross"]),
        })
    rows.sort(key=lambda x: x["weight"], reverse=True)
    for i, r in enumerate(rows, 1):
        r["weightRank"] = i

    weight_sum = sum(r["weight"] for r in rows)
    max_weight = max(r["weight"] for r in rows)
    top10_weight = sum(r["weight"] for r in rows[:10])
    if abs(weight_sum - 1.0) > 1e-9:
        raise RuntimeError(f"weight sum {weight_sum}")
    if max_weight > core.CAP + 1e-9:
        raise RuntimeError(f"20% cap violation {max_weight}")

    float_known_selected = int(float_diag.get("selectedFloatKnownCount") or 0)
    payload = {
        "strategy": "QUQU v2",
        "version": "2.0",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "universe": "Nasdaq Composite PIT proxy",
        "allocationMonth": core.month_add(chosen, 1),
        "signalMonth": chosen,
        "signalDate": chosen_panel["signalDate"],
        "earlyDate": chosen_panel["earlyDate"],
        "pitSource": pit.get("sourcePage"),
        "pitDatasetGeneratedAt": pit.get("generatedAt"),
        "pitEligibilityPolicy": pit.get("eligibilityPolicy") or pit.get("caveat"),
        "targetTop10Count": k,
        "holdings": len(rows),
        "rules": {
            "universeSelection": "Nasdaq Composite PIT proxy; top 10% by float-adjusted capitalization proxy",
            "momentum": "6-1 gross momentum; missing history uses selected cross-sectional median",
            "volatility": "63D adjusted-close sigma; excess momentum × (median sigma / stock sigma)^0.25",
            "liquidity": "Remove bottom 10% by trailing 63D median raw Close × Volume",
            "winsorization": "Cross-sectional vol-adjusted excess momentum z-score clipped to ±3",
            "weighting": "sqrt(FloatCap) × (winsorized gross / cross-sectional median gross)^3",
            "singleNameCap": 0.20,
            "rebalance": "monthly",
        },
        "diagnostics": {
            "candidateSignalMonths": eligible_signal_months,
            "rejectedNewerSignalMonths": rejected,
            "pitEligibleCount": chosen_panel["pitEligibleCount"],
            "pricedCapCount": chosen_panel["pricedCapCount"],
            "targetTop10Count": k,
            "floatBufferCount": float_diag.get("bufferN"),
            "floatKnownInBuffer": float_diag.get("floatKnownInBuffer"),
            "floatKnownPctInBuffer": float_diag.get("floatKnownPctInBuffer"),
            "selectedFloatKnownCount": float_known_selected,
            "floatSelectionProven": bool(float_diag.get("selectionProvenAgainstOutsideBuffer")),
            "momentumMedianGross": momentum_median,
            "momentumFallbackCount": momentum_fallbacks,
            "sigmaMedian63": sigma_median,
            "volFallbackCount": vol_fallbacks,
            "liquidityRemovedCount": len(removed_liq),
            "liquidityRemovedSample": [r["ticker"] for r in removed_liq[:30]],
            "winsorClippedCount": winsor_diag.get("clippedCount"),
            "winsorMean": winsor_diag.get("mean"),
            "winsorStd": winsor_diag.get("std"),
            "weightSignalMedianGross": signal_median,
            "priceCoverageCount": len(price_symbols),
            "priceUniverseCount": len(symbols),
        },
        "freeDataCaveat": (
            "Historical Nasdaq Public Float is a paid Fundamental Data field. The free-data implementation "
            "uses the latest available floatShares/sharesOutstanding ratio as a proxy on contemporaneous "
            "total market cap; it is not a fully point-in-time historical free-float series."
        ),
        "weightSum": weight_sum,
        "maxWeight": max_weight,
        "top10Weight": top10_weight,
        "rows": rows,
    }
    Path(args.output).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "strategy": payload["strategy"],
                "allocationMonth": payload["allocationMonth"],
                "signalMonth": payload["signalMonth"],
                "holdings": payload["holdings"],
                "maxWeightPct": max_weight * 100.0,
                "top10WeightPct": top10_weight * 100.0,
                "floatKnownSelected": float_known_selected,
                "liquidityRemoved": len(removed_liq),
                "winsorClipped": winsor_diag.get("clippedCount"),
            },
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
