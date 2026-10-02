#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import median, pstdev

import numpy as np
import pandas as pd

BASE_PATH = Path('/tmp/backfill_snpi_ququ.py')
spec = importlib.util.spec_from_file_location('quu_base', BASE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError('cannot load backfill base')
base = importlib.util.module_from_spec(spec)
sys.modules['quu_base'] = base
spec.loader.exec_module(base)

START = '2007-08'
END = '2026-09'
CAP = 0.20


def metrics(rs):
    a = np.asarray(rs, dtype=float) / 100.0
    if not len(a):
        return None
    eq = np.concatenate([[1.0], np.cumprod(1.0 + a)])
    peaks = np.maximum.accumulate(eq)
    dd = eq / peaks - 1.0
    cagr = float(eq[-1] ** (12.0 / len(a)) - 1.0)
    mdd = float(dd.min())
    return {
        'months': int(len(a)),
        'totalReturnPct': float((eq[-1] - 1.0) * 100.0),
        'CAGRpct': cagr * 100.0,
        'MDDpct': mdd * 100.0,
        'Calmar': cagr / abs(mdd) if mdd < 0 else None,
        'endingIndex': float(eq[-1] * 100.0),
    }


def cap_and_redistribute(raw, cap=CAP):
    free = set(raw)
    out = {}
    remaining = 1.0
    while free:
        total = sum(raw[s] for s in free)
        if total <= 0:
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


def normalize_100(rec):
    rows = list(rec.get('rows') or [])
    if len(rows) < 100:
        raise RuntimeError(f"only {len(rows)}/100 priced securities")
    if len(rows) > 100:
        # Same normalization rule used by the production historical backfill.
        rows = sorted(rows, key=lambda x: float(x.get('marketCap') or 0), reverse=True)[:100]
    if len(rows) != 100:
        raise RuntimeError(f'normalized holdings {len(rows)}')
    gross = [1.0 + float(x.get('momentum') or 0.0) for x in rows]
    med = float(np.median(gross))
    raw = {}
    for x in rows:
        cap = float(x.get('marketCap') or 0.0)
        g = 1.0 + float(x.get('momentum') or 0.0)
        if not (cap > 0 and g > 0 and med > 0):
            raise RuntimeError('bad cap/momentum')
        raw[x['ticker']] = math.sqrt(cap) * ((g / med) ** 3)
    w = cap_and_redistribute(raw)
    for x in rows:
        x['weight'] = float(w[x['ticker']])
    rows.sort(key=lambda x: x['weight'], reverse=True)
    missing_w = sum(x['weight'] for x in rows if x.get('monthlyReturn') is None)
    if missing_w > 0.02:
        raise RuntimeError(f'missing return weight {missing_w:.2%}')
    port = 100.0 * sum(x['weight'] * (float(x['monthlyReturn']) if x.get('monthlyReturn') is not None else 0.0) for x in rows)
    out = dict(rec)
    out['rows'] = rows
    out['holdingsCount'] = 100
    out['missingReturnWeight'] = float(missing_w)
    out['portfolioReturn'] = float(port)
    out['maxWeight'] = max(x['weight'] for x in rows)
    out['top10Weight'] = sum(x['weight'] for x in rows[:10])
    return out


def dispersion(rec):
    xs = [float(x['momentum']) for x in rec['rows'] if isinstance(x.get('momentum'), (int, float))]
    if len(xs) != 100:
        raise RuntimeError(f'dispersion rows {len(xs)}')
    return float(pstdev(xs))


def tukey(xs, k=1.5):
    if len(xs) < 8:
        return None
    a = np.asarray(xs, dtype=float)
    q1 = float(np.quantile(a, 0.25))
    q3 = float(np.quantile(a, 0.75))
    return q3 + k * (q3 - q1)


def qtile(xs, q):
    return float(np.quantile(np.asarray(xs, dtype=float), q)) if xs else None


def run_rule(records, qqq, leadership_q=0.5, tukey_k=1.5, min_drop=0.0):
    prior = []
    rows = []
    for month, rec in records:
        d = dispersion(rec)
        threshold = qtile(prior, leadership_q)
        fence = tukey(prior, tukey_k)
        prev = prior[-1] if prior else None
        leadership = True if threshold is None else d > threshold
        drop = ((prev - d) / prev) if prev and prev > 0 else None
        extreme = bool(fence is not None and d > fence)
        rollover = bool(extreme and prev is not None and d < prev and (drop is None or drop >= min_drop))
        sleeve = 'QUQU' if leadership and not rollover else 'QQQ'
        qr = float(rec['portfolioReturn'])
        br = float(qqq[month])
        rows.append({
            'month': month, 'dispersion': d, 'leadershipThreshold': threshold,
            'tukeyUpper': fence, 'priorDispersion': prev, 'leadership': leadership,
            'extreme': extreme, 'rollover': rollover, 'sleeve': sleeve,
            'QUQU': qr, 'QQQ': br, 'QUU': qr if sleeve == 'QUQU' else br,
        })
        prior.append(d)
    return rows


def contiguous_suffix(valid_months, end_month):
    s = set(valid_months)
    cur = end_month
    out = []
    while cur in s:
        out.append(cur)
        cur = base.month_add(cur, -1)
    return list(reversed(out))


def summarize_rows(rows):
    return {
        'QUQU': metrics([x['QUQU'] for x in rows]),
        'QQQ': metrics([x['QQQ'] for x in rows]),
        'LeadershipBaselineNoRollover': metrics([x['QUQU'] if x['leadership'] else x['QQQ'] for x in rows]),
        'QUU': metrics([x['QUU'] for x in rows]),
        'rolloverTriggers': [x for x in rows if x['rollover']],
        'qqqSleeveMonths': [x['month'] for x in rows if x['sleeve'] == 'QQQ'],
    }


def main():
    membership = base.load_membership(base.NDX100_URL)
    latest_signal = base.month_end(base.month_add(END, -1))
    begin = base.month_end(base.month_add(START, -6))
    symbols = set(base.members_at(membership, begin))
    for dt, tickers in membership:
        if begin < dt <= latest_signal:
            symbols.update(tickers)
    symbols.update(base.members_at(membership, latest_signal))
    symbols.add('QQQ')
    symbols = sorted(symbols)
    print('NDX union symbols', len(symbols), flush=True)

    price_start = str((pd.Period(START, freq='M') - 7).start_time.date())
    price_end = str((pd.Period(END, freq='M') + 2).start_time.date())
    prices = base.download_prices(symbols, price_start, price_end)
    print('price coverage', len(prices), '/', len(symbols), flush=True)
    shares = base.load_shares([s for s in symbols if s != 'QQQ'], price_start, price_end)

    valid = {}
    failures = {}
    qqq = {}
    m = START
    while m <= END:
        q = base.month_return(prices, 'QQQ', m)
        if q:
            qqq[m] = float(q['return']) * 100.0
        try:
            rec = normalize_100(base.strategy_month('QUQU-MAX-PIT', membership, prices, shares, m))
            if m not in qqq:
                raise RuntimeError('QQQ return unavailable')
            valid[m] = rec
            print(f"OK {m} QUQU={rec['portfolioReturn']:+.2f}% histCap={rec.get('historicalCapCount')} proxy={rec.get('currentSharesProxyCount')} medianCap={rec.get('medianCapFallbackCount')}", flush=True)
        except Exception as e:
            failures[m] = str(e)
            print('FAIL', m, e, flush=True)
        m = base.month_add(m, 1)

    suffix = contiguous_suffix(sorted(valid), END)
    if len(suffix) < 48:
        raise RuntimeError(f'final contiguous PIT suffix only {len(suffix)} months: {suffix[:1]}..{suffix[-1:] if suffix else []}')
    records = [(m, valid[m]) for m in suffix]
    base_rows = run_rule(records, qqq)
    headline = summarize_rows(base_rows)

    sensitivity = []
    for k in [1.0, 1.25, 1.5, 1.75, 2.0, 2.5]:
        r = run_rule(records, qqq, tukey_k=k)
        sensitivity.append({'dimension': 'tukey_k', 'value': k, 'metrics': metrics([x['QUU'] for x in r]), 'rolloverMonths': [x['month'] for x in r if x['rollover']]})
    for q in [0.40, 0.45, 0.50, 0.55, 0.60]:
        r = run_rule(records, qqq, leadership_q=q)
        sensitivity.append({'dimension': 'leadership_quantile', 'value': q, 'metrics': metrics([x['QUU'] for x in r]), 'rolloverMonths': [x['month'] for x in r if x['rollover']]})
    for d in [0.0, 0.05, 0.10, 0.20]:
        r = run_rule(records, qqq, min_drop=d)
        sensitivity.append({'dimension': 'rollover_min_drop', 'value': d, 'metrics': metrics([x['QUU'] for x in r]), 'rolloverMonths': [x['month'] for x in r if x['rollover']]})

    # Fixed-rule temporal splits, no refitting.
    split_defs = [
        ('2007-08_to_2011-12', '2007-08', '2011-12'),
        ('2012_to_2016', '2012-01', '2016-12'),
        ('2017_to_2021', '2017-01', '2021-12'),
        ('2022_to_2026-09', '2022-01', '2026-09'),
    ]
    subperiods = {}
    for name, a, b in split_defs:
        rr = [x for x in base_rows if a <= x['month'] <= b]
        if rr:
            subperiods[name] = summarize_rows(rr)

    # Event dependence: remove each rollover intervention one at a time.
    trigger_months = [x['month'] for x in base_rows if x['rollover']]
    event_dependence = []
    for t in trigger_months:
        rs = [x['QUQU'] if x['month'] == t else x['QUU'] for x in base_rows]
        event_dependence.append({'removedTrigger': t, 'metrics': metrics(rs)})

    # Monte Carlo sanity check: among leadership-strong months, randomly switch the same
    # number to QQQ and compare ending wealth. This is descriptive, not a p-value.
    leadership_months = [x for x in base_rows if x['leadership']]
    k = len(trigger_months)
    actual_end = headline['QUU']['endingIndex']
    rng = random.Random(20261002)
    mc = []
    if k and len(leadership_months) >= k:
        for _ in range(10000):
            chosen = {x['month'] for x in rng.sample(leadership_months, k)}
            rs = [(x['QQQ'] if x['month'] in chosen else (x['QUQU'] if x['leadership'] else x['QQQ'])) for x in base_rows]
            mc.append(metrics(rs)['endingIndex'])
    pct = (100.0 * sum(v <= actual_end for v in mc) / len(mc)) if mc else None

    data_quality = {
        'attemptedStart': START,
        'attemptedEnd': END,
        'attemptedMonths': len(pd.period_range(START, END, freq='M')),
        'validMonths': len(valid),
        'failedMonths': len(failures),
        'finalContiguousStart': suffix[0],
        'finalContiguousEnd': suffix[-1],
        'finalContiguousMonths': len(suffix),
        'failuresBeforeContiguousStart': {k: failures[k] for k in sorted(failures) if k < suffix[0]},
        'maxMedianCapFallbackCount': max(int(valid[m].get('medianCapFallbackCount') or 0) for m in suffix),
        'minHistoricalCapCount': min(int(valid[m].get('historicalCapCount') or 0) for m in suffix),
        'maxCurrentSharesProxyCount': max(int(valid[m].get('currentSharesProxyCount') or 0) for m in suffix),
        'marketCapCaveat': 'Historical shares are used when Yahoo supplies them; otherwise current-share proxies or cross-sectional median caps are used. Counts are reported and long-history results are research-grade, not licensed point-in-time fundamentals.',
    }

    out = {
        'generatedAt': datetime.now(timezone.utc).isoformat(),
        'fixedRule': {
            'leadership': 'dispersion > expanding prior median',
            'extreme': 'dispersion > expanding prior Q3 + 1.5×IQR',
            'rollover': 'extreme AND dispersion < previous month dispersion',
            'allocation': 'QUQU when leadership strong and no rollover; otherwise QQQ',
            'parametersFrozen': True,
        },
        'membershipSourceStart': '2007-02-01',
        'earliestAttemptedAllocationMonth': START,
        'dataQuality': data_quality,
        'headline': headline,
        'sensitivity': sensitivity,
        'subperiods': subperiods,
        'eventDependence': event_dependence,
        'randomSameCountSwitchSanity': {
            'trials': len(mc), 'actualEndingIndex': actual_end,
            'percentileVsRandomSameCountSwitches': pct,
            'randomMedianEndingIndex': float(np.median(mc)) if mc else None,
            'random95thEndingIndex': float(np.quantile(mc, 0.95)) if mc else None,
        },
        'failures': failures,
        'months': base_rows,
    }
    Path('quu-max-pit-validation.json').write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({
        'dataQuality': data_quality,
        'headline': headline,
        'randomSameCountSwitchSanity': out['randomSameCountSwitchSanity'],
    }, ensure_ascii=False, indent=2, allow_nan=False), flush=True)


if __name__ == '__main__':
    main()
