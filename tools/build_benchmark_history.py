#!/usr/bin/env python3
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import yfinance as yf

SYMBOLS=["SPY","QQQ","QLD","TQQQ","SPMO"]
START="2022-10-01"

def adjusted_month_return(d: pd.DataFrame, ym: str):
    a=pd.Period(ym,freq="M").start_time.normalize()
    b=(pd.Period(ym,freq="M")+1).start_time.normalize()
    g=d[(d.index>=a)&(d.index<b)].dropna(subset=["Open","Close","Adj Close"])
    if g.empty:return None
    f=g.iloc[0]; l=g.iloc[-1]
    ro,rc,ac,al=map(float,[f["Open"],f["Close"],f["Adj Close"],l["Adj Close"]])
    if min(ro,rc,ac,al)<=0:return None
    adj_open=ro*(ac/rc)
    return (al/adj_open-1)*100

def main():
    now=pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    end=str(now.to_period("M").start_time.date())
    data=yf.download(SYMBOLS,start=START,end=end,auto_adjust=False,actions=False,repair=False,progress=False,threads=False,group_by="column")
    series={}
    for s in SYMBOLS:
        if isinstance(data.columns,pd.MultiIndex):
            if s in data.columns.get_level_values(1): d=data.xs(s,axis=1,level=1,drop_level=True).copy()
            else: d=data.xs(s,axis=1,level=0,drop_level=True).copy()
        else:d=data.copy()
        if "Adj Close" not in d.columns:d["Adj Close"]=d["Close"]
        d=d[["Open","Close","Adj Close"]].copy()
        d.index=pd.to_datetime(d.index).tz_localize(None).normalize()
        series[s]=d.sort_index()
    first=pd.Period("2022-10",freq="M"); last=now.to_period("M")-1
    months={}
    p=first
    while p<=last:
        ym=str(p); row={}
        for s in SYMBOLS:
            v=adjusted_month_return(series[s],ym)
            if v is not None:row[s]=v
        if row:months[ym]=row
        p+=1
    payload={"generatedAt":datetime.now(timezone.utc).isoformat(),"method":"Adjusted first-trading-day open to adjusted last-trading-day close; percent return","symbols":SYMBOLS,"months":months}
    Path("benchmark-history.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    print("benchmark months",len(months),min(months),max(months))
    for s in SYMBOLS:
        n=sum(1 for x in months.values() if s in x)
        print(s,n)

if __name__=="__main__":main()
