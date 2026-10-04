import importlib.util, pathlib, pandas as pd
spec=importlib.util.spec_from_file_location('flow',pathlib.Path(__file__).with_name('flow-signal.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
dates=list(pd.bdate_range('2024-01-01',periods=21).strftime('%Y-%m-%d'))
records=[('A',{'dollars':pd.Series(list(range(1,21))+[210],index=dates)}),('B',{'dollars':pd.Series([15]*21,index=dates)}),('BAD',None)]
rows,excluded=m.rank_flow(records,['A','B','BAD'],dates)
assert rows[0]['ticker']=='A' and rows[0]['score']==20
assert rows[1]['score']==15 and rows[0]['rank']==1 and excluded==['BAD']
try:m.rank_flow(records,['A'],dates[:-1]);raise AssertionError('partial window accepted')
except ValueError:pass
print('PASS: completed-session 21-day arithmetic mean, not median; missing coverage excluded; stable ranking')
