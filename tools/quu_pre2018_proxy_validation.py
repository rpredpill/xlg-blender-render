#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import math
import random
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from statistics import pstdev

import numpy as np
import pandas as pd
import requests

# Reuse frozen PIT membership and Yahoo recovery from the max-period script.
spec=importlib.util.spec_from_file_location('maxpit','/tmp/quu_max_pit_validation.py')
if spec is None or spec.loader is None:raise RuntimeError('cannot load max PIT module')
mp=importlib.util.module_from_spec(spec);sys.modules['maxpit']=mp;spec.loader.exec_module(mp);base=mp.base
START='2007-08';END='2018-02';CAP=0.20


def metrics(rs):
    a=np.asarray(rs,dtype=float)/100.0
    if not len(a):return None
    eq=np.concatenate([[1.0],np.cumprod(1.0+a)]);peaks=np.maximum.accumulate(eq);dd=eq/peaks-1.0;cagr=float(eq[-1]**(12.0/len(a))-1.0);mdd=float(dd.min())
    return {'months':int(len(a)),'totalReturnPct':float((eq[-1]-1)*100),'CAGRpct':cagr*100,'MDDpct':mdd*100,'Calmar':cagr/abs(mdd) if mdd<0 else None,'endingIndex':float(eq[-1]*100)}


def cap_and_redistribute(raw,cap=CAP):
    free=set(raw);out={};remaining=1.0
    while free:
        total=sum(raw[s] for s in free)
        if not(total>0):raise RuntimeError('raw sum <=0')
        over=[s for s in free if remaining*raw[s]/total>cap+1e-12]
        if not over:
            for s in free:out[s]=remaining*raw[s]/total
            break
        for s in over:out[s]=cap;remaining-=cap;free.remove(s)
    return out


def tukey(xs,k=1.5):
    if len(xs)<8:return None
    a=np.asarray(xs,float);q1=float(np.quantile(a,.25));q3=float(np.quantile(a,.75));return q3+k*(q3-q1)


def run_rule(months,leadership_q=.5,tukey_k=1.5):
    prior=[];out=[]
    for x in months:
        d=x['dispersion'];threshold=float(np.quantile(prior,leadership_q)) if prior else None;fence=tukey(prior,tukey_k);prev=prior[-1] if prior else None
        leadership=True if threshold is None else d>threshold;extreme=bool(fence is not None and d>fence);rollover=bool(extreme and prev is not None and d<prev);sleeve='PROXY' if leadership and not rollover else 'QQQ'
        y=dict(x);y.update({'leadershipThreshold':threshold,'tukeyUpper':fence,'priorDispersion':prev,'leadership':leadership,'extreme':extreme,'rollover':rollover,'sleeve':sleeve,'QUUProxy':x['proxyReturn'] if sleeve=='PROXY' else x['QQQ']});out.append(y);prior.append(d)
    return out


def load_wiki_adjusted(symbols,start):
    """Quandl WIKI with native adj_open/adj_close. This avoids mixing raw open with adjusted close."""
    archive=Path('/tmp/quandl-wiki-prices-us-equites.zip')
    if not archive.exists():
        print('Downloading public Quandl WIKI archive...',flush=True)
        with requests.get(mp.KAGGLE_WIKI_URL,headers=mp.UA,timeout=120,stream=True) as r:
            r.raise_for_status()
            with archive.open('wb') as f:
                for chunk in r.iter_content(1024*1024):
                    if chunk:f.write(chunk)
        print('WIKI archive MB',round(archive.stat().st_size/1e6,1),flush=True)
    want=set(symbols);frames=[];usecols=['ticker','date','open','close','adj_open','adj_close']
    with zipfile.ZipFile(archive) as outer:
        kind,member=mp._wiki_csv_member(outer)
        if not member:raise RuntimeError('WIKI_PRICES.csv missing')
        inner=None
        if kind=='direct':raw=outer.open(member)
        else:
            nested_path=Path('/tmp/wiki-prices-inner.zip')
            if not nested_path.exists():nested_path.write_bytes(outer.read(member))
            inner=zipfile.ZipFile(nested_path);csv_name=next((n for n in inner.namelist() if n.upper().endswith('WIKI_PRICES.CSV')),None)
            if not csv_name:raise RuntimeError('nested WIKI_PRICES.csv missing')
            raw=inner.open(csv_name)
        try:
            for idx,chunk in enumerate(pd.read_csv(raw,usecols=usecols,chunksize=500000),1):
                x=chunk[chunk['ticker'].astype(str).str.upper().isin(want)].copy()
                if not x.empty:
                    x['date']=pd.to_datetime(x['date'],errors='coerce');x=x[(x['date']>=pd.Timestamp(start))&(x['date']<=mp.WIKI_CUTOFF)]
                    if not x.empty:frames.append(x)
                if idx%10==0:print('WIKI chunks',idx,'matched rows',sum(len(f) for f in frames),flush=True)
        finally:
            raw.close()
            if inner is not None:inner.close()
    if not frames:raise RuntimeError('no WIKI rows matched')
    allx=pd.concat(frames,ignore_index=True);out={}
    for s,g in allx.groupby(allx['ticker'].astype(str).str.upper()):
        d=pd.DataFrame({'Open':pd.to_numeric(g['open'],errors='coerce').to_numpy(),'Close':pd.to_numeric(g['close'],errors='coerce').to_numpy(),'Adj Open':pd.to_numeric(g['adj_open'],errors='coerce').to_numpy(),'Adj Close':pd.to_numeric(g['adj_close'],errors='coerce').to_numpy()},index=pd.to_datetime(g['date']).dt.normalize())
        d=d[~d.index.duplicated(keep='last')].sort_index().dropna(subset=['Adj Close'])
        if not d.empty:out[s]=d
    print('WIKI adjusted symbols',len(out),'rows',len(allx),flush=True);return out


def month_return_hybrid(prices,symbol,ym,wiki_symbols):
    d=prices.get(symbol)
    if d is None:return None
    start=pd.Period(ym,freq='M').start_time.normalize();end=base.next_month_start(ym);g=d[(d.index>=start)&(d.index<end)].copy()
    if g.empty:return None
    if symbol in wiki_symbols and 'Adj Open' in g.columns:
        gw=g.dropna(subset=['Adj Open','Adj Close'])
        if not gw.empty:
            o=float(gw.iloc[0]['Adj Open']);c=float(gw.iloc[-1]['Adj Close'])
            if o>0 and c>0 and math.isfinite(o) and math.isfinite(c):return {'return':c/o-1.0,'firstDate':str(gw.index[0].date()),'lastDate':str(gw.index[-1].date())}
    return base.month_return(prices,symbol,ym)


def month_build(membership,prices,qqq_prices,month,wiki_symbols):
    signal_date=base.month_end(base.month_add(month,-1));recent=signal_date;early=base.month_end(base.month_add(month,-6));universe=base.members_at(membership,signal_date);rows=[];bad=[];source_counts={'WIKI':0,'YahooOnly':0}
    for s in universe:
        if s not in prices:continue
        a=base.close_on_or_before(prices,s,recent,'Adj Close');b=base.close_on_or_before(prices,s,early,'Adj Close');mr=month_return_hybrid(prices,s,month,wiki_symbols)
        if not(a and b and b>0 and mr):continue
        ret=float(mr['return'])
        if ret>5.0 or ret<-.95:bad.append({'ticker':s,'monthlyReturn':ret});continue
        gross=float(a/b)
        if gross<=0 or not math.isfinite(gross):continue
        rows.append({'ticker':s,'gross':gross,'momentum':gross-1.0,'monthlyReturn':ret});source_counts['WIKI' if s in wiki_symbols else 'YahooOnly']+=1
    cov=len(rows)/max(1,len(universe))
    if cov<.90:raise RuntimeError(f'coverage {len(rows)}/{len(universe)}={cov:.2%}; bad={bad[:5]}')
    med=float(np.median([r['gross'] for r in rows]));raw={r['ticker']:(r['gross']/med)**3 for r in rows};w=cap_and_redistribute(raw);port=sum(w[r['ticker']]*r['monthlyReturn'] for r in rows)*100;disp=float(pstdev([r['momentum'] for r in rows]));qr=base.month_return(qqq_prices,'QQQ',month)
    if not qr:raise RuntimeError('QQQ unavailable')
    return {'month':month,'universeCount':len(universe),'holdingsCount':len(rows),'coverageRatio':cov,'badIdentityRows':bad,'sourceCounts':source_counts,'dispersion':disp,'proxyReturn':float(port),'QQQ':float(qr['return'])*100}


def longest_run(items):
    by={x['month']:x for x in items};best=[]
    for start in sorted(by):
        run=[];m=start
        while m in by:run.append(by[m]);m=base.month_add(m,1)
        if len(run)>len(best):best=run
    return best


def summary(rows):
    return {'MomentumOnlyP3Proxy':metrics([x['proxyReturn'] for x in rows]),'QQQ':metrics([x['QQQ'] for x in rows]),'LeadershipBaseline':metrics([x['proxyReturn'] if x['leadership'] else x['QQQ'] for x in rows]),'LeadershipRollover':metrics([x['QUUProxy'] for x in rows]),'triggers':[{'month':x['month'],'proxyReturn':x['proxyReturn'],'QQQ':x['QQQ'],'savedPctPoints':x['QQQ']-x['proxyReturn'],'dispersion':x['dispersion'],'priorDispersion':x['priorDispersion']} for x in rows if x['rollover']]}


def main():
    membership=base.load_membership(base.NDX100_URL);begin=base.month_end(base.month_add(START,-6));end_signal=base.month_end(base.month_add(END,-1));symbols=set(base.members_at(membership,begin))
    for dt,tickers in membership:
        if begin<dt<=end_signal:symbols.update(tickers)
    symbols.update(base.members_at(membership,end_signal));symbols=sorted(symbols);print('pre2018 PIT union',len(symbols),flush=True)
    start_day=str((pd.Period(START,freq='M')-7).start_time.date());end_day='2018-04-02';prices=base.download_prices(symbols,start_day,end_day);mp.recover_yahoo_prices(prices,symbols,start_day,end_day)
    wiki=load_wiki_adjusted(symbols,start_day);wiki_symbols=set(wiki);merge_info=mp.merge_wiki(prices,wiki);qqq=base.download_prices(['QQQ'],'2006-12-01',end_day)
    if 'QQQ' not in qqq:raise RuntimeError('QQQ download failed')
    print('hybrid symbols',len(prices),'wiki',len(wiki_symbols),'wikiAdded',len(merge_info['newSymbols']),flush=True)
    good=[];failures={};m=START
    while m<=END:
        try:x=month_build(membership,prices,qqq,m,wiki_symbols);good.append(x);print('OK',m,f"cov={x['coverageRatio']:.1%}",f"proxy={x['proxyReturn']:+.2f}%",x['sourceCounts'],flush=True)
        except Exception as e:failures[m]=str(e);print('FAIL',m,e,flush=True)
        m=base.month_add(m,1)
    run=longest_run(good)
    if len(run)<36:raise RuntimeError(f'longest clean proxy run only {len(run)} months')
    rows=run_rule(run);head=summary(rows);sensitivity=[]
    for k in [1.0,1.25,1.5,1.75,2.0,2.5]:
        r=run_rule(run,tukey_k=k);sensitivity.append({'dimension':'tukey_k','value':k,'metrics':metrics([x['QUUProxy'] for x in r]),'triggers':[x['month'] for x in r if x['rollover']]})
    for q in [.40,.45,.50,.55,.60]:
        r=run_rule(run,leadership_q=q);sensitivity.append({'dimension':'leadership_quantile','value':q,'metrics':metrics([x['QUUProxy'] for x in r]),'triggers':[x['month'] for x in r if x['rollover']]})
    triggers=[x['month'] for x in rows if x['rollover']];leaders=[x for x in rows if x['leadership']];k=len(triggers);actual=head['LeadershipRollover']['endingIndex'];rng=random.Random(20261002);mc=[]
    if k and len(leaders)>=k:
        for _ in range(10000):
            chosen={x['month'] for x in rng.sample(leaders,k)};rs=[x['QQQ'] if x['month'] in chosen else(x['proxyReturn'] if x['leadership'] else x['QQQ']) for x in rows];mc.append(metrics(rs)['endingIndex'])
    out={'generatedAt':datetime.now(timezone.utc).isoformat(),'purpose':'Independent pre-2018 regime-signal validation. NOT an exact QUQU backtest: historical market caps for delisted Nasdaq-100 names are not reliable. Portfolio proxy uses RelativeMomentum^3 only with the same 20% cap.','fixedRule':{'leadership':'dispersion > expanding prior median','extreme':'dispersion > expanding prior Q3 + 1.5×IQR','rollover':'extreme AND dispersion < previous month dispersion','parametersFrozen':True},'data':{'attemptedStart':START,'attemptedEnd':END,'cleanRunStart':run[0]['month'],'cleanRunEnd':run[-1]['month'],'cleanRunMonths':len(run),'minimumCoverageRatio':min(x['coverageRatio'] for x in run),'minimumWikiCount':min(x['sourceCounts']['WIKI'] for x in run),'maximumYahooOnlyCount':max(x['sourceCounts']['YahooOnly'] for x in run),'failures':failures,'priceSource':'WIKI-first hybrid using native WIKI Adj Open/Adj Close; Yahoo only where WIKI lacks ticker; Yahoo QQQ benchmark'},'headline':head,'sensitivity':sensitivity,'randomSameCountSwitchSanity':{'triggerCount':k,'trials':len(mc),'actualEndingIndex':actual,'percentileVsRandomSameCountSwitches':100*sum(v<=actual for v in mc)/len(mc) if mc else None,'randomMedianEndingIndex':float(np.median(mc)) if mc else None,'random95thEndingIndex':float(np.quantile(mc,.95)) if mc else None},'months':rows}
    Path('quu-pre2018-proxy-validation.json').write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8');print(json.dumps({'data':out['data'],'headline':head,'random':out['randomSameCountSwitchSanity']},ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
