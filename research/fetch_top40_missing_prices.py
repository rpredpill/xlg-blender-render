#!/usr/bin/env python3
from pathlib import Path
import pandas as pd
import yfinance as yf

OUT=Path('research/top40_missing_price_output')
OUT.mkdir(parents=True, exist_ok=True)
TICKERS=['ABNB','ARM','ASML','ATVI','CSX','MELI','PDD']
rows=[]
for t in TICKERS:
    yt=t.replace('.','-')
    try:
        d=yf.download(yt,start='2021-08-01',end='2026-09-28',auto_adjust=False,actions=False,progress=False,threads=False,timeout=60)
        if d.empty:
            continue
        if isinstance(d.columns,pd.MultiIndex):
            c=d['Close'].iloc[:,0]
            a=d['Adj Close'].iloc[:,0] if 'Adj Close' in d.columns.get_level_values(0) else c
        else:
            c=d['Close']; a=d['Adj Close'] if 'Adj Close' in d.columns else c
        z=pd.DataFrame({'date':d.index,'ticker':t,'close':c.values,'adj_close':a.values}).dropna(subset=['close'])
        rows.append(z)
    except Exception as e:
        print(t,repr(e))
allp=pd.concat(rows,ignore_index=True) if rows else pd.DataFrame(columns=['date','ticker','close','adj_close'])
allp.to_csv(OUT/'prices_long.csv',index=False)
print('downloaded',sorted(allp.ticker.unique().tolist()))
print('missing',sorted(set(TICKERS)-set(allp.ticker.unique())))
