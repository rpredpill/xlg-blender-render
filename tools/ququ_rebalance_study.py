#!/usr/bin/env python3
"""Compare QUQU v2 rebalance cadences without changing its signal or weighting math.

Variants
--------
1M  : full portfolio reset to the monthly QUQU v2 target every month.
2M  : full reset every 2 months, anchored to the first study month.
3M  : full reset every 3 months, anchored to the first study month.
6M  : full reset every 6 months, anchored to the first study month.
3S  : three independently compounded sleeves. All sleeves start in the first
      QUQU target; thereafter exactly one sleeve is reset each month and each
      sleeve is therefore refreshed every 3 months.

The QUQU target itself is unchanged: Nasdaq Composite PIT -> float-cap top 10%
proxy -> 6-1 momentum -> 63D volatility adjustment -> liquidity bottom-decile
removal -> +/-3z winsorization -> sqrt(float cap) * relative signal^3 -> 20% cap.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import ququ_v2_ablation as core
import ququ_v2_bulk_data as bulk

# Use the same bulk public-data adapters as the production QUQU v2 ablation.
core.download_prices = bulk.load_prices_hf
core.load_shares = bulk.load_shares_hf_plus_proxy
core.load_float_info = bulk.load_float_info_bulk


def normalize(weights: dict[str, float]) -> dict[str, float]:
    total = sum(float(v) for v in weights.values())
    if not math.isfinite(total) or total <= 0:
        raise RuntimeError("weight sum <= 0")
    return {k: float(v) / total for k, v in weights.items() if float(v) > 0}


def month_stock_returns(weights: dict[str, float], month: str, prices: dict):
    returns = {}
    missing_weight = 0.0
    for t, w in weights.items():
        r = core.month_return(prices.get(t), month)
        if r is None or not math.isfinite(float(r)):
            missing_weight += float(w)
            returns[t] = None
        else:
            returns[t] = float(r)
    if missing_weight > core.MISSING_RETURN_LIMIT + 1e-12:
        raise RuntimeError(f"{month}: missing return weight {missing_weight:.2%}")
    return returns, missing_weight


def apply_month(weights: dict[str, float], month: str, prices: dict):
    """Return portfolio return, end-of-month drifted weights, stock returns, missing weight."""
    weights = normalize(weights)
    stock_r, missing = month_stock_returns(weights, month, prices)
    gross = {}
    portfolio_rel = 0.0
    for t, w in weights.items():
        rr = stock_r[t] if stock_r[t] is not None else 0.0
        rel = max(0.0, 1.0 + rr)
        gross[t] = w * rel
        portfolio_rel += w * rel
    if not math.isfinite(portfolio_rel) or portfolio_rel <= 0:
        raise RuntimeError(f"{month}: invalid portfolio relative return {portfolio_rel}")
    end_weights = {t: v / portfolio_rel for t, v in gross.items() if v > 0}
    return portfolio_rel - 1.0, end_weights, stock_r, missing


def turnover(from_weights: dict[str, float] | None, to_weights: dict[str, float]) -> float | None:
    if not from_weights:
        return None
    a = normalize(from_weights)
    b = normalize(to_weights)
    names = set(a) | set(b)
    return 0.5 * sum(abs(a.get(t, 0.0) - b.get(t, 0.0)) for t in names)


def trend_metrics(returns: list[float]):
    arr = np.asarray(returns, dtype=float)
    eq = np.cumprod(1.0 + arr)
    highs = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    at_high = eq >= highs - 1e-12
    underwater = ~at_high
    longest = 0
    cur = 0
    for x in underwater:
        if x:
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 0
    roll3 = []
    for i in range(2, len(arr)):
        roll3.append(np.prod(1.0 + arr[i-2:i+1]) - 1.0)
    return {
        "newHighMonthPct": float(np.mean(at_high) * 100.0) if len(arr) else None,
        "longestUnderwaterMonths": int(longest),
        "positive3MonthWindowPct": float(np.mean(np.asarray(roll3) > 0) * 100.0) if roll3 else None,
        "monthlyReturnStdPct": float(arr.std(ddof=1) * 100.0) if len(arr) >= 2 else None,
    }


def metrics(returns: list[float], turnovers: list[float | None]):
    out = core.perf_metrics(returns, turnovers)
    out.update(trend_metrics(returns))
    return out


def simulate_cadence(months, targets, prices, cadence: int):
    returns = []
    turns = []
    records = []
    end_weights = None
    for i, month in enumerate(months):
        do_rebalance = (i == 0) or (i % cadence == 0)
        if do_rebalance:
            start_weights = normalize(targets[month])
            trn = turnover(end_weights, start_weights)
        else:
            if end_weights is None:
                raise RuntimeError("carry requested before initialization")
            start_weights = normalize(end_weights)
            trn = 0.0
        ret, end_weights, _, missing = apply_month(start_weights, month, prices)
        returns.append(ret)
        turns.append(trn)
        records.append({
            "month": month,
            "returnPct": ret * 100.0,
            "turnoverPct": trn * 100.0 if trn is not None else None,
            "rebalanced": bool(do_rebalance),
            "holdings": len(start_weights),
            "missingReturnWeightPct": missing * 100.0,
        })
    return {"returns": returns, "turnovers": turns, "months": records, "metrics": metrics(returns, turns)}


def simulate_three_sleeves(months, targets, prices):
    # Sleeve NAVs are independent capital buckets; no cross-sleeve capital reset.
    sleeves = [
        {"weights": normalize(targets[months[0]]), "nav": 1.0 / 3.0},
        {"weights": normalize(targets[months[0]]), "nav": 1.0 / 3.0},
        {"weights": normalize(targets[months[0]]), "nav": 1.0 / 3.0},
    ]
    returns = []
    turns = []
    records = []
    for i, month in enumerate(months):
        total_start = sum(s["nav"] for s in sleeves)
        if not math.isfinite(total_start) or total_start <= 0:
            raise RuntimeError(f"{month}: invalid sleeve NAV")
        trn = None if i == 0 else 0.0
        refreshed = None
        if i > 0:
            j = i % 3
            refreshed = j
            sleeve_share = sleeves[j]["nav"] / total_start
            inside = turnover(sleeves[j]["weights"], targets[month]) or 0.0
            trn = sleeve_share * inside
            sleeves[j]["weights"] = normalize(targets[month])

        total_end = 0.0
        missing_total = 0.0
        for s in sleeves:
            capital_share = s["nav"] / total_start
            r, ew, _, missing = apply_month(s["weights"], month, prices)
            s["weights"] = ew
            s["nav"] *= 1.0 + r
            total_end += s["nav"]
            missing_total += capital_share * missing
        ret = total_end / total_start - 1.0
        returns.append(ret)
        turns.append(trn)
        records.append({
            "month": month,
            "returnPct": ret * 100.0,
            "turnoverPct": trn * 100.0 if trn is not None else None,
            "refreshedSleeve": refreshed,
            "missingReturnWeightPct": missing_total * 100.0,
            "sleeveNavSharesEnd": [s["nav"] / total_end for s in sleeves],
        })
    return {"returns": returns, "turnovers": turns, "months": records, "metrics": metrics(returns, turns)}


def benchmark_metrics(path: Path, months: list[str], symbols=("QQQ", "SPMO")):
    if not path.exists():
        return {}
    j = json.loads(path.read_text(encoding="utf-8"))
    source = j.get("months") or {}
    out = {}
    for sym in symbols:
        vals = []
        used = []
        for m in months:
            x = (source.get(m) or {}).get(sym)
            if isinstance(x, (int, float)) and math.isfinite(float(x)):
                vals.append(float(x) / 100.0)
                used.append(m)
        if len(vals) == len(months):
            out[sym] = {"metrics": metrics(vals, []), "months": used}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pit", default="nasdaq-composite-pit.json")
    ap.add_argument("--benchmark", default="benchmark-history.json")
    ap.add_argument("--start", default="2022-10")
    ap.add_argument("--end", default="2026-09")
    ap.add_argument("--output", default="ququ-rebalance-study.json")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    pit = json.loads(Path(args.pit).read_text(encoding="utf-8"))
    pit_months = pit.get("months") or {}
    months = []
    cur = args.start
    while pd.Period(cur, freq="M") <= pd.Period(args.end, freq="M"):
        if core.month_add(cur, -1) in pit_months:
            months.append(cur)
        cur = core.month_add(cur, 1)
    if len(months) < 12:
        raise RuntimeError(f"only {len(months)} usable months")

    signal_months = {core.month_add(m, -1) for m in months}
    symbols = sorted({core.norm_symbol(s) for sm in signal_months for s in (pit_months[sm].get("symbols") or [])})
    print(f"months={len(months)} {months[0]}..{months[-1]} unionSymbols={len(symbols)}", flush=True)

    price_start = str((pd.Period(months[0], freq="M") - 8).start_time.date())
    price_end = str((pd.Period(months[-1], freq="M") + 2).start_time.date())
    prices = core.download_prices(symbols, price_start, price_end)
    if len(prices) < 1000:
        raise RuntimeError(f"price coverage implausibly low: {len(prices)}")
    shares = core.load_shares(sorted(prices), price_start, price_end, args.workers)

    panels = {}
    float_candidates = set()
    for m in months:
        sm = core.month_add(m, -1)
        panel = core.build_month_rows(m, pit_months[sm], prices, shares)
        panels[m] = panel
        k = panel["targetK"]
        buffer_n = min(len(panel["rows"]), max(k, core.FLOAT_BUFFER_MULTIPLE * k))
        float_candidates.update(r["ticker"] for r in panel["rows"][:buffer_n])
        print(f"panel {m}: PIT={panel['pitEligibleCount']} capRows={panel['pricedCapCount']} k={k}", flush=True)

    float_info = core.load_float_info(sorted(float_candidates), shares, args.workers)

    targets = {}
    monthly_reference = {}
    target_meta = {}
    for m in months:
        panel = panels[m]
        flt, _ = core.with_float_caps(panel, float_info)
        flt, _, _ = core.fill_momentum(flt)
        vol, _, _ = core.apply_vol(flt)
        liq, _ = core.apply_liquidity(vol)
        win, _ = core.apply_winsor(liq)
        w, _, ret, meta = core.weights_and_return(win, "floatCap", "winsorGross")
        targets[m] = w
        monthly_reference[m] = ret
        target_meta[m] = meta
        print(f"target {m}: holdings={len(w)} reference={ret*100:+.2f}%", flush=True)

    variants = {
        "QUQU-1M": simulate_cadence(months, targets, prices, 1),
        "QUQU-2M": simulate_cadence(months, targets, prices, 2),
        "QUQU-3M": simulate_cadence(months, targets, prices, 3),
        "QUQU-6M": simulate_cadence(months, targets, prices, 6),
        "QUQU-3S": simulate_three_sleeves(months, targets, prices),
    }

    # Exact consistency check: 1M must reproduce the production monthly target backtest.
    max_abs = max(abs(a - monthly_reference[m]) for a, m in zip(variants["QUQU-1M"]["returns"], months))
    if max_abs > 1e-10:
        raise RuntimeError(f"1M consistency check failed: max abs diff={max_abs}")

    summary = {name: v["metrics"] for name, v in variants.items()}
    out = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "strategy": "QUQU v2 rebalance cadence study",
        "period": {"start": months[0], "end": months[-1], "months": len(months)},
        "method": {
            "target": "Unchanged production QUQU v2 target every month",
            "cadenceAnchor": months[0],
            "QUQU-1M": "Full target reset monthly",
            "QUQU-2M": "Full target reset every 2 months",
            "QUQU-3M": "Full target reset every 3 months",
            "QUQU-6M": "Full target reset every 6 months",
            "QUQU-3S": "Three independent sleeves; one sleeve refreshed monthly, each sleeve every 3 months",
            "betweenRebalances": "Hold and let weights drift with realized stock returns",
            "missingReturnPolicy": f"Use 0 only when aggregate missing weight <= {core.MISSING_RETURN_LIMIT:.0%}; otherwise reject month",
        },
        "dataCaveat": (
            "Same free-data QUQU v2 inputs as the existing ablation: historical total-cap data plus a latest free-float-ratio proxy. "
            "This is not a fully point-in-time historical public-float backtest."
        ),
        "consistency": {"oneMonthMaxAbsReturnDiff": max_abs},
        "summary": summary,
        "benchmarks": benchmark_metrics(Path(args.benchmark), months),
        "variants": {name: {"metrics": v["metrics"], "months": v["months"]} for name, v in variants.items()},
        "targets": {
            m: {
                "holdings": len(targets[m]),
                "maxWeight": target_meta[m].get("maxWeight"),
                "top10Weight": target_meta[m].get("top10Weight"),
            }
            for m in months
        },
    }
    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"period": out["period"], "summary": summary, "benchmarks": out["benchmarks"], "consistency": out["consistency"]}, ensure_ascii=False, indent=2, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
