#!/usr/bin/env python3
import json, math
from pathlib import Path
import requests

URL='https://raw.githubusercontent.com/rpredpill/xlg-blender-render/main/ququ-core-attribution.json'
obj=requests.get(URL,timeout=30).json()
rows=obj['rows']

def finite(x): return isinstance(x,(int,float)) and math.isfinite(x)
def perf(rs):
    eq=1.0; peak=1.0; mdd=0.0
    for r in rs:
        eq*=1+r/100; peak=max(peak,eq); mdd=min(mdd,eq/peak-1)
    n=len(rs); cagr=eq**(12/n)-1 if n else None
    return {'months':n,'totalReturnPct':(eq-1)*100,'CAGRpct':cagr*100 if cagr is not None else None,'MDDpct':mdd*100}

years={}
for r in rows:
    y=r['month'][:4]
    years.setdefault(y,[]).append(r)
by_year={}
for y,rr in years.items():
    by_year[y]={
      'months':len(rr),
      'actual':perf([x['actual'] for x in rr]),
      'mom3':perf([x['mom3'] for x in rr]),
      'sqrtCapContributionArithmeticPp':sum(x['actual']-x['mom3'] for x in rr),
      'avgMonthlyActualMinusMom3Pp':sum(x['actual']-x['mom3'] for x in rr)/len(rr),
      'positiveContributionMonths':sum(1 for x in rr if x['actual']>x['mom3'])
    }
contrib=sorted([{'month':r['month'],'actualMinusMom3Pp':r['actual']-r['mom3'],'actual':r['actual'],'mom3':r['mom3']} for r in rows], key=lambda x:x['actualMinusMom3Pp'], reverse=True)
all_sum=sum(x['actualMinusMom3Pp'] for x in contrib)
positive=[x for x in contrib if x['actualMinusMom3Pp']>0]
negative=[x for x in contrib if x['actualMinusMom3Pp']<0]
out={
  'period':obj['period'],
  'months':len(rows),
  'byYear':by_year,
  'contributionSummary':{
    'arithmeticTotalActualMinusMom3Pp':all_sum,
    'positiveMonths':len(positive),
    'negativeMonths':len(negative),
    'medianMonthlyContributionPp':sorted(x['actualMinusMom3Pp'] for x in contrib)[len(contrib)//2],
    'top3PositiveContributionPp':sum(x['actualMinusMom3Pp'] for x in positive[:3]),
    'top5PositiveContributionPp':sum(x['actualMinusMom3Pp'] for x in positive[:5]),
    'top3ShareOfNetPct':100*sum(x['actualMinusMom3Pp'] for x in positive[:3])/all_sum if all_sum else None,
    'top5ShareOfNetPct':100*sum(x['actualMinusMom3Pp'] for x in positive[:5])/all_sum if all_sum else None
  },
  'topPositiveMonths':positive[:10],
  'topNegativeMonths':sorted(negative,key=lambda x:x['actualMinusMom3Pp'])[:10]
}
Path('ququ-core-attribution-breakdown.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
print(json.dumps(out,indent=2))
