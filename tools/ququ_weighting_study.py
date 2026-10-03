#!/usr/bin/env python3
"""Same QUQU v2 constituents, 6-month cadence, three weight formulae.
Reuses audited return inputs; only new market/float cap inputs are downloaded.
"""
import argparse,json,math,csv,copy,hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pandas as pd
import ququ_v2_ablation as core
import ququ_v2_bulk_data as bulk
import ququ_cadence_analysis as audit
import ququ_rebalance_study as fast

NAMES=['FloatCap-6M','TotalCap-6M','QUQU-v2-6M']

def build_caps(inputs,cachefile):
 if cachefile.exists():return json.loads(cachefile.read_text())
 symbols=sorted({t for w in inputs['targets'].values() for t in w})
 valuation_cache={}
 original_valuation=bulk.load_current_valuation
 def cached_valuation(syms):
  key=tuple(sorted(syms))
  if key not in valuation_cache:valuation_cache[key]=original_valuation(syms)
  return valuation_cache[key]
 bulk.load_current_valuation=cached_valuation
 with ThreadPoolExecutor(max_workers=2) as ex:
  price_f=ex.submit(bulk.load_prices_hf,symbols,'2022-09-01','2026-10-01')
  shares_f=ex.submit(bulk.load_shares_hf_plus_proxy,symbols,'2022-09-01','2026-10-01',8)
  prices=price_f.result();shares=shares_f.result()
 floats=bulk.load_float_info_bulk(symbols,shares,8)
 out={'symbols':symbols,'months':{},'floatInfo':floats,'lastPriceDate':str(max(d.index.max() for d in prices.values()).date()),'sources':{'prices':bulk.PRICE_URLS,'shares':bulk.SHARES_URL,'floatSnapshot':bulk.VALUATION_URL}}
 for m,targets in inputs['targets'].items():
  signal=core.month_end(core.month_add(m,-1));rows={}
  for t in targets:
   cap,method=core.historical_cap(prices.get(t),shares.get(t,{ }),signal)
   if cap is None or not math.isfinite(cap) or cap<=0:raise ValueError(f'{m} {t}: no market cap')
   ratio=floats.get(t,{}).get('ratio')
   ratio=float(ratio) if ratio is not None else 1.
   rows[t]={'marketCap':cap,'floatCap':cap*ratio,'floatRatio':ratio,'capMethod':method,'floatMethod':floats.get(t,{}).get('method') or 'missing-assume-1.0'}
  out['months'][m]=rows
  print('caps',m,len(rows),flush=True)
 cachefile.write_text(json.dumps(out,allow_nan=False));return out

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--inputs',required=True);ap.add_argument('--benchmarks',required=True);ap.add_argument('--reference',required=True);ap.add_argument('--outdir',required=True);args=ap.parse_args()
 out=Path(args.outdir);out.mkdir(parents=True,exist_ok=True)
 inp=json.loads(Path(args.inputs).read_text());bench=json.loads(Path(args.benchmarks).read_text());ref=json.loads(Path(args.reference).read_text())
 caps=build_caps(inp,out/'ququ_weight_caps.json')
 months=[m for m in inp['months'] if m<inp['lastPriceDate'][:7]]
 variants={};inputs_by_variant={};diagnostics={}
 for name in NAMES:
  variant=copy.deepcopy(inp);meta={}
  for m in inp['months']:
   rr=caps['months'][m]
   if name!='QUQU-v2-6M':
    field='floatCap' if name.startswith('Float') else 'marketCap'
    variant['targets'][m]=core.cap_and_redistribute({t:r[field] for t,r in rr.items()})
   w=variant['targets'][m]
   assert set(w)==set(inp['targets'][m])
   assert abs(sum(w.values())-1)<1e-10
   assert max(w.values())<=.20+1e-10
   meta[m]={'holdings':len(w),'maxWeightPct':max(w.values())*100,'top10WeightPct':sum(sorted(w.values(),reverse=True)[:10])*100,'currentSharesProxyWeightPct':sum(w[t] for t,r in rr.items() if r['capMethod']=='current-shares-proxy')*100,'missingFloatProxyWeightPct':sum(w[t] for t,r in rr.items() if r['floatMethod']=='missing-assume-1.0')*100,'top10':[{'ticker':t,'weightPct':v*100} for t,v in sorted(w.items(),key=lambda x:x[1],reverse=True)[:10]]}
  inputs_by_variant[name]=variant;diagnostics[name]=meta
  rows=audit.simulate(months,variant,6,0,True)
  variants[name]={'metrics':audit.metrics(rows),'rows':rows}
 original=ref['results']['QUQU-6M']['rows']
 maxdiff=max(abs(a['return']-b['return']) for a,b in zip(variants['QUQU-v2-6M']['rows'],original));assert maxdiff<1e-12,maxdiff
 phases={};failures={}
 for name,variant in inputs_by_variant.items():
  phases[name]={}
  for phase in range(6):
   try:
    rows=audit.simulate(months,variant,6,phase,True);phases[name][str(phase)]={'metrics':audit.metrics(rows),'rows':rows}
   except ValueError as e:failures[name+'-phase'+str(phase)]=str(e)
 for name,values in bench.items():
  rows=[{'month':m,'return':values[m]['openClose'] if m==months[0] else values[m]['closeClose'],'turnover':0} for m in months]
  variants[name]={'metrics':audit.metrics(rows),'rows':rows}
 sensitivity={}
 for name,v in phases.items():
  vals=[x['metrics']['CAGRpct'] for x in v.values()]
  sensitivity[name]={'CAGRminPct':min(vals),'CAGRmedianPct':float(np.median(vals)),'CAGRmaxPct':max(vals),'validPhases':len(vals)}
 costs={name:{str(bp):audit.metrics(audit.net_rows(v['rows'],bp)) for bp in [0,10,25,50]} for name,v in variants.items() if name in NAMES}
 comparisons={}
 for name in NAMES:
  comparisons[name]={}
  for benchmark in ['QQQ','SPMO']:
   a=variants[name];b=variants[benchmark];comparisons[name][benchmark]={'CAGRgapPp':a['metrics']['CAGRpct']-b['metrics']['CAGRpct'],'monthlyWinRatePct':sum(x['return']>y['return'] for x,y in zip(a['rows'],b['rows']))/len(months)*100}
 result={'period':ref['period'],'formulas':{'FloatCap-6M':'Float-adjusted market capitalization, linear weight, capped at 20%','TotalCap-6M':'Total market capitalization, linear weight, capped at 20%','QUQU-v2-6M':'Original QUQU v2 sqrt(FloatCap) × relative adjusted momentum^3, capped at 20%'},'universe':'Exact same saved QUQU v2 constituent set at each allocation month; only weight formula differs. All rules for target selection, returns, corporate actions, 6M phases remain the same.','variants':variants,'phaseSensitivity':sensitivity,'phaseResults':phases,'costSensitivity':costs,'comparisons':comparisons,'allocationDiagnostics':diagnostics,'failures':failures,'consistency':{'original6MMaxAbsoluteReturnDiff':maxdiff},'corporateActions':inp['corporateActions'],'dataCaveat':'Latest free-float ratios are proxies for historical float. Some missing historical shares use current implied shares. Not a complete historical float-PIT backtest; month-end MDD; no tax; missing price weight up to 2% treated as zero.','targets':{n:v['targets'] for n,v in inputs_by_variant.items()}}
 (out/'ququ_weighting_comparison.json').write_text(json.dumps(result,indent=2,allow_nan=False))
 with (out/'ququ_weighting_summary.csv').open('w') as f:
  w=csv.DictWriter(f,fieldnames=['strategy']+list(variants[NAMES[0]]['metrics']));w.writeheader()
  for n,v in variants.items():w.writerow({'strategy':n,**v['metrics']})
 with (out/'ququ_weighting_monthly.csv').open('w') as f:
  w=csv.writer(f);w.writerow(['month']+list(variants))
  for i,m in enumerate(months):w.writerow([m]+[v['rows'][i]['return']*100 for v in variants.values()])
 print(json.dumps({'metrics':{n:v['metrics'] for n,v in variants.items()},'phases':sensitivity,'consistency':result['consistency'],'failures':failures},indent=2),flush=True)
if __name__=='__main__':main()
