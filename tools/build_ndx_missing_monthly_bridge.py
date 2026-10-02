#!/usr/bin/env python3
import base64,csv,io,json,zlib
from pathlib import Path
import pandas as pd, yfinance as yf, requests

SYMBOLS='ALNY ARM ASML AZN BIDU BMRN CHKP CTRP DOCU FER FLEX GFS HANS INFY INSM JD LBTYA LBTYK LCID LITE MDB MELI MRVL MSTR NTES OKTA PDD PTON QRTEA RIMM RIVN SGEN SHOP SHPG SIRI SPLK TEAM TEVA TRI VOD VSNT ZM ZS'.split()
# Same-company ticker/name continuations only. No acquisition substitution.
ALIASES={'CTRP':'TCOM','RIMM':'BB','HANS':'MNST'}
URL='https://raw.githubusercontent.com/thuningxu/sp500nq100/main/nasdaq100_components_history.csv'

rawcsv=requests.get(URL,timeout=30).text
hist=[]
for r in csv.DictReader(io.StringIO(rawcsv)):
    hist.append((pd.Timestamp(r['date']),set(r['tickers'].split(','))))
hist.sort()

def universe_at(d):
    best=None
    for dt,u in hist:
        if dt<=d: best=u
        else: break
    return best or set()

# Include the eight-month pre-investment leadership warm-up as well as invest months.
needed={s:set() for s in SYMBOLS}
for p in pd.period_range('2011-08','2025-12',freq='M'):
    u=universe_at((p-1).end_time)
    for s in SYMBOLS:
        if s in u:
            for q in [p,p-1,p-6]: needed[s].add(str(q))

def get(sym):
    q=ALIASES.get(sym,sym)
    try:
        df=yf.download(q,start='2011-01-01',end='2026-01-05',auto_adjust=True,progress=False,threads=False)
        if df is None or df.empty:return {}
        if isinstance(df.columns,pd.MultiIndex):df.columns=df.columns.get_level_values(0)
        s=df['Close'].dropna(); s.index=pd.to_datetime(s.index)
        m=s.resample('ME').last(); want=needed[sym]
        return {d.strftime('%Y-%m'):round(float(v),6) for d,v in m.items() if d.strftime('%Y-%m') in want}
    except Exception:
        return {}

obj={s:get(s) for s in SYMBOLS}
raw=json.dumps(obj,separators=(',',':')).encode()
enc=base64.b85encode(zlib.compress(raw,9)).decode()
Path('ndx-missing-monthly.b85').write_text('\n'.join(enc[i:i+400] for i in range(0,len(enc),400))+'\n')
print('symbols',len(obj),'withdata',sum(bool(v) for v in obj.values()),'values',sum(len(v) for v in obj.values()),'raw',len(raw),'enc',len(enc),'lines',(len(enc)+399)//400,'missing',[s for s,v in obj.items() if not v])
