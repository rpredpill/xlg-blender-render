#!/usr/bin/env python3
from __future__ import annotations

import csv, io, importlib.util, json, math, sys, time
from datetime import datetime, timezone
from pathlib import Path
from statistics import pstdev

import numpy as np
import pandas as pd
import requests

# Reuse the frozen price/membership engines already used by the QUU research.
spec = importlib.util.spec_from_file_location('maxpit','/tmp/quu_max_pit_validation.py')
if spec is None or spec.loader is None:
    raise RuntimeError('cannot load max PIT module')
mp = importlib.util.module_from_spec(spec); sys.modules['maxpit']=mp; spec.loader.exec_module(mp)
base = mp.base

spec2 = importlib.util.spec_from_file_location('preproxy','/tmp/quu_pre2018_proxy_validation.py')
if spec2 is None or spec2.loader is None:
    raise RuntimeError('cannot load pre-2018 proxy module')
pre = importlib.util.module_from_spec(spec2); sys.modules['preproxy']=pre; spec2.loader.exec_module(pre)

START='2012-11'; END='2025-12'; BUILD_START='2012-05'; CAP=0.20
OUT=Path('quu-sec-pit-marketcap-validation.json')
UA={'User-Agent':'QUU historical research contact research@example.com'}

CURRENT_CIK='https://raw.githubusercontent.com/jadchaar/sec-cik-mapper/main/mappings/stocks/ticker_to_cik.json'
TICKER_CHANGES='https://raw.githubusercontent.com/Quant-Lodge/ticker-reference-data/main/data/ticker_changes.json'
SP500_HIST='https://raw.githubusercontent.com/lawcal/sp500-components-history/main/data/components_history.csv'
SNAP2017='https://raw.githubusercontent.com/erez-meoded/cik-ticker/master/companies-2017-11-15T175637.450940.csv'
EXACT_QUU='https://raw.githubusercontent.com/rpredpill/xlg-blender-render/somx-pages/quu-history.json'

ALIASES={'NLOK':'GEN','CTRP':'TCOM','PCLN':'BKNG','FB':'META','RIMM':'BB','WLTW':'WTW','FI':'FISV'}


def norm(x): return str(x or '').strip().upper().replace('/','.').replace('-','.')
def zcik(x):
    s=''.join(ch for ch in str(x or '') if ch.isdigit())
    return s.zfill(10) if s else None

def get(url, timeout=60):
    r=requests.get(url,headers=UA,timeout=timeout); r.raise_for_status(); return r

def load_cik_map():
    out={}; src={}
    def put(t,c,source,overwrite=False):
        t=norm(t); c=zcik(c)
        if not t or not c: return
        if overwrite or t not in out:
            out[t]=c; src[t]=source
    # Current SEC-derived mapping.
    for t,c in get(CURRENT_CIK).json().items(): put(t,c,'current-sec-map')
    # Rename cache with stable CIK; map both old and current symbols.
    try:
        ch=get(TICKER_CHANGES,120).json()
        for cur,v in ch.items():
            if not isinstance(v,dict): continue
            c=v.get('cik'); old=v.get('old_ticker')
            if c:
                put(cur,c,'ticker-changes')
                if old: put(old,c,'ticker-changes-old')
    except Exception as e: print('ticker changes failed',e,flush=True)
    # S&P history is useful for acquired/delisted former Nasdaq-100 names.
    try:
        d=pd.read_csv(io.StringIO(get(SP500_HIST).text))
        for _,r in d.iterrows(): put(r.get('symbol'),r.get('cik'),'sp500-history')
    except Exception as e: print('sp500 history failed',e,flush=True)
    # 2017 broad SEC snapshot catches many names later delisted/renamed.
    try:
        d=pd.read_csv(io.StringIO(get(SNAP2017).text),low_memory=False)
        cols={str(c).lower():c for c in d.columns}
        tc=next((cols[k] for k in cols if k in ('ticker','tickersymbol','symbol')),None)
        cc=next((cols[k] for k in cols if k in ('cik','ciknumber','cik number')),None)
        if tc is not None and cc is not None:
            for _,r in d.iterrows(): put(r.get(tc),r.get(cc),'2017-sec-snapshot')
        else: print('2017 snapshot columns',list(d.columns),flush=True)
    except Exception as e: print('2017 snapshot failed',e,flush=True)
    # Same-company ticker aliases; resolve through whatever source has the new ticker.
    for old,new in ALIASES.items():
        if norm(new) in out: put(old,out[norm(new)],'manual-same-company-alias',overwrite=True)
    return out,src


def companyfacts(cik):
    url=f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json'
    try:
        r=requests.get(url,headers=UA,timeout=45)
        if r.status_code!=200: return None
        return r.json()
    except Exception: return None


def extract_share_facts(cf):
    if not isinstance(cf,dict): return []
    candidates=[]
    facts=cf.get('facts') or {}
    preferred=[('dei','EntityCommonStockSharesOutstanding'),('us-gaap','CommonStockSharesOutstanding')]
    seen=set()
    for ns,tag in preferred:
        node=((facts.get(ns) or {}).get(tag) or {})
        for unit,rows in (node.get('units') or {}).items():
            if str(unit).lower() not in ('shares','share'): continue
            for x in rows or []:
                try:
                    val=float(x.get('val')); end=pd.Timestamp(x.get('end')).normalize(); filed=pd.Timestamp(x.get('filed')).normalize()
                except Exception: continue
                if not(math.isfinite(val) and val>0): continue
                key=(end,filed,val)
                if key in seen: continue
                seen.add(key)
                candidates.append({'end':end,'filed':filed,'shares':val,'tag':f'{ns}:{tag}','form':x.get('form')})
    # Conservative fallback: any instant-style SEC fact with SharesOutstanding in tag name.
    if not candidates:
        for ns,nodes in facts.items():
            for tag,node in (nodes or {}).items():
                if 'sharesoutstanding' not in tag.lower(): continue
                for unit,rows in (node.get('units') or {}).items():
                    if str(unit).lower() not in ('shares','share'): continue
                    for x in rows or []:
                        try:
                            val=float(x.get('val')); end=pd.Timestamp(x.get('end')).normalize(); filed=pd.Timestamp(x.get('filed')).normalize()
                        except Exception: continue
                        if math.isfinite(val) and val>0:
                            candidates.append({'end':end,'filed':filed,'shares':val,'tag':f'{ns}:{tag}','form':x.get('form')})
    candidates.sort(key=lambda x:(x['filed'],x['end']))
    return candidates


def latest_fact(facts, signal_date):
    usable=[x for x in facts if x['filed']<=signal_date and x['end']<=signal_date]
    if not usable: return None
    x=max(usable,key=lambda z:(z['filed'],z['end']))
    # More than 18 months stale is too weak for a market-cap reconstruction.
    if (signal_date-x['end']).days>550: return None
    return x


def cap_and_redistribute(raw,cap=CAP):
    free=set(raw); out={}; rem=1.0
    while free:
        total=sum(raw[s] for s in free)
        if not(total>0): raise RuntimeError('raw sum <=0')
        over=[s for s in free if rem*raw[s]/total>cap+1e-12]
        if not over:
            for s in free: out[s]=rem*raw[s]/total
            break
        for s in over:
            out[s]=cap; rem-=cap; free.remove(s)
    return out


def adj_month_return(prices,symbol,ym):
    d=prices.get(symbol)
    if d is None or d.empty: return None
    start=pd.Period(ym,freq='M').start_time.normalize(); end=base.next_month_start(ym)
    g=d[(d.index>=start)&(d.index<end)].copy()
    if g.empty:return None
    # WIKI native adjusted-open if available.
    if 'Adj Open' in g.columns:
        z=g.dropna(subset=['Adj Open','Adj Close'])
        if not z.empty:
            a=float(z.iloc[0]['Adj Open']); b=float(z.iloc[-1]['Adj Close'])
            if a>0 and b>0:return b/a-1
    z=g.dropna(subset=['Open','Close','Adj Close'])
    if z.empty:return None
    f=z.iloc[0]; l=z.iloc[-1]
    o=float(f['Open']); c=float(f['Close']); a0=float(f['Adj Close']); a1=float(l['Adj Close'])
    if min(o,c,a0,a1)<=0:return None
    return a1/(o*a0/c)-1


def reconstructed_cap(prices,symbol,fact,signal_date):
    # Build market cap at the public fact's end date from raw price and reported shares,
    # then roll it forward with adjusted-price relative performance. This is split-safe
    # while keeping the share count strictly point-in-time (filed <= signal date).
    end=fact['end']
    raw_end=base.close_on_or_before(prices,symbol,end,'Close')
    adj_end=base.close_on_or_before(prices,symbol,end,'Adj Close')
    adj_sig=base.close_on_or_before(prices,symbol,signal_date,'Adj Close')
    if not(raw_end and adj_end and adj_sig): return None
    cap=fact['shares']*raw_end*(adj_sig/adj_end)
    return float(cap) if math.isfinite(cap) and cap>0 else None


def pct_prior(xs,x):
    return None if not xs else 100.0*sum(1 for y in xs if y<=x)/len(xs)

def metrics(rs):
    a=np.asarray(rs,float)/100.; eq=np.concatenate([[1.],np.cumprod(1+a)]); peak=np.maximum.accumulate(eq); dd=eq/peak-1
    cagr=eq[-1]**(12/len(a))-1
    return {'months':len(a),'totalReturnPct':float((eq[-1]-1)*100),'CAGRpct':float(cagr*100),'MDDpct':float(dd.min()*100),'Calmar':float(cagr/abs(dd.min())) if dd.min()<0 else None,'endingIndex':float(eq[-1]*100)}


def main():
    membership=base.load_membership(base.NDX100_URL)
    # Union of relevant PIT names.
    begin=base.month_end(base.month_add(BUILD_START,-6)); end_signal=base.month_end(base.month_add(END,-1))
    symbols=set(base.members_at(membership,begin))
    for dt,tks in membership:
        if begin<dt<=end_signal:symbols.update(tks)
    symbols.update(base.members_at(membership,end_signal)); symbols=sorted(symbols)
    print('PIT union',len(symbols),flush=True)

    cikmap,ciksrc=load_cik_map()
    mapped={s:cikmap.get(s) for s in symbols if cikmap.get(s)}
    unmapped=[s for s in symbols if s not in mapped]
    print('CIK mapped',len(mapped),'/',len(symbols),'unmapped',unmapped,flush=True)

    # Fetch SEC share facts once per CIK.
    facts_by_cik={}; unique=sorted(set(mapped.values()))
    for i,c in enumerate(unique,1):
        cf=companyfacts(c); facts_by_cik[c]=extract_share_facts(cf)
        if i%25==0 or i==len(unique):
            ok=sum(bool(v) for v in facts_by_cik.values())
            print('companyfacts',i,'/',len(unique),'withShares',ok,flush=True)
        time.sleep(0.11)

    # Price engine: Yahoo + same-company aliases + WIKI for old/delisted names.
    start_day=str((pd.Period(BUILD_START,freq='M')-7).start_time.date()); end_day='2026-01-05'
    prices=base.download_prices(symbols,start_day,end_day)
    recovery=mp.recover_yahoo_prices(prices,symbols,start_day,end_day)
    wiki=pre.load_wiki_adjusted(symbols,start_day)
    merge=mp.merge_wiki(prices,wiki)
    print('price symbols',len(prices),'recovery',recovery,flush=True)

    bench=base.download_prices(['QQQ','QQQE'],'2012-01-01',end_day)
    exact=requests.get(EXACT_QUU,headers=UA,timeout=60).json().get('months') or {}

    month_rows=[]; m=BUILD_START
    while m<=END:
        signal_date=base.month_end(base.month_add(m,-1)); early=base.month_end(base.month_add(m,-6)); universe=base.members_at(membership,signal_date)
        rows=[]
        for s in universe:
            recent=base.close_on_or_before(prices,s,signal_date,'Adj Close'); earlyp=base.close_on_or_before(prices,s,early,'Adj Close')
            mr=adj_month_return(prices,s,m)
            if not(recent and earlyp and mr is not None): continue
            gross=recent/earlyp
            if not(0.10<=gross<=6.0 and -0.95<=mr<=5.0): continue
            c=mapped.get(s); fact=latest_fact(facts_by_cik.get(c,[]),signal_date) if c else None
            cap=reconstructed_cap(prices,s,fact,signal_date) if fact else None
            rows.append({'ticker':s,'gross':gross,'momentum':gross-1,'monthlyReturn':mr,'cik':c,'cap':cap,'fact':fact})
        price_cov=len(rows)/max(1,len(universe)); cap_rows=[r for r in rows if r['cap']]
        cap_cov=len(cap_rows)/max(1,len(universe))
        month_rows.append({'month':m,'signalDate':str(signal_date.date()),'universeCount':len(universe),'priceCoverage':price_cov,'capCoverage':cap_cov,'rows':rows})
        print(m,f'price={price_cov:.1%}',f'cap={cap_cov:.1%}',flush=True)
        m=base.month_add(m,1)

    # Determine first continuous month from which every invested month has >=90% price and cap coverage.
    eligible={x['month'] for x in month_rows if x['priceCoverage']>=.90 and x['capCoverage']>=.90}
    starts=[]
    for x in month_rows:
        if x['month']<START or x['month'] not in eligible: continue
        cur=x['month']; ok=True
        while cur<=END:
            if cur not in eligible: ok=False; break
            cur=base.month_add(cur,1)
        if ok: starts.append(x['month']); break
    continuous_start=starts[0] if starts else None

    # Build exact-ish risk sleeve with <=10% median-cap fallback, then current QUU rule.
    prior_disp=[]; results=[]
    for x in month_rows:
        rows=x['rows']
        if x['priceCoverage']<.90 or x['capCoverage']<.90: continue
        caps=[r['cap'] for r in rows if r['cap']]
        medcap=float(np.median(caps)); medgross=float(np.median([r['gross'] for r in rows]))
        raw={}; fallback=0
        for r in rows:
            cap=r['cap']
            if not cap: cap=medcap; fallback+=1
            raw[r['ticker']]=math.sqrt(cap)*((r['gross']/medgross)**3)
        w=cap_and_redistribute(raw)
        risk=100*sum(w[r['ticker']]*r['monthlyReturn'] for r in rows)
        d=float(pstdev([r['momentum'] for r in rows])); p=pct_prior(prior_disp,d); falling=bool(prior_disp and d<prior_disp[-1])
        if len(prior_disp)<8:
            med=float(np.median(prior_disp)) if prior_disp else None; strong=True if med is None else d>=med; overlay=False
        else:
            strong=bool(p is not None and p>=50); overlay=bool(p is not None and p>=90 and falling)
        qr=adj_month_return(bench,'QQQ',x['month']); er=adj_month_return(bench,'QQQE',x['month'])
        if qr is None or er is None: continue
        qqq=qr*100; qqqe=er*100
        sleeve='QQQE' if overlay else ('RISK' if strong else 'QQQ')
        quu=qqqe if overlay else (risk if strong else qqq)
        results.append({'month':x['month'],'riskReturn':risk,'QQQ':qqq,'QQQE':qqqe,'QUU':quu,'sleeve':sleeve,'dispersion':d,'percentile':p,'falling':falling,'capCoverage':x['capCoverage'],'medianCapFallbackCount':fallback,'rows':rows,'weights':w})
        prior_disp.append(d)

    # Recent exact validation: compare reconstructed cap levels/weights and portfolio returns on overlapping RISK months.
    cap_pairs=[]; weight_maes=[]; ret_diffs=[]; recent_months=[]
    for r in results:
        e=exact.get(r['month'])
        if not isinstance(e,dict): continue
        erows=e.get('rows') or []; emap={z.get('ticker'):z for z in erows if z.get('ticker')}
        pairs=[]
        for z in r['rows']:
            t=z['ticker']; ec=emap.get(t,{}).get('marketCap')
            if z.get('cap') and isinstance(ec,(int,float)) and ec>0:
                cap_pairs.append((math.log(z['cap']),math.log(ec))); pairs.append((z['cap'],ec))
        common=[t for t in r['weights'] if t in emap and isinstance(emap[t].get('weight'),(int,float))]
        if common:
            s=sum(float(emap[t]['weight']) for t in common)
            if s>0:
                mae=float(np.mean([abs(r['weights'][t]-float(emap[t]['weight'])/s) for t in common]))
                weight_maes.append(mae)
        if e.get('selectedSleeve')=='QUQU' and isinstance(e.get('portfolioReturn'),(int,float)):
            ret_diffs.append(r['riskReturn']-float(e['portfolioReturn']))
            recent_months.append(r['month'])

    cap_corr=float(np.corrcoef(np.array(cap_pairs).T)[0,1]) if len(cap_pairs)>2 else None
    validation={
        'capPairCount':len(cap_pairs),'logMarketCapCorrelation':cap_corr,
        'meanWeightMAE':float(np.mean(weight_maes)) if weight_maes else None,
        'riskReturnOverlapMonths':len(ret_diffs),'meanRiskMinusExactPp':float(np.mean(ret_diffs)) if ret_diffs else None,
        'MAERiskReturnPp':float(np.mean(np.abs(ret_diffs))) if ret_diffs else None,
        'overlapMonths':recent_months,
    }

    use=[r for r in results if continuous_start and r['month']>=continuous_start]
    output={
        'generatedAt':datetime.now(timezone.utc).isoformat(),
        'method':'SEC-filed shares outstanding + contemporaneous raw close at fact end, rolled to signal date by adjusted-price ratio; filed<=signal date; max 550-day staleness; <=10% median-cap fallback only after >=90% cap coverage.',
        'mapping':{'unionSymbols':len(symbols),'mappedSymbols':len(mapped),'unmappedSymbols':unmapped,'mappingSourceCounts':{s:sum(1 for t in mapped if ciksrc.get(t)==s) for s in sorted(set(ciksrc.values()))}},
        'companyfacts':{'uniqueCIKs':len(unique),'withShareFacts':sum(bool(v) for v in facts_by_cik.values())},
        'priceRecovery':recovery,'wikiMerge':merge,
        'coverageByMonth':[{'month':x['month'],'priceCoverage':x['priceCoverage'],'capCoverage':x['capCoverage'],'universeCount':x['universeCount']} for x in month_rows],
        'continuousStart90Pct':continuous_start,
        'validationAgainstRecentExact':validation,
        'performance':{
            'period': [use[0]['month'],use[-1]['month']] if use else None,
            'months':len(use),
            'QUU_SEC_PIT':metrics([r['QUU'] for r in use]) if use else None,
            'QQQ':metrics([r['QQQ'] for r in use]) if use else None,
            'RiskSleeve':metrics([r['riskReturn'] for r in use]) if use else None,
            'sleeveCounts':{s:sum(1 for r in use if r['sleeve']==s) for s in ('RISK','QQQ','QQQE')},
            'mr90Months':[r['month'] for r in use if r['sleeve']=='QQQE'],
        },
        'monthly':[ {k:v for k,v in r.items() if k not in ('rows','weights')} for r in use],
    }
    OUT.write_text(json.dumps(output,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({k:output[k] for k in ('mapping','companyfacts','continuousStart90Pct','validationAgainstRecentExact','performance')},ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__': main()
