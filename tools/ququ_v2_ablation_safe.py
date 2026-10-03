#!/usr/bin/env python3
"""Transactional driver for ququ_v2_ablation.

All expensive data/feature functions come from ququ_v2_ablation.py.  The only
behavioral difference is month-level atomicity: a month updates return series,
turnover state, and prior weights only after every ablation variant succeeds.
This prevents a late-variant failure from misaligning later performance series.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import ququ_v2_ablation as core


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pit", default="nasdaq-composite-pit.json")
    ap.add_argument("--legacy", default="ququ-history.json")
    ap.add_argument("--start", default="2022-10")
    ap.add_argument("--end", default="2026-09")
    ap.add_argument("--output", default="ququ-v2-ablation.json")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    pit = json.loads(Path(args.pit).read_text(encoding="utf-8"))
    pit_months = pit.get("months") or {}

    months = []
    cur = args.start
    while pd.Period(cur, freq="M") <= pd.Period(args.end, freq="M"):
        signal_month = core.month_add(cur, -1)
        if signal_month in pit_months:
            months.append(cur)
        cur = core.month_add(cur, 1)
    if len(months) < 12:
        raise RuntimeError(f"only {len(months)} allocation months have PIT signal universes")

    signal_months = {core.month_add(m, -1) for m in months}
    symbols = sorted({
        core.norm_symbol(s)
        for sm in signal_months
        for s in (pit_months[sm].get("symbols") or [])
    })
    print(
        f"allocation months={len(months)} {months[0]}..{months[-1]} "
        f"union symbols={len(symbols)}",
        flush=True,
    )

    price_start = str((pd.Period(months[0], freq="M") - 8).start_time.date())
    price_end = str((pd.Period(months[-1], freq="M") + 2).start_time.date())
    prices = core.download_prices(symbols, price_start, price_end)
    price_symbols = sorted(prices)
    print(f"price coverage={len(price_symbols)}/{len(symbols)}", flush=True)
    if len(price_symbols) < 1000:
        raise RuntimeError("price coverage implausibly low")

    shares = core.load_shares(price_symbols, price_start, price_end, args.workers)

    month_panels = {}
    float_candidates = set()
    for m in months:
        signal_month = core.month_add(m, -1)
        panel = core.build_month_rows(m, pit_months[signal_month], prices, shares)
        month_panels[m] = panel
        k = panel["targetK"]
        buffer_n = min(len(panel["rows"]), max(k, core.FLOAT_BUFFER_MULTIPLE * k))
        float_candidates.update(r["ticker"] for r in panel["rows"][:buffer_n])
        print(
            f"panel {m}: PIT={panel['pitEligibleCount']} "
            f"capRows={panel['pricedCapCount']} k={k} floatBuffer={buffer_n}",
            flush=True,
        )

    float_symbols = sorted(float_candidates)
    print(f"float candidate union={len(float_symbols)}", flush=True)
    float_info = core.load_float_info(float_symbols, shares, args.workers)

    monthly_output = []
    series = {v: [] for v in core.VARIANTS}
    turnovers = {v: [] for v in core.VARIANTS}
    prev_weights = {v: None for v in core.VARIANTS}
    prev_stock_returns = {v: None for v in core.VARIANTS}
    failures = {}

    for m in months:
        panel = month_panels[m]
        record = {
            "month": m,
            "signalMonth": panel["signalMonth"],
            "signalDate": panel["signalDate"],
            "pitEligibleCount": panel["pitEligibleCount"],
            "pricedCapCount": panel["pricedCapCount"],
            "targetK": panel["targetK"],
            "capMethods": panel["capMethods"],
            "variants": {},
        }
        try:
            base = core.select_total_cap(panel)
            base, base_mom_med, base_mom_fb = core.fill_momentum(base)

            flt, float_diag = core.with_float_caps(panel, float_info)
            flt, float_mom_med, float_mom_fb = core.fill_momentum(flt)
            vol, sigma_med, vol_fb = core.apply_vol(flt)
            liq, removed_liq = core.apply_liquidity(vol)
            win, winsor_diag = core.apply_winsor(liq)

            definitions = [
                ("Composite TotalCap base", base, "marketCap", "grossMomentumUsed"),
                ("+ FloatCap", flt, "floatCap", "grossMomentumUsed"),
                ("+ Vol", vol, "floatCap", "volAdjustedGross"),
                ("+ Liquidity", liq, "floatCap", "volAdjustedGross"),
                ("+ Winsorization", win, "floatCap", "winsorGross"),
            ]

            record["diagnostics"] = {
                "baseMomentumMedian": base_mom_med,
                "baseMomentumFallbackCount": base_mom_fb,
                "floatMomentumMedian": float_mom_med,
                "floatMomentumFallbackCount": float_mom_fb,
                "float": float_diag,
                "sigmaMedian63": sigma_med,
                "volFallbackCount": vol_fb,
                "liquidityRemovedCount": len(removed_liq),
                "liquidityRemovedSample": [x["ticker"] for x in removed_liq[:20]],
                "winsor": winsor_diag,
            }

            # Stage every variant without mutating committed series/state.
            staged = {}
            for name, rows, cap_field, gross_field in definitions:
                w, stock_r, ret, meta = core.weights_and_return(rows, cap_field, gross_field)
                turnover = core.drifted_turnover(
                    prev_weights[name], prev_stock_returns[name], w
                )
                staged[name] = {
                    "weights": w,
                    "stockReturns": stock_r,
                    "return": ret,
                    "turnover": turnover,
                    "meta": meta,
                }

            # Commit only after all five variants succeeded.
            for name in core.VARIANTS:
                st = staged[name]
                series[name].append(st["return"])
                turnovers[name].append(st["turnover"])
                prev_weights[name] = st["weights"]
                prev_stock_returns[name] = st["stockReturns"]
                record["variants"][name] = {
                    "returnPct": st["return"] * 100.0,
                    "turnoverPct": (
                        st["turnover"] * 100.0
                        if st["turnover"] is not None else None
                    ),
                    **st["meta"],
                    "topHoldings": [
                        {"ticker": t, "weight": wt}
                        for t, wt in sorted(
                            st["weights"].items(),
                            key=lambda x: x[1],
                            reverse=True,
                        )[:25]
                    ],
                }

            monthly_output.append(record)
            print(
                f"ablation {m}: "
                + " | ".join(
                    f"{v}={record['variants'][v]['returnPct']:+.2f}%"
                    for v in core.VARIANTS
                ),
                flush=True,
            )
        except Exception as exc:
            failures[m] = str(exc)
            print(f"WARN ablation {m}: {exc}", flush=True)

    successful_months = [r["month"] for r in monthly_output]
    if len(successful_months) < 12:
        raise RuntimeError(
            f"only {len(successful_months)} successful common months; failures={failures}"
        )

    # Atomic commits guarantee exact month alignment for all variants.
    expected_n = len(successful_months)
    for name in core.VARIANTS:
        if len(series[name]) != expected_n or len(turnovers[name]) != expected_n:
            raise RuntimeError(
                f"alignment invariant failed for {name}: "
                f"returns={len(series[name])} turnover={len(turnovers[name])} "
                f"months={expected_n}"
            )

    metrics = {
        v: core.perf_metrics(series[v], turnovers[v])
        for v in core.VARIANTS
    }
    increments = []
    for before, after in zip(core.VARIANTS[:-1], core.VARIANTS[1:]):
        a, b = metrics[before], metrics[after]
        increments.append({
            "from": before,
            "to": after,
            "deltaCAGRpctPoints": b["CAGRpct"] - a["CAGRpct"],
            "deltaMDDpctPoints": b["MDDpct"] - a["MDDpct"],
            "deltaSharpe": (
                b["Sharpe0rf"] - a["Sharpe0rf"]
                if b["Sharpe0rf"] is not None and a["Sharpe0rf"] is not None
                else None
            ),
            "deltaAnnualizedTurnoverPctPoints": (
                b["annualizedTurnoverPct"] - a["annualizedTurnoverPct"]
                if b["annualizedTurnoverPct"] is not None
                and a["annualizedTurnoverPct"] is not None
                else None
            ),
        })

    out = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "strategy": "QUQU v2 ablation",
        "engine": "transactional-safe-driver-v1",
        "period": {
            "requestedStart": args.start,
            "requestedEnd": args.end,
            "successfulStart": successful_months[0],
            "successfulEnd": successful_months[-1],
            "successfulMonths": len(successful_months),
        },
        "universe": {
            "name": "Nasdaq Composite proxy from PIT Nasdaq-listed monthly reports",
            "pitSource": pit.get("sourcePage"),
            "pitDatasetGeneratedAt": pit.get("generatedAt"),
            "eligibilityPolicy": pit.get("eligibilityPolicy") or pit.get("caveat"),
            "selection": (
                "Top 10% by contemporaneous capitalization; FloatCap variants "
                "rank by float-adjusted cap proxy."
            ),
        },
        "rules": {
            "legacyCore": (
                "sqrt(cap) × (relative 6-1 gross momentum)^3; "
                "20% single-name cap; monthly rebalance"
            ),
            "momentum": (
                "P(signal month end) / P(allocation month - 6 month end) - 1; "
                "insufficient 6-1 history uses selected cross-sectional median gross momentum"
            ),
            "totalCap": (
                "Yahoo historical shares × raw close when available; otherwise "
                "split-safe current-share proxy × adjusted close"
            ),
            "floatCap": (
                "historical total cap × latest available Yahoo "
                "floatShares/sharesOutstanding ratio; missing ratio assumes 1.0 and is audited"
            ),
            "floatSelectionBuffer": (
                f"Fetch float data for up to {core.FLOAT_BUFFER_MULTIPLE}× target count "
                "by total-cap rank; verify kth float cap >= max total cap outside buffer"
            ),
            "volatility": (
                f"63-trading-day adjusted-close return standard deviation; adjusted "
                f"excess momentum = excess × (cross-sectional median sigma / stock sigma)^{core.VOL_EXPONENT}"
            ),
            "liquidity": (
                "Remove bottom 10% of FloatCap-selected holdings by trailing "
                "63-trading-day median raw Close × Volume"
            ),
            "winsorization": (
                "Cross-sectional z-score of vol-adjusted excess momentum; "
                "clip z to ±3 and map back to excess-return units"
            ),
            "turnover": (
                "One-way monthly turnover = 0.5 × sum absolute difference between "
                "new target and prior successful target drifted by prior realized stock returns"
            ),
            "return": (
                "Adjusted first trading-day open to adjusted last observed close within "
                "allocation month; >2% missing-return weight rejects the whole month atomically"
            ),
        },
        "freeDataCaveat": (
            "Historical Nasdaq Public Float is a paid Fundamental Data field. "
            "This free-data study therefore uses a latest-float-ratio proxy, reports "
            "its coverage, and must not be labeled a fully point-in-time free-float backtest."
        ),
        "metrics": metrics,
        "increments": increments,
        "legacyQUQU": core.load_legacy(Path(args.legacy), successful_months),
        "failures": failures,
        "months": monthly_output,
    }
    Path(args.output).write_text(
        json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "period": out["period"],
                "metrics": metrics,
                "increments": increments,
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
