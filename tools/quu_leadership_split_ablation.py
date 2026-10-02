#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

SRC=Path('quu-leadership-percentile-study.json')
EW=Path('quu-mr90-equal-weight-study.json')
OUT=Path('quu-leadership-split-ablation.json')


def metrics(returns):
    vals=[float(x) for x in returns]
    eq=1.0; peak=1.0; mdd=0.0
    for r in vals:
        eq*=1+r/100.0
        peak=max(peak,eq)
        mdd=min(mdd,eq/peak-1.0)
    n=len(vals)
    cagr=(eq**(12.0/n)-1)*100 if n else None
    mdd_pct=mdd*100 if n else None
    return {
        'months':n,
        'totalReturnPct':(eq-1)*100 if n else None,
        'CAGRpct':cagr,
        'MDDpct':mdd_pct,
        'Calmar':cagr/abs(mdd_pct) if n and mdd_pct<0 else None,
        'endingIndex':eq*100 if n else None,
    }


def summarize(rows):
    if not rows:
        return {'months':0}
    ex=[float(r['riskReturn'])-float(r['QQQ']) for r in rows]
    return {
        'months':len(rows),
        'avgRiskReturnPct':sum(float(r['riskReturn']) for r in rows)/len(rows),
        'avgQQQReturnPct':sum(float(r['QQQ']) for r in rows)/len(rows),
        'avgRiskMinusQQQPctPoints':sum(ex)/len(ex),
        'riskWinRatePct':100*sum(x>0 for x in ex)/len(ex),
        'monthsList':[r['month'] for r in rows],
    }


def analyze(sample, ew_events):
    ew_map={r['month']:float(r['QQQEReturnPct']) for r in ew_events}
    cur=[]; no50=[]; low=[]; high=[]; overlays=[]
    for r in sample['rows']:
        m=r['month']; p=float(r['percentile']); falling=bool(r['falling'])
        risk=float(r['riskReturn']); qqq=float(r['QQQ'])
        overlay=p>=90 and falling
        if p<50: low.append(r)
        else: high.append(r)
        if overlay:
            if m not in ew_map: raise RuntimeError(f'missing QQQE for MR90 month {m}')
            ret=ew_map[m]
            cur.append(ret); no50.append(ret)
            overlays.append({'month':m,'QQQEReturnPct':ret,'percentile':p})
        else:
            cur.append(risk if p>=50 else qqq)
            no50.append(risk)
    # Direct impact of the P<50 sleeve choice only.
    low_diff=[float(r['QQQ'])-float(r['riskReturn']) for r in low]
    return {
        'period':{'start':sample['rows'][0]['month'],'end':sample['rows'][-1]['month'],'months':len(sample['rows'])},
        'strategies':{
            'CurrentQUU':metrics(cur),
            'No50Split_AlwaysRiskExceptMR90':metrics(no50),
        },
        'lowLeadership':summarize(low),
        'highLeadership':summarize(high),
        'splitContribution':{
            'lowLeadershipMonths':len(low),
            'avgQQQminusRiskPctPoints':sum(low_diff)/len(low_diff) if low_diff else None,
            'sumQQQminusRiskPctPoints':sum(low_diff),
            'QQQWinsVsRisk':sum(x>0 for x in low_diff),
            'RiskWinsVsQQQ':sum(x<0 for x in low_diff),
            'ties':sum(x==0 for x in low_diff),
        },
        'mr90OverlayMonths':overlays,
    }


def main():
    src=json.loads(SRC.read_text(encoding='utf-8'))
    ew=json.loads(EW.read_text(encoding='utf-8'))
    exact=analyze(src['exact2022_2026'], ew['exact2022_2026']['interventions']['rows'])
    old=analyze(src['pre2018Proxy'], ew['pre2018Proxy']['interventions']['rows'])
    out={
        'purpose':'Ablate the 50th-percentile Leadership split while keeping the MR90->QQQE overlay fixed. No threshold optimization.',
        'currentRule':'P<50 => QQQ; P>=50 => risk sleeve; P>=90 and falling => QQQE.',
        'no50Rule':'Always risk sleeve except P>=90 and falling => QQQE.',
        'exact2022_2026':exact,
        'pre2018Proxy':old,
        'crossSample':{
            'splitHelpsCAGRBothSamples': exact['strategies']['CurrentQUU']['CAGRpct']>exact['strategies']['No50Split_AlwaysRiskExceptMR90']['CAGRpct'] and old['strategies']['CurrentQUU']['CAGRpct']>old['strategies']['No50Split_AlwaysRiskExceptMR90']['CAGRpct'],
            'splitHelpsMDDBothSamples': exact['strategies']['CurrentQUU']['MDDpct']>exact['strategies']['No50Split_AlwaysRiskExceptMR90']['MDDpct'] and old['strategies']['CurrentQUU']['MDDpct']>old['strategies']['No50Split_AlwaysRiskExceptMR90']['MDDpct'],
            'lowLeadershipRiskExcessSignExact': 'positive' if exact['lowLeadership']['avgRiskMinusQQQPctPoints']>0 else 'negative',
            'lowLeadershipRiskExcessSignPre2018': 'positive' if old['lowLeadership']['avgRiskMinusQQQPctPoints']>0 else 'negative',
        },
        'limitations':[
            'Exact sample is recent and short; pre-2018 uses Momentum^3 proxy rather than exact QUQU.',
            'This is an ablation of the already-used 50 split, not a search over alternative cutoffs.',
            'Live QUU is not modified by this research workflow.'
        ]
    }
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False))

if __name__=='__main__': main()
