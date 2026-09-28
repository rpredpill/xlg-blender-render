#!/usr/bin/env python3
from pathlib import Path
import io, json, time, requests
import pandas as pd
import yfinance as yf

OUT=Path('research/sp500_pre2021_output'); OUT.mkdir(parents=True,exist_ok=True)
SRC='https://raw.githubusercontent.com/chinobing/historical_sp500_constituents/main/sp_500_historical_components.csv'
START='2020-08-01'; END='2021-09-03'

def norm(t): return str(t).strip().strip("'\"").replace('.','-')

r=requests.get(SRC,timeout=60); r.raise_for_status()
df=pd.read_csv(io.BytesIO(r.content))
date_col=[c for c in df.columns if c.lower() in ('date','datetime')][0]
list_col=[c for c in df.columns if c!=date_col][0]
df[date_col]=pd.to_datetime(df[date_col])
# Use union of all constituent lists up through the study period; over-inclusive is fine for price cache.
tickers=set()
for val in df[list_col].dropna():
    s=str(val).strip().strip('[]')
    for x in s.split(','):
        x=norm(x)
        if x: tickers.add(x)
tickers=sorted(tickers)
rows=[]; status={}
for k in range(0,len(tickers),40):
    chunk=tickers[k:k+40]
    try:
        d=yf.download(chunk,start=START,end=END,auto_adjust=False,actions=False,progress=False,threads=True,group_by='ticker',timeout=60)
        for t in chunk:
            try:
                g=d[t] if len(chunk)>1 and t in d.columns.get_level_values(0) else d if len(chunk)==1 else None
                if g is None or len(g)==0: status[t]='empty'; continue
                c=g['Close'] if 'Close' in g.columns else None
                a=g['Adj Close'] if 'Adj Close' in g.columns else c
                if c is None: status[t]='no_close'; continue
                z=pd.DataFrame({'date':g.index,'ticker':t,'close':c.values,'adj_close':a.values}).dropna(subset=['close'])
                if len(z): rows.append(z); status[t]=f'ok:{len(z)}'
                else: status[t]='empty'
            except Exception as e: status[t]='parse_error:'+repr(e)
    except Exception as e:
        for t in chunk: status[t]='chunk_error:'+repr(e)
    print('chunk',k,'/',len(tickers),flush=True); time.sleep(.25)
prices=pd.concat(rows,ignore_index=True) if rows else pd.DataFrame(columns=['date','ticker','close','adj_close'])
prices.to_csv(OUT/'prices_long.csv',index=False)
(OUT/'status.json').write_text(json.dumps({'tickers':len(tickers),'price_tickers':int(prices.ticker.nunique()),'status':status},indent=2),encoding='utf-8')
print('done',len(tickers),prices.ticker.nunique())
