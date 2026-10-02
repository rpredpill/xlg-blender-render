#!/usr/bin/env python3
from __future__ import annotations

import importlib.util, json, math, sys, time
from datetime import datetime, timezone
from pathlib import Path
from statistics import pstdev

import numpy as np
import pandas as pd
import requests

# Public FINSABER loader + SEC v2 mapping/facts helpers.
spec=importlib.util.spec_from_file_location('secv3','/tmp/quu_sec_pit_marketcap_validation_v3.py')
if spec is None or spec.loader is None: raise RuntimeError('cannot load v3')
v3=importlib.util.module_from_spec(spec); sys.modules['secv3']=v3; spec.loader.exec_module(v3)
v2=v3.v2; v1=v3.v1; base=v1.base; mp=v1.mp; pre=v1.pre

START='2012-11'; BUILD_START='2012-05'; END='2025-12'; CAP=.20
OUT=Path('quu-sec-pit-marketcap-validation-v4.json')
EXACT='https://raw.githubusercontent.com/rpredpill/xlg-blender-render/somx-pages/quu-history.json'
UA=v1.UA


def zcik(x):
    s=''.join(ch for ch in str(x or '') if ch.isdigit())
    return s.zfill(10) if s else None


def enhanced_cik_map(finsaber):
    m,src=v2.load_cik_map_v2()
    for sym,g in finsaber.groupby('symbol'):
        cs=sorted(c for c in g['cik_norm'].dropna().unique() if c)
        t=str(sym).upper()
        if t not in m and len(cs)==1:
            m[t]=cs[0]; src[t]='finsaber-unique-cik'
    for old,new in v1.ALIASES.items():
        if new in m:
            m[old]=m[new]; src[old]='manual-same-company-alias'
    return m,src


def enhanced_share_facts(cf):
    rows=v2.extract_share_facts_v2(cf)
    if not isinstance(cf,dict): return rows
    facts=cf.get('facts') or {}; seen={(x['tag'],x['end'],x['filed'],x['shares']) for x in rows}
    # Catch IFRS/custom standardized weighted-average share tags not covered by us-gaap.
    for ns,nodes in facts.items():
        for tag,node in (nodes or {}).items():
            tl=tag.lower()
            if not(('weightedaverage' in tl or 'averagenumber' in tl) and 'share' in tl and ('outstanding' in tl or 'shares' in tl)):
                continue
            for unit,vals in (node.get('units') or {}).items():
                if str(unit).lower() not in ('shares','share'): continue
                for x in vals or []:
                    try:
                        val=float(x.get('val')); end=pd.Timestamp(x.get('end')).normalize(); filed=pd.Timestamp(x.get('filed')).normalize()
                    except Exception: continue
                    if not(math.isfinite(val) and val>0): continue
                    key=(f'{ns}:{tag}',end,filed,val)
                    if key in seen: continue
                    seen.add(key)
                    rows.append({'end':end,'filed':filed,'shares':val,'tag':f'{ns}:{tag}','form':x.get('form'),'kind':'generic-weighted-average','priority':4})
    rows.sort(key=lambda x:(x['filed'],x['end'],-int(x.get('priority',9))))
    return rows


def latest_fact(facts,signal):
    usable=[x for x in facts if x['filed']<=signal and x['end']<=signal and (signal-x['end']).days<=550]
    if not usable:return None
    return max(usable,key=lambda z:(z['end'],-int(z.get('priority',9)),z['filed']))


def point(frame,date,col,max_lag=45):
    if frame is None or frame.empty or col not in frame.columns:return None
    z=frame.loc[frame.index<=date,col].dropna()
    if z.empty:return None
    dt=z.index[-1]
    if (date-pd.Timestamp(dt).normalize()).days>max_lag:return None
    v=float(z.iloc[-1])
    return v if math.isfinite(v) and v>0 else None


def month_return(frame,month):
    if frame is None or frame.empty:return None
    try:return v1.adj_month_return({'X':frame},'X',month)
    except Exception:return None


def same_source_signal(frame,month):
    sig=base.month_end(base.month_add(month,-1)); early=base.month_end(base.month_add(month,-6))
    a=point(frame,sig,'Adj Close',45); b=point(frame,early,'Adj Close',45); mr=month_return(frame,month)
    if not(a and b and b>0 and mr is not None):return None
    gross=a/b
    if not(0.10<=gross<=6.0 and -0.95<=mr<=5.0):return None
    return {'gross':float(gross),'momentum':float(gross-1),'monthlyReturn':float(mr)}


def cap_from_source(frame,fact,signal):
    end=fact['end']
    raw=point(frame,end,'Close',45); ae=point(frame,end,'Adj Close',45); a1=point(frame,signal,'Adj Close',45)
    if not(raw and ae and a1):return None
    cap=fact['shares']*raw*(a1/ae)
    return float(cap) if math.isfinite(cap) and cap>0 else None


def cap_weights(raw,cap=CAP):
    free=set(raw); out={}; rem=1.0
    while free:
        total=sum(raw[x] for x in free)
        if total<=0:raise RuntimeError('raw<=0')
        over=[x for x in free if rem*raw[x]/total>cap+1e-12]
        if not over:
            for x in free:out[x]=rem*raw[x]/total
            break
        for x in over:
            out[x]=cap; rem-=cap; free.remove(x)
    return out


def pct(prior,x): return None if not prior else 100*sum(1 for y in prior if y<=x)/len(prior)

def metrics(rs):
    a=np.asarray(rs,float)/100; eq=np.concatenate([[1.],np.cumprod(1+a)]); peak=np.maximum.accumulate(eq); dd=eq/peak-1
    cagr=eq[-1]**(12/len(a))-1
    return {'months':len(a),'totalReturnPct':float((eq[-1]-1)*100),'CAGRpct':float(cagr*100),'MDDpct':float(dd.min()*100),'Calmar':float(cagr/abs(dd.min())) if dd.min()<0 else None,'endingIndex':float(eq[-1]*100)}


def prepare_sources(symbols,cikmap):
    allf=v3.load_finsaber_all()
    fv={}; audit={}
    for s in symbols:
        g=allf[allf['symbol']==s]
        if g.empty:continue
        expected=cikmap.get(s); cs=sorted(c for c in g['cik_norm'].dropna().unique() if c)
        if expected and not g[g['cik_norm']==expected].empty:
            g=g[g['cik_norm']==expected]; audit[s]='expected-cik'
        elif len(cs)==1:
            audit[s]='unique-cik'
        else:
            audit[s]='rejected-multi-cik'; continue
        f=v3.to_price_frame(g)
        if f is not None:fv[s]=f
    # Yahoo is a separate complete source candidate, not merged into FINSABER.
    yy=v3.ORIG_DOWNLOAD(symbols,'2011-10-01','2026-01-05')
    for old,new in v1.ALIASES.items():
        if old in symbols and (old not in yy or yy[old] is None or yy[old].empty):
            got=v3.ORIG_DOWNLOAD([new],'2011-10-01','2026-01-05')
            if new in got and got[new] is not None and not got[new].empty: yy[old]=got[new].copy()
    wiki=pre.load_wiki_adjusted(symbols,'2011-10-01')
    return {'FINSABER':fv,'YAHOO':yy,'WIKI':wiki},audit


def main():
    membership=base.load_membership(base.NDX100_URL)
    begin=base.month_end(base.month_add(BUILD_START,-6)); end_sig=base.month_end(base.month_add(END,-1))
    symbols=set(base.members_at(membership,begin))
    for dt,tks in membership:
        if begin<dt<=end_sig:symbols.update(tks)
    symbols.update(base.members_at(membership,end_sig)); symbols=sorted(symbols)

    allf=v3.load_finsaber_all(); cikmap,ciksrc=enhanced_cik_map(allf)
    mapped={s:cikmap.get(s) for s in symbols if cikmap.get(s)}; unmapped=[s for s in symbols if s not in mapped]
    print('CIK',len(mapped),'/',len(symbols),'unmapped',unmapped,flush=True)

    facts={}; unique=sorted(set(mapped.values()))
    for i,c in enumerate(unique,1):
        cf=v1.companyfacts(c); facts[c]=enhanced_share_facts(cf)
        if i%25==0 or i==len(unique):print('facts',i,'/',len(unique),'with',sum(bool(x) for x in facts.values()),flush=True)
        time.sleep(.11)

    sources,identity=prepare_sources(symbols,cikmap)
    print('sources',{k:len(v) for k,v in sources.items()},flush=True)
    bench=v3.ORIG_DOWNLOAD(['QQQ','QQQE'],'2012-01-01','2026-01-05')
    exact=requests.get(EXACT,headers=UA,timeout=60).json().get('months') or {}

    months=[]; m=BUILD_START
    source_counts={'signal':{},'cap':{}}
    while m<=END:
        signal=base.month_end(base.month_add(m,-1)); universe=base.members_at(membership,signal); rows=[]
        for s in universe:
            sig=None; sigsrc=None
            for name in ('FINSABER','YAHOO','WIKI'):
                q=same_source_signal(sources[name].get(s),m)
                if q is not None: sig=q; sigsrc=name; break
            if sig is None:continue
            fact=latest_fact(facts.get(mapped.get(s),[]),signal) if mapped.get(s) else None
            cap=None; capsrc=None
            if fact:
                # Prefer same source as signal, then remaining sources.
                order=[sigsrc]+[x for x in ('FINSABER','YAHOO','WIKI') if x!=sigsrc]
                for name in order:
                    cap=cap_from_source(sources[name].get(s),fact,signal)
                    if cap is not None:capsrc=name;break
            rows.append({'ticker':s,**sig,'signalSource':sigsrc,'cap':cap,'capSource':capsrc})
            source_counts['signal'][sigsrc]=source_counts['signal'].get(sigsrc,0)+1
            if capsrc:source_counts['cap'][capsrc]=source_counts['cap'].get(capsrc,0)+1
        pc=len(rows)/max(1,len(universe)); cc=sum(1 for r in rows if r['cap'])/max(1,len(universe))
        months.append({'month':m,'signalDate':str(signal.date()),'universeCount':len(universe),'priceCoverage':pc,'capCoverage':cc,'rows':rows})
        print(m,f'price={pc:.1%}',f'cap={cc:.1%}',flush=True)
        m=base.month_add(m,1)

    eligible={x['month'] for x in months if x['priceCoverage']>=.90 and x['capCoverage']>=.90}
    continuous=None
    for x in months:
        if x['month']<START or x['month'] not in eligible:continue
        cur=x['month'];ok=True
        while cur<=END:
            if cur not in eligible:ok=False;break
            cur=base.month_add(cur,1)
        if ok:continuous=x['month'];break

    # Preserve percentile history from all valid >=90% months before continuous start.
    prior=[]; results=[]
    for x in months:
        if x['priceCoverage']<.90 or x['capCoverage']<.90:continue
        rows=x['rows']; caps=[r['cap'] for r in rows if r['cap']]
        medcap=float(np.median(caps)); medgross=float(np.median([r['gross'] for r in rows])); fallback=0; raw={}
        for r in rows:
            c=r['cap']
            if not c:c=medcap; fallback+=1
            raw[r['ticker']]=math.sqrt(c)*(r['gross']/medgross)**3
        w=cap_weights(raw); risk=100*sum(w[r['ticker']]*r['monthlyReturn'] for r in rows)
        d=float(pstdev([r['momentum'] for r in rows])); p=pct(prior,d); falling=bool(prior and d<prior[-1])
        if len(prior)<8:
            med=float(np.median(prior)) if prior else None; strong=True if med is None else d>=med; overlay=False
        else:
            strong=bool(p is not None and p>=50); overlay=bool(p is not None and p>=90 and falling)
        qr=month_return(bench.get('QQQ'),x['month']); er=month_return(bench.get('QQQE'),x['month'])
        if qr is None or er is None:prior.append(d);continue
        qqq=qr*100;qqqe=er*100;sleeve='QQQE' if overlay else ('RISK' if strong else 'QQQ');quu=qqqe if overlay else (risk if strong else qqq)
        results.append({'month':x['month'],'riskReturn':risk,'QQQ':qqq,'QQQE':qqqe,'QUU':quu,'sleeve':sleeve,'dispersion':d,'percentile':p,'falling':falling,'capCoverage':x['capCoverage'],'priceCoverage':x['priceCoverage'],'medianCapFallbackCount':fallback,'weights':w,'rows':rows})
        prior.append(d)

    cap_pairs=[]; maes=[]; rd=[]; overlap=[]
    for r in results:
        e=exact.get(r['month'])
        if not isinstance(e,dict):continue
        em={z.get('ticker'):z for z in e.get('rows',[]) if z.get('ticker')}
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
    out={'generatedAt':datetime.now(timezone.utc).isoformat(),'method':'Endpoint-consistent v4: each ticker-month 6-1 signal and holding-month return must come entirely from one source, priority FINSABER V2 > Yahoo > WIKI. Market cap independently uses one source for fact-end raw/adjusted and signal adjusted price. SEC share facts are PIT filed<=signal, with actual shares preferred and generic US-GAAP/IFRS weighted-average shares as fallback. >=90% price and cap coverage required.','mapping':{'unionSymbols':len(symbols),'mappedSymbols':len(mapped),'unmappedSymbols':unmapped,'mappingSourceCounts':{s:sum(1 for t in mapped if ciksrc.get(t)==s) for s in sorted(set(ciksrc.values()))}},'companyfacts':{'uniqueCIKs':len(unique),'withShareFacts':sum(bool(x) for x in facts.values())},'identityAuditCounts':{q:sum(1 for x in identity.values() if x==q) for q in sorted(set(identity.values()))},'sourceCounts':source_counts,'coverageByMonth':[{'month':x['month'],'priceCoverage':x['priceCoverage'],'capCoverage':x['capCoverage'],'universeCount':x['universeCount']} for x in months],'continuousStart90Pct':continuous,'validationAgainstRecentExact':validation,'performance':{'period':[use[0]['month'],use[-1]['month']] if use else None,'months':len(use),'QUU_SEC_PIT':metrics([r['QUU'] for r in use]) if use else None,'QQQ':metrics([r['QQQ'] for r in use]) if use else None,'RiskSleeve':metrics([r['riskReturn'] for r in use]) if use else None,'sleeveCounts':{s:sum(1 for r in use if r['sleeve']==s) for s in ('RISK','QQQ','QQQE')},'mr90Months':[r['month'] for r in use if r['sleeve']=='QQQE']},'monthly':[{k:v for k,v in r.items() if k not in ('rows','weights')} for r in use]}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print('V4_FINAL',json.dumps({k:out[k] for k in ('mapping','companyfacts','sourceCounts','continuousStart90Pct','validationAgainstRecentExact','performance')},ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
