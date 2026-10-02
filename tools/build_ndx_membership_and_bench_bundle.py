#!/usr/bin/env python3
import base64, json, zlib
from pathlib import Path
import requests, yfinance as yf, pandas as pd

URL='https://raw.githubusercontent.com/thuningxu/sp500nq100/main/nasdaq100_components_history.csv'

def monthly_returns(symbol):
    df=yf.download(symbol,start='2011-01-01',end='2026-10-03',auto_adjust=True,progress=False,threads=False)
    if isinstance(df.columns,pd.MultiIndex): df.columns=df.columns.get_level_values(0)
    df=df[['Close']].dropna(); df.index=pd.to_datetime(df.index)
    m=df['Close'].resample('ME').last()
    r=m.pct_change()*100
    return {idx.strftime('%Y-%m'):float(v) for idx,v in r.items() if pd.notna(v)}

csv=requests.get(URL,timeout=30).text
obj={'membershipCsv':csv,'QQQ':monthly_returns('QQQ'),'QQQE':monthly_returns('QQQE')}
raw=json.dumps(obj,separators=(',',':')).encode()
enc=base64.b85encode(zlib.compress(raw,9)).decode()
wrapped='\n'.join(enc[i:i+400] for i in range(0,len(enc),400))+'\n'
Path('ndx-bridge.b85').write_text(wrapped,encoding='ascii')
print('raw',len(raw),'compressed-text',len(enc),'lines',len(wrapped.splitlines()))
