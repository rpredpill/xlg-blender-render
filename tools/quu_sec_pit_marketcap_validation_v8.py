#!/usr/bin/env python3
from __future__ import annotations

import importlib.util, io, json, math, sys, time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import pstdev

import numpy as np
import pandas as pd
import requests

# Reuse the already validated v7 price / SEC / identity utilities.
spec7 = importlib.util.spec_from_file_location('secv7','/tmp/quu_sec_pit_marketcap_validation_v7.py')
if spec7 is None or spec7.loader is None:
    raise RuntimeError('cannot load v7')
v7 = importlib.util.module_from_spec(spec7); sys.modules['secv7'] = v7; spec7.loader.exec_module(v7)
v4=v7.v4; v3=v7.v3; v2=v7.v2; v1=v7.v1; v6=v7.v6; base=v7.base; mp=v7.mp; pre=v7.pre

BUILD_START='2011-08'
START='2012-04'
END='2025-12'
CAP=.20
MIN_COVERAGE=.90
MIN_PRIOR=8
OUT=Path('quu-sec-pit-marketcap-validation-v8.json')

HF_BASE='https://huggingface.co/datasets/SarthakVishnu/dissertation-dataset/resolve/main/'
HF_COMPUSTAT=HF_BASE+'compustat/compustat_fundamentals.csv?download=true'
HF_DSNAMES=HF_BASE+'raw/crsp_dsenames.csv?download=true'
HF_CCM=HF_BASE+'raw/ccm_linking_table.csv?download=true'
HF_PERMNO_LINK=HF_BASE+'intermediate_data_ref_only/permno_linkage.csv?download=true'


def get_csv(url:str)->pd.DataFrame:
    r=requests.get(url,headers=v1.UA,timeout=180)
    r.raise_for_status()
    return pd.read_csv(io.BytesIO(r.content),low_memory=False)


def norm_ticker(x):
    return str(x or '').strip().upper().replace('/','.').replace('-','.')


def as_num(x):
    try:
        z=float(x)
        return z if math.isfinite(z) else None
    except Exception:
        return None


def prep_compustat():
    comp=get_csv(HF_COMPUSTAT)
    names=get_csv(HF_DSNAMES)
    ccm=get_csv(HF_CCM)
    plink=get_csv(HF_PERMNO_LINK)

    comp.columns=[str(c).strip().lower() for c in comp.columns]
    names.columns=[str(c).strip().lower() for c in names.columns]
    ccm.columns=[str(c).strip().lower() for c in ccm.columns]
    plink.columns=[str(c).strip().lower() for c in plink.columns]

    if 'gvkey' not in comp.columns:
        raise RuntimeError(f'Compustat gvkey missing: {list(comp.columns)}')
    comp['gvkey_norm']=comp['gvkey'].astype(str).str.replace(r'\.0$','',regex=True).str.zfill(6)
    if 'datadate' in comp.columns:
        comp['datadate_norm']=pd.to_datetime(comp['datadate'],errors='coerce').dt.tz_localize(None).dt.normalize()
    else:
        # Conservative fallback when the saved query omitted datadate: assume fiscal year-end Dec-31.
        comp['datadate_norm']=pd.to_datetime(pd.to_numeric(comp.get('fyear'),errors='coerce').astype('Int64').astype(str)+'-12-31',errors='coerce')
    for c in ('csho','mkvalt','prcc_f','fyear'):
        if c in comp.columns: comp[c]=pd.to_numeric(comp[c],errors='coerce')

    # CRSP name history gives ticker -> PERMNO valid on a specific date.
    ticker_col='ticker' if 'ticker' in names.columns else None
    permno_col=next((c for c in ('permno','lpermno') if c in names.columns),None)
    if not ticker_col or not permno_col:
        raise RuntimeError(f'CRSP names columns unexpected: {list(names.columns)}')
    names['ticker_norm']=names[ticker_col].map(norm_ticker)
    names['permno_norm']=pd.to_numeric(names[permno_col],errors='coerce').astype('Int64')
    names['namedt_norm']=pd.to_datetime(names.get('namedt'),errors='coerce').dt.tz_localize(None).dt.normalize()
    names['nameendt_norm']=pd.to_datetime(names.get('nameendt'),errors='coerce').dt.tz_localize(None).dt.normalize()

    ccm_perm=next((c for c in ('lpermno','permno') if c in ccm.columns),None)
    if not ccm_perm or 'gvkey' not in ccm.columns:
        raise RuntimeError(f'CCM columns unexpected: {list(ccm.columns)}')
    ccm['permno_norm']=pd.to_numeric(ccm[ccm_perm],errors='coerce').astype('Int64')
    ccm['gvkey_norm']=ccm['gvkey'].astype(str).str.replace(r'\.0$','',regex=True).str.zfill(6)
    ccm['linkdt_norm']=pd.to_datetime(ccm.get('linkdt'),errors='coerce').dt.tz_localize(None).dt.normalize()
    ccm['linkenddt_norm']=pd.to_datetime(ccm.get('linkenddt'),errors='coerce').dt.tz_localize(None).dt.normalize()

    # Backup CIK -> GVKEY linkage for names that CRSP cannot resolve by ticker-date.
    if 'cik' in plink.columns:
        plink['cik_norm']=plink['cik'].map(v1.zcik)
    else:
        plink['cik_norm']=None
    if 'gvkey' in plink.columns:
        plink['gvkey_norm']=plink['gvkey'].astype(str).str.replace(r'\.0$','',regex=True).str.zfill(6)
    else:
        plink['gvkey_norm']=None

    print('COMPUSTAT columns',list(comp.columns),flush=True)
    print('COMPUSTAT rows',len(comp),'gvkeys',comp['gvkey_norm'].nunique(),flush=True)
    return comp,names,ccm,plink


def gvkey_at(ticker:str, dt:pd.Timestamp, cik:str|None, names,ccm,plink):
    t=norm_ticker(ticker)
    n=names[names['ticker_norm']==t]
    if not n.empty:
        good=n[(n['namedt_norm'].isna() | (n['namedt_norm']<=dt)) & (n['nameendt_norm'].isna() | (n['nameendt_norm']>=dt))]
        if not good.empty:
            good=good.sort_values('namedt_norm',na_position='first')
            p=good.iloc[-1]['permno_norm']
            if pd.notna(p):
                z=ccm[ccm['permno_norm']==int(p)]
                z=z[(z['linkdt_norm'].isna() | (z['linkdt_norm']<=dt)) & (z['linkenddt_norm'].isna() | (z['linkenddt_norm']>=dt))]
                if not z.empty:
                    if 'linkprim' in z.columns:
                        z=z.assign(_pri=z['linkprim'].astype(str).map({'P':0,'C':1}).fillna(2)).sort_values(['_pri','linkdt_norm'])
                    return str(z.iloc[-1]['gvkey_norm']),'crsp-date-link'
    if cik:
        z=plink[plink['cik_norm']==v1.zcik(cik)]
        z=z[z['gvkey_norm'].notna()]
        if not z.empty:
            return str(z.iloc[-1]['gvkey_norm']),'cik-permno-link'
    return None,None


def comp_cap_record(comp, gvkey:str, signal:pd.Timestamp):
    z=comp[comp['gvkey_norm']==gvkey].copy()
    if z.empty:return None
    # Compustat annual data becomes usable only 180 days after fiscal year-end.
    # This is intentionally conservative and avoids using year-end values before filing/publication.
    z=z[z['datadate_norm'].notna()]
    z=z[(z['datadate_norm']+pd.Timedelta(days=180) <= signal) & ((signal-z['datadate_norm']).dt.days <= 730)]
    if z.empty:return None
    z=z.sort_values('datadate_norm')
    r=z.iloc[-1]
    mk=as_num(r.get('mkvalt'))
    csho=as_num(r.get('csho')); pr=as_num(r.get('prcc_f'))
    if mk and mk>0: base_cap=mk*1_000_000.0; mode='mkvalt'
    elif csho and csho>0 and pr and pr>0: base_cap=csho*1_000_000.0*pr; mode='prcc_f*csho'
    else:return None
    return {'end':pd.Timestamp(r['datadate_norm']).normalize(),'baseCap':float(base_cap),'mode':mode,'gvkey':gvkey}


def adj_point(frame,dt,max_lag=45):
    if frame is None or frame.empty or 'Adj Close' not in frame.columns:return None
    z=frame.loc[frame.index<=dt,'Adj Close'].dropna()
    if z.empty:return None
    d=pd.Timestamp(z.index[-1]).normalize()
    if (dt-d).days>max_lag:return None
    v=float(z.iloc[-1]); return v if math.isfinite(v) and v>0 else None


def comp_cap_to_signal(rec, signal, primary_frame, fallback_frame):
    for name,frame in (('V2_PRIMARY',primary_frame),('FINSABER_FALLBACK',fallback_frame)):
        ae=adj_point(frame,rec['end']); a1=adj_point(frame,signal)
        if ae and a1:
            c=rec['baseCap']*(a1/ae)
            if math.isfinite(c) and c>0:return float(c),f'COMPUSTAT_{name}'
    return None,None


def pct_prior(prior,x):
    return None if len(prior)<MIN_PRIOR else 100.0*sum(1 for y in prior if y<=x)/len(prior)


def metrics(rs):
    a=np.asarray(rs,float)/100.0
    if len(a)==0:return None
    eq=np.concatenate([[1.0],np.cumprod(1.0+a)]); peak=np.maximum.accumulate(eq); dd=eq/peak-1
    cagr=eq[-1]**(12.0/len(a))-1.0
    return {'months':len(a),'totalReturnPct':float((eq[-1]-1)*100),'CAGRpct':float(cagr*100),'MDDpct':float(dd.min()*100),'Calmar':float(cagr/abs(dd.min())) if dd.min()<0 else None,'endingIndex':float(eq[-1]*100)}


def main():
    membership=base.load_membership(base.NDX100_URL)
    begin=base.month_end(base.month_add(BUILD_START,-6)); end_signal=base.month_end(base.month_add(END,-1))
    symbols=set(base.members_at(membership,begin))
    for dt,tks in membership:
        if begin<dt<=end_signal:symbols.update(tks)
    symbols.update(base.members_at(membership,end_signal)); symbols=sorted(symbols)

    # Identity + SEC facts from v7.
    allf=v3.load_finsaber_all(); wanted=set(symbols)
    ident_rows=allf[allf['symbol'].isin(wanted)][['date','symbol','cik_norm']].dropna().copy()
    identity={}
    for s,g in ident_rows.groupby('symbol'):
        g=g.sort_values('date').drop_duplicates('date',keep='last'); identity[s]=(g['date'].to_numpy(),g['cik_norm'].to_numpy())
    static_map,_=v2.load_cik_map_v2(); hist_ciks=sorted(set(ident_rows['cik_norm'].astype(str))); all_ciks=sorted(set(hist_ciks)|{static_map[s] for s in symbols if static_map.get(s)})
    facts={}
    for i,c in enumerate(all_ciks,1):
        facts[c]=v4.enhanced_share_facts(v1.companyfacts(c))
        if i%30==0 or i==len(all_ciks):print('SEC facts',i,'/',len(all_ciks),flush=True)
        time.sleep(.11)

    comp,names,ccm,plink=prep_compustat()

    # V2 price path remains primary; FINSABER is missing-only fallback.
    start_day=str((pd.Period(BUILD_START,freq='M')-7).start_time.date()); end_day='2026-01-05'
    prices=base.download_prices(symbols,start_day,end_day); recovery=mp.recover_yahoo_prices(prices,symbols,start_day,end_day); wiki=pre.load_wiki_adjusted(symbols,start_day); merge=mp.merge_wiki(prices,wiki)
    fb={}
    for (s,c),g in allf[allf['symbol'].isin(wanted)].groupby(['symbol','cik_norm']):
        f=v3.to_price_frame(g)
        if f is not None:fb[(str(s),str(c))]=f

    bench=base.download_prices(['QQQ','QQQE'],'2011-01-01',end_day)
    exact=requests.get(v1.EXACT_QUU,headers=v1.UA,timeout=60).json().get('months') or {}

    src_signal=Counter(); src_cap=Counter(); missing=Counter(); months=[]; m=BUILD_START
    while m<=END:
        signal=base.month_end(base.month_add(m,-1)); universe=base.members_at(membership,signal); rows=[]
        for s in universe:
            c=v6.cik_at(identity,s,signal) or static_map.get(s)
            sig=v7.baseline_signal(prices,s,m); sigsrc='V2_PRIMARY' if sig else None
            if sig is None and c:
                sig=v4.same_source_signal(fb.get((s,c)),m); sigsrc='FINSABER_FALLBACK' if sig else None
            if sig is None:
                missing['PRICE_OR_RETURN']+=1; continue
            src_signal[sigsrc]+=1

            cap=None; capsrc=None
            fact=v4.latest_fact(facts.get(c,[]),signal) if c else None
            if fact:
                cap=v1.reconstructed_cap(prices,s,fact,signal)
                if cap:capsrc='SEC_V2_PRIMARY'
                elif c:
                    cap=v4.cap_from_source(fb.get((s,c)),fact,signal)
                    if cap:capsrc='SEC_FINSABER_FALLBACK'
            if cap is None:
                gv,gsrc=gvkey_at(s,signal,c,names,ccm,plink)
                crec=comp_cap_record(comp,gv,signal) if gv else None
                if crec:
                    cap,capsrc=comp_cap_to_signal(crec,signal,prices.get(s),fb.get((s,c)) if c else None)
                if cap is None:
                    if not c:missing['NO_CIK']+=1
                    if not fact:missing['NO_SEC_SHARE_FACT']+=1
                    if not gv:missing['NO_GVKEY']+=1
                    elif not crec:missing['NO_PIT_COMPUSTAT']+=1
                    else:missing['COMPUSTAT_PRICE_ROLL_GAP']+=1
            if capsrc:src_cap[capsrc]+=1
            rows.append({'ticker':s,**sig,'cik':c,'cap':cap,'capSource':capsrc})
        pc=len(rows)/max(1,len(universe)); cc=sum(bool(r['cap']) for r in rows)/max(1,len(universe))
        months.append({'month':m,'universeCount':len(universe),'priceCoverage':pc,'capCoverage':cc,'rows':rows})
        print(m,f'price={pc:.1%}',f'cap={cc:.1%}',flush=True)
        m=base.month_add(m,1)

    # Build exact-ish risk sleeve on every >=90% month.
    risk_by_month={}; prior=[]; state=[]
    for x in months:
        if x['priceCoverage']<MIN_COVERAGE or x['capCoverage']<MIN_COVERAGE:continue
        rows=x['rows']; caps=[r['cap'] for r in rows if r['cap']]
        medcap=float(np.median(caps)); medgross=float(np.median([r['gross'] for r in rows])); raw={}; fallback=0
        for r in rows:
            c=r['cap']
            if not c:c=medcap; fallback+=1
            raw[r['ticker']]=math.sqrt(c)*(r['gross']/medgross)**3
        w=v7.cap_weights(raw); risk=100.0*sum(w[r['ticker']]*r['monthlyReturn'] for r in rows)
        d=float(pstdev([r['momentum'] for r in rows])); p=pct_prior(prior,d); falling=bool(prior and d<prior[-1])
        if p is None:
            med=float(np.median(prior)) if prior else None; strong=True if med is None else d>med; overlay=False
        else:
            strong=p>=50.0; overlay=p>=90.0 and falling
        risk_by_month[x['month']]={'riskReturn':risk,'weights':w,'rows':rows,'dispersion':d,'percentile':p,'falling':falling,'strong':strong,'overlay':overlay,'fallbackCount':fallback,'priorCount':len(prior),'priceCoverage':x['priceCoverage'],'capCoverage':x['capCoverage']}
        prior.append(d)

    # Continuous period must have >=90% data every month and 8 prior valid observations.
    eligible=set(risk_by_month)
    continuous=None
    for x in months:
        m=x['month']
        if m<START or m not in eligible or risk_by_month[m]['priorCount']<MIN_PRIOR:continue
        cur=m; ok=True
        while cur<=END:
            if cur not in eligible:ok=False;break
            cur=base.month_add(cur,1)
        if ok:continuous=m;break

    perf_rows=[]
    if continuous:
        m=continuous
        while m<=END:
            z=risk_by_month[m]
            q=v1.adj_month_return(bench,'QQQ',m); e=v1.adj_month_return(bench,'QQQE',m)
            if q is None or e is None:raise RuntimeError(f'benchmark gap {m}')
            q*=100;e*=100
            sleeve='QQQE' if z['overlay'] else ('QUQU' if z['strong'] else 'QQQ')
            quu=e if sleeve=='QQQE' else (z['riskReturn'] if sleeve=='QUQU' else q)
            perf_rows.append({'month':m,'QUU':quu,'QQQ':q,'QQQE':e,'riskReturn':z['riskReturn'],'sleeve':sleeve,'percentile':z['percentile'],'falling':z['falling'],'capCoverage':z['capCoverage'],'priceCoverage':z['priceCoverage'],'fallbackCount':z['fallbackCount']})
            m=base.month_add(m,1)

    # Recent exact validation is only for the reconstructed QUQU risk sleeve.
    cap_pairs=[]; maes=[]; rd=[]; overlap=[]
    for m,z in risk_by_month.items():
        ex=exact.get(m)
        if not isinstance(ex,dict):continue
        em={r.get('ticker'):r for r in (ex.get('rows') or []) if r.get('ticker')}
        for r in z['rows']:
            ec=em.get(r['ticker'],{}).get('marketCap')
            if r.get('cap') and isinstance(ec,(int,float)) and ec>0:cap_pairs.append((math.log(r['cap']),math.log(ec)))
        common=[t for t in z['weights'] if t in em and isinstance(em[t].get('weight'),(int,float))]
        if common:
            ss=sum(float(em[t]['weight']) for t in common)
            if ss>0:maes.append(float(np.mean([abs(z['weights'][t]-float(em[t]['weight'])/ss) for t in common])))
        if ex.get('selectedSleeve')=='QUQU' and isinstance(ex.get('portfolioReturn'),(int,float)):
            rd.append(z['riskReturn']-float(ex['portfolioReturn'])); overlap.append(m)
    validation={'capPairCount':len(cap_pairs),'logMarketCapCorrelation':float(np.corrcoef(np.array(cap_pairs).T)[0,1]) if len(cap_pairs)>2 else None,'meanWeightMAE':float(np.mean(maes)) if maes else None,'riskReturnOverlapMonths':len(rd),'meanRiskMinusExactPp':float(np.mean(rd)) if rd else None,'MAERiskReturnPp':float(np.mean(np.abs(rd))) if rd else None,'overlapMonths':overlap}

    out={
        'generatedAt':datetime.now(timezone.utc).isoformat(),
        'method':'SEC PIT v8: v7 dated identity + V2 price primary/FINSABER missing-only. Market cap uses SEC PIT shares first. Missing SEC cap is filled only from Compustat annual fiscal-year-end market cap/shares after a conservative 180-day availability lag, mapped by historical CRSP ticker->PERMNO->CCM GVKEY (CIK linkage backup), and rolled to the signal date with adjusted price. Raw licensed Compustat/CRSP data is not stored in output.',
        'compustatFallback':{'availabilityLagDays':180,'maxFiscalYearEndAgeDays':730,'rawDataPersisted':False},
        'sourceCounts':{'signal':dict(src_signal),'cap':dict(src_cap)},
        'missingReasonCounts':dict(missing),
        'coverageByMonth':[{'month':x['month'],'priceCoverage':x['priceCoverage'],'capCoverage':x['capCoverage'],'universeCount':x['universeCount']} for x in months],
        'continuousStart90Pct':continuous,
        'validationAgainstRecentExact':validation,
        'performance':{
            'period':[perf_rows[0]['month'],perf_rows[-1]['month']] if perf_rows else None,
            'months':len(perf_rows),
            'QUU_MR90_QQQE':metrics([r['QUU'] for r in perf_rows]) if perf_rows else None,
            'QQQ':metrics([r['QQQ'] for r in perf_rows]) if perf_rows else None,
            'RiskSleeve':metrics([r['riskReturn'] for r in perf_rows]) if perf_rows else None,
        },
        'monthly':perf_rows,
        'priceRecovery':recovery,'wikiMerge':merge,
    }
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print('V8_FINAL',json.dumps({'continuousStart90Pct':continuous,'sourceCounts':out['sourceCounts'],'missingReasonCounts':out['missingReasonCounts'],'validation':validation,'performance':out['performance']},ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':
    main()
