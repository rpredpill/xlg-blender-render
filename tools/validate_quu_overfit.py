#!/usr/bin/env python3
from __future__ import annotations

import itertools
import json
import math
from pathlib import Path
from statistics import median, pstdev

import numpy as np


def metrics(returns_pct):
    a=np.asarray(list(returns_pct),dtype=float)/100.0
    if len(a)==0:
        return {'months':0,'totalReturnPct':0.0,'CAGRpct':0.0,'MDDpct':0.0,'Calmar':None,'endingIndex':100.0}
    eq=np.concatenate([[1.0],np.cumprod(1.0+a)])
    peaks=np.maximum.accumulate(eq)
    dd=eq/peaks-1.0
    total=float(eq[-1]-1.0)
    cagr=float(eq[-1]**(12.0/len(a))-1.0)
    mdd=float(dd.min())
    return {
        'months':int(len(a)),
        'totalReturnPct':total*100.0,
        'CAGRpct':cagr*100.0,
        'MDDpct':mdd*100.0,
        'Calmar':(cagr/abs(mdd)) if mdd<0 else None,
        'endingIndex':float(eq[-1]*100.0),
    }


def dispersion(rows):
    xs=[float(r['momentum']) for r in rows if isinstance(r.get('momentum'),(int,float)) and math.isfinite(float(r['momentum']))]
    if len(xs)<80:
        raise RuntimeError(f'only {len(xs)} momentum values')
    return float(pstdev(xs))


def qtile(xs,q):
    if not xs:
        return None
    return float(np.quantile(np.asarray(xs,dtype=float),q))


def run_variant(records, leadership_q=0.5, tukey_k=1.5, rollover_drop=0.0):
    prior=[]
    out=[]
    for r in records:
        d=r['dispersion']
        threshold=qtile(prior,leadership_q)
        leadership=True if threshold is None else d>threshold
        fence=None
        if len(prior)>=8:
            q1=qtile(prior,0.25); q3=qtile(prior,0.75)
            fence=q3+tukey_k*(q3-q1)
        prev=prior[-1] if prior else None
        extreme=bool(fence is not None and d>fence)
        rollover=bool(extreme and prev is not None and d < prev*(1.0-rollover_drop))
        baseline='QUQU' if leadership else 'QQQ'
        sleeve='QUQU' if leadership and not rollover else 'QQQ'
        ret=r['ququ'] if sleeve=='QUQU' else r['qqq']
        base_ret=r['ququ'] if baseline=='QUQU' else r['qqq']
        out.append({**r,'leadershipThreshold':threshold,'tukeyFence':fence,'leadership':leadership,'extreme':extreme,'rollover':rollover,'baselineSleeve':baseline,'sleeve':sleeve,'return':ret,'baselineReturn':base_ret})
        prior.append(d)
    return out


def subset_metrics(rows,start=None,end=None,key='return'):
    sub=[r for r in rows if (start is None or r['month']>=start) and (end is None or r['month']<=end)]
    return metrics([r[key] for r in sub])


def main():
    qhist=json.loads(Path('ququ-history.json').read_text(encoding='utf-8'))
    bench=json.loads(Path('benchmark-history.json').read_text(encoding='utf-8'))
    qm=qhist.get('months') or {}; bm=bench.get('months') or {}
    records=[]
    for m in sorted(qm):
        rec=qm[m] or {}; rows=rec.get('rows') or []
        qr=rec.get('portfolioReturn'); br=(bm.get(m) or {}).get('QQQ')
        if len(rows)<80 or not isinstance(qr,(int,float)) or not isinstance(br,(int,float)):
            continue
        records.append({'month':m,'dispersion':dispersion(rows),'ququ':float(qr),'qqq':float(br)})
    if len(records)<36:
        raise RuntimeError(f'only {len(records)} completed months')

    fixed=run_variant(records,0.5,1.5,0.0)
    triggers=[r for r in fixed if r['rollover'] and r['baselineSleeve']=='QUQU']
    base_metrics=metrics([r['baselineReturn'] for r in fixed])
    fixed_metrics=metrics([r['return'] for r in fixed])
    ququ_metrics=metrics([r['ququ'] for r in fixed])
    qqq_metrics=metrics([r['qqq'] for r in fixed])

    # Event dependence: undo each or all rollover interventions while leaving leadership rule unchanged.
    leave_one=[]
    for t in triggers:
        arr=[(r['baselineReturn'] if r['month']==t['month'] else r['return']) for r in fixed]
        leave_one.append({'removedTrigger':t['month'],'metrics':metrics(arr),'savedPctPointsThatMonth':t['qqq']-t['ququ']})
    no_rollover=metrics([r['baselineReturn'] for r in fixed])

    # Excluding the largest saved month tests whether the headline survives that observation at all.
    biggest=max(triggers,key=lambda r:r['qqq']-r['ququ']) if triggers else None
    excl_big=[] if biggest is None else [r['return'] for r in fixed if r['month']!=biggest['month']]
    excl_big_base=[] if biggest is None else [r['baselineReturn'] for r in fixed if r['month']!=biggest['month']]

    # Exact data-mining check: among all pairs of months where leadership baseline held QUQU,
    # compare every possible two-month switch to QQQ with the actual rollover pair.
    candidate_idx=[i for i,r in enumerate(fixed) if r['baselineSleeve']=='QUQU']
    pair_stats=[]
    if len(triggers)==2:
        for a,b in itertools.combinations(candidate_idx,2):
            arr=[r['baselineReturn'] for r in fixed]
            arr[a]=fixed[a]['qqq']; arr[b]=fixed[b]['qqq']
            mm=metrics(arr)
            pair_stats.append({'months':[fixed[a]['month'],fixed[b]['month']],'endingIndex':mm['endingIndex'],'MDDpct':mm['MDDpct'],'CAGRpct':mm['CAGRpct']})
    actual_pair=sorted(r['month'] for r in triggers)
    actual_pair_stat=next((x for x in pair_stats if x['months']==actual_pair),None)
    pair_test=None
    if actual_pair_stat and pair_stats:
        wealth_pct=100.0*sum(x['endingIndex']<=actual_pair_stat['endingIndex']+1e-12 for x in pair_stats)/len(pair_stats)
        mdd_pct=100.0*sum(x['MDDpct']<=actual_pair_stat['MDDpct']+1e-12 for x in pair_stats)/len(pair_stats)
        pair_test={
            'candidateLeadershipMonths':len(candidate_idx),
            'possibleTwoSwitchPairs':len(pair_stats),
            'actualPair':actual_pair,
            'actualPairMetrics':actual_pair_stat,
            'endingWealthPercentileAmongAllPairs':wealth_pct,
            'MDDPercentileAmongAllPairsHigherIsBetter':mdd_pct,
            'top5ByEndingWealth':sorted(pair_stats,key=lambda x:x['endingIndex'],reverse=True)[:5],
        }

    sensitivity=[]
    for k in [1.0,1.25,1.5,1.75,2.0,2.5]:
        rr=run_variant(records,0.5,k,0.0)
        sensitivity.append({'dimension':'tukey_k','value':k,'triggerMonths':[r['month'] for r in rr if r['rollover'] and r['baselineSleeve']=='QUQU'],'metrics':metrics([r['return'] for r in rr])})
    for q in [0.40,0.45,0.50,0.55,0.60]:
        rr=run_variant(records,q,1.5,0.0)
        sensitivity.append({'dimension':'leadership_quantile','value':q,'triggerMonths':[r['month'] for r in rr if r['rollover'] and r['baselineSleeve']=='QUQU'],'metrics':metrics([r['return'] for r in rr])})
    for drop in [0.0,0.05,0.10,0.20]:
        rr=run_variant(records,0.5,1.5,drop)
        sensitivity.append({'dimension':'rollover_min_drop','value':drop,'triggerMonths':[r['month'] for r in rr if r['rollover'] and r['baselineSleeve']=='QUQU'],'metrics':metrics([r['return'] for r in rr])})

    periods={
        '2022-10_to_2024-12':{'start':'2022-10','end':'2024-12'},
        '2025':{'start':'2025-01','end':'2025-12'},
        '2026_YTD':{'start':'2026-01','end':'2026-09'},
        'pre_2025_first_trigger':{'start':'2022-10','end':'2025-01'},
        'through_2026-06_pre_big_crash':{'start':'2022-10','end':'2026-06'},
    }
    subperiods={}
    for name,p in periods.items():
        subperiods[name]={
            'QUU':subset_metrics(fixed,p['start'],p['end'],'return'),
            'LeadershipBaseline':subset_metrics(fixed,p['start'],p['end'],'baselineReturn'),
            'QUQU':metrics([r['ququ'] for r in fixed if p['start']<=r['month']<=p['end']]),
            'QQQ':metrics([r['qqq'] for r in fixed if p['start']<=r['month']<=p['end']]),
            'triggerMonths':[r['month'] for r in fixed if p['start']<=r['month']<=p['end'] and r['rollover'] and r['baselineSleeve']=='QUQU'],
        }

    payload={
        'period':{'start':records[0]['month'],'end':records[-1]['month'],'months':len(records)},
        'fixedRule':{
            'leadership':'dispersion > expanding prior median',
            'extreme':'dispersion > expanding prior Q3 + 1.5×IQR',
            'rollover':'extreme AND dispersion < previous month dispersion',
            'allocation':'QUQU when leadership strong and no rollover; otherwise QQQ',
        },
        'headline':{
            'QUQU':ququ_metrics,
            'QQQ':qqq_metrics,
            'LeadershipBaselineNoRollover':base_metrics,
            'QUU':fixed_metrics,
            'rolloverTriggerCount':len(triggers),
            'rolloverTriggers':[{'month':r['month'],'dispersion':r['dispersion'],'priorDispersion':next((x['dispersion'] for x in fixed if x['month']<r['month']),None),'QUQU':r['ququ'],'QQQ':r['qqq'],'savedPctPoints':r['qqq']-r['ququ']} for r in triggers],
        },
        'eventDependence':{
            'withoutAllRolloverInterventions':no_rollover,
            'leaveOneTriggerOut':leave_one,
            'largestSavedTrigger':None if biggest is None else {'month':biggest['month'],'savedPctPoints':biggest['qqq']-biggest['ququ']},
            'excludeLargestSavedMonthEntirely':None if biggest is None else {'QUU':metrics(excl_big),'LeadershipBaseline':metrics(excl_big_base)},
        },
        'twoSwitchExactPermutationTest':pair_test,
        'sensitivity':sensitivity,
        'subperiods':subperiods,
        'months':[{'month':r['month'],'dispersion':r['dispersion'],'leadership':r['leadership'],'extreme':r['extreme'],'rollover':r['rollover'],'sleeve':r['sleeve'],'QUQU':r['ququ'],'QQQ':r['qqq'],'QUU':r['return']} for r in fixed],
        'validationLimits':[
            'The QUU rule form was created after observing the 2026-07 drawdown; the 48-month sample is therefore not a genuine out-of-sample test.',
            'Only two rollover interventions occur in the available 48 completed months, so crash-guard reliability cannot be statistically established from this sample alone.',
            'No parameter is re-optimized in this validation; sensitivity tests perturb the frozen rule only to measure fragility.',
        ],
    }
    Path('quu-overfit-validation.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({'headline':payload['headline'],'eventDependence':payload['eventDependence'],'twoSwitchExactPermutationTest':pair_test,'sensitivity':sensitivity,'subperiods':subperiods},ensure_ascii=False,indent=2,allow_nan=False))

if __name__=='__main__':
    main()
