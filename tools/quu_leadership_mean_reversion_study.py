#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from statistics import mean, median

SRC = Path('quu-leadership-percentile-study.json')
OUT = Path('quu-leadership-mean-reversion-study.json')
CUTS = [80, 90, 95]
HORIZONS = [1, 2, 3, 6]


def avg(xs):
    return float(mean(xs)) if xs else None


def med(xs):
    return float(median(xs)) if xs else None


def summarize(events):
    if not events:
        return {
            'events': 0,
            'avgDispersionChangePct': None,
            'medianDispersionChangePct': None,
            'dispersionLowerRatePct': None,
            'avgGapClosedPct': None,
            'medianGapClosedPct': None,
            'reachedOrCrossedPriorMedianRatePct': None,
            'months': [],
        }
    changes = [e['dispersionChangePct'] for e in events]
    closes = [e['gapClosedPct'] for e in events]
    return {
        'events': len(events),
        'avgDispersionChangePct': avg(changes),
        'medianDispersionChangePct': med(changes),
        'dispersionLowerRatePct': 100.0 * sum(x < 0 for x in changes) / len(events),
        'avgGapClosedPct': avg(closes),
        'medianGapClosedPct': med(closes),
        'reachedOrCrossedPriorMedianRatePct': 100.0 * sum(e['futureDispersion'] <= e['priorMedianDispersion'] for e in events) / len(events),
        'months': [e['month'] for e in events],
    }


def build_prior_medians(rows):
    ds = [float(r['dispersion']) for r in rows]
    out = []
    prior = []
    for d in ds:
        out.append(float(median(prior)) if prior else None)
        prior.append(d)
    return out


def analyze(sample):
    rows = sorted(sample['rows'], key=lambda r: r['month'])
    prior_medians = build_prior_medians(rows)
    result = {
        'months': len(rows),
        'cuts': {},
        'rolloverForwardPaths': [],
    }

    # Fixed percentile cutoffs; no return optimization.
    for cut in CUTS:
        cblock = {}
        for h in HORIZONS:
            groups = {'allExtreme': [], 'risingOrFlat': [], 'falling': []}
            for i, r in enumerate(rows):
                if i + h >= len(rows):
                    continue
                p = float(r['percentile'])
                if p < cut:
                    continue
                d0 = float(r['dispersion'])
                d1 = float(rows[i + h]['dispersion'])
                base = prior_medians[i]
                if base is None or d0 == 0 or d0 == base:
                    continue
                evt = {
                    'month': r['month'],
                    'futureMonth': rows[i + h]['month'],
                    'percentile': p,
                    'falling': bool(r['falling']),
                    'currentDispersion': d0,
                    'futureDispersion': d1,
                    'priorMedianDispersion': base,
                    'dispersionChangePct': 100.0 * (d1 / d0 - 1.0),
                    'gapClosedPct': 100.0 * (d0 - d1) / (d0 - base),
                }
                groups['allExtreme'].append(evt)
                groups['falling' if r['falling'] else 'risingOrFlat'].append(evt)
            cblock[str(h)] = {k: summarize(v) for k, v in groups.items()}
        result['cuts'][str(cut)] = cblock

    # Prespecified state-transition event: High+Rising -> High+Falling (50th percentile state definition).
    # This is the candidate rollover identified before this forward-dispersion study.
    for i in range(1, len(rows)):
        prev = rows[i - 1]
        cur = rows[i]
        prev_high_rising = float(prev['percentile']) >= 50.0 and not bool(prev['falling'])
        cur_high_falling = float(cur['percentile']) >= 50.0 and bool(cur['falling'])
        if not (prev_high_rising and cur_high_falling):
            continue
        path = {
            'month': cur['month'],
            'currentPercentile': float(cur['percentile']),
            'currentDispersion': float(cur['dispersion']),
            'priorMedianDispersion': prior_medians[i],
            'forward': {},
        }
        for h in HORIZONS:
            if i + h >= len(rows):
                continue
            d0 = float(cur['dispersion'])
            d1 = float(rows[i + h]['dispersion'])
            base = prior_medians[i]
            path['forward'][str(h)] = {
                'futureMonth': rows[i + h]['month'],
                'futureDispersion': d1,
                'dispersionChangePct': 100.0 * (d1 / d0 - 1.0),
                'gapClosedPct': None if base is None or d0 == base else 100.0 * (d0 - d1) / (d0 - base),
            }
        result['rolloverForwardPaths'].append(path)

    # Aggregate prespecified rollover paths by horizon.
    rollagg = {}
    for h in HORIZONS:
        ev = []
        for p in result['rolloverForwardPaths']:
            f = p['forward'].get(str(h))
            if not f or f['gapClosedPct'] is None:
                continue
            ev.append({
                'month': p['month'],
                'futureDispersion': f['futureDispersion'],
                'priorMedianDispersion': p['priorMedianDispersion'],
                'dispersionChangePct': f['dispersionChangePct'],
                'gapClosedPct': f['gapClosedPct'],
            })
        rollagg[str(h)] = summarize(ev)
    result['rolloverAggregate'] = rollagg
    return result


def main():
    src = json.loads(SRC.read_text(encoding='utf-8'))
    exact = analyze(src['exact2022_2026'])
    proxy = analyze(src['pre2018Proxy'])
    out = {
        'purpose': 'Test whether extreme cross-sectional momentum leadership dispersion mean-reverts after reaching high prior-only percentiles, and whether a prespecified High+Rising -> High+Falling rollover marks the start of normalization.',
        'definitions': {
            'extremeCuts': CUTS,
            'horizonsMonths': HORIZONS,
            'dispersionChangePct': '100 * (future dispersion / current dispersion - 1); negative means normalization.',
            'gapClosedPct': '100 * (current dispersion - future dispersion) / (current dispersion - current-date prior-median dispersion); positive means movement toward the prior median.',
            'percentile': 'Current dispersion percentile versus prior months only.',
            'rollover': 'Previous classified month High+Rising and current classified month High+Falling, with High defined as prior-only percentile >=50.',
            'lookahead': 'No lookahead in signal classification or baseline. Future dispersion is used only as the outcome being measured.',
        },
        'exact2022_2026': exact,
        'pre2018Proxy': proxy,
        'limitations': [
            'Recent exact history is short and 6-month forward observations are few.',
            'Overlapping extreme months create overlapping forward windows, so event observations are not independent.',
            'The pre-2018 sleeve is a RelativeMomentum^3 proxy without reliable historical market-cap weighting, but the dispersion process itself is directly measured from the historical constituent sample.',
            'Fixed 80/90/95 percentile cutoffs and 1/2/3/6 month horizons are descriptive; no cutoff is chosen by return optimization.'
        ]
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({
        'exactCuts': exact['cuts'],
        'exactRollover': exact['rolloverAggregate'],
        'proxyCuts': proxy['cuts'],
        'proxyRollover': proxy['rolloverAggregate'],
    }, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
