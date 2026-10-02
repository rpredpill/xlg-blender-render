#!/usr/bin/env python3
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

CAP = 0.20


def cap_and_redistribute(raw: dict[str, float], cap: float = CAP) -> dict[str, float]:
    free = set(raw)
    out: dict[str, float] = {}
    remaining = 1.0
    while free:
        total = sum(raw[s] for s in free)
        if not math.isfinite(total) or total <= 0:
            raise RuntimeError('raw sum <= 0')
        over = [s for s in free if remaining * raw[s] / total > cap + 1e-12]
        if not over:
            for s in free:
                out[s] = remaining * raw[s] / total
            break
        for s in over:
            out[s] = cap
            remaining -= cap
            free.remove(s)
    return out


def variant_return(record: dict, power: float) -> float:
    rows = list(record.get('rows') or [])
    gross = [1.0 + float(r['momentum']) for r in rows if isinstance(r.get('momentum'), (int, float)) and 1.0 + float(r['momentum']) > 0]
    if len(gross) < 90:
        raise RuntimeError(f'not enough momentum rows: {len(gross)}')
    med = float(np.median(gross))
    raw: dict[str, float] = {}
    returns: dict[str, float | None] = {}
    for r in rows:
        t = str(r.get('ticker') or '').strip().upper()
        mc = float(r.get('marketCap') or 0)
        mom = r.get('momentum')
        mr = r.get('monthlyReturn')
        if not t or mc <= 0 or not isinstance(mom, (int, float)):
            continue
        g = 1.0 + float(mom)
        if g <= 0:
            continue
        raw[t] = math.sqrt(mc) * ((g / med) ** power)
        returns[t] = float(mr) if isinstance(mr, (int, float)) else None
    if len(raw) < 90:
        raise RuntimeError(f'eligible {len(raw)}')
    w = cap_and_redistribute(raw)
    missing_w = sum(w[t] for t in w if returns.get(t) is None)
    if missing_w > 0.02:
        raise RuntimeError(f'missing return weight {missing_w:.2%}')
    return sum(w[t] * (returns[t] if returns[t] is not None else 0.0) for t in w) * 100.0


def metrics(returns_pct: list[float], months: list[str]) -> dict:
    arr = np.asarray(returns_pct, dtype=float) / 100.0
    eq = np.concatenate([[1.0], np.cumprod(1.0 + arr)])
    peaks = np.maximum.accumulate(eq)
    dd = eq / peaks - 1.0
    n = len(arr)
    total = float(eq[-1] - 1.0)
    cagr = float(eq[-1] ** (12.0 / n) - 1.0)
    mdd_i = int(np.argmin(dd))
    # dd index 0 is pre-start; return month index is dd index-1
    trough_month = months[mdd_i - 1] if mdd_i > 0 else None
    peak_i = int(np.argmax(eq[:mdd_i + 1])) if mdd_i >= 0 else 0
    peak_month = months[peak_i - 1] if peak_i > 0 else 'START'
    mdd = float(dd[mdd_i])
    return {
        'months': n,
        'totalReturnPct': total * 100.0,
        'CAGRpct': cagr * 100.0,
        'MDDpct': mdd * 100.0,
        'Calmar': cagr / abs(mdd) if mdd < 0 else None,
        'positiveMonthRatePct': float(np.mean(arr > 0) * 100.0),
        'bestMonthPct': float(arr.max() * 100.0),
        'worstMonthPct': float(arr.min() * 100.0),
        'endingIndex': float(eq[-1] * 100.0),
        'mddPeakMonth': peak_month,
        'mddTroughMonth': trough_month,
    }


def main():
    qhist = json.loads(Path('ququ-history.json').read_text(encoding='utf-8'))
    bench = json.loads(Path('benchmark-history.json').read_text(encoding='utf-8'))
    months = sorted(set(qhist.get('months', {})) & set(bench.get('months', {})))
    months = [m for m in months if isinstance(qhist['months'][m].get('portfolioReturn'), (int, float)) and isinstance(bench['months'][m].get('QQQ'), (int, float))]
    if len(months) < 36:
        raise RuntimeError(f'only {len(months)} months')

    prior_disps: list[float] = []
    rows_out = []
    r_p3: list[float] = []
    r_qqq: list[float] = []
    r_base: list[float] = []
    r_guard: list[float] = []

    for idx, m in enumerate(months):
        rec = qhist['months'][m]
        rs = list(rec.get('rows') or [])
        moms = np.asarray([float(r['momentum']) for r in rs if isinstance(r.get('momentum'), (int, float))], dtype=float)
        if len(moms) < 90:
            raise RuntimeError(f'{m}: only {len(moms)} momentums')
        disp = float(np.std(moms, ddof=0))
        median_mom = float(np.median(moms))
        positive_breadth = float(np.mean(moms > 0))
        threshold = float(np.median(prior_disps)) if prior_disps else None
        leadership = True if threshold is None else disp > threshold
        broad_positive = median_mom > 0.0
        guarded = leadership and broad_positive

        p3 = float(rec['portfolioReturn'])
        qqq = float(bench['months'][m]['QQQ'])
        base_ret = p3 if leadership else qqq
        guard_ret = p3 if guarded else qqq

        rows_out.append({
            'month': m,
            'momentumDispersion': disp,
            'priorExpandingMedianDispersion': threshold,
            'medianMomentum': median_mom,
            'positiveMomentumBreadth': positive_breadth,
            'leadershipStrong': leadership,
            'broadTrendPositive': broad_positive,
            'guardedQUQU': guarded,
            'QUQU_p3': p3,
            'QQQ': qqq,
            'leadershipSwitch': base_ret,
            'leadershipBreadthGuardSwitch': guard_ret,
        })
        r_p3.append(p3); r_qqq.append(qqq); r_base.append(base_ret); r_guard.append(guard_ret)
        prior_disps.append(disp)

    variants = {
        'QUQU p=3': metrics(r_p3, months),
        'QQQ': metrics(r_qqq, months),
        'Leadership p3/QQQ': metrics(r_base, months),
        'Leadership + breadth guard p3/QQQ': metrics(r_guard, months),
    }

    # Identify the exact baseline drawdown trough and its signal state.
    trough = variants['Leadership p3/QQQ']['mddTroughMonth']
    trough_row = next((r for r in rows_out if r['month'] == trough), None)
    switched_months = [r['month'] for r in rows_out if r['leadershipStrong'] != r['guardedQUQU']]
    changed_delta = []
    for r in rows_out:
        if r['leadershipStrong'] != r['guardedQUQU']:
            changed_delta.append({
                'month': r['month'],
                'baselineReturn': r['leadershipSwitch'],
                'guardedReturn': r['leadershipBreadthGuardSwitch'],
                'differencePctPoints': r['leadershipBreadthGuardSwitch'] - r['leadershipSwitch'],
                'medianMomentum': r['medianMomentum'],
                'positiveMomentumBreadth': r['positiveMomentumBreadth'],
            })

    out = {
        'generatedAt': datetime.now(timezone.utc).isoformat(),
        'period': {'start': months[0], 'end': months[-1], 'months': len(months)},
        'rule': {
            'leadership': 'Current Nasdaq-100 cross-sectional 6-1 momentum dispersion > median of all prior monthly dispersions',
            'breadthGuard': 'Additionally require current cross-sectional median 6-1 momentum > 0 (equivalent to more than half of names having positive 6-1 momentum, aside from ties)',
            'allocation': 'If both are true hold QUQU p=3; otherwise QQQ',
            'lookahead': 'All inputs are current pre-allocation signal rows; dispersion threshold uses prior months only',
        },
        'variants': variants,
        'baselineMDDTroughSignal': trough_row,
        'guardChangedMonths': switched_months,
        'guardChangedMonthDetails': changed_delta,
        'months': rows_out,
    }
    Path('ququ-leadership-guard-analysis.json').write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({
        'period': out['period'],
        'variants': variants,
        'baselineMDDTroughSignal': trough_row,
        'guardChangedMonths': switched_months,
        'guardChangedMonthDetails': changed_delta,
    }, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
