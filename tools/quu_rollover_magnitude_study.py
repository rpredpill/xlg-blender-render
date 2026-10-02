#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

SRC = Path('quu-leadership-percentile-study.json')
OUT = Path('quu-rollover-magnitude-study.json')


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def median(xs):
    return statistics.median(xs) if xs else None


def pearson(xs, ys):
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mx, my = mean(xs), mean(ys)
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    den = math.sqrt(sum(v*v for v in dx) * sum(v*v for v in dy))
    return sum(a*b for a,b in zip(dx,dy)) / den if den > 0 else None


def average_ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i + 1
        while j < len(order) and xs[order[j]] == xs[order[i]]:
            j += 1
        r = ((i + 1) + j) / 2.0
        for k in range(i, j):
            ranks[order[k]] = r
        i = j
    return ranks


def spearman(xs, ys):
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    return pearson(average_ranks(xs), average_ranks(ys))


def lin_slope(xs, ys):
    if len(xs) < 2:
        return None
    mx, my = mean(xs), mean(ys)
    den = sum((x-mx)**2 for x in xs)
    return sum((x-mx)*(y-my) for x,y in zip(xs,ys)) / den if den > 0 else None


def state(row):
    high = float(row['percentile']) >= 50.0
    falling = bool(row['falling'])
    return ('High' if high else 'Low') + ('+Falling' if falling else '+Rising')


def analyze(block, sample_name):
    rows = sorted(block['rows'], key=lambda r:r['month'])
    events = []
    for i in range(1, len(rows)):
        prev, cur = rows[i-1], rows[i]
        if state(prev) == 'High+Rising' and state(cur) == 'High+Falling':
            d0 = float(prev['dispersion'])
            d1 = float(cur['dispersion'])
            drop = (d0-d1)/d0 if d0 else None
            events.append({
                'sample': sample_name,
                'month': cur['month'],
                'prevMonth': prev['month'],
                'prevDispersion': d0,
                'currentDispersion': d1,
                'dropRatio': drop,
                'dropPct': drop*100.0 if drop is not None else None,
                'prevPercentile': prev['percentile'],
                'currentPercentile': cur['percentile'],
                'riskReturnPct': float(cur['riskReturn']),
                'QQQReturnPct': float(cur['QQQ']),
                'excessPctPoints': float(cur['excess']),
            })

    xs = [e['dropRatio'] for e in events]
    ex = [e['excessPctPoints'] for e in events]
    risk = [e['riskReturnPct'] for e in events]

    # If deeper rollover is harmful, correlations with returns should be negative.
    stats = {
        'events': len(events),
        'pearsonDropVsExcess': pearson(xs, ex),
        'spearmanDropVsExcess': spearman(xs, ex),
        'pearsonDropVsRiskReturn': pearson(xs, risk),
        'spearmanDropVsRiskReturn': spearman(xs, risk),
        'slopeExcessPpPer100PctDrop': None if lin_slope(xs, ex) is None else lin_slope(xs, ex),
        'avgDropPct': None if not xs else mean(xs)*100.0,
        'medianDropPct': None if not xs else median(xs)*100.0,
        'avgExcessPctPoints': mean(ex),
        'medianExcessPctPoints': median(ex),
        'negativeExcessRatePct': None if not ex else 100.0*sum(v<0 for v in ex)/len(ex),
        'eventsSortedByDrop': sorted(events, key=lambda e:e['dropRatio']),
    }

    # Leave-one-out sign stability. No parameter fitting; just checks whether one event drives the correlation sign.
    loo=[]
    if len(events) >= 4:
        for i,e in enumerate(events):
            xx=xs[:i]+xs[i+1:]
            yy=ex[:i]+ex[i+1:]
            loo.append({
                'leftOutMonth': e['month'],
                'pearsonDropVsExcess': pearson(xx,yy),
                'spearmanDropVsExcess': spearman(xx,yy),
            })
    stats['leaveOneOut']=loo
    stats['allLOOPearsonNegative']=bool(loo) and all(x['pearsonDropVsExcess'] is not None and x['pearsonDropVsExcess']<0 for x in loo)
    stats['allLOOSpearmanNegative']=bool(loo) and all(x['spearmanDropVsExcess'] is not None and x['spearmanDropVsExcess']<0 for x in loo)
    return stats, events


def combined_stats(events):
    xs=[e['dropRatio'] for e in events]
    ex=[e['excessPctPoints'] for e in events]
    # Normalize excess within each sample to z-scores before a pooled correlation, because exact QUQU and proxy have different volatility scales.
    by_sample={}
    for e in events:
        by_sample.setdefault(e['sample'],[]).append(e)
    zmap={}
    for s,es in by_sample.items():
        vals=[e['excessPctPoints'] for e in es]
        m=mean(vals)
        sd=statistics.pstdev(vals) if len(vals)>1 else 0.0
        for e in es:
            zmap[(s,e['month'])]=(e['excessPctPoints']-m)/sd if sd>0 else 0.0
    zs=[zmap[(e['sample'],e['month'])] for e in events]
    return {
        'events':len(events),
        'pearsonDropVsRawExcess_referenceOnly':pearson(xs,ex),
        'spearmanDropVsRawExcess_referenceOnly':spearman(xs,ex),
        'pearsonDropVsWithinSampleZExcess':pearson(xs,zs),
        'spearmanDropVsWithinSampleZExcess':spearman(xs,zs),
        'eventsSortedByDrop':sorted(events,key=lambda e:e['dropRatio']),
        'note':'Pooled raw excess is reference-only because pre-2018 uses a momentum^3 proxy. Within-sample z-score pooling is more comparable but still exploratory.'
    }


def main():
    src=json.loads(SRC.read_text(encoding='utf-8'))
    exact, ee=analyze(src['exact2022_2026'],'exact2022_2026')
    proxy, pe=analyze(src['pre2018Proxy'],'pre2018Proxy')
    pooled=combined_stats(ee+pe)
    out={
        'purpose':'Test, without threshold tuning, whether deeper High+Rising -> High+Falling leadership rollovers are continuously associated with worse QUQU-vs-QQQ relative returns.',
        'definition':{
            'event':'Previous month High+Rising and current month High+Falling, using prior-only dispersion percentile >=50 for High.',
            'dropRatio':'(previous dispersion - current dispersion) / previous dispersion. Larger means a deeper leadership contraction.',
            'hypothesisDirection':'If rollover magnitude matters, correlation(dropRatio, current-month excess return) should be negative.',
            'lookahead':'None; state and dispersion are pre-allocation signal data already used by the existing studies.'
        },
        'exact2022_2026':exact,
        'pre2018Proxy':proxy,
        'pooledReference':pooled,
        'crossSampleSignReplication':{
            'pearsonNegativeBoth': exact['pearsonDropVsExcess'] is not None and proxy['pearsonDropVsExcess'] is not None and exact['pearsonDropVsExcess']<0 and proxy['pearsonDropVsExcess']<0,
            'spearmanNegativeBoth': exact['spearmanDropVsExcess'] is not None and proxy['spearmanDropVsExcess'] is not None and exact['spearmanDropVsExcess']<0 and proxy['spearmanDropVsExcess']<0,
        },
        'limitations':[
            'There are very few rollover events, especially in the recent exact sample.',
            'Pre-2018 risk sleeve is RelativeMomentum^3 without reliable historical market-cap weighting, not exact QUQU.',
            'Correlation does not establish causality and can be unstable with small event counts.',
            'No cutoff, polynomial transform, lag, or return-maximizing parameter is selected here.'
        ]
    }
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({
        'exact':{k:v for k,v in exact.items() if k not in ('eventsSortedByDrop','leaveOneOut')},
        'proxy':{k:v for k,v in proxy.items() if k not in ('eventsSortedByDrop','leaveOneOut')},
        'pooled':{k:v for k,v in pooled.items() if k!='eventsSortedByDrop'},
        'signs':out['crossSampleSignReplication'],
        'exactEvents':exact['eventsSortedByDrop'],
        'proxyEvents':proxy['eventsSortedByDrop'],
        'exactLOO':exact['leaveOneOut'],
        'proxyLOO':proxy['leaveOneOut'],
    },ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
