#!/usr/bin/env python3
from __future__ import annotations

import importlib.util, json, sys, time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# Reuse the current SEC-PIT v2 identity/share logic and the frozen PIT price/membership engines.
spec=importlib.util.spec_from_file_location('secv2','/tmp/quu_sec_pit_marketcap_validation_v2.py')
if spec is None or spec.loader is None:
    raise RuntimeError('cannot load SEC PIT v2')
v2=importlib.util.module_from_spec(spec); sys.modules['secv2']=v2; spec.loader.exec_module(v2)
v1=v2.v1
base=v1.base
mp=v1.mp
pre=v1.pre

START='2012-05'; END='2018-12'
OUT=Path('quu-pit-gap-diagnostics.json')


def month_add(ym,n): return base.month_add(ym,n)


def main():
    membership=base.load_membership(base.NDX100_URL)
    begin=base.month_end(base.month_add(START,-6)); end_signal=base.month_end(base.month_add(END,-1))
    symbols=set(base.members_at(membership,begin))
    for dt,tks in membership:
        if begin<dt<=end_signal: symbols.update(tks)
    symbols.update(base.members_at(membership,end_signal)); symbols=sorted(symbols)
    print('symbols',len(symbols),flush=True)

    cikmap,ciksrc=v2.load_cik_map_v2()
    mapped={s:cikmap.get(s) for s in symbols if cikmap.get(s)}
    unique=sorted(set(mapped.values()))
    facts_by_cik={}
    for i,c in enumerate(unique,1):
        cf=v1.companyfacts(c)
        facts_by_cik[c]=v2.extract_share_facts_v2(cf)
        if i%25==0 or i==len(unique): print('facts',i,'/',len(unique),flush=True)
        time.sleep(0.11)

    start_day=str((pd.Period(START,freq='M')-7).start_time.date()); end_day='2019-02-05'
    prices=base.download_prices(symbols,start_day,end_day)
    recovery=mp.recover_yahoo_prices(prices,symbols,start_day,end_day)
    wiki=pre.load_wiki_adjusted(symbols,start_day)
    merge=mp.merge_wiki(prices,wiki)

    by_reason=Counter(); by_symbol=Counter(); symbol_reasons=defaultdict(Counter); month_summary=[]; details=[]
    m=START
    while m<=END:
        signal_date=base.month_end(base.month_add(m,-1)); early=base.month_end(base.month_add(m,-6)); universe=base.members_at(membership,signal_date)
        mc=Counter(); cap_ok=0; price_ok=0
        for s in universe:
            recent=base.close_on_or_before(prices,s,signal_date,'Adj Close')
            earlyp=base.close_on_or_before(prices,s,early,'Adj Close')
            mr=v1.adj_month_return(prices,s,m)
            if not(recent and earlyp and mr is not None):
                reason='PRICE_OR_RETURN'
            else:
                price_ok+=1
                c=mapped.get(s)
                if not c:
                    reason='NO_CIK'
                else:
                    fact=v2.latest_fact_v2(facts_by_cik.get(c,[]),signal_date)
                    if not fact:
                        reason='NO_PIT_SHARE_FACT'
                    else:
                        cap=v1.reconstructed_cap(prices,s,fact,signal_date)
                        if not cap:
                            reason='CAP_RECON_PRICE_GAP'
                        else:
                            cap_ok+=1; continue
            by_reason[reason]+=1; by_symbol[s]+=1; symbol_reasons[s][reason]+=1; mc[reason]+=1
            details.append({'month':m,'ticker':s,'reason':reason,'cik':mapped.get(s)})
        month_summary.append({'month':m,'universeCount':len(universe),'priceCoverage':price_ok/max(1,len(universe)),'capCoverage':cap_ok/max(1,len(universe)),'missingByReason':dict(mc)})
        print(m,'price',f'{price_ok/max(1,len(universe)):.1%}','cap',f'{cap_ok/max(1,len(universe)):.1%}',dict(mc),flush=True)
        m=month_add(m,1)

    top=[]
    for s,n in by_symbol.most_common():
        top.append({'ticker':s,'missingMonths':n,'reasons':dict(symbol_reasons[s]),'cik':mapped.get(s),'mappingSource':ciksrc.get(s)})

    out={
        'generatedAt':datetime.now(timezone.utc).isoformat(),
        'period':[START,END],
        'method':'SEC PIT v2 gap diagnostic. Counts one reason per constituent-month in priority: missing price/return, missing CIK, missing PIT share fact, cap reconstruction price gap.',
        'summary':{'symbols':len(symbols),'mappedSymbols':len(mapped),'reasonCounts':dict(by_reason),'totalMissingConstituentMonths':sum(by_reason.values())},
        'topMissingSymbols':top[:80],
        'months':month_summary,
        'details':details,
        'priceRecovery':recovery,'wikiMerge':merge,
    }
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print('TOP20',json.dumps(top[:20],ensure_ascii=False),flush=True)
    print('REASONS',json.dumps(dict(by_reason),ensure_ascii=False),flush=True)

if __name__=='__main__': main()
