#!/usr/bin/env python3
"""Audit cadence results from saved QUQU targets and monthly adjusted returns.
Keeps monthly targets unchanged. Accounts for carried holdings' overnight gaps,
checks every calendar phase, and separates incomplete September observations.
"""
import argparse, json, math, csv
from pathlib import Path
import numpy as np

LIMIT=.02

def norm(w):
 s=sum(w.values());assert s>0
 return {k:v/s for k,v in w.items() if v>0}

def mark(w, prices, kind):
 missing=sum(v for t,v in w.items() if t!='__CASH__' and prices.get(t,{}).get(kind) is None)
 vals={t: v*max(0.,1+(prices.get(t,{}).get(kind) or 0.)) for t,v in w.items()}
 gross=sum(vals.values())
 if gross<=0:raise ValueError('portfolio lost all value')
 return gross,norm(vals),missing

def target_weights(inputs, month, continuous):
 w=dict(inputs['targets'][month])
 if continuous:
  for t,action in inputs.get('corporateActions',{}).items():
   if action['effectiveDate'][:7]<month and t in w:
    w['__CASH__']=w.get('__CASH__',0)+w.pop(t)
 return norm(w)

def cash_conversion(w, inputs, month, continuous):
 if continuous:
  for t,action in inputs.get('corporateActions',{}).items():
   if action['effectiveDate'][:7]==month and t in w:
    w['__CASH__']=w.get('__CASH__',0)+w.pop(t)
 return w

def turn(a,b):return .5*sum(abs(a.get(t,0)-b.get(t,0)) for t in set(a)|set(b))

def simulate(months, inputs, cadence, phase=0, continuous=True):
 w=None;recs=[]
 for i,m in enumerate(months):
  target=target_weights(inputs,m,continuous);p=inputs['stockReturns'][m]
  rebal=i==0 or i%cadence==phase
  gap=1.;gapmiss=0.
  if w is not None and continuous and rebal:
   gap,w,gapmiss=mark(w,p,'gap')
  tr=0. if i==0 or not rebal else turn(w,target)
  start=target if rebal else w
  kind='openClose' if rebal or not continuous else 'closeClose'
  gross,w,missing=mark(start,p,kind)
  if max(missing,gapmiss)>LIMIT+1e-12:raise ValueError(f'{m}: missing {max(missing,gapmiss):.4%}')
  w=cash_conversion(w,inputs,m,continuous)
  r=gap*gross-1
  recs.append({'month':m,'return':r,'turnover':tr,'rebalanced':rebal,'missingWeight':missing,'gapMissingWeight':gapmiss})
 return recs

def sleeves(months, inputs, continuous=True):
 ss=[{'w':None,'nav':1/3} for _ in range(3)];out=[]
 for i,m in enumerate(months):
  p=inputs['stockReturns'][m];total=sum(s['nav'] for s in ss)
  missing=gapmissing=tr=0.
  for j,s in enumerate(ss):
   rebal=i==0 or i%3==j
   gap=1.;gm=0.
   if i and continuous and rebal:
    gap,s['w'],gm=mark(s['w'],p,'gap')
   sw=target_weights(inputs,m,continuous) if rebal else s['w']
   gross,ew,miss=mark(sw,p,'openClose' if rebal or not continuous else 'closeClose')
   share=s['nav']/total
   if i and rebal:tr+=share*gap*turn(s['w'],sw)
   missing+=share*miss;gapmissing+=share*gm
   s['w']=cash_conversion(ew,inputs,m,continuous);s['nav']*=gap*gross
  if max(missing,gapmissing)>LIMIT+1e-12:raise ValueError(f'{m}: aggregate missing {max(missing,gapmissing):.4%}')
  out.append({'month':m,'return':sum(s['nav'] for s in ss)/total-1,'turnover':tr,'missingWeight':missing,'gapMissingWeight':gapmissing})
 return out

def metrics(rows):
 a=np.array([r['return'] for r in rows]);n=len(a);eq=np.r_[1,np.cumprod(1+a)]
 highs=np.maximum.accumulate(eq);dd=eq/highs-1
 run=longest=0;closed=[];peak=0
 for i,x in enumerate(eq[1:],1):
  if x>=highs[i]-1e-12:
   if run:closed.append(i-peak)
   run=0;peak=i
  else:run+=1;longest=max(longest,run)
 ts=[r['turnover'] for r in rows[1:]]
 return {'months':n,'totalReturnPct':float((eq[-1]-1)*100),'CAGRpct':float((eq[-1]**(12/n)-1)*100),
 'MDDpct':float(dd.min()*100),'Sharpe0rf':float(a.mean()/a.std(ddof=1)*12**.5),
 'annualizedTurnoverPct':float(sum(ts)/n*12*100),'positiveMonthRatePct':float((a>0).mean()*100),
 'newHighMonthPct':float((eq[1:]>=highs[1:]-1e-12).mean()*100),'longestUnderwaterMonths':longest,
 'longestCompletedRecoveryMonths':max(closed,default=0),'currentUnderwaterMonths':run,
 'maxMissingWeightPct':max(r.get('missingWeight',0) for r in rows)*100,
 'maxGapMissingWeightPct':max(r.get('gapMissingWeight',0) for r in rows)*100,
 'endingIndex':float(eq[-1]*100)}

def net_rows(rows,bps):
 # bps on each unit of gross traded notional; two-way notional = 2*one-way turnover.
 return [{**r,'return':(1+r['return'])*(1-2*r['turnover']*bps/10000)-1} for r in rows]

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--inputs',required=True);ap.add_argument('--benchmarks',required=True);ap.add_argument('--legacy',required=True);ap.add_argument('--outdir',required=True);args=ap.parse_args()
 inp=json.loads(Path(args.inputs).read_text());original_inp=json.loads(Path(args.inputs).with_name('ququ-rebalance-inputs.json').read_text());bench=json.loads(Path(args.benchmarks).read_text());legacy=json.loads(Path(args.legacy).read_text())
 out=Path(args.outdir);out.mkdir(exist_ok=True,parents=True)
 # Source ends on 2026-09-09: latest month is a partial period.
 end=inp['lastPriceDate'][:7];months=[m for m in inp['months'] if m<end]
 results={};failures={};phase_results={}
 for c in [1,2,3,6]:
  for phase in range(c):
   name=f'QUQU-{c}M-phase{phase}'
   try:
    rows=simulate(months,inp,c,phase,True);phase_results[name]={'metrics':metrics(rows),'rows':rows}
    if phase==0:results[f'QUQU-{c}M']=phase_results[name]
   except ValueError as e:failures[name]=str(e)
 try:
  rows=sleeves(months,inp,True);results['QUQU-3S']={'metrics':metrics(rows),'rows':rows}
 except ValueError as e:failures['QUQU-3S']=str(e)
 for name,vals in bench.items():
  rows=[{'month':m,'return':(vals[m]['openClose'] if m==months[0] else vals[m]['closeClose']),'turnover':0} for m in months]
  results[name]={'metrics':metrics(rows),'rows':rows}
 comparisons={}
 for name,result in results.items():
  if not name.startswith('QUQU'):continue
  comparisons[name]={}
  for bm in ['QQQ','SPMO']:
   rr=result['rows'];bb=results[bm]['rows'];rel=[{'return':(1+r['return'])/(1+b['return'])-1,'turnover':0} for r,b in zip(rr,bb)]
   comparisons[name][bm]={'CAGRgapPp':result['metrics']['CAGRpct']-results[bm]['metrics']['CAGRpct'],'monthlyWinRatePct':sum(r['return']>b['return'] for r,b in zip(rr,bb))/len(rr)*100,'relativeTotalReturnPct':metrics(rel)['totalReturnPct'],'relativeMDDpct':metrics(rel)['MDDpct']}
 sensitivity={}
 for c in [1,2,3,6]:
  vals=[v['metrics']['CAGRpct'] for k,v in phase_results.items() if k.startswith(f'QUQU-{c}M-')]
  sensitivity[f'{c}M']={'CAGRminPct':min(vals) if vals else None,'CAGRmedianPct':float(np.median(vals)),'CAGRmaxPct':max(vals) if vals else None,'validPhases':len(vals),'requestedPhases':c}
 costs={name:{str(bps):metrics(net_rows(v['rows'],bps)) for bps in [0,10,25,50]} for name,v in results.items() if name.startswith('QUQU')}
 consistency={}
 for c in [1,2,3,6]:
  rr=simulate(original_inp['months'],original_inp,c,0,False)
  orig=legacy['variants'][f'QUQU-{c}M']['months']
  diff=max(abs(r['return']-b['returnPct']/100) for r,b in zip(rr,orig));assert diff<1e-10
  consistency[f'{c}M-maxAbsoluteLegacyDiff']=diff
 rr=sleeves(original_inp['months'],original_inp,False);orig=legacy['variants']['QUQU-3S']['months'];diff=max(abs(r['return']-b['returnPct']/100) for r,b in zip(rr,orig));assert diff<1e-10;consistency['3S-maxAbsoluteLegacyDiff']=diff
 summary={'period':{'start':months[0],'end':months[-1],'months':len(months),'lastSourcePriceDate':inp['lastPriceDate']},'method':'continuous holding returns; rebalance at first trading day adjusted open; retained holdings earn previous close-to-open gap; unchanged QUQU monthly targets; all calendar phases; 20% cap at resets only; monthly-end drawdown; before tax', 'corporateActions':inp.get('corporateActions',{}),'results':results,'phaseSensitivity':sensitivity,'phaseResults':phase_results,'costSensitivity':costs,'comparisons':comparisons,'failures':failures,'legacyConsistency':consistency,'legacyPartial48Months':legacy['summary']}
 (out/'ququ_cadence_audit.json').write_text(json.dumps(summary,indent=2,allow_nan=False))
 with (out/'ququ_cadence_summary.csv').open('w') as f:
  w=csv.DictWriter(f,fieldnames=['strategy']+list(next(iter(results.values()))['metrics']));w.writeheader()
  for name,v in results.items():w.writerow({'strategy':name,**v['metrics']})
 with (out/'ququ_cadence_monthly.csv').open('w') as f:
  w=csv.writer(f);w.writerow(['month']+list(results));
  for i,m in enumerate(months):w.writerow([m]+[v['rows'][i]['return']*100 for v in results.values()])
 print(json.dumps({'period':summary['period'],'metrics':{k:v['metrics'] for k,v in results.items()},'phases':sensitivity,'failures':failures,'comparisons':comparisons},indent=2))
if __name__=='__main__':main()
