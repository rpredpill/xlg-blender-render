#!/usr/bin/env python3
from pathlib import Path
import io, json, time, requests
import pandas as pd
import yfinance as yf

OUT=Path('research/sp500_data_output'); OUT.mkdir(parents=True,exist_ok=True)
SRC='https://raw.githubusercontent.com/chinobing/historical_sp500_constituents/main/sp_500_historical_components.csv'
START='2021-08-01'; END='2026-09-29'

def norm_ticker(t):
    t=str(t).strip()
    return t.replace('.','-')

r=requests.get(SRC,timeout=60); r.raise_for_status()
(OUT/'source_sp500_history.csv').write_bytes(r.content)
df=pd.read_csv(io.BytesIO(r.content))
print('columns',df.columns.tolist())
# Expected columns: date, tickers (comma-separated list). Be permissive.
date_col=[c for c in df.columns if c.lower() in ('date','datetime')][0]
list_col=[c for c in df.columns if c!=date_col][0]
df[date_col]=pd.to_datetime(df[date_col])
df=df.sort_values(date_col)

# Monthly first-business-day reference dates matching the QQMO study months.
months=pd.date_range('2021-09-01','2026-09-01',freq='MS')
rows=[]
for d in months:
    hist=df[df[date_col]<=d]
    if hist.empty: continue
    val=hist.iloc[-1][list_col]
    # Dataset stores comma-separated symbols; tolerate brackets/quotes.
    s=str(val).strip().strip('[]')
    toks=[]
    for x in s.split(','):
        x=x.strip().strip("'\"")
        if x: toks.append(norm_ticker(x))
    for t in sorted(set(toks)):
        rows.append({'month':d.date().isoformat(),'ticker':t})
mem=pd.DataFrame(rows)
mem.to_csv(OUT/'monthly_membership.csv',index=False)
print('months',mem.month.nunique(),'unique tickers',mem.ticker.nunique(),'median count',mem.groupby('month').ticker.nunique().median())

# Fetch adjusted prices in chunks. Include ETF baselines for comparison.
tickers=sorted(set(mem.ticker)|{'VOO','RSP','SPY','QQQ','SPMO','XLG','OEF','VUG','SCHG'})
price_rows=[]; status={}
for k in range(0,len(tickers),40):
    chunk=tickers[k:k+40]
    try:
        d=yf.download(chunk,start=START,end=END,auto_adjust=False,actions=False,progress=False,threads=True,group_by='ticker',timeout=60)
        for t in chunk:
            try:
                if len(chunk)==1:
                    g=d
                else:
                    g=d[t] if t in d.columns.get_level_values(0) else None
                if g is None or len(g)==0:
                    status[t]='empty'; continue
                c=g['Close'] if 'Close' in g.columns else None
                a=g['Adj Close'] if 'Adj Close' in g.columns else c
                if c is None: status[t]='no_close'; continue
                z=pd.DataFrame({'date':g.index,'ticker':t,'close':c.values,'adj_close':a.values}).dropna(subset=['close'])
                if len(z):
                    price_rows.append(z); status[t]=f'ok:{len(z)}'
                else: status[t]='empty'
            except Exception as e: status[t]='parse_error:'+repr(e)
    except Exception as e:
        for t in chunk: status[t]='chunk_error:'+repr(e)
    print('chunk',k,'/',len(tickers),flush=True)
    time.sleep(.3)
prices=pd.concat(price_rows,ignore_index=True) if price_rows else pd.DataFrame(columns=['date','ticker','close','adj_close'])
prices.to_csv(OUT/'prices_long.csv',index=False)
(OUT/'status.json').write_text(json.dumps({'source':SRC,'unique_members':int(mem.ticker.nunique()),'price_tickers':int(prices.ticker.nunique()),'status':status},indent=2),encoding='utf-8')
print('price tickers',prices.ticker.nunique(),'missing',sum(not str(v).startswith('ok:') for v in status.values()))
