#!/usr/bin/env python3
from pathlib import Path
import json, time, io, requests
import pandas as pd
import yfinance as yf

OUT=Path('research/top20_price_output')
OUT.mkdir(parents=True, exist_ok=True)
TICKERS=['ALAB','ATVI','DDOG','MSTR','NXPI','WBD','WDAY']
START='2021-08-01'
END='2026-09-28'

def ys(t): return t.replace('.', '-')
rows=[]; status=[]
for orig in TICKERS:
    yt=ys(orig)
    try:
        d=yf.download(yt,start=START,end=END,auto_adjust=False,actions=False,progress=False,threads=False,timeout=60)
        if not d.empty:
            if isinstance(d.columns,pd.MultiIndex):
                c=d['Close'].iloc[:,0]
                a=d['Adj Close'].iloc[:,0] if 'Adj Close' in d.columns.get_level_values(0) else c
            else:
                c=d['Close']; a=d['Adj Close'] if 'Adj Close' in d.columns else c
            z=pd.DataFrame({'date':d.index,'ticker':orig,'close':c.values,'adj_close':a.values}).dropna(subset=['close'])
            if len(z):
                rows.append(z); status.append({'ticker':orig,'source':'yfinance','rows':len(z)}); continue
    except Exception as e:
        status.append({'ticker':orig,'yfinance_error':repr(e)})
    try:
        p1=int(pd.Timestamp(START,tz='UTC').timestamp()); p2=int(pd.Timestamp(END,tz='UTC').timestamp())
        u=f'https://query1.finance.yahoo.com/v8/finance/chart/{yt}?period1={p1}&period2={p2}&interval=1d&events=div%2Csplits'
        r=requests.get(u,headers={'User-Agent':'Mozilla/5.0'},timeout=30)
        if r.ok:
            js=r.json(); rr=(js.get('chart',{}).get('result') or [None])[0]
            if rr and rr.get('timestamp'):
                q=rr['indicators']['quote'][0]; aa=(rr['indicators'].get('adjclose') or [{}])[0].get('adjclose')
                c=q.get('close'); aa=aa if aa is not None else c
                z=pd.DataFrame({'date':pd.to_datetime(rr['timestamp'],unit='s',utc=True).tz_convert(None).normalize(),'ticker':orig,'close':c,'adj_close':aa}).dropna(subset=['close'])
                if len(z):
                    rows.append(z); status.append({'ticker':orig,'source':'direct_yahoo','rows':len(z)}); continue
    except Exception as e:
        status.append({'ticker':orig,'direct_yahoo_error':repr(e)})
    try:
        u=f"https://stooq.com/q/d/l/?s={orig.lower().replace('.', '-')}.us&d1=20210801&d2=20260928&i=d"
        r=requests.get(u,headers={'User-Agent':'Mozilla/5.0'},timeout=30)
        if r.ok and r.text.startswith('Date,'):
            z0=pd.read_csv(io.StringIO(r.text))
            if len(z0):
                z=pd.DataFrame({'date':pd.to_datetime(z0['Date']),'ticker':orig,'close':z0['Close'],'adj_close':z0['Close']}).dropna(subset=['close'])
                rows.append(z); status.append({'ticker':orig,'source':'stooq','rows':len(z)})
    except Exception as e:
        status.append({'ticker':orig,'stooq_error':repr(e)})
    time.sleep(0.5)

allp=pd.concat(rows,ignore_index=True) if rows else pd.DataFrame(columns=['date','ticker','close','adj_close'])
allp=allp.sort_values(['ticker','date']).drop_duplicates(['ticker','date'],keep='first')
allp.to_csv(OUT/'prices_long.csv',index=False)
(OUT/'status.json').write_text(json.dumps(status,indent=2),encoding='utf-8')
print('downloaded',sorted(allp.ticker.unique()))
print('missing',sorted(set(TICKERS)-set(allp.ticker.unique())))
