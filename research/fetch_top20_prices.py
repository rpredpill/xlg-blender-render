#!/usr/bin/env python3
from pathlib import Path
import json, time
import pandas as pd
import yfinance as yf

OUT=Path("research/top20_price_output")
OUT.mkdir(parents=True, exist_ok=True)
TICKERS=['AAPL', 'ABBV', 'ABT', 'ACN', 'ADBE', 'ADI', 'ADM', 'ADP', 'AMAT', 'AMD', 'AMGN', 'AMZN', 'ANET', 'AON', 'APH', 'APP', 'AVGO', 'AXP', 'AZO', 'BA', 'BAC', 'BKNG', 'BLK', 'BMY', 'BRK.B', 'BSX', 'C', 'CARR', 'CAT', 'CB', 'CHTR', 'CI', 'CMCSA', 'CME', 'COF', 'COP', 'COST', 'CRM', 'CRWD', 'CSCO', 'CTAS', 'CVS', 'CVX', 'DASH', 'DE', 'DHR', 'DVN', 'EL', 'ELV', 'EOG', 'ETN', 'EXC', 'F', 'FCX', 'FISV', 'GE', 'GEV', 'GILD', 'GLW', 'GM', 'GOOG', 'GOOGL', 'GS', 'HCA', 'HES', 'HON', 'HUM', 'HWM', 'IBM', 'ICE', 'IDXX', 'INTC', 'INTU', 'ISRG', 'JCI', 'JNJ', 'JPM', 'KKR', 'KLAC', 'KO', 'LIN', 'LLY', 'LMT', 'LOW', 'LRCX', 'MCK', 'MDLZ', 'META', 'MPC', 'MRK', 'MRNA', 'MRSH', 'MRVL', 'MS', 'MSFT', 'MU', 'NEM', 'NFLX', 'NKE', 'NOC', 'NOW', 'NUE', 'NVDA', 'ORCL', 'ORLY', 'OXY', 'PANW', 'PCAR', 'PEP', 'PFE', 'PG', 'PGR', 'PH', 'PLD', 'PLTR', 'PM', 'PNC', 'PSA', 'PXD', 'PYPL', 'QCOM', 'RCL', 'REGN', 'RTX', 'SBUX', 'SCHW', 'SHOP', 'SIVB', 'SLB', 'SNDK', 'SO', 'STX', 'SYK', 'T', 'TDG', 'TGT', 'TJX', 'TMO', 'TMUS', 'TSLA', 'TT', 'TXN', 'UBER', 'UNH', 'UPS', 'V', 'VLO', 'VRTX', 'WDC', 'WELL', 'WFC', 'WMT', 'XOM']
START="2021-08-01"
END="2026-09-28"

def ys(t): return t.replace(".","-")
rows=[]; status=[]
for k in range(0,len(TICKERS),35):
    chunk=TICKERS[k:k+35]
    ychunk=[ys(t) for t in chunk]
    try:
        d=yf.download(ychunk,start=START,end=END,auto_adjust=False,actions=False,progress=False,threads=True,timeout=60)
    except Exception as e:
        status.append({"chunk":k,"error":repr(e)})
        continue
    if d.empty:
        status.append({"chunk":k,"error":"empty"}); continue
    for orig,yt in zip(chunk,ychunk):
        try:
            if isinstance(d.columns,pd.MultiIndex):
                c=d[("Close",yt)] if ("Close",yt) in d.columns else None
                a=d[("Adj Close",yt)] if ("Adj Close",yt) in d.columns else c
            else:
                c=d["Close"]
                a=d["Adj Close"] if "Adj Close" in d.columns else c
            if c is None: raise KeyError("Close missing")
            z=pd.DataFrame({"date":d.index,"ticker":orig,"close":c.values,"adj_close":a.values})
            z=z.dropna(subset=["close"],how="all")
            rows.append(z)
            status.append({"ticker":orig,"rows":int(len(z)),"first":str(z.date.min().date()) if len(z) else None,"last":str(z.date.max().date()) if len(z) else None})
        except Exception as e:
            status.append({"ticker":orig,"rows":0,"error":repr(e)})
    time.sleep(1)

# Individual fallback for failures/empty histories.
done={x["ticker"] for x in status if x.get("rows",0)>0}
for orig in [t for t in TICKERS if t not in done]:
    yt=ys(orig)
    try:
        d=yf.download(yt,start=START,end=END,auto_adjust=False,actions=False,progress=False,threads=False,timeout=60)
        if d.empty: continue
        if isinstance(d.columns,pd.MultiIndex):
            c=d[("Close",yt)] if ("Close",yt) in d.columns else d["Close"].iloc[:,0]
            a=d[("Adj Close",yt)] if ("Adj Close",yt) in d.columns else (d["Adj Close"].iloc[:,0] if "Adj Close" in d.columns.get_level_values(0) else c)
        else:
            c=d["Close"]; a=d["Adj Close"] if "Adj Close" in d.columns else c
        z=pd.DataFrame({"date":d.index,"ticker":orig,"close":c.values,"adj_close":a.values}).dropna(subset=["close"],how="all")
        rows.append(z)
        status.append({"ticker":orig,"fallback":True,"rows":int(len(z)),"first":str(z.date.min().date()) if len(z) else None,"last":str(z.date.max().date()) if len(z) else None})
    except Exception as e:
        status.append({"ticker":orig,"fallback":True,"rows":0,"error":repr(e)})

allp=pd.concat(rows,ignore_index=True) if rows else pd.DataFrame(columns=["date","ticker","close","adj_close"])
allp.to_csv(OUT/"prices_long.csv",index=False)
(OUT/"status.json").write_text(json.dumps(status,indent=2),encoding="utf-8")
print("tickers",len(TICKERS),"downloaded",allp.ticker.nunique(),"rows",len(allp))
print("missing",sorted(set(TICKERS)-set(allp.ticker.unique())))
