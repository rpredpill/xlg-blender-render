#!/usr/bin/env python3
from __future__ import annotations

import importlib.util, io, json, math, sys
from pathlib import Path

import pandas as pd

BASE='/tmp/quu_sec_pit_marketcap_validation.py'
spec=importlib.util.spec_from_file_location('secv1',BASE)
if spec is None or spec.loader is None:
    raise RuntimeError('cannot load SEC PIT v1')
v1=importlib.util.module_from_spec(spec); sys.modules['secv1']=v1; spec.loader.exec_module(v1)

OUT=Path('quu-sec-pit-marketcap-validation-v2.json')


def load_cik_map_v2():
    out,src=v1.load_cik_map()
    def put(t,c,source):
        t=v1.norm(t); c=v1.zcik(c)
        if t and c and t not in out:
            out[t]=c; src[t]=source
    # v1 intentionally used conservative column names; this 2017 SEC snapshot
    # calls the ticker column `primarysymbol`.
    try:
        d=pd.read_csv(io.StringIO(v1.get(v1.SNAP2017).text),low_memory=False)
        cols={str(c).lower():c for c in d.columns}
        tc=next((cols[k] for k in ('primarysymbol','ticker','tickersymbol','symbol') if k in cols),None)
        cc=next((cols[k] for k in ('cik','ciknumber','cik number') if k in cols),None)
        if tc is not None and cc is not None:
            for _,r in d.iterrows(): put(r.get(tc),r.get(cc),'2017-sec-snapshot')
    except Exception as e:
        print('v2 2017 snapshot failed',e,flush=True)
    # Re-apply same-company aliases after the extra snapshot mapping is loaded.
    for old,new in v1.ALIASES.items():
        nn=v1.norm(new)
        if nn in out:
            out[v1.norm(old)]=out[nn]; src[v1.norm(old)]='manual-same-company-alias'
    return out,src


def extract_share_facts_v2(cf):
    if not isinstance(cf,dict): return []
    facts=cf.get('facts') or {}
    candidates=[]; seen=set()

    # Priority 0/1: actual shares outstanding (instant facts).
    tags=[
        (0,'dei','EntityCommonStockSharesOutstanding','instant'),
        (0,'us-gaap','CommonStockSharesOutstanding','instant'),
        # Priority 2/3: period-average share counts, used only as a PIT fallback.
        (2,'us-gaap','WeightedAverageNumberOfDilutedSharesOutstanding','weighted-diluted'),
        (3,'us-gaap','WeightedAverageNumberOfSharesOutstandingBasic','weighted-basic'),
        (3,'us-gaap','WeightedAverageNumberOfShareOutstandingBasicAndDiluted','weighted-basic-diluted'),
    ]

    def add_rows(priority,ns,tag,kind):
        node=((facts.get(ns) or {}).get(tag) or {})
        for unit,rows in (node.get('units') or {}).items():
            if str(unit).lower() not in ('shares','share'): continue
            for x in rows or []:
                try:
                    val=float(x.get('val')); end=pd.Timestamp(x.get('end')).normalize(); filed=pd.Timestamp(x.get('filed')).normalize()
                except Exception: continue
                if not(math.isfinite(val) and val>0): continue
                key=(tag,end,filed,val)
                if key in seen: continue
                seen.add(key)
                candidates.append({'end':end,'filed':filed,'shares':val,'tag':f'{ns}:{tag}','form':x.get('form'),'kind':kind,'priority':priority})

    for a,b,c,d in tags: add_rows(a,b,c,d)

    # Foreign issuers and older taxonomies sometimes use a different standardized
    # shares-outstanding tag. Add those conservatively as lower-priority instant facts.
    for ns,nodes in facts.items():
        for tag,node in (nodes or {}).items():
            tl=tag.lower()
            if 'sharesoutstanding' not in tl: continue
            if any(tag==x[2] and ns==x[1] for x in tags): continue
            for unit,rows in (node.get('units') or {}).items():
                if str(unit).lower() not in ('shares','share'): continue
                for x in rows or []:
                    try:
                        val=float(x.get('val')); end=pd.Timestamp(x.get('end')).normalize(); filed=pd.Timestamp(x.get('filed')).normalize()
                    except Exception: continue
                    if not(math.isfinite(val) and val>0): continue
                    key=(tag,end,filed,val)
                    if key in seen: continue
                    seen.add(key)
                    candidates.append({'end':end,'filed':filed,'shares':val,'tag':f'{ns}:{tag}','form':x.get('form'),'kind':'generic-instant','priority':1})

    candidates.sort(key=lambda x:(x['filed'],x['end'],-x['priority']))
    return candidates


def latest_fact_v2(facts,signal_date):
    usable=[x for x in facts if x['filed']<=signal_date and x['end']<=signal_date and (signal_date-x['end']).days<=550]
    if not usable: return None
    # Latest economic period first; for the same period prefer actual instant shares,
    # then diluted/basic weighted averages. `filed` is only a tie-breaker and is
    # always constrained to <= signal_date, so there is no look-ahead.
    return max(usable,key=lambda z:(z['end'], -int(z.get('priority',9)), z['filed']))


def main():
    v1.load_cik_map=load_cik_map_v2
    v1.extract_share_facts=extract_share_facts_v2
    v1.latest_fact=latest_fact_v2
    v1.OUT=OUT
    v1.main()
    j=json.loads(OUT.read_text(encoding='utf-8'))
    j['method']='SEC PIT v2: filed<=signal date. Prefer actual shares outstanding; when unavailable use latest SEC-filed quarterly basic/diluted weighted-average shares. Convert fact-end shares to market cap at fact-end raw close, then roll to signal date with adjusted-price ratio. Max fact staleness 550 days. Require >=90% cap coverage before allowing <=10% cross-sectional median-cap fallback.'
    j['shareFactFallback']='actual shares outstanding > generic shares-outstanding > diluted weighted-average > basic weighted-average; latest fact period wins subject to PIT filing constraint.'
    OUT.write_text(json.dumps(j,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print('V2_FINAL',json.dumps({'continuousStart90Pct':j.get('continuousStart90Pct'),'validation':j.get('validationAgainstRecentExact'),'performance':j.get('performance'),'mapping':j.get('mapping')},ensure_ascii=False),flush=True)

if __name__=='__main__': main()
