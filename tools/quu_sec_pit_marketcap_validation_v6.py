#!/usr/bin/env python3
from __future__ import annotations

import importlib.util, json, math, sys, time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

spec=importlib.util.spec_from_file_location('secv2','/tmp/quu_sec_pit_marketcap_validation_v2.py')
if spec is None or spec.loader is None: raise RuntimeError('cannot load SEC PIT v2')
v2=importlib.util.module_from_spec(spec); sys.modules['secv2']=v2; spec.loader.exec_module(v2)
v1=v2.v1; base=v1.base; mp=v1.mp; pre=v1.pre

START='2012-11'; END='2025-12'; BUILD_START='2012-05'; CAP=.20
OUT=Path('quu-sec-pit-marketcap-validation-v6.json')
HF='https://huggingface.co/datasets/finsaber-team/FINSABER-V2-Data/resolve/main/price_daily/year={year}/part-000.parquet?download=true'
CACHE=Path('/tmp/finsaber-cik-v6'); CACHE.mkdir(parents=True,exist_ok=True)


def zcik(x):
    s=''.join(ch for ch in str(x or '') if ch.isdigit())
    return s.zfill(10) if s else None


def download_year(y):
    p=CACHE/f'{y}.parquet'
    if p.exists() and p.stat().st_size>1000:return p
    r=requests.get(HF.format(year=y),timeout=180); r.raise_for_status(); p.write_bytes(r.content)
    print('FINSABER identity',y,'MB',round(len(r.content)/1e6,2),flush=True)
    return p


def load_identity(symbols):
    wanted=set(symbols); frames=[]
    for y in range(2011,2026):
        d=pd.read_parquet(download_year(y),columns=['date','symbol','cik'])
        d['symbol']=d['symbol'].astype(str).str.upper().str.strip(); d=d[d['symbol'].isin(wanted)].copy()
        if d.empty:continue
        d['date']=pd.to_datetime(d['date']).dt.tz_localize(None).dt.normalize(); d['cik_norm']=d['cik'].map(zcik)
        d=d.dropna(subset=['cik_norm'])[['date','symbol','cik_norm']].drop_duplicates()
        frames.append(d)
    x=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame(columns=['date','symbol','cik_norm'])
    by={}
    for s,g in x.groupby('symbol'):
        g=g.sort_values('date').drop_duplicates('date',keep='last')
        by[s]=(g['date'].to_numpy(),g['cik_norm'].to_numpy())
    return by,x


def cik_at(identity,symbol,dt):
    z=identity.get(symbol)
    if not z:return None
    dates,ciks=z; i=np.searchsorted(dates,np.datetime64(dt),side='right')-1
    if i<0:return None
    if (pd.Timestamp(dt)-pd.Timestamp(dates[i])).days>400:return None
    return str(ciks[i])


def cap_and_redistribute(raw,cap=CAP):
    free=set(raw); out={}; rem=1.0
    while free:
        total=sum(raw[s] for s in free)
        if total<=0:raise RuntimeError('raw sum <= 0')
        over=[s for s in free if rem*raw[s]/total>cap+1e-12]
        if not over:
            for s in free:out[s]=rem*raw[s]/total
            break
        for s in over:out[s]=cap; rem-=cap; free.remove(s)
    return out


def metrics(rs):
    a=np.asarray(rs,float)/100.; eq=np.concatenate([[1.],np.cumprod(1+a)]); peak=np.maximum.accumulate(eq); dd=eq/peak-1
    cagr=eq[-1]**(12/len(a))-1
    return {'months':len(a),'totalReturnPct':float((eq[-1]-1)*100),'CAGRpct':float(cagr*100),'MDDpct':float(dd.min()*100),'Calmar':float(cagr/abs(dd.min())) if dd.min()<0 else None,'endingIndex':float(eq[-1]*100)}


def main():
    membership=base.load_membership(base.NDX100_URL)
    begin=base.month_end(base.month_add(BUILD_START,-6)); end_signal=base.month_end(base.month_add(END,-1))
    symbols=set(base.members_at(membership,begin))
    for dt,tks in membership:
        if begin<dt<=end_signal:symbols.update(tks)
    symbols.update(base.members_at(membership,end_signal)); symbols=sorted(symbols)
    print('PIT union',len(symbols),flush=True)

    static_map,static_src=v2.load_cik_map_v2(); identity,idrows=load_identity(symbols)
    hist_ciks=sorted(set(idrows['cik_norm'].dropna().astype(str))); static_ciks={static_map[s] for s in symbols if static_map.get(s)}
    all_ciks=sorted(set(hist_ciks)|static_ciks)
    print('identity symbols',len(identity),'historical CIKs',len(hist_ciks),'all CIKs',len(all_ciks),flush=True)

    switches=[]
    for s in symbols:
        vals=list(dict.fromkeys(str(x) for x in identity[s][1])) if s in identity else []
        st=static_map.get(s)
        if vals and (len(vals)>1 or st not in vals):switches.append({'ticker':s,'historicalCIKs':vals,'staticCIK':st})
    print('identity switch count',len(switches),'examples',json.dumps(switches[:30]),flush=True)

    facts_by_cik={}
    for i,c in enumerate(all_ciks,1):
        facts_by_cik[c]=v2.extract_share_facts_v2(v1.companyfacts(c))
        if i%25==0 or i==len(all_ciks):print('companyfacts',i,'/',len(all_ciks),'withShares',sum(bool(v) for v in facts_by_cik.values()),flush=True)
        time.sleep(.11)

    start_day=str((pd.Period(BUILD_START,freq='M')-7).start_time.date()); end_day='2026-01-05'
    prices=base.download_prices(symbols,start_day,end_day); recovery=mp.recover_yahoo_prices(prices,symbols,start_day,end_day)
    wiki=pre.load_wiki_adjusted(symbols,start_day); merge=mp.merge_wiki(prices,wiki)
    bench=base.download_prices(['QQQ'],'2012-01-01',end_day)
    exact=requests.get(v1.EXACT_QUU,headers=v1.UA,timeout=60).json().get('months') or {}

    month_rows=[]; identity_sources=Counter(); missing=Counter(); m=BUILD_START
    while m<=END:
        signal_date=base.month_end(base.month_add(m,-1)); early=base.month_end(base.month_add(m,-6)); universe=base.members_at(membership,signal_date)
        rows=[]
        for s in universe:
            recent=base.close_on_or_before(prices,s,signal_date,'Adj Close'); earlyp=base.close_on_or_before(prices,s,early,'Adj Close'); mr=v1.adj_month_return(prices,s,m)
            if not(recent and earlyp and mr is not None):missing['PRICE_OR_RETURN']+=1;continue
            gross=recent/earlyp
            if not(0.10<=gross<=6.0 and -0.95<=mr<=5.0):missing['DATA_GUARD']+=1;continue
            c=cik_at(identity,s,signal_date); src='finsaber-date-cik' if c else None
            if not c:c=static_map.get(s); src='static-fallback' if c else None
            if c:identity_sources[src]+=1
            if not c:missing['NO_CIK']+=1;fact=None
            else:fact=v2.latest_fact_v2(facts_by_cik.get(c,[]),signal_date)
            if c and not fact:missing['NO_PIT_SHARE_FACT']+=1
            cap=v1.reconstructed_cap(prices,s,fact,signal_date) if fact else None
            if fact and not cap:missing['CAP_RECON_PRICE_GAP']+=1
            rows.append({'ticker':s,'gross':gross,'momentum':gross-1,'monthlyReturn':mr,'cik':c,'identitySource':src,'cap':cap})
        pc=len(rows)/max(1,len(universe)); cc=sum(bool(r['cap']) for r in rows)/max(1,len(universe))
        month_rows.append({'month':m,'universeCount':len(universe),'priceCoverage':pc,'capCoverage':cc,'rows':rows})
        print(m,f'price={pc:.1%}',f'cap={cc:.1%}',flush=True);m=base.month_add(m,1)

    eligible={x['month'] for x in month_rows if x['priceCoverage']>=.90 and x['capCoverage']>=.90}; continuous=None
    for x in month_rows:
        if x['month']<START or x['month'] not in eligible:continue
        cur=x['month'];ok=True
        while cur<=END:
            if cur not in eligible:ok=False;break
            cur=base.month_add(cur,1)
        if ok:continuous=x['month'];break

    results=[]
    for x in month_rows:
        if x['priceCoverage']<.90 or x['capCoverage']<.90:continue
        rows=x['rows'];caps=[r['cap'] for r in rows if r['cap']];medcap=float(np.median(caps));medgross=float(np.median([r['gross'] for r in rows]));raw={};fallback=0
        for r in rows:
            c=r['cap']
            if not c:c=medcap;fallback+=1
            raw[r['ticker']]=math.sqrt(c)*((r['gross']/medgross)**3)
        w=cap_and_redistribute(raw);risk=100*sum(w[r['ticker']]*r['monthlyReturn'] for r in rows)
        qr=v1.adj_month_return(bench,'QQQ',x['month'])
        if qr is None:continue
        results.append({'month':x['month'],'riskReturn':risk,'QQQ':qr*100,'capCoverage':x['capCoverage'],'priceCoverage':x['priceCoverage'],'medianCapFallbackCount':fallback,'weights':w,'rows':rows})

    cap_pairs=[];maes=[];rd=[];overlap=[]
    for r in results:
        e=exact.get(r['month'])
        if not isinstance(e,dict):continue
        em={z.get('ticker'):z for z in (e.get('rows') or []) if z.get('ticker')}
        for z in r['rows']:
            ec=em.get(z['ticker'],{}).get('marketCap')
            if z.get('cap') and isinstance(ec,(int,float)) and ec>0:cap_pairs.append((math.log(z['cap']),math.log(ec)))
        common=[t for t in r['weights'] if t in em and isinstance(em[t].get('weight'),(int,float))]
        if common:
            ss=sum(float(em[t]['weight']) for t in common)
            if ss>0:maes.append(float(np.mean([abs(r['weights'][t]-float(em[t]['weight'])/ss) for t in common])))
        if e.get('selectedSleeve')=='QUQU' and isinstance(e.get('portfolioReturn'),(int,float)):
            rd.append(r['riskReturn']-float(e['portfolioReturn']));overlap.append(r['month'])
    validation={'capPairCount':len(cap_pairs),'logMarketCapCorrelation':float(np.corrcoef(np.array(cap_pairs).T)[0,1]) if len(cap_pairs)>2 else None,'meanWeightMAE':float(np.mean(maes)) if maes else None,'riskReturnOverlapMonths':len(rd),'meanRiskMinusExactPp':float(np.mean(rd)) if rd else None,'MAERiskReturnPp':float(np.mean(np.abs(rd))) if rd else None,'overlapMonths':overlap}
    use=[r for r in results if continuous and r['month']>=continuous]
    out={'generatedAt':datetime.now(timezone.utc).isoformat(),'method':'SEC PIT v6: dated ticker->CIK identity from FINSABER rows at each signal date; static CIK only fallback. Price engine intentionally stays on the better-validated v2 Yahoo+aliases+WIKI path. SEC shares filed<=signal; actual shares preferred, weighted-average fallback; 550d max staleness; >=90% price/cap coverage gate before median-cap fallback.','identity':{'finsaberSymbols':len(identity),'historicalCIKs':len(hist_ciks),'allFetchedCIKs':len(all_ciks),'switchCount':len(switches),'switches':switches,'sourceCounts':dict(identity_sources)},'missingReasonCounts':dict(missing),'priceRecovery':recovery,'wikiMerge':merge,'coverageByMonth':[{'month':x['month'],'priceCoverage':x['priceCoverage'],'capCoverage':x['capCoverage'],'universeCount':x['universeCount']} for x in month_rows],'continuousStart90Pct':continuous,'validationAgainstRecentExact':validation,'performance':{'period':[use[0]['month'],use[-1]['month']] if use else None,'months':len(use),'RiskSleeve':metrics([r['riskReturn'] for r in use]) if use else None,'QQQ':metrics([r['QQQ'] for r in use]) if use else None},'monthly':[{k:v for k,v in r.items() if k not in ('rows','weights')} for r in use]}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print('V6_FINAL',json.dumps({'continuousStart90Pct':continuous,'validation':validation,'performance':out['performance'],'missingReasonCounts':dict(missing),'identitySwitchCount':len(switches)},ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
