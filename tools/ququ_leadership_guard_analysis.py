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


def metrics(returns_pct: list[float], months: list[str]) -> dict:
    arr = np.asarray(returns_pct, dtype=float) / 100.0
    eq = np.concatenate([[1.0], np.cumprod(1.0 + arr)])
    peaks = np.maximum.accumulate(eq)
    dd = eq / peaks - 1.0
    n = len(arr)
    total = float(eq[-1] - 1.0)
    cagr = float(eq[-1] ** (12.0 / n) - 1.0)
    mdd_i = int(np.argmin(dd))
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


def tukey_upper_fence(xs: list[float]) -> float | None:
    if len(xs) < 8:
        return None
    a = np.asarray(xs, dtype=float)
    q1 = float(np.quantile(a, 0.25))
    q3 = float(np.quantile(a, 0.75))
    return q3 + 1.5 * (q3 - q1)


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
    r_breadth: list[float] = []
    r_extreme: list[float] = []

    for m in months:
        rec = qhist['months'][m]
        rs = list(rec.get('rows') or [])
        moms = np.asarray([float(r['momentum']) for r in rs if isinstance(r.get('momentum'), (int, float))], dtype=float)
        if len(moms) < 90:
            raise RuntimeError(f'{m}: only {len(moms)} momentums')

        disp = float(np.std(moms, ddof=0))
        median_mom = float(np.median(moms))
        positive_breadth = float(np.mean(moms > 0))
        threshold = float(np.median(prior_disps)) if prior_disps else None
        upper_fence = tukey_upper_fence(prior_disps)

        leadership = True if threshold is None else disp > threshold
        broad_positive = median_mom > 0.0
        breadth_guarded = leadership and broad_positive
        extreme_dispersion = bool(upper_fence is not None and disp > upper_fence)
        extreme_guarded = leadership and not extreme_dispersion

        p3 = float(rec['portfolioReturn'])
        qqq = float(bench['months'][m]['QQQ'])
        base_ret = p3 if leadership else qqq
        breadth_ret = p3 if breadth_guarded else qqq
        extreme_ret = p3 if extreme_guarded else qqq

        rows_out.append({
            'month': m,
            'momentumDispersion': disp,
            'priorExpandingMedianDispersion': threshold,
            'priorTukeyUpperFence': upper_fence,
            'dispersionVsMedian': (disp / threshold) if threshold else None,
            'dispersionVsUpperFence': (disp / upper_fence) if upper_fence else None,
            'medianMomentum': median_mom,
            'positiveMomentumBreadth': positive_breadth,
            'leadershipStrong': leadership,
            'broadTrendPositive': broad_positive,
            'extremeDispersion': extreme_dispersion,
            'breadthGuardedQUQU': breadth_guarded,
            'extremeGuardedQUQU': extreme_guarded,
            'QUQU_p3': p3,
            'QQQ': qqq,
            'leadershipSwitch': base_ret,
            'leadershipBreadthGuardSwitch': breadth_ret,
            'leadershipExtremeGuardSwitch': extreme_ret,
        })
        r_p3.append(p3)
        r_qqq.append(qqq)
        r_base.append(base_ret)
        r_breadth.append(breadth_ret)
        r_extreme.append(extreme_ret)
        prior_disps.append(disp)

    variants = {
        'QUQU p=3': metrics(r_p3, months),
        'QQQ': metrics(r_qqq, months),
        'Leadership p3/QQQ': metrics(r_base, months),
        'Leadership + breadth guard p3/QQQ': metrics(r_breadth, months),
        'Leadership + extreme-dispersion guard p3/QQQ': metrics(r_extreme, months),
    }

    trough = variants['Leadership p3/QQQ']['mddTroughMonth']
    trough_row = next((r for r in rows_out if r['month'] == trough), None)
    extreme_changed = [r for r in rows_out if r['leadershipStrong'] != r['extremeGuardedQUQU']]

    out = {
        'generatedAt': datetime.now(timezone.utc).isoformat(),
        'period': {'start': months[0], 'end': months[-1], 'months': len(months)},
        'rules': {
            'leadership': 'Current Nasdaq-100 cross-sectional 6-1 momentum dispersion > median of all prior monthly dispersions',
            'breadthGuard': 'Additionally require current cross-sectional median 6-1 momentum > 0',
            'extremeDispersionGuard': 'If current dispersion exceeds the prior-history Tukey upper fence Q3 + 1.5×IQR, treat it as an extreme/outlier regime and hold QQQ instead of QUQU',
            'lookahead': 'All current-month inputs are pre-allocation signal rows; both median threshold and Tukey fence use prior months only',
        },
        'variants': variants,
        'baselineMDDTroughSignal': trough_row,
        'extremeGuardChangedMonths': [r['month'] for r in extreme_changed],
        'extremeGuardChangedMonthDetails': [{
            'month': r['month'],
            'dispersion': r['momentumDispersion'],
            'upperFence': r['priorTukeyUpperFence'],
            'dispersionVsUpperFence': r['dispersionVsUpperFence'],
            'QUQU': r['QUQU_p3'],
            'QQQ': r['QQQ'],
            'differencePctPoints': r['QQQ'] - r['QUQU_p3'],
        } for r in extreme_changed],
        'months': rows_out,
    }
    Path('ququ-leadership-guard-analysis.json').write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({
        'period': out['period'],
        'variants': variants,
        'baselineMDDTroughSignal': trough_row,
        'extremeGuardChangedMonths': out['extremeGuardChangedMonths'],
        'extremeGuardChangedMonthDetails': out['extremeGuardChangedMonthDetails'],
    }, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
