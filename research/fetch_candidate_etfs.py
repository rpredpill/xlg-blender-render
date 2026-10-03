#!/usr/bin/env python3
from pathlib import Path
import json
import pandas as pd
import yfinance as yf

OUT=Path('research/candidate_etfs_output')
OUT.mkdir(parents=True,exist_ok=True)
TICKERS=['XMMO','QMOM','SMH','MTUM','SPMO','QQQ','VOO']
rows=[]; status={}
for t in TICKERS:
    try:
        d=yf.download(t,start='2021-08-01',end='2026-09-29',auto_adjust=False,actions=False,progress=False,threads=False,timeout=60)
        if d.empty:
            status[t]='empty'; continue
        if isinstance(d.columns,pd.MultiIndex):
            c=d['Close'].iloc[:,0]
            a=d['Adj Close'].iloc[:,0] if 'Adj Close' in d.columns.get_level_values(0) else c
        else:
            c=d['Close']; a=d['Adj Close'] if 'Adj Close' in d.columns else c
        z=pd.DataFrame({'date':d.index,'ticker':t,'close':c.values,'adj_close':a.values}).dropna(subset=['close'])
        rows.append(z); status[t]=f'ok:{len(z)}'
    except Exception as e:
        status[t]='error:'+repr(e)
allp=pd.concat(rows,ignore_index=True) if rows else pd.DataFrame(columns=['date','ticker','close','adj_close'])
allp.to_csv(OUT/'prices_long.csv',index=False)
(OUT/'status.json').write_text(json.dumps(status,indent=2),encoding='utf-8')
print(status)
