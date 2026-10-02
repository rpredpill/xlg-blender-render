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


def p2_return(record: dict) -> tuple[float, dict]:
    rows = list(record.get('rows') or [])
    gross = [1.0 + float(r['momentum']) for r in rows
             if isinstance(r.get('momentum'), (int, float)) and 1.0 + float(r['momentum']) > 0]
    if len(gross) < 90:
        raise RuntimeError(f'not enough gross observations: {len(gross)}')
    med = float(np.median(gross))
    raw: dict[str, float] = {}
    eligible: list[tuple[str, float | None]] = []
    for r in rows:
        t = str(r.get('ticker') or '').strip().upper()
        cap = float(r.get('marketCap') or 0)
        g = 1.0 + float(r.get('momentum') or 0)
        mr = r.get('monthlyReturn')
        if not t or cap <= 0 or g <= 0:
            continue
        raw[t] = math.sqrt(cap) * ((g / med) ** 2)
        eligible.append((t, mr if isinstance(mr, (int, float)) else None))
    if len(raw) < 90:
        raise RuntimeError(f'p2 eligible {len(raw)}')
    w = cap_and_redistribute(raw)
    missing_w = sum(w[t] for t, mr in eligible if mr is None)
    if missing_w > 0.02:
        raise RuntimeError(f'missing return weight {missing_w:.2%}')
    ret = sum(w[t] * (float(mr) if mr is not None else 0.0) for t, mr in eligible) * 100.0
    top = sorted(w.values(), reverse=True)
    return ret, {'maxWeight': max(top), 'top10Weight': sum(top[:10]), 'missingReturnWeight': missing_w}


def momentum_dispersion(record: dict) -> float:
    vals = [float(r['momentum']) for r in (record.get('rows') or []) if isinstance(r.get('momentum'), (int, float))]
    if len(vals) < 90:
        raise RuntimeError(f'not enough momentum observations: {len(vals)}')
    # One simple leadership metric: cross-sectional standard deviation of 6-1 momentum.
    return float(np.std(np.asarray(vals, dtype=float), ddof=0))


def metrics(returns_pct: list[float]) -> dict:
    arr = np.asarray(returns_pct, dtype=float) / 100.0
    eq = np.concatenate([[1.0], np.cumprod(1.0 + arr)])
    peaks = np.maximum.accumulate(eq)
    dd = eq / peaks - 1.0
    n = len(arr)
    total = float(eq[-1] - 1.0)
    cagr = float(eq[-1] ** (12.0 / n) - 1.0) if n else float('nan')
    mdd = float(dd.min())
    calmar = cagr / abs(mdd) if mdd < 0 else None
    return {
        'months': n,
        'totalReturnPct': total * 100.0,
        'CAGRpct': cagr * 100.0,
        'MDDpct': mdd * 100.0,
        'Calmar': calmar,
        'positiveMonthRatePct': float((arr > 0).mean() * 100.0),
        'bestMonthPct': float(arr.max() * 100.0),
        'worstMonthPct': float(arr.min() * 100.0),
        'endingIndex': float(eq[-1] * 100.0),
    }


def compound(xs_pct: list[float]) -> float:
    if not xs_pct:
        return 0.0
    return (float(np.prod(1.0 + np.asarray(xs_pct, dtype=float) / 100.0)) - 1.0) * 100.0


def main() -> None:
    qhist = json.loads(Path('ququ-history.json').read_text(encoding='utf-8'))
    bench = json.loads(Path('benchmark-history.json').read_text(encoding='utf-8'))
    months = sorted(set(qhist.get('months', {})) & set(bench.get('months', {})))
    months = [m for m in months
              if isinstance(qhist['months'][m].get('portfolioReturn'), (int, float))
              and isinstance(bench['months'][m].get('QQQ'), (int, float))]
    if len(months) < 36:
        raise RuntimeError(f'only {len(months)} common months')

    dispersions: list[float] = []
    rows_out: list[dict] = []
    r_p3: list[float] = []
    r_p2: list[float] = []
    r_qqq: list[float] = []
    r_p3p2: list[float] = []
    r_p3qqq: list[float] = []
    strong_p3: list[float] = []
    strong_p2: list[float] = []
    strong_qqq: list[float] = []
    weak_p3: list[float] = []
    weak_p2: list[float] = []
    weak_qqq: list[float] = []

    for idx, m in enumerate(months):
        rec = qhist['months'][m]
        p3 = float(rec['portfolioReturn'])
        p2, p2meta = p2_return(rec)
        qqq = float(bench['months'][m]['QQQ'])
        disp = momentum_dispersion(rec)

        # No fitted numeric threshold. Compare current pre-allocation dispersion with the median
        # of all prior observed monthly dispersions. First month defaults to p=3 because no history exists.
        threshold = float(np.median(dispersions)) if dispersions else None
        strong = True if threshold is None else disp > threshold
        p3p2 = p3 if strong else p2
        p3qqq = p3 if strong else qqq

        rows_out.append({
            'month': m,
            'momentumDispersion': disp,
            'priorExpandingMedian': threshold,
            'leadershipStrong': strong,
            'QUQU_p3': p3,
            'QUQU_p2': p2,
            'QQQ': qqq,
            'P3_P2_switch': p3p2,
            'P3_QQQ_switch': p3qqq,
            'p2MaxWeight': p2meta['maxWeight'],
            'p2Top10Weight': p2meta['top10Weight'],
        })

        r_p3.append(p3); r_p2.append(p2); r_qqq.append(qqq)
        r_p3p2.append(p3p2); r_p3qqq.append(p3qqq)
        if strong:
            strong_p3.append(p3); strong_p2.append(p2); strong_qqq.append(qqq)
        else:
            weak_p3.append(p3); weak_p2.append(p2); weak_qqq.append(qqq)
        dispersions.append(disp)

    variants = {
        'QUQU p=3': metrics(r_p3),
        'QUQU p=2': metrics(r_p2),
        'Leadership p3/p2': metrics(r_p3p2),
        'Leadership p3/QQQ': metrics(r_p3qqq),
        'QQQ': metrics(r_qqq),
    }
    for name, rs in [('QUQU p=3', r_p3), ('QUQU p=2', r_p2), ('Leadership p3/p2', r_p3p2), ('Leadership p3/QQQ', r_p3qqq)]:
        variants[name]['winRateVsQQQpct'] = float(np.mean(np.asarray(rs) > np.asarray(r_qqq)) * 100.0)

    breakdown = {
        'strongMonths': len(strong_p3),
        'weakMonths': len(weak_p3),
        'strong': {
            'p3CompoundPct': compound(strong_p3),
            'p2CompoundPct': compound(strong_p2),
            'QQQcompoundPct': compound(strong_qqq),
            'p3AvgMonthPct': float(np.mean(strong_p3)) if strong_p3 else None,
            'p2AvgMonthPct': float(np.mean(strong_p2)) if strong_p2 else None,
            'QQQavgMonthPct': float(np.mean(strong_qqq)) if strong_qqq else None,
            'p3WinVsP2Pct': float(np.mean(np.asarray(strong_p3) > np.asarray(strong_p2)) * 100.0) if strong_p3 else None,
            'p3WinVsQQQPct': float(np.mean(np.asarray(strong_p3) > np.asarray(strong_qqq)) * 100.0) if strong_p3 else None,
        },
        'weak': {
            'p3CompoundPct': compound(weak_p3),
            'p2CompoundPct': compound(weak_p2),
            'QQQcompoundPct': compound(weak_qqq),
            'p3AvgMonthPct': float(np.mean(weak_p3)) if weak_p3 else None,
            'p2AvgMonthPct': float(np.mean(weak_p2)) if weak_p2 else None,
            'QQQavgMonthPct': float(np.mean(weak_qqq)) if weak_qqq else None,
            'p2WinVsP3Pct': float(np.mean(np.asarray(weak_p2) > np.asarray(weak_p3)) * 100.0) if weak_p3 else None,
            'QQQwinVsP3Pct': float(np.mean(np.asarray(weak_qqq) > np.asarray(weak_p3)) * 100.0) if weak_p3 else None,
        },
    }

    out = {
        'generatedAt': datetime.now(timezone.utc).isoformat(),
        'period': {'start': months[0], 'end': months[-1], 'months': len(months)},
        'rules': {
            'leadershipMetric': 'Cross-sectional population standard deviation of current pre-allocation Nasdaq-100 6-1 momentum',
            'threshold': 'Current dispersion > median of all prior monthly dispersions = leadership strong; otherwise weak',
            'strongAction': 'QUQU p=3',
            'weakActionA': 'QUQU p=2',
            'weakActionB': 'QQQ',
            'firstMonth': 'Defaults to p=3 because no prior dispersion history exists',
            'lookahead': 'Current month QUQU signal rows are pre-allocation; threshold uses prior months only',
        },
        'variants': variants,
        'leadershipBreakdown': breakdown,
        'months': rows_out,
    }
    Path('ququ-leadership-comparison.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({'period': out['period'], 'variants': variants, 'leadershipBreakdown': breakdown}, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
