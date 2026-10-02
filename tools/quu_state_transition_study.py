#!/usr/bin/env python3
import json, math, statistics
from collections import defaultdict

SRC='quu-state-matrix-study.json'
OUT='quu-state-transition-study.json'

with open(SRC,encoding='utf-8') as f:
    src=json.load(f)

STATE_ORDER=['Low+Rising','Low+Falling','High+Rising','High+Falling']

def mean(xs):
    return sum(xs)/len(xs) if xs else None

def median(xs):
    return statistics.median(xs) if xs else None

def pct(n,d):
    return 100*n/d if d else None

def metrics(items):
    if not items:
        return {'months':0,'avgExcessPctPoints':None,'medianExcessPctPoints':None,'winRatePct':None,
                'avgRiskReturnPct':None,'riskLossLeMinus5PctRatePct':None,'riskLossLeMinus10PctRatePct':None,
                'underperformQQQLeMinus5ppRatePct':None,'drawdownLeMinus10PctMonthRatePct':None,
                'worstRiskReturnPct':None,'worstExcessPctPoints':None,'monthsList':[]}
    ex=[x['excess'] for x in items]
    rr=[x['riskReturn'] for x in items]
    dd=[x.get('riskDrawdownPct') for x in items if isinstance(x.get('riskDrawdownPct'),(int,float))]
    return {
        'months':len(items),
        'avgExcessPctPoints':mean(ex),
        'medianExcessPctPoints':median(ex),
        'winRatePct':pct(sum(v>0 for v in ex),len(ex)),
        'avgRiskReturnPct':mean(rr),
        'riskLossLeMinus5PctRatePct':pct(sum(v<=-5 for v in rr),len(rr)),
        'riskLossLeMinus10PctRatePct':pct(sum(v<=-10 for v in rr),len(rr)),
        'underperformQQQLeMinus5ppRatePct':pct(sum(v<=-5 for v in ex),len(ex)),
        'drawdownLeMinus10PctMonthRatePct':pct(sum(v<=-10 for v in dd),len(dd)) if dd else None,
        'worstRiskReturnPct':min(rr),
        'worstExcessPctPoints':min(ex),
        'monthsList':[x['month'] for x in items],
    }

def analyze(block):
    rows=sorted(block['rows'],key=lambda x:x['month'])
    trans=defaultdict(list)
    enriched=[]
    for i in range(1,len(rows)):
        prev=rows[i-1]
        cur=dict(rows[i])
        cur['prevMonth']=prev['month']
        cur['prevState']=prev['state']
        cur['transition']=prev['state']+' -> '+cur['state']
        cur['nextState']=rows[i+1]['state'] if i+1<len(rows) else None
        trans[cur['transition']].append(cur)
        enriched.append(cur)

    matrix={}
    for a in STATE_ORDER:
        matrix[a]={}
        for b in STATE_ORDER:
            xs=trans.get(a+' -> '+b,[])
            matrix[a][b]=metrics(xs)

    hf=[x for x in enriched if x['state']=='High+Falling']
    rollover=[x for x in hf if x['prevState']=='High+Rising']
    continued=[x for x in hf if x['prevState']=='High+Falling']
    from_low=[x for x in hf if x['prevState'].startswith('Low+')]
    entered=[x for x in hf if x['prevState']!='High+Falling']

    paths=[]
    for x in rollover:
        paths.append({k:x.get(k) for k in ['month','prevMonth','prevState','state','nextState','riskReturn','QQQ','excess','riskDrawdownPct']})

    return {
        'transitionMonths':len(enriched),
        'transitionMatrix':matrix,
        'focusHighFalling':{
            'allHighFalling':metrics(hf),
            'HighRising_to_HighFalling':metrics(rollover),
            'HighFalling_to_HighFalling':metrics(continued),
            'Low_to_HighFalling':metrics(from_low),
            'entryIntoHighFalling_anyNonHF':metrics(entered),
            'rolloverPaths':paths,
        },
        'contrast':{
            'HRtoHF_minus_continuedHF_avgExcessPctPoints':(
                mean([x['excess'] for x in rollover])-mean([x['excess'] for x in continued])
                if rollover and continued else None),
            'HRtoHF_minus_LowToHF_avgExcessPctPoints':(
                mean([x['excess'] for x in rollover])-mean([x['excess'] for x in from_low])
                if rollover and from_low else None),
        }
    }

exact=analyze(src['exact2022_2026'])
proxy=analyze(src['pre2018Proxy'])

out={
    'purpose':'Test whether the key Leadership Rollover event is specifically the state transition High+Rising -> High+Falling, rather than any High+Falling month. No new strategy rule or threshold optimization.',
    'stateDefinition':src['definitions'],
    'exact2022_2026':exact,
    'pre2018Proxy':proxy,
    'signReplication':{
        'HRtoHF_negative_exact': exact['focusHighFalling']['HighRising_to_HighFalling']['avgExcessPctPoints'] is not None and exact['focusHighFalling']['HighRising_to_HighFalling']['avgExcessPctPoints']<0,
        'HRtoHF_negative_proxy': proxy['focusHighFalling']['HighRising_to_HighFalling']['avgExcessPctPoints'] is not None and proxy['focusHighFalling']['HighRising_to_HighFalling']['avgExcessPctPoints']<0,
    },
    'limitations':[
        'Recent exact QUQU history is short; transition counts can be small.',
        'Pre-2018 block is a RelativeMomentum^3 proxy without reliable historical market-cap weighting, not exact QUQU.',
        'This is descriptive validation of a prespecified state representation; no transition is selected by optimizing return.'
    ]
}
with open(OUT,'w',encoding='utf-8') as f:
    json.dump(out,f,ensure_ascii=False,indent=2)

print(json.dumps({
    'exactHF':exact['focusHighFalling'],
    'proxyHF':proxy['focusHighFalling'],
    'signReplication':out['signReplication']
},ensure_ascii=False,indent=2))
