#!/usr/bin/env python3
from __future__ import annotations

import importlib.util, json, math, sys
from pathlib import Path

import pandas as pd
import requests

# Load SEC PIT v2, which itself loads v1 and the frozen QUU engines.
spec=importlib.util.spec_from_file_location('secv2','/tmp/quu_sec_pit_marketcap_validation_v2.py')
if spec is None or spec.loader is None:
    raise RuntimeError('cannot load SEC PIT v2')
v2=importlib.util.module_from_spec(spec); sys.modules['secv2']=v2; spec.loader.exec_module(v2)
v1=v2.v1
base=v1.base
mp=v1.mp

OUT=Path('quu-sec-pit-marketcap-validation-v3.json')
HF='https://huggingface.co/datasets/finsaber-team/FINSABER-V2-Data/resolve/main/price_daily/year={year}/part-000.parquet?download=true'
CACHE=Path('/tmp/finsaber-v2-price'); CACHE.mkdir(parents=True,exist_ok=True)
ORIG_DOWNLOAD=base.download_prices
ORIG_RECOVER=mp.recover_yahoo_prices
ORIG_MERGE_WIKI=mp.merge_wiki

# Build the same public ticker->CIK map that v2 will use, so the FINSABER
# price rows can be identity-filtered before entering the backtest.
EXPECTED_CIK,_=v2.load_cik_map_v2()
FINSABER_CACHE=None
SOURCE_STATS={}


def zcik(x):
    s=''.join(ch for ch in str(x or '') if ch.isdigit())
    return s.zfill(10) if s else None


def download_year(year:int)->Path:
    p=CACHE/f'{year}.parquet'
    if p.exists() and p.stat().st_size>1000: return p
    u=HF.format(year=year)
    r=requests.get(u,timeout=180)
    r.raise_for_status()
    p.write_bytes(r.content)
    print('FINSABER',year,'MB',round(len(r.content)/1e6,2),flush=True)
    return p


def load_finsaber_all():
    global FINSABER_CACHE
    if FINSABER_CACHE is not None: return FINSABER_CACHE
    frames=[]
    for y in range(2011,2026):
        p=download_year(y)
        d=pd.read_parquet(p,columns=['date','symbol','cik','open','high','low','close','adjusted_close','volume'])
        d['date']=pd.to_datetime(d['date']).dt.tz_localize(None).dt.normalize()
        d['symbol']=d['symbol'].astype(str).str.upper().str.strip()
        d['cik_norm']=d['cik'].map(zcik)
        frames.append(d)
    x=pd.concat(frames,ignore_index=True)
    FINSABER_CACHE=x
    print('FINSABER rows',len(x),'symbols',x['symbol'].nunique(),flush=True)
    return x


def to_price_frame(g):
    if g.empty:return None
    z=g.sort_values('date').drop_duplicates('date',keep='last').set_index('date')
    out=pd.DataFrame(index=z.index)
    out['Open']=pd.to_numeric(z['open'],errors='coerce')
    out['High']=pd.to_numeric(z['high'],errors='coerce')
    out['Low']=pd.to_numeric(z['low'],errors='coerce')
    out['Close']=pd.to_numeric(z['close'],errors='coerce')
    out['Adj Close']=pd.to_numeric(z['adjusted_close'],errors='coerce')
    out['Volume']=pd.to_numeric(z['volume'],errors='coerce')
    out=out.replace([float('inf'),-float('inf')],pd.NA).dropna(subset=['Close','Adj Close'])
    return out if not out.empty else None


def finsaber_prices(symbols,start,end):
    allp=load_finsaber_all()
    s0=pd.Timestamp(start).normalize(); s1=pd.Timestamp(end).normalize()
    out={}; identities={}
    for sym in symbols:
        t=str(sym).upper()
        g=allp[(allp['symbol']==t)&(allp['date']>=s0)&(allp['date']<s1)]
        if g.empty: continue
        expected=EXPECTED_CIK.get(t)
        ciks=sorted(c for c in g['cik_norm'].dropna().unique())
        if expected:
            match=g[g['cik_norm']==expected]
            if not match.empty:
                g=match; identities[t]={'mode':'expected-cik','cik':expected}
            elif len(ciks)==1:
                # Do not silently discard an otherwise unique historical identity,
                # but record the mismatch for audit.
                identities[t]={'mode':'unique-cik-mismatch','expected':expected,'observed':ciks[0]}
            else:
                identities[t]={'mode':'rejected-multi-cik','expected':expected,'observed':ciks}; continue
        elif len(ciks)>1:
            identities[t]={'mode':'rejected-unmapped-multi-cik','observed':ciks}; continue
        else:
            identities[t]={'mode':'unique-unmapped','cik':ciks[0] if ciks else None}
        f=to_price_frame(g)
        if f is not None and len(f)>=2: out[t]=f
    SOURCE_STATS['identityAudit']=identities
    return out


def hybrid_download(symbols,start,end):
    syms=[str(s).upper() for s in symbols]
    # Benchmarks are not constituents in FINSABER; always use the frozen Yahoo loader.
    if set(syms).issubset({'QQQ','QQQE'}):
        return ORIG_DOWNLOAD(syms,start,end)
    fv=finsaber_prices(syms,start,end)
    missing=[s for s in syms if s not in fv]
    yy=ORIG_DOWNLOAD(missing,start,end) if missing else {}
    out=dict(fv)
    for s,d in yy.items():
        if s not in out and d is not None and not d.empty: out[s]=d
    SOURCE_STATS['finsaberSymbols']=sorted(fv)
    SOURCE_STATS['yahooPrimarySymbols']=sorted(set(out)-set(fv))
    SOURCE_STATS['missingAfterPrimary']=sorted(set(syms)-set(out))
    print('PRIMARY prices: FINSABER',len(fv),'Yahoo',len(set(out)-set(fv)),'missing',len(set(syms)-set(out)),flush=True)
    return out


def recover_missing_only(prices,symbols,start,end):
    # v1 normally retries Yahoo and aliases. Here primary Yahoo was already attempted;
    # keep only same-company aliases for fully missing symbols and never splice dates
    # into an existing FINSABER symbol.
    aliases=[]; direct=[]
    missing=[s for s in symbols if s not in prices or prices[s] is None or prices[s].empty]
    for s in missing:
        alias=mp.SAME_COMPANY_ALIASES.get(s) if hasattr(mp,'SAME_COMPANY_ALIASES') else None
        if not alias: continue
        got=ORIG_DOWNLOAD([alias],start,end)
        d=got.get(alias)
        if d is not None and not d.empty:
            prices[s]=d.copy(); aliases.append(f'{s}->{alias}')
    return {'direct':direct,'aliases':aliases,'stillMissing':[s for s in symbols if s not in prices or prices[s] is None or prices[s].empty]}


def add_only_missing_wiki(prices,wiki):
    new=[]
    for s,d in wiki.items():
        if s not in prices or prices[s] is None or prices[s].empty:
            prices[s]=d.copy(); new.append(s)
    return {'newSymbols':sorted(new),'extendedSymbols':[]}


def main():
    base.download_prices=hybrid_download
    mp.recover_yahoo_prices=recover_missing_only
    mp.merge_wiki=add_only_missing_wiki
    v2.OUT=OUT
    v2.main()
    j=json.loads(OUT.read_text(encoding='utf-8'))
    j['method']='SEC PIT v3 + FINSABER V2 price primary: PIT SEC shares logic from v2; per-symbol price source priority FINSABER V2 > Yahoo > WIKI, with no endpoint splicing into an existing higher-priority symbol. FINSABER rows are CIK-filtered where possible. filed<=signal date; no look-ahead.'
    j['priceSourceAudit']={
        'finsaberSymbolCount':len(SOURCE_STATS.get('finsaberSymbols',[])),
        'yahooPrimarySymbolCount':len(SOURCE_STATS.get('yahooPrimarySymbols',[])),
        'missingAfterPrimaryCount':len(SOURCE_STATS.get('missingAfterPrimary',[])),
        'identityAuditCounts':{},
    }
    for a in SOURCE_STATS.get('identityAudit',{}).values():
        m=a.get('mode','unknown'); j['priceSourceAudit']['identityAuditCounts'][m]=j['priceSourceAudit']['identityAuditCounts'].get(m,0)+1
    OUT.write_text(json.dumps(j,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print('V3_FINAL',json.dumps({'continuousStart90Pct':j.get('continuousStart90Pct'),'validation':j.get('validationAgainstRecentExact'),'performance':j.get('performance'),'mapping':j.get('mapping'),'priceSourceAudit':j.get('priceSourceAudit')},ensure_ascii=False),flush=True)

if __name__=='__main__': main()
