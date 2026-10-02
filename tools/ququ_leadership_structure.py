#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


def metrics(returns_pct: list[float]) -> dict:
    arr = np.asarray(returns_pct, dtype=float) / 100.0
    eq = np.concatenate([[1.0], np.cumprod(1.0 + arr)])
    peaks = np.maximum.accumulate(eq)
    dd = eq / peaks - 1.0
    n = len(arr)
    total = float(eq[-1] - 1.0)
    cagr = float(eq[-1] ** (12.0 / n) - 1.0) if n else float('nan')
    mdd = float(dd.min()) if len(dd) else float('nan')
    return {
        'months': n,
        'totalReturnPct': total * 100.0,
        'CAGRpct': cagr * 100.0,
        'MDDpct': mdd * 100.0,
        'Calmar': cagr / abs(mdd) if mdd < 0 else None,
        'endingIndex': float(eq[-1] * 100.0),
        'winRateVsQQQpct': None,
    }


def rankdata(a: np.ndarray) -> np.ndarray:
    order = np.argsort(a, kind='mergesort')
    ranks = np.empty(len(a), dtype=float)
    i = 0
    while i < len(a):
        j = i + 1
        while j < len(a) and a[order[j]] == a[order[i]]:
            j += 1
        r = (i + j - 1) / 2.0 + 1.0
        ranks[order[i:j]] = r
        i = j
    return ranks


def corr(x: list[float], y: list[float], spearman: bool = False) -> float | None:
    if len(x) < 3:
        return None
    a = np.asarray(x, dtype=float)
    b = np.asarray(y, dtype=float)
    if spearman:
        a = rankdata(a); b = rankdata(b)
    if np.std(a) == 0 or np.std(b) == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def signal_stats(values: list[float], excess: list[float]) -> dict:
    a = np.asarray(values, dtype=float)
    e = np.asarray(excess, dtype=float)
    med = float(np.median(a))
    hi = e[a > med]
    lo = e[a <= med]
    return {
        'months': int(len(a)),
        'overallMedian': med,
        'pearsonVsQUQUminusQQQ': corr(values, excess, False),
        'spearmanVsQUQUminusQQQ': corr(values, excess, True),
        'highMonths': int(len(hi)),
        'lowMonths': int(len(lo)),
        'highAvgExcessPctPoints': float(np.mean(hi)) if len(hi) else None,
        'lowAvgExcessPctPoints': float(np.mean(lo)) if len(lo) else None,
        'highWinRatePct': float(np.mean(hi > 0) * 100.0) if len(hi) else None,
        'lowWinRatePct': float(np.mean(lo > 0) * 100.0) if len(lo) else None,
    }


def main() -> None:
    qhist = json.loads(Path('ququ-history.json').read_text(encoding='utf-8'))
    bench = json.loads(Path('benchmark-history.json').read_text(encoding='utf-8'))
    months = sorted(set(qhist.get('months', {})) & set(bench.get('months', {})))
    months = [m for m in months
              if isinstance(qhist['months'][m].get('portfolioReturn'), (int, float))
              and isinstance(bench['months'][m].get('QQQ'), (int, float))]
    if len(months) < 36:
        raise RuntimeError(f'only {len(months)} common months')

    rows_out = []
    prev_top20: set[str] | None = None
    history = {'dispersion': [], 'top20Median': [], 'decileSpread': [], 'persistence': []}
    signals = {k: [] for k in history}
    excess_all = []
    p3_all, qqq_all = [], []
    switch = {k: [] for k in history}
    combo = []

    for i, m in enumerate(months):
        rec = qhist['months'][m]
        rows = [r for r in (rec.get('rows') or []) if isinstance(r.get('momentum'), (int, float))]
        if len(rows) < 90:
            raise RuntimeError(f'{m}: only {len(rows)} momentum rows')
        ranked = sorted(rows, key=lambda r: float(r['momentum']), reverse=True)
        moms = np.asarray([float(r['momentum']) for r in ranked], dtype=float)
        tickers = [str(r.get('ticker') or '').strip().upper() for r in ranked]
        top20 = set(tickers[:20])

        dispersion = float(np.std(moms, ddof=0))
        median_mom = float(np.median(moms))
        top20_median = float(np.mean(moms[:20]) - median_mom)
        decile = max(1, len(moms) // 10)
        decile_spread = float(np.mean(moms[:decile]) - np.mean(moms[-decile:]))
        persistence = None if prev_top20 is None else len(top20 & prev_top20) / 20.0

        vals = {
            'dispersion': dispersion,
            'top20Median': top20_median,
            'decileSpread': decile_spread,
            'persistence': persistence,
        }
        thresholds = {
            k: (float(np.median(v)) if v else None)
            for k, v in history.items()
        }
        strong = {
            k: (True if vals[k] is None or thresholds[k] is None else vals[k] > thresholds[k])
            for k in vals
        }

        p3 = float(rec['portfolioReturn'])
        qqq = float(bench['months'][m]['QQQ'])
        excess = p3 - qqq
        p3_all.append(p3); qqq_all.append(qqq); excess_all.append(excess)

        for k in history:
            if vals[k] is not None:
                signals[k].append(float(vals[k]))
            # First observation defaults to QUQU; afterwards current signal is compared only with prior observations.
            switch[k].append(p3 if strong[k] else qqq)

        # Minimal two-factor definition: QUQU only when both cross-sectional dispersion
        # and actual leader persistence are above their own prior expanding medians.
        combo_strong = strong['dispersion'] and strong['persistence']
        combo.append(p3 if combo_strong else qqq)

        rows_out.append({
            'month': m,
            'dispersion': dispersion,
            'top20MedianSpread': top20_median,
            'top10Bottom10Spread': decile_spread,
            'top20Persistence': persistence,
            'priorThresholds': thresholds,
            'strong': strong,
            'dispersionAndPersistenceStrong': combo_strong,
            'QUQU': p3,
            'QQQ': qqq,
            'QUQUminusQQQ': excess,
            'top20': sorted(top20),
        })

        for k in history:
            if vals[k] is not None:
                history[k].append(float(vals[k]))
        prev_top20 = top20

    variants = {
        'QUQU': metrics(p3_all),
        'QQQ': metrics(qqq_all),
        'Dispersion switch': metrics(switch['dispersion']),
        'Top20-Median switch': metrics(switch['top20Median']),
        'Top10-Bottom10 switch': metrics(switch['decileSpread']),
        'Top20 Persistence switch': metrics(switch['persistence']),
        'Dispersion AND Persistence': metrics(combo),
    }
    q = np.asarray(qqq_all)
    for name, rs in [('QUQU', p3_all),
                     ('Dispersion switch', switch['dispersion']),
                     ('Top20-Median switch', switch['top20Median']),
                     ('Top10-Bottom10 switch', switch['decileSpread']),
                     ('Top20 Persistence switch', switch['persistence']),
                     ('Dispersion AND Persistence', combo)]:
        variants[name]['winRateVsQQQpct'] = float(np.mean(np.asarray(rs) > q) * 100.0)

    # persistence starts one month later, so align diagnostic excess series accordingly.
    diagnostic = {
        'dispersion': signal_stats([r['dispersion'] for r in rows_out], [r['QUQUminusQQQ'] for r in rows_out]),
        'top20Median': signal_stats([r['top20MedianSpread'] for r in rows_out], [r['QUQUminusQQQ'] for r in rows_out]),
        'decileSpread': signal_stats([r['top10Bottom10Spread'] for r in rows_out], [r['QUQUminusQQQ'] for r in rows_out]),
        'persistence': signal_stats([r['top20Persistence'] for r in rows_out if r['top20Persistence'] is not None],
                                    [r['QUQUminusQQQ'] for r in rows_out if r['top20Persistence'] is not None]),
    }

    # Threshold-free event study: does a fall in persistence coincide with worse QUQU relative return?
    persistence_roll = []
    prev_p = None
    for r in rows_out:
        p = r['top20Persistence']
        if p is not None and prev_p is not None:
            persistence_roll.append({'month': r['month'], 'falling': p < prev_p, 'excess': r['QUQUminusQQQ'], 'persistence': p, 'priorPersistence': prev_p})
        if p is not None:
            prev_p = p
    fall = [x['excess'] for x in persistence_roll if x['falling']]
    nonfall = [x['excess'] for x in persistence_roll if not x['falling']]
    persistence_event = {
        'fallingMonths': len(fall),
        'nonFallingMonths': len(nonfall),
        'fallingAvgQUQUminusQQQ': float(np.mean(fall)) if fall else None,
        'nonFallingAvgQUQUminusQQQ': float(np.mean(nonfall)) if nonfall else None,
        'fallingQUQUWinRatePct': float(np.mean(np.asarray(fall) > 0) * 100.0) if fall else None,
        'nonFallingQUQUWinRatePct': float(np.mean(np.asarray(nonfall) > 0) * 100.0) if nonfall else None,
    }

    out = {
        'generatedAt': datetime.now(timezone.utc).isoformat(),
        'period': {'start': months[0], 'end': months[-1], 'months': len(months)},
        'definitions': {
            'dispersion': 'Population std.dev. of current pre-allocation Nasdaq-100 6-1 momentum',
            'top20Median': 'Mean momentum of top 20 minus cross-sectional median momentum',
            'decileSpread': 'Mean top 10% momentum minus mean bottom 10% momentum',
            'persistence': 'Fraction of prior month momentum Top20 that remains in current month Top20',
            'switchRule': 'Signal above median of all prior observed signal values => QUQU, otherwise QQQ; first observation defaults to QUQU',
            'combo': 'Dispersion above prior median AND Top20 persistence above prior median => QUQU, otherwise QQQ',
            'lookahead': 'All signal values are from pre-allocation rows; thresholds use prior months only',
        },
        'diagnostic': diagnostic,
        'persistenceRolloverEventStudy': persistence_event,
        'variants': variants,
        'months': rows_out,
    }
    Path('ququ-leadership-structure.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({
        'period': out['period'],
        'diagnostic': diagnostic,
        'persistenceRolloverEventStudy': persistence_event,
        'variants': variants,
    }, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
