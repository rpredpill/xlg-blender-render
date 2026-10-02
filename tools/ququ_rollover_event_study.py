#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import numpy as np


def tukey_upper(xs: list[float]) -> float | None:
    if len(xs) < 8:
        return None
    a=np.asarray(xs,dtype=float)
    q1=float(np.quantile(a,.25)); q3=float(np.quantile(a,.75))
    return q3+1.5*(q3-q1)


def stats(rows: list[dict]) -> dict:
    if not rows:
        return {'months':0}
    ex=np.asarray([r['excess'] for r in rows],dtype=float)
    return {
        'months':len(rows),
        'avgExcessPctPoints':float(np.mean(ex)),
        'medianExcessPctPoints':float(np.median(ex)),
        'QUQUwinRatePct':float(np.mean(ex>0)*100),
        'avgQUQUpct':float(np.mean([r['QUQU'] for r in rows])),
        'avgQQQpct':float(np.mean([r['QQQ'] for r in rows])),
        'avgDispersion':float(np.mean([r['dispersion'] for r in rows])),
        'avgMedianMomentum':float(np.mean([r['medianMomentum'] for r in rows])),
        'avgPositiveBreadthPct':float(np.mean([r['positiveBreadthPct'] for r in rows])),
        'avgTop10WeightPct':float(np.mean([r['top10WeightPct'] for r in rows])),
        'avgEffectiveN':float(np.mean([r['effectiveN'] for r in rows])),
        'monthsList':[r['month'] for r in rows],
    }


def main():
    q=json.loads(Path('ququ-history.json').read_text(encoding='utf-8'))
    b=json.loads(Path('benchmark-history.json').read_text(encoding='utf-8'))
    months=sorted(set(q.get('months',{})) & set(b.get('months',{})))
    months=[m for m in months if isinstance(q['months'][m].get('portfolioReturn'),(int,float)) and isinstance(b['months'][m].get('QQQ'),(int,float))]
    prior=[]; out=[]
    for m in months:
        rec=q['months'][m]; rows=rec.get('rows') or []
        moms=np.asarray([float(r['momentum']) for r in rows if isinstance(r.get('momentum'),(int,float))],dtype=float)
        if len(moms)<90: raise RuntimeError((m,len(moms)))
        weights=sorted([float(r['weight']) for r in rows if isinstance(r.get('weight'),(int,float)) and float(r['weight'])>0],reverse=True)
        disp=float(np.std(moms,ddof=0)); prev=prior[-1] if prior else None; fence=tukey_upper(prior)
        extreme=bool(fence is not None and disp>fence)
        direction='first' if prev is None else ('falling' if disp<prev else 'rising_or_flat')
        p3=float(rec['portfolioReturn']); qqq=float(b['months'][m]['QQQ'])
        median=float(np.median(moms)); breadth=float(np.mean(moms>0)*100)
        top10=sum(weights[:10])*100
        eff=1.0/sum(w*w for w in weights) if weights else float('nan')
        if extreme and direction=='falling': cat='extreme_falling_rollover'
        elif extreme: cat='extreme_rising_or_flat'
        elif direction=='falling': cat='nonextreme_falling'
        else: cat='nonextreme_rising_or_flat'
        out.append({
            'month':m,'category':cat,'dispersion':disp,'priorDispersion':prev,'priorTukeyUpperFence':fence,'extreme':extreme,'direction':direction,
            'medianMomentum':median,'positiveBreadthPct':breadth,'top10WeightPct':top10,'effectiveN':eff,
            'QUQU':p3,'QQQ':qqq,'excess':p3-qqq,
        })
        prior.append(disp)

    cats={k:stats([r for r in out if r['category']==k]) for k in ['extreme_falling_rollover','extreme_rising_or_flat','nonextreme_falling','nonextreme_rising_or_flat']}
    roll=[r for r in out if r['category']=='extreme_falling_rollover']
    windows=[]
    pos={r['month']:i for i,r in enumerate(out)}
    for r in roll:
        i=pos[r['month']]
        windows.append({'eventMonth':r['month'],'rows':out[max(0,i-2):min(len(out),i+2)]})
    result={
        'generatedAt':datetime.now(timezone.utc).isoformat(),
        'period':{'start':months[0],'end':months[-1],'months':len(months)},
        'rule':'Extreme = current dispersion above Tukey upper fence computed only from prior months. Rollover = extreme AND current dispersion < immediately prior month dispersion.',
        'categories':cats,
        'rolloverEvents':roll,
        'eventWindows':windows,
    }
    Path('ququ-rollover-event-study.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({'period':result['period'],'categories':cats,'rolloverEvents':roll,'eventWindows':windows},ensure_ascii=False,indent=2,allow_nan=False))

if __name__=='__main__': main()
