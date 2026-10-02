#!/usr/bin/env python3
import json, math, statistics
from pathlib import Path
import requests

QUU_URL='https://raw.githubusercontent.com/rpredpill/xlg-blender-render/somx-pages/quu-history.json'
BENCH_URL='https://raw.githubusercontent.com/rpredpill/xlg-blender-render/somx-pages/benchmark-history.json'
START='2022-10'; END='2026-09'; CAP=0.20

def finite(x):
    return isinstance(x,(int,float)) and math.isfinite(x)

def cap_redistribute(raw, cap=CAP):
    names=list(raw)
    w={k:max(0.0,float(raw[k])) for k in names}
    s=sum(w.values())
    if s<=0: return {k:0.0 for k in names}
    w={k:v/s for k,v in w.items()}
    # iterative cap and redistribute excess proportional to uncapped weights
    for _ in range(100):
        over=[k for k,v in w.items() if v>cap+1e-14]
        if not over: break
        fixed=set(k for k,v in w.items() if v>=cap-1e-14)
        # cap all current over
        for k in over: w[k]=cap
        fixed=set(k for k,v in w.items() if v>=cap-1e-14)
        rem=1.0-sum(w[k] for k in fixed)
        free=[k for k in names if k not in fixed]
        if not free or rem<=0: break
        base=sum(raw[k] for k in free)
        if base<=0:
            each=rem/len(free)
            for k in free: w[k]=each
        else:
            for k in free: w[k]=rem*raw[k]/base
    s=sum(w.values())
    return {k:v/s for k,v in w.items()} if s>0 else w

def perf(rets):
    eq=1.0; peak=1.0; mdd=0.0
    for r in rets:
        eq*=1+r/100.0
        peak=max(peak,eq)
        mdd=min(mdd,eq/peak-1)
    n=len(rets)
    cagr=eq**(12/n)-1 if n else float('nan')
    return {'months':n,'totalReturnPct':(eq-1)*100,'CAGRpct':cagr*100,'MDDpct':mdd*100,'Calmar':cagr/abs(mdd) if mdd<0 else None,'endingIndex':eq*100}

def weighted_return(rows, mode):
    clean=[]
    for r in rows:
        m=r.get('momentum'); mc=r.get('marketCap'); rr=r.get('monthlyReturn'); t=r.get('ticker')
        if not t or not finite(m) or not finite(mc) or mc<=0 or not finite(rr): continue
        gross=1.0+m
        if gross<=0: continue
        clean.append((t,gross,mc,rr))
    if not clean: return None, None
    med=statistics.median(x[1] for x in clean)
    raw={}
    for t,g,mc,rr in clean:
        rel=g/med if med>0 else 1.0
        if mode=='actual': raw[t]=math.sqrt(mc)*(rel**3)
        elif mode=='mom3': raw[t]=rel**3
        elif mode=='sqrtcap': raw[t]=math.sqrt(mc)
        elif mode=='equal': raw[t]=1.0
        else: raise ValueError(mode)
    w=cap_redistribute(raw)
    ret=sum(w[t]*rr for t,g,mc,rr in clean)*100.0
    return ret,w

def qqq_map(obj):
    out={}
    # benchmark-history format may have months or rows; recursively inspect common shapes
    if isinstance(obj,dict):
        if isinstance(obj.get('months'),dict):
            for m,x in obj['months'].items():
                if isinstance(x,dict):
                    for k in ('QQQ','qqq','return','monthlyReturn','portfolioReturn'):
                        v=x.get(k)
                        if finite(v):
                            # monthlyReturn sometimes decimal; QQQ likely percentage in benchmark file; infer magnitude conservatively
                            out[m]=float(v)*100 if k=='monthlyReturn' and abs(v)<1 else float(v)
                            break
        for key in ('QQQ','qqq'):
            x=obj.get(key)
            if isinstance(x,dict):
                for m,v in x.items():
                    if finite(v): out[m]=float(v)
                    elif isinstance(v,dict):
                        for k in ('return','monthlyReturn','portfolioReturn'):
                            z=v.get(k)
                            if finite(z): out[m]=float(z)*100 if k=='monthlyReturn' and abs(z)<1 else float(z); break
    return out

quu=requests.get(QUU_URL,timeout=60).json()
bench=requests.get(BENCH_URL,timeout=60).json()
qqq=qqq_map(bench)
rowsout=[]
for m,x in sorted(quu.get('months',{}).items()):
    if not (START<=m<=END): continue
    rows=x.get('rows') or []
    vals={}
    weights={}
    for mode in ('actual','mom3','sqrtcap','equal'):
        vals[mode],weights[mode]=weighted_return(rows,mode)
    if vals['actual'] is None: continue
    # compare reconstructed actual weights vs stored weights on rows after renormalizing valid-return names
    stored={r['ticker']:r.get('weight') for r in rows if r.get('ticker') and finite(r.get('weight')) and finite(r.get('monthlyReturn'))}
    ss=sum(stored.values())
    if ss>0: stored={k:v/ss for k,v in stored.items()}
    common=set(stored)&set(weights['actual'])
    weight_mae=sum(abs(stored[k]-weights['actual'][k]) for k in common)/len(common) if common else None
    stored_ququ=sum(stored.get(r.get('ticker'),0)*r.get('monthlyReturn',0) for r in rows if finite(r.get('monthlyReturn')))*100 if ss>0 else None
    rowsout.append({'month':m,**{k:vals[k] for k in vals},'qqq':qqq.get(m),'weightMAE':weight_mae,'storedWeightReturn':stored_ququ,'actualMinusStoredPp':vals['actual']-stored_ququ if stored_ququ is not None else None})

summary={'period':[rowsout[0]['month'],rowsout[-1]['month']],'months':len(rowsout),'formulae':{
'actual':'sqrt(MarketCap) * RelativeMomentum^3, 20% cap',
'mom3':'RelativeMomentum^3 only, 20% cap',
'sqrtcap':'sqrt(MarketCap) only, 20% cap',
'equal':'equal weight, 20% cap'},
'performance':{k:perf([r[k] for r in rowsout if finite(r[k])]) for k in ('actual','mom3','sqrtcap','equal')},
'validation':{
'meanWeightMAE':sum(r['weightMAE'] for r in rowsout if finite(r['weightMAE']))/sum(1 for r in rowsout if finite(r['weightMAE'])),
'meanActualMinusStoredPp':sum(r['actualMinusStoredPp'] for r in rowsout if finite(r['actualMinusStoredPp']))/sum(1 for r in rowsout if finite(r['actualMinusStoredPp'])),
'maxAbsActualMinusStoredPp':max(abs(r['actualMinusStoredPp']) for r in rowsout if finite(r['actualMinusStoredPp']))},
'attribution':{},'rows':rowsout}
A=summary['performance']['actual']; B=summary['performance']['mom3']; C=summary['performance']['sqrtcap']; E=summary['performance']['equal']
summary['attribution']={
'actualMinusMom3CAGRpp':A['CAGRpct']-B['CAGRpct'],
'actualMinusSqrtCapCAGRpp':A['CAGRpct']-C['CAGRpct'],
'mom3MinusEqualCAGRpp':B['CAGRpct']-E['CAGRpct'],
'sqrtCapMinusEqualCAGRpp':C['CAGRpct']-E['CAGRpct'],
'actualMinusMom3TotalReturnPp':A['totalReturnPct']-B['totalReturnPct']}
Path('ququ-core-attribution.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in summary.items() if k!='rows'},indent=2))
