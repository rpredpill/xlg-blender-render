#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import pstdev

import numpy as np
import pandas as pd

# Reuse the frozen PIT membership + WIKI loader from the max-period research script.
spec = importlib.util.spec_from_file_location('maxpit', '/tmp/quu_max_pit_validation.py')
if spec is None or spec.loader is None:
    raise RuntimeError('cannot load max PIT module')
mp = importlib.util.module_from_spec(spec)
sys.modules['maxpit'] = mp
spec.loader.exec_module(mp)
base = mp.base

START = '2007-08'
END = '2018-02'  # WIKI is complete only through 2018-03-27; keep full realized months.
CAP = 0.20


def metrics(rs):
    a = np.asarray(rs, dtype=float) / 100.0
    if not len(a): return None
    eq = np.concatenate([[1.0], np.cumprod(1.0 + a)])
    peaks = np.maximum.accumulate(eq)
    dd = eq / peaks - 1.0
    cagr = float(eq[-1] ** (12.0 / len(a)) - 1.0)
    mdd = float(dd.min())
    return {'months':len(a),'totalReturnPct':float((eq[-1]-1)*100),'CAGRpct':cagr*100,'MDDpct':mdd*100,'Calmar':cagr/abs(mdd) if mdd<0 else None,'endingIndex':float(eq[-1]*100)}


def cap_and_redistribute(raw, cap=CAP):
    free=set(raw); out={}; remaining=1.0
    while free:
        total=sum(raw[s] for s in free)
        if not (total>0): raise RuntimeError('raw sum <=0')
        over=[s for s in free if remaining*raw[s]/total > cap+1e-12]
        if not over:
            for s in free: out[s]=remaining*raw[s]/total
            break
        for s in over:
            out[s]=cap; remaining-=cap; free.remove(s)
    return out


def tukey(xs,k=1.5):
    if len(xs)<8: return None
    a=np.asarray(xs,float); q1=float(np.quantile(a,.25)); q3=float(np.quantile(a,.75))
    return q3+k*(q3-q1)


def run_rule(months, leadership_q=.5, tukey_k=1.5):
    prior=[]; out=[]
    for x in months:
        d=x['dispersion']; threshold=float(np.quantile(prior,leadership_q)) if prior else None; fence=tukey(prior,tukey_k); prev=prior[-1] if prior else None
        leadership=True if threshold is None else d>threshold
        extreme=bool(fence is not None and d>fence)
        rollover=bool(extreme and prev is not None and d<prev)
        sleeve='PROXY' if leadership and not rollover else 'QQQ'
        y=dict(x); y.update({'leadershipThreshold':threshold,'tukeyUpper':fence,'priorDispersion':prev,'leadership':leadership,'extreme':extreme,'rollover':rollover,'sleeve':sleeve,'QUUProxy':x['proxyReturn'] if sleeve=='PROXY' else x['QQQ']})
        out.append(y); prior.append(d)
    return out


def month_build(membership,wiki,qqq_prices,month):
    signal_date=base.month_end(base.month_add(month,-1)); recent=signal_date; early=base.month_end(base.month_add(month,-6)); universe=base.members_at(membership,signal_date)
    rows=[]; bad=[]
    for s in universe:
        df=wiki.get(s)
        if df is None: continue
        a=base.close_on_or_before(df,recent); b=base.close_on_or_before(df,early); mr=base.month_return(wiki,s,month)
        if not (a and b and b[1]>0 and mr): continue
        ret=float(mr['return'])
        # Data-quality sanity only: a Nasdaq-100 constituent moving >+500% or below -95%
        # in one month is treated as an identity/adjustment failure, not as a strategy event.
        if ret > 5.0 or ret < -0.95:
            bad.append({'ticker':s,'monthlyReturn':ret}); continue
        gross=float(a[1]/b[1])
        if gross<=0 or not math.isfinite(gross): continue
        rows.append({'ticker':s,'gross':gross,'momentum':gross-1.0,'monthlyReturn':ret})
    cov=len(rows)/max(1,len(universe))
    if cov<.90: raise RuntimeError(f'coverage {len(rows)}/{len(universe)}={cov:.2%}; bad={bad[:5]}')
    med=float(np.median([r['gross'] for r in rows])); raw={r['ticker']:(r['gross']/med)**3 for r in rows}; w=cap_and_redistribute(raw)
    port=sum(w[r['ticker']]*r['monthlyReturn'] for r in rows)*100
    disp=float(pstdev([r['momentum'] for r in rows]))
    qr=base.month_return(qqq_prices,'QQQ',month)
    if not qr: raise RuntimeError('QQQ unavailable')
    return {'month':month,'universeCount':len(universe),'holdingsCount':len(rows),'coverageRatio':cov,'badIdentityRows':bad,'dispersion':disp,'proxyReturn':float(port),'QQQ':float(qr['return'])*100}


def longest_run(items):
    by={x['month']:x for x in items}; best=[]
    for start in sorted(by):
        run=[]; m=start
        while m in by:
            run.append(by[m]); m=base.month_add(m,1)
        if len(run)>len(best): best=run
    return best


def summary(rows):
    return {'MomentumOnlyP3Proxy':metrics([x['proxyReturn'] for x in rows]),'QQQ':metrics([x['QQQ'] for x in rows]),'LeadershipBaseline':metrics([x['proxyReturn'] if x['leadership'] else x['QQQ'] for x in rows]),'LeadershipRollover':metrics([x['QUUProxy'] for x in rows]),'triggers':[{'month':x['month'],'proxyReturn':x['proxyReturn'],'QQQ':x['QQQ'],'savedPctPoints':x['QQQ']-x['proxyReturn'],'dispersion':x['dispersion'],'priorDispersion':x['priorDispersion']} for x in rows if x['rollover']]}


def main():
    membership=base.load_membership(base.NDX100_URL)
    begin=base.month_end(base.month_add(START,-6)); end_signal=base.month_end(base.month_add(END,-1)); symbols=set(base.members_at(membership,begin))
    for dt,tickers in membership:
        if begin<dt<=end_signal: symbols.update(tickers)
    symbols.update(base.members_at(membership,end_signal)); symbols=sorted(symbols)
    print('pre2018 PIT union',len(symbols),flush=True)
    wiki=mp.load_wiki_prices(symbols,start=str((pd.Period(START,freq='M')-7).start_time.date()))
    qqq=base.download_prices(['QQQ'],'2006-12-01','2018-04-02')
    if 'QQQ' not in qqq: raise RuntimeError('QQQ download failed')
    good=[]; failures={}; m=START
    while m<=END:
        try:
            x=month_build(membership,wiki,qqq,m); good.append(x); print('OK',m,f"cov={x['coverageRatio']:.1%}",f"proxy={x['proxyReturn']:+.2f}%",flush=True)
        except Exception as e:
            failures[m]=str(e); print('FAIL',m,e,flush=True)
        m=base.month_add(m,1)
    run=longest_run(good)
    if len(run)<36: raise RuntimeError(f'longest clean proxy run only {len(run)} months')
    rows=run_rule(run); head=summary(rows)
    sensitivity=[]
    for k in [1.0,1.25,1.5,1.75,2.0,2.5]:
        r=run_rule(run,tukey_k=k); sensitivity.append({'dimension':'tukey_k','value':k,'metrics':metrics([x['QUUProxy'] for x in r]),'triggers':[x['month'] for x in r if x['rollover']]})
    for q in [.40,.45,.50,.55,.60]:
        r=run_rule(run,leadership_q=q); sensitivity.append({'dimension':'leadership_quantile','value':q,'metrics':metrics([x['QUUProxy'] for x in r]),'triggers':[x['month'] for x in r if x['rollover']]})
    triggers=[x['month'] for x in rows if x['rollover']]; leaders=[x for x in rows if x['leadership']]; k=len(triggers); actual=head['LeadershipRollover']['endingIndex']; rng=random.Random(20261002); mc=[]
    if k and len(leaders)>=k:
        for _ in range(10000):
            chosen={x['month'] for x in rng.sample(leaders,k)}; rs=[x['QQQ'] if x['month'] in chosen else (x['proxyReturn'] if x['leadership'] else x['QQQ']) for x in rows]; mc.append(metrics(rs)['endingIndex'])
    out={'generatedAt':datetime.now(timezone.utc).isoformat(),'purpose':'Independent pre-2018 regime-signal validation. This is NOT an exact QUQU backtest because historical market caps for delisted Nasdaq-100 names are not reliable. Portfolio proxy uses RelativeMomentum^3 only with the same 20% cap.','fixedRule':{'leadership':'dispersion > expanding prior median','extreme':'dispersion > expanding prior Q3 + 1.5×IQR','rollover':'extreme AND dispersion < previous month dispersion','parametersFrozen':True},'data':{'attemptedStart':START,'attemptedEnd':END,'cleanRunStart':run[0]['month'],'cleanRunEnd':run[-1]['month'],'cleanRunMonths':len(run),'minimumCoverageRatio':min(x['coverageRatio'] for x in run),'failures':failures,'priceSource':'Quandl WIKI adjusted historical stock prices + Yahoo QQQ'},'headline':head,'sensitivity':sensitivity,'randomSameCountSwitchSanity':{'triggerCount':k,'trials':len(mc),'actualEndingIndex':actual,'percentileVsRandomSameCountSwitches':100*sum(v<=actual for v in mc)/len(mc) if mc else None,'randomMedianEndingIndex':float(np.median(mc)) if mc else None,'random95thEndingIndex':float(np.quantile(mc,.95)) if mc else None},'months':rows}
    Path('quu-pre2018-proxy-validation.json').write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({'data':out['data'],'headline':head,'random':out['randomSameCountSwitchSanity']},ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__': main()
