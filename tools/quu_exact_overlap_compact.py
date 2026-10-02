#!/usr/bin/env python3
import json, math, statistics, requests
from pathlib import Path

HIST_URL='https://raw.githubusercontent.com/rpredpill/xlg-blender-render/somx-pages/quu-history.json'
STATE_URL='https://raw.githubusercontent.com/rpredpill/xlg-blender-render/somx-pages/quu-state-matrix-study.json'

hist=requests.get(HIST_URL,timeout=30).json()
state=requests.get(STATE_URL,timeout=30).json()
proxy=json.loads(Path('research/quu-proxy-overlap-input.json').read_text())

def perf(rs):
    eq=1.0; peak=1.0; mdd=0.0
    for r in rs:
        eq*=1+r/100.0; peak=max(peak,eq); mdd=min(mdd,eq/peak-1)
    n=len(rs); cagr=eq**(12/n)-1 if n else float('nan')
    return {'months':n,'totalReturnPct':(eq-1)*100,'CAGRpct':cagr*100,'MDDpct':mdd*100,'Calmar':cagr/abs(mdd) if mdd<0 else None}

def corr(a,b):
    if len(a)<2:return None
    ma=sum(a)/len(a); mb=sum(b)/len(b)
    sa=sum((x-ma)**2 for x in a); sb=sum((y-mb)**2 for y in b)
    return sum((x-ma)*(y-mb) for x,y in zip(a,b))/math.sqrt(sa*sb) if sa and sb else None

out={
  'generatedAt':hist.get('generatedAt'),
  'rules':hist.get('rules'),
  'metricsCompletedMonths':hist.get('metricsCompletedMonths'),
  'months':{},
}
for m,x in sorted(hist.get('months',{}).items()):
    if '2022-10' <= m <= '2025-12':
        out['months'][m]={
          'selectedSleeve':x.get('selectedSleeve'),
          'portfolioReturn':x.get('portfolioReturn'),
          'coverageRatio':x.get('coverageRatio'),
          'decision':x.get('decision'),
          'proxy':proxy.get(m)
        }

# Portfolio-level overlap, acknowledging that decisions can differ because proxy percentile history starts in 2011.
common=[m for m in sorted(proxy) if m in out['months'] and isinstance(out['months'][m].get('portfolioReturn'),(int,float))]
ex=[out['months'][m]['portfolioReturn'] for m in common]
pr=[proxy[m]['proxyReturn'] for m in common]
errors=[p-e for p,e in zip(pr,ex)]
same_sleeve=0
for m in common:
    es=out['months'][m]['selectedSleeve']
    ps={'PROXY':'QUQU','QQQ':'QQQ','QQQE':'QQQE'}[proxy[m]['sleeve']]
    same_sleeve += int(es==ps)
out['portfolioOverlap']={
  'period':[common[0],common[-1]],'months':len(common),
  'pearsonMonthlyReturn':corr(ex,pr),
  'meanProxyMinusExactPp':sum(errors)/len(errors),
  'MAEpp':sum(abs(x) for x in errors)/len(errors),
  'RMSEpp':math.sqrt(sum(x*x for x in errors)/len(errors)),
  'sameSleeveMonths':same_sleeve,'sameSleeveRatePct':same_sleeve/len(common)*100,
  'exactPerformance':perf(ex),'proxyPerformance':perf(pr)
}

# Direct risk-sleeve comparison from the existing exact state-matrix rows.
exact_rows={r['month']:r for r in state['exact2022_2026']['rows']}
risk_common=[m for m in sorted(proxy) if m in exact_rows]
er=[exact_rows[m]['riskReturn'] for m in risk_common]
rr=[proxy[m]['riskReturn'] for m in risk_common]
re=[p-e for p,e in zip(rr,er)]
out['riskSleeveOverlap']={
  'period':[risk_common[0],risk_common[-1]],'months':len(risk_common),
  'pearsonMonthlyReturn':corr(er,rr),
  'meanProxyMinusExactPp':sum(re)/len(re),
  'MAEpp':sum(abs(x) for x in re)/len(re),
  'RMSEpp':math.sqrt(sum(x*x for x in re)/len(re)),
  'exactPerformance':perf(er),'proxyPerformance':perf(rr)
}

# Compare state classification itself.
same_state=0; same_level=0; same_dir=0; rows=[]
for m in risk_common:
    e=exact_rows[m]; p=proxy[m]
    elevel=e['percentile']>=50; plevel=p['percentile']>=50
    edir=e['falling']; pdir=p['falling']
    same_level+=int(elevel==plevel); same_dir+=int(edir==pdir); same_state+=int(elevel==plevel and edir==pdir)
    rows.append({'month':m,'exactPercentile':e['percentile'],'proxyPercentile':p['percentile'],'exactFalling':edir,'proxyFalling':pdir,'exactState':e['state'],'proxyState':('High' if plevel else 'Low')+(' + Falling' if pdir else ' + Rising')})
out['stateOverlap']={
  'months':len(risk_common),
  'sameLevelRatePct':same_level/len(risk_common)*100,
  'sameDirectionRatePct':same_dir/len(risk_common)*100,
  'sameFullStateRatePct':same_state/len(risk_common)*100,
  'pearsonPercentile':corr([exact_rows[m]['percentile'] for m in risk_common],[proxy[m]['percentile'] for m in risk_common]),
  'rows':rows
}

Path('quu-exact-overlap-compact.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
print(json.dumps({k:out[k] for k in ['portfolioOverlap','riskSleeveOverlap','stateOverlap'] if k in out},indent=2))
