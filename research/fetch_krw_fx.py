from pathlib import Path
import pandas as pd
import yfinance as yf

OUT=Path('research/krw_fx_output')
OUT.mkdir(parents=True,exist_ok=True)

x=yf.download('KRW=X', start='2021-08-01', end='2026-09-27', auto_adjust=False, progress=False, actions=False)
if isinstance(x.columns,pd.MultiIndex):
    x.columns=x.columns.get_level_values(0)
x=x.reset_index()
x.columns=[str(c).lower().replace(' ','_') for c in x.columns]
keep=[c for c in ['date','close','adj_close'] if c in x.columns]
x=x[keep]
x.to_csv(OUT/'krw_fx.csv',index=False)
print(x.head())
print(x.tail())
