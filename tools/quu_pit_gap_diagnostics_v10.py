#!/usr/bin/env python3
from __future__ import annotations

import importlib.util, json, sys, time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# Reuse v9/v8 engines. This script is diagnostic only: no strategy parameters change.
spec9=importlib.util.spec_from_file_location('secv9','/tmp/quu_sec_pit_marketcap_validation_v9.py')
if spec9 is None or spec9.loader is None: raise RuntimeError('cannot load v9')
v9=importlib.util.module_from_spec(spec9); sys.modules['secv9']=v9; spec9.loader.exec_module(v9)
v8=v9.v8; v7=v8.v7; v4=v8.v4; v3=v8.v3; v2=v8.v2; v1=v8.v1; v6=v8.v6; base=v8.base; mp=v8.mp; pre=v8.pre

START='2012-01'; END='2016-12'
OUT=Path('quu-pit-gap-diagnostics-v10.json')


def main():
    membership=base.load_membership(base.NDX100_URL)
    begin=base.month_end(base.month_add(START,-6)); end_signal=base.month_end(base.month_add(END,-1))
    symbols=set(base.members_at(membership,begin))
    for dt,tks in membership:
        if begin<dt<=end_signal: symbols.update(tks)
    symbols.update(base.members_at(membership,end_signal)); symbols=sorted(symbols)

    # Date-corrected FINSABER identity from v9.
    allf=v9.corrected_finsaber(); wanted=set(symbols)
    ident_rows=allf[allf['symbol'].isin(wanted)][['date','symbol','cik_norm']].dropna().copy()
    identity={}
    for s,g in ident_rows.groupby('symbol'):
        g=g.sort_values('date').drop_duplicates('date',keep='last')
        identity[s]=(g['date'].to_numpy(),g['cik_norm'].to_numpy())
    static_map,_=v2.load_cik_map_v2()
    all_ciks=sorted(set(ident_rows['cik_norm'].astype(str))|{static_map[s] for s in symbols if static_map.get(s)})

    facts={}
    for i,c in enumerate(all_ciks,1):
        facts[c]=v4.enhanced_share_facts(v1.companyfacts(c))
        if i%30==0 or i==len(all_ciks): print('SEC facts',i,'/',len(all_ciks),flush=True)
        time.sleep(.11)

    comp,names,ccm,plink=v8.prep_compustat()

    start_day=str((pd.Period(START,freq='M')-7).start_time.date()); end_day='2017-01-05'
    prices=base.download_prices(symbols,start_day,end_day); mp.recover_yahoo_prices(prices,symbols,start_day,end_day)
    wiki=pre.load_wiki_adjusted(symbols,start_day); mp.merge_wiki(prices,wiki)
    fb={}
    for (s,c),g in allf[allf['symbol'].isin(wanted)].groupby(['symbol','cik_norm']):
        f=v3.to_price_frame(g)
        if f is not None: fb[(str(s),str(c))]=f

    gaps=[]; month_summary=[]; ticker_counts=Counter(); reason_counts=Counter(); reason_by_ticker=defaultdict(Counter)
    m=START
    while m<=END:
        signal=base.month_end(base.month_add(m,-1)); universe=base.members_at(membership,signal)
        valid_signal=0; valid_cap=0
        for s in universe:
            c=v6.cik_at(identity,s,signal) or static_map.get(s)
            sig=v7.baseline_signal(prices,s,m)
            sigsrc='V2_PRIMARY' if sig else None
            if sig is None and c:
                sig=v4.same_source_signal(fb.get((s,c)),m); sigsrc='FINSABER_FALLBACK' if sig else None
            if sig is None:
                reason='PRICE_OR_RETURN'
                gaps.append({'month':m,'ticker':s,'reason':reason,'cik':c,'gvkey':None,'gvkeySource':None})
                ticker_counts[s]+=1; reason_counts[reason]+=1; reason_by_ticker[s][reason]+=1
                continue
            valid_signal+=1

            cap=None
            fact=v4.latest_fact(facts.get(c,[]),signal) if c else None
            if fact:
                cap=v1.reconstructed_cap(prices,s,fact,signal)
                if cap is None and c: cap=v4.cap_from_source(fb.get((s,c)),fact,signal)
            gv,gsrc=v8.gvkey_at(s,signal,c,names,ccm,plink)
            crec=None
            if cap is None and gv:
                crec=v8.comp_cap_record(comp,gv,signal)
                if crec:
                    cap,_=v8.comp_cap_to_signal(crec,signal,prices.get(s),fb.get((s,c)) if c else None)
            if cap is not None:
                valid_cap+=1
                continue

            if not c: reason='NO_CIK'
            elif fact is None and not gv: reason='NO_SEC_SHARE_AND_NO_GVKEY'
            elif fact is None and gv and crec is None: reason='NO_SEC_SHARE_AND_NO_PIT_COMPUSTAT'
            elif fact is not None and gv is None: reason='SEC_CAP_PRICE_GAP_AND_NO_GVKEY'
            elif fact is not None and gv and crec is None: reason='SEC_CAP_PRICE_GAP_AND_NO_PIT_COMPUSTAT'
            else: reason='CAP_PRICE_ROLL_GAP'
            gaps.append({'month':m,'ticker':s,'reason':reason,'cik':c,'gvkey':gv,'gvkeySource':gsrc,'hasSECFact':fact is not None,'hasCompRecord':crec is not None,'signalSource':sigsrc})
            ticker_counts[s]+=1; reason_counts[reason]+=1; reason_by_ticker[s][reason]+=1

        month_summary.append({'month':m,'universeCount':len(universe),'priceCoverage':valid_signal/max(1,len(universe)),'capCoverage':valid_cap/max(1,len(universe)),'missingCapCount':len(universe)-valid_cap})
        print(m,'price',valid_signal,'/',len(universe),'cap',valid_cap,'/',len(universe),flush=True)
        m=base.month_add(m,1)

    top=[]
    for t,n in ticker_counts.most_common():
        ms=[g['month'] for g in gaps if g['ticker']==t]
        cs=sorted({g.get('cik') for g in gaps if g['ticker']==t and g.get('cik')})
        gs=sorted({g.get('gvkey') for g in gaps if g['ticker']==t and g.get('gvkey')})
        top.append({'ticker':t,'missingMonths':n,'firstMonth':min(ms),'lastMonth':max(ms),'reasons':dict(reason_by_ticker[t]),'ciks':cs,'gvkeys':gs})

    # Greedy count: how many top missing tickers would need perfect repair to cross 90% in every month.
    needed=[]
    for row in month_summary:
        target=int(-(-0.90*row['universeCount']//1))
        needed.append({'month':row['month'],'missingTo90':max(0,target-(row['universeCount']-row['missingCapCount']))})

    out={
        'generatedAt':datetime.now(timezone.utc).isoformat(),
        'scope':[START,END],
        'method':'Diagnostic only. Uses v9 dated CIK corrections + v8 SEC/Compustat PIT cap logic. No strategy parameters changed.',
        'reasonCounts':dict(reason_counts),
        'topMissingTickers':top[:60],
        'monthSummary':month_summary,
        'monthsNeedToReach90':needed,
        'gaps':gaps,
    }
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print('TOP20',json.dumps(top[:20],ensure_ascii=False,indent=2),flush=True)
    print('REASONS',json.dumps(dict(reason_counts),ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__': main()
