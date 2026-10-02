#!/usr/bin/env python3
from __future__ import annotations

import json, math, statistics
from pathlib import Path

SRC = Path('quu-rollover-magnitude-study.json')
OUT = Path('quu-rollover-level-study.json')


def mean(xs):
    return sum(xs)/len(xs) if xs else None


def median(xs):
    return statistics.median(xs) if xs else None


def pearson(xs, ys):
    if len(xs) < 2:
        return None
    mx, my = mean(xs), mean(ys)
    dx = [x-mx for x in xs]
    dy = [y-my for y in ys]
    den = math.sqrt(sum(x*x for x in dx) * sum(y*y for y in dy))
    return sum(x*y for x,y in zip(dx,dy))/den if den else None


def ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0]*len(xs)
    i = 0
    while i < len(order):
        j = i+1
        while j < len(order) and xs[order[j]] == xs[order[i]]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0
        for k in range(i,j):
            r[order[k]] = avg_rank
        i = j
    return r


def spearman(xs, ys):
    return pearson(ranks(xs), ranks(ys)) if len(xs) >= 2 else None


def slope(xs, ys):
    if len(xs) < 2:
        return None
    mx, my = mean(xs), mean(ys)
    den = sum((x-mx)**2 for x in xs)
    return sum((x-mx)*(y-my) for x,y in zip(xs,ys))/den if den else None


def sample_z(vals):
    if not vals:
        return []
    m = mean(vals)
    var = mean([(v-m)**2 for v in vals])
    sd = math.sqrt(var)
    return [(v-m)/sd if sd else 0.0 for v in vals]


def summarize(events):
    p = [float(e['currentPercentile']) for e in events]
    x = [float(e['excessPctPoints']) for e in events]
    r = [float(e['riskReturnPct']) for e in events]
    out = {
        'events': len(events),
        'pearsonCurrentPercentileVsExcess': pearson(p,x),
        'spearmanCurrentPercentileVsExcess': spearman(p,x),
        'pearsonCurrentPercentileVsRiskReturn': pearson(p,r),
        'spearmanCurrentPercentileVsRiskReturn': spearman(p,r),
        'slopeExcessPpPer100PercentilePoints': (slope(p,x)*100.0 if slope(p,x) is not None else None),
        'avgCurrentPercentile': mean(p),
        'medianCurrentPercentile': median(p),
        'avgExcessPctPoints': mean(x),
        'medianExcessPctPoints': median(x),
        'negativeExcessRatePct': 100.0*sum(v<0 for v in x)/len(x) if x else None,
        'eventsSortedByCurrentPercentile': sorted(events, key=lambda e: e['currentPercentile']),
    }
    loo=[]
    for i,e in enumerate(events):
        pp=p[:i]+p[i+1:]
        xx=x[:i]+x[i+1:]
        loo.append({
            'leftOutMonth':e['month'],
            'pearsonCurrentPercentileVsExcess':pearson(pp,xx),
            'spearmanCurrentPercentileVsExcess':spearman(pp,xx),
        })
    out['leaveOneOut']=loo
    out['allLOOPearsonNegative']=bool(loo) and all(v['pearsonCurrentPercentileVsExcess'] is not None and v['pearsonCurrentPercentileVsExcess']<0 for v in loo)
    out['allLOOSpearmanNegative']=bool(loo) and all(v['spearmanCurrentPercentileVsExcess'] is not None and v['spearmanCurrentPercentileVsExcess']<0 for v in loo)
    return out


def extract(block_name, block):
    # Source already contains only prespecified HR->HF events in this subsection.
    events=[]
    for e in block['eventsSortedByDrop']:
        events.append({
            'sample':block_name,
            'month':e['month'],
            'prevMonth':e['prevMonth'],
            'prevPercentile':float(e['prevPercentile']),
            'currentPercentile':float(e['currentPercentile']),
            'dropPct':float(e['dropPct']),
            'riskReturnPct':float(e['riskReturnPct']),
            'QQQReturnPct':float(e['QQQReturnPct']),
            'excessPctPoints':float(e['excessPctPoints']),
        })
    return events


def main():
    src=json.loads(SRC.read_text(encoding='utf-8'))
    exact_events=extract('exact2022_2026',src['exact2022_2026'])
    proxy_events=extract('pre2018Proxy',src['pre2018Proxy'])
    exact=summarize(exact_events)
    proxy=summarize(proxy_events)

    pooled=exact_events+proxy_events
    p=[e['currentPercentile'] for e in pooled]
    raw=[e['excessPctPoints'] for e in pooled]
    z=sample_z([e['excessPctPoints'] for e in exact_events])+sample_z([e['excessPctPoints'] for e in proxy_events])

    out={
        'purpose':'Test, without cutoff tuning, whether High+Rising -> High+Falling rollovers are worse when leadership remains at a higher current historical percentile after the turn.',
        'definition':{
            'event':'Previous month High+Rising and current month High+Falling, using prior-only dispersion percentile >=50 for High.',
            'residualLevel':'Current dispersion percentile versus prior months only, after the direction has turned down.',
            'hypothesisDirection':'If residual extreme leadership matters, correlation(current percentile, current-month QUQU-vs-QQQ excess return) should be negative.',
            'lookahead':'None. Current percentile and direction are signal information available before the allocation month.'
        },
        'exact2022_2026':exact,
        'pre2018Proxy':proxy,
        'pooledReference':{
            'events':len(pooled),
            'pearsonCurrentPercentileVsRawExcess_referenceOnly':pearson(p,raw),
            'spearmanCurrentPercentileVsRawExcess_referenceOnly':spearman(p,raw),
            'pearsonCurrentPercentileVsWithinSampleZExcess':pearson(p,z),
            'spearmanCurrentPercentileVsWithinSampleZExcess':spearman(p,z),
            'eventsSortedByCurrentPercentile':sorted(pooled,key=lambda e:e['currentPercentile']),
            'note':'Pooled raw excess is reference-only because pre-2018 uses a momentum^3 proxy. Within-sample z-score pooling is more comparable but exploratory.'
        },
        'crossSampleSignReplication':{
            'pearsonNegativeBoth': exact['pearsonCurrentPercentileVsExcess'] is not None and exact['pearsonCurrentPercentileVsExcess']<0 and proxy['pearsonCurrentPercentileVsExcess'] is not None and proxy['pearsonCurrentPercentileVsExcess']<0,
            'spearmanNegativeBoth': exact['spearmanCurrentPercentileVsExcess'] is not None and exact['spearmanCurrentPercentileVsExcess']<0 and proxy['spearmanCurrentPercentileVsExcess'] is not None and proxy['spearmanCurrentPercentileVsExcess']<0,
        },
        'limitations':[
            'There are only 5 recent exact and 9 pre-2018 proxy rollover events.',
            'Pre-2018 risk sleeve is RelativeMomentum^3 without reliable historical market-cap weighting, not exact QUQU.',
            'Correlation is descriptive and can be unstable with small event counts.',
            'No percentile cutoff, nonlinear transform, lag, or return-maximizing parameter is selected here.'
        ]
    }
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({
        'exact':exact,
        'proxy':proxy,
        'pooledReference':out['pooledReference'],
        'crossSampleSignReplication':out['crossSampleSignReplication']
    },ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
