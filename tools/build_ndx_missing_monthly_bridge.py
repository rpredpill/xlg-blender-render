#!/usr/bin/env python3
import base64,json,zlib
from pathlib import Path
import pandas as pd, yfinance as yf

SYMBOLS='ALNY ARM ASML AZN BIDU BMRN CHKP CTRP DOCU FER GFS INSM JD LBTYA LBTYK LCID LITE MDB MELI MRVL MSTR NTES OKTA PDD PTON QRTEA RIVN SGEN SHOP SHPG SIRI SPLK TEAM TRI VOD VSNT ZM ZS'.split()
ALIASES={'CTRP':'TCOM'}

def get(sym):
    q=ALIASES.get(sym,sym)
    try:
        df=yf.download(q,start='2011-01-01',end='2026-01-05',auto_adjust=True,progress=False,threads=False)
        if df is None or df.empty:return {}
        if isinstance(df.columns,pd.MultiIndex):df.columns=df.columns.get_level_values(0)
        s=df['Close'].dropna(); s.index=pd.to_datetime(s.index)
        m=s.resample('ME').last()
        return {d.strftime('%Y-%m'):float(v) for d,v in m.items()}
    except Exception:
        return {}

obj={s:get(s) for s in SYMBOLS}
raw=json.dumps(obj,separators=(',',':')).encode()
enc=base64.b85encode(zlib.compress(raw,9)).decode()
Path('ndx-missing-monthly.b85').write_text('\n'.join(enc[i:i+400] for i in range(0,len(enc),400))+'\n')
print('symbols',len(obj),'withdata',sum(bool(v) for v in obj.values()),'raw',len(raw),'enc',len(enc),'missing',[s for s,v in obj.items() if not v])
