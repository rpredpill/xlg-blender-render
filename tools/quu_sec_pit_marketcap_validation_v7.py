#!/usr/bin/env python3
from __future__ import annotations

import importlib.util, json, math, sys, time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# Load v4 utilities (enhanced share facts + endpoint-consistent FINSABER helpers)
spec4=importlib.util.spec_from_file_location('secv4','/tmp/quu_sec_pit_marketcap_validation_v4.py')
if spec4 is None or spec4.loader is None: raise RuntimeError('cannot load v4')
v4=importlib.util.module_from_spec(spec4); sys.modules['secv4']=v4; spec4.loader.exec_module(v4)
v3=v4.v3; v2=v4.v2; v1=v4.v1; base=v1.base; mp=v1.mp; pre=v1.pre

# Load only v6 identity helpers without calling main.
spec6=importlib.util.spec_from_file_location('secv6','/tmp/quu_sec_pit_marketcap_validation_v6.py')
if spec6 is None or spec6.loader is None: raise RuntimeError('cannot load v6')
v6=importlib.util.module_from_spec(spec6); sys.modules['secv6']=v6; spec6.loader.exec_module(v6)

START='2012-11'; END='2025-12'; BUILD_START='2012-05'; CAP=.20
OUT=Path('quu-sec-pit-marketcap-validation-v7.json')


def baseline_signal(prices,s,m):
    signal=base.month_end(base.month_add(m,-1)); early=base.month_end(base.month_add(m,-6))
    a=base.close_on_or_before(prices,s,signal,'Adj Close'); b=base.close_on_or_before(prices,s,early,'Adj Close'); mr=v1.adj_month_return(prices,s,m)
    if not(a and b and mr is not None):return None
    gross=a/b
    if not(0.10<=gross<=6.0 and -0.95<=mr<=5.0):return None
    return {'gross':float(gross),'momentum':float(gross-1),'monthlyReturn':float(mr)}


def cap_weights(raw,cap=CAP):
    free=set(raw);out={};rem=1.0
    while free:
        total=sum(raw[s] for s in free)
        if total<=0:raise RuntimeError('raw<=0')
        over=[s for s in free if rem*raw[s]/total>cap+1e-12]
        if not over:
            for s in free:out[s]=rem*raw[s]/total
            break
        for s in over:out[s]=cap;rem-=cap;free.remove(s)
    return out


def metrics(rs):
    a=np.asarray(rs,float)/100.;eq=np.concatenate([[1.],np.cumprod(1+a)]);peak=np.maximum.accumulate(eq);dd=eq/peak-1;cagr=eq[-1]**(12/len(a))-1
    return {'months':len(a),'totalReturnPct':float((eq[-1]-1)*100),'CAGRpct':float(cagr*100),'MDDpct':float(dd.min()*100),'Calmar':float(cagr/abs(dd.min())) if dd.min()<0 else None,'endingIndex':float(eq[-1]*100)}


def main():
    membership=base.load_membership(base.NDX100_URL)
    begin=base.month_end(base.month_add(BUILD_START,-6));end_signal=base.month_end(base.month_add(END,-1))
    symbols=set(base.members_at(membership,begin))
    for dt,tks in membership:
        if begin<dt<=end_signal:symbols.update(tks)
    symbols.update(base.members_at(membership,end_signal));symbols=sorted(symbols)

    # One FINSABER load supplies both dated identity and missing-only price fallback.
    allf=v3.load_finsaber_all(); wanted=set(symbols); ident_rows=allf[allf['symbol'].isin(wanted)][['date','symbol','cik_norm']].dropna().copy()
    identity={}
    for s,g in ident_rows.groupby('symbol'):
        g=g.sort_values('date').drop_duplicates('date',keep='last');identity[s]=(g['date'].to_numpy(),g['cik_norm'].to_numpy())

    static_map,static_src=v2.load_cik_map_v2(); hist_ciks=sorted(set(ident_rows['cik_norm'].astype(str))); all_ciks=sorted(set(hist_ciks)|{static_map[s] for s in symbols if static_map.get(s)})
    switches=[]
    for s in symbols:
        vals=list(dict.fromkeys(str(x) for x in identity[s][1])) if s in identity else [];st=static_map.get(s)
        if vals and (len(vals)>1 or st not in vals):switches.append({'ticker':s,'historicalCIKs':vals,'staticCIK':st})
    print('identity',len(identity),'ciks',len(all_ciks),'switches',len(switches),flush=True)

    facts={}
    for i,c in enumerate(all_ciks,1):
        facts[c]=v4.enhanced_share_facts(v1.companyfacts(c))
        if i%25==0 or i==len(all_ciks):print('facts',i,'/',len(all_ciks),'with',sum(bool(x) for x in facts.values()),flush=True)
        time.sleep(.11)

    # Better-validated V2 path stays primary.
    start_day=str((pd.Period(BUILD_START,freq='M')-7).start_time.date());end_day='2026-01-05'
    prices=base.download_prices(symbols,start_day,end_day);recovery=mp.recover_yahoo_prices(prices,symbols,start_day,end_day);wiki=pre.load_wiki_adjusted(symbols,start_day);merge=mp.merge_wiki(prices,wiki)

    # FINSABER frames are keyed by exact dated issuer identity, so ticker reuse cannot bleed across CIKs.
    fb={}
    for (s,c),g in allf[allf['symbol'].isin(wanted)].groupby(['symbol','cik_norm']):
        f=v3.to_price_frame(g)
        if f is not None:fb[(str(s),str(c))]=f
    print('finsaber issuer frames',len(fb),flush=True)

    bench=base.download_prices(['QQQ'],'2012-01-01',end_day);exact=requests.get(v1.EXACT_QUU,headers=v1.UA,timeout=60).json().get('months') or {}
    src_signal=Counter();src_cap=Counter();missing=Counter();months=[];m=BUILD_START
    while m<=END:
        signal_date=base.month_end(base.month_add(m,-1));universe=base.members_at(membership,signal_date);rows=[]
        for s in universe:
            c=v6.cik_at(identity,s,signal_date);idsrc='finsaber-date-cik' if c else None
            if not c:c=static_map.get(s);idsrc='static-fallback' if c else None
            sig=baseline_signal(prices,s,m);sigsrc='V2_PRIMARY' if sig else None
            if sig is None and c:
                sig=v4.same_source_signal(fb.get((s,c)),m);sigsrc='FINSABER_FALLBACK' if sig else None
            if sig is None:missing['PRICE_OR_RETURN']+=1;continue
            src_signal[sigsrc]+=1
            fact=v4.latest_fact(facts.get(c,[]),signal_date) if c else None
            if not c:missing['NO_CIK']+=1
            elif not fact:missing['NO_PIT_SHARE_FACT']+=1
            cap=None;capsrc=None
            if fact:
                cap=v1.reconstructed_cap(prices,s,fact,signal_date)
                if cap:capsrc='V2_PRIMARY'
                elif c:
                    cap=v4.cap_from_source(fb.get((s,c)),fact,signal_date)
                    if cap:capsrc='FINSABER_FALLBACK'
                if not cap:missing['CAP_RECON_PRICE_GAP']+=1
            if capsrc:src_cap[capsrc]+=1
            rows.append({'ticker':s,**sig,'cik':c,'identitySource':idsrc,'signalSource':sigsrc,'cap':cap,'capSource':capsrc})
        pc=len(rows)/max(1,len(universe));cc=sum(bool(r['cap']) for r in rows)/max(1,len(universe));months.append({'month':m,'universeCount':len(universe),'priceCoverage':pc,'capCoverage':cc,'rows':rows})
        print(m,f'price={pc:.1%}',f'cap={cc:.1%}',flush=True);m=base.month_add(m,1)

    eligible={x['month'] for x in months if x['priceCoverage']>=.90 and x['capCoverage']>=.90};continuous=None
    for x in months:
        if x['month']<START or x['month'] not in eligible:continue
        cur=x['month'];ok=True
        while cur<=END:
            if cur not in eligible:ok=False;break
            cur=base.month_add(cur,1)
        if ok:continuous=x['month'];break

    results=[]
    for x in months:
        if x['priceCoverage']<.90 or x['capCoverage']<.90:continue
        rows=x['rows'];caps=[r['cap'] for r in rows if r['cap']];medcap=float(np.median(caps));medgross=float(np.median([r['gross'] for r in rows]));raw={};fallback=0
        for r in rows:
            c=r['cap']
            if not c:c=medcap;fallback+=1
            raw[r['ticker']]=math.sqrt(c)*((r['gross']/medgross)**3)
        w=cap_weights(raw);risk=100*sum(w[r['ticker']]*r['monthlyReturn'] for r in rows);qr=v1.adj_month_return(bench,'QQQ',x['month'])
        if qr is not None:results.append({'month':x['month'],'riskReturn':risk,'QQQ':qr*100,'capCoverage':x['capCoverage'],'priceCoverage':x['priceCoverage'],'medianCapFallbackCount':fallback,'weights':w,'rows':rows})

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
    out={'generatedAt':datetime.now(timezone.utc).isoformat(),'method':'SEC PIT v7: dated ticker->CIK identity. V2 Yahoo+aliases+WIKI prices are always primary; exact-CIK FINSABER price frame is used only when the primary path cannot form a ticker-month signal/cap. Enhanced SEC actual/weighted-average share facts are filed<=signal with max 550d staleness. >=90% price/cap coverage gate before median-cap fallback.','identity':{'symbols':len(identity),'historicalCIKs':len(hist_ciks),'allFetchedCIKs':len(all_ciks),'switchCount':len(switches),'switches':switches},'sourceCounts':{'signal':dict(src_signal),'cap':dict(src_cap)},'missingReasonCounts':dict(missing),'priceRecovery':recovery,'wikiMerge':merge,'coverageByMonth':[{'month':x['month'],'priceCoverage':x['priceCoverage'],'capCoverage':x['capCoverage'],'universeCount':x['universeCount']} for x in months],'continuousStart90Pct':continuous,'validationAgainstRecentExact':validation,'performance':{'period':[use[0]['month'],use[-1]['month']] if use else None,'months':len(use),'RiskSleeve':metrics([r['riskReturn'] for r in use]) if use else None,'QQQ':metrics([r['QQQ'] for r in use]) if use else None},'monthly':[{k:v for k,v in r.items() if k not in ('rows','weights')} for r in use]}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print('V7_FINAL',json.dumps({'continuousStart90Pct':continuous,'sourceCounts':out['sourceCounts'],'missingReasonCounts':dict(missing),'validation':validation,'performance':out['performance']},ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
