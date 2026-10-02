#!/usr/bin/env python3
from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import median

BINS = [(0,70),(70,80),(80,90),(90,95),(95,100.000001)]
MIN_PRIOR = 8


def pct_rank_prior(prior, current):
    if len(prior) < MIN_PRIOR:
        return None
    # Causal expanding percentile: current value compared only with prior months.
    return 100.0 * sum(1 for x in prior if x <= current) / len(prior)


def bucket_name(p):
    for lo, hi in BINS:
        if lo <= p < hi:
            return f'{lo:g}-{min(hi,100):g}'
    return '95-100'


def stats(rows):
    if not rows:
        return {'months':0,'avgExcessPctPoints':None,'medianExcessPctPoints':None,'QUQUwinRatePct':None,'monthsList':[]}
    xs=[r['excess'] for r in rows]
    return {
        'months':len(rows),
        'avgExcessPctPoints':sum(xs)/len(xs),
        'medianExcessPctPoints':median(xs),
        'QUQUwinRatePct':100.0*sum(x>0 for x in xs)/len(xs),
        'monthsList':[r['month'] for r in rows],
    }


def analyze(rows, risk_label):
    prior=[]; enriched=[]
    for r in rows:
        d=float(r['dispersion']); p=pct_rank_prior(prior,d)
        prev=prior[-1] if prior else None
        if p is not None:
            enriched.append({
                'month':r['month'], 'dispersion':d, 'percentile':p,
                'falling': bool(prev is not None and d < prev),
                'riskReturn':float(r[risk_label]), 'QQQ':float(r['QQQ']),
                'excess':float(r[risk_label])-float(r['QQQ']),
            })
        prior.append(d)

    buckets={}
    for lo,hi in BINS:
        key=f'{lo:g}-{min(hi,100):g}'
        grp=[x for x in enriched if lo <= x['percentile'] < hi]
        buckets[key]=stats(grp)

    # Top-percentile direction study, prespecified cutoffs only.
    top_direction={}
    for cut in [70,80,90,95]:
        top=[x for x in enriched if x['percentile'] >= cut]
        up=[x for x in top if not x['falling']]
        down=[x for x in top if x['falling']]
        top_direction[str(cut)]={
            'risingOrFlat':stats(up),
            'falling':stats(down),
        }

    avgs=[buckets[f'{lo:g}-{min(hi,100):g}']['avgExcessPctPoints'] for lo,hi in BINS]
    finite=[x for x in avgs if isinstance(x,(int,float)) and math.isfinite(x)]
    monotonic_nondec = len(finite)==len(avgs) and all(avgs[i] <= avgs[i+1] for i in range(len(avgs)-1))
    monotonic_last3 = all(isinstance(x,(int,float)) for x in avgs[-3:]) and avgs[-3] <= avgs[-2] <= avgs[-1]

    return {
        'monthsTotal':len(rows), 'monthsClassified':len(enriched),
        'bins':buckets,
        'topPercentileDirection':top_direction,
        'monotonicAvgExcessAll5Bins':monotonic_nondec,
        'monotonicAvgExcess80to100':monotonic_last3,
        'rows':enriched,
    }


def main():
    q=json.loads(Path('ququ-history.json').read_text(encoding='utf-8'))
    b=json.loads(Path('benchmark-history.json').read_text(encoding='utf-8'))
    exact=[]
    for m in sorted(set(q.get('months',{})) & set(b.get('months',{}))):
        qr=q['months'][m].get('portfolioReturn'); qq=b['months'][m].get('QQQ')
        rows=q['months'][m].get('rows') or []
        moms=[float(r['momentum']) for r in rows if isinstance(r.get('momentum'),(int,float))]
        if not isinstance(qr,(int,float)) or not isinstance(qq,(int,float)) or len(moms)<90: continue
        mu=sum(moms)/len(moms); d=(sum((x-mu)**2 for x in moms)/len(moms))**0.5
        exact.append({'month':m,'dispersion':d,'QUQU':float(qr),'QQQ':float(qq)})

    p=json.loads(Path('quu-pre2018-proxy-validation.json').read_text(encoding='utf-8'))
    proxy=[]
    for r in p.get('months',[]):
        if all(isinstance(r.get(k),(int,float)) for k in ['dispersion','proxyReturn','QQQ']):
            proxy.append({'month':r['month'],'dispersion':float(r['dispersion']),'PROXY':float(r['proxyReturn']),'QQQ':float(r['QQQ'])})

    out={
        'purpose':'Test whether leadership strength has a monotonic relationship with next-month QUQU-vs-QQQ excess return. No threshold is optimized.',
        'definition':{
            'percentile':'Current dispersion percentile rank versus prior months only',
            'minimumPriorMonths':MIN_PRIOR,
            'bins':['0-70','70-80','80-90','90-95','95-100'],
            'direction':'falling if current dispersion < immediately prior month dispersion',
            'lookahead':'none: current signal uses pre-allocation momentum dispersion and prior-only percentile reference',
        },
        'exact2022_2026':analyze(exact,'QUQU'),
        'pre2018Proxy':analyze(proxy,'PROXY'),
    }
    Path('quu-leadership-percentile-study.json').write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({
        'exact':{k:v for k,v in out['exact2022_2026'].items() if k!='rows'},
        'proxy':{k:v for k,v in out['pre2018Proxy'].items() if k!='rows'},
    },ensure_ascii=False,indent=2,allow_nan=False))

if __name__=='__main__': main()
