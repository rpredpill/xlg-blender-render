#!/usr/bin/env python3
from __future__ import annotations

import importlib.util, json, sys
from pathlib import Path
import pandas as pd

spec=importlib.util.spec_from_file_location('secv8','/tmp/quu_sec_pit_marketcap_validation_v8.py')
if spec is None or spec.loader is None: raise RuntimeError('cannot load v8')
v8=importlib.util.module_from_spec(spec); sys.modules['secv8']=v8; spec.loader.exec_module(v8)

OUT=Path('quu-sec-pit-marketcap-validation-v9.json')
ORIG_LOAD=v8.v3.load_finsaber_all

# SEC-verified issuer identities for ticker histories where the current CIK belongs
# to a successor/reorganized issuer. End dates are the last date on which the old
# issuer should be used for allocation signals.
DATED_CIK_OVERRIDES={
    'DELL': [('2013-10-29','0000826083')],
    'LBTYA':[('2013-06-06','0001316631')],
    'LBTYK':[('2013-06-06','0001316631')],
    'PRGO': [('2013-12-17','0000820096')],
    'GOOG': [('2015-10-01','0001288776')],
    'GOOGL':[('2015-10-01','0001288776')],
    'ALTR': [('2015-12-27','0000768251')],
    'AVGO': [('2016-01-31','0001441634'),('2018-04-03','0001649338')],
    'SNDK': [('2016-05-11','0001000180')],
    'FOXA': [('2019-03-19','0001308161')],
    'FOX':  [('2019-03-19','0001308161')],
    'MRVL': [('2021-04-19','0001058057')],
}

CACHE=None

def corrected_finsaber():
    global CACHE
    if CACHE is not None:return CACHE
    d=ORIG_LOAD().copy()
    d['date']=pd.to_datetime(d['date']).dt.tz_localize(None).dt.normalize()
    for ticker,rules in DATED_CIK_OVERRIDES.items():
        start=pd.Timestamp.min
        for end,cik in rules:
            e=pd.Timestamp(end)
            mask=(d['symbol'].astype(str).str.upper()==ticker)&(d['date']>=start)&(d['date']<=e)
            d.loc[mask,'cik_norm']=cik
            start=e+pd.Timedelta(days=1)
    CACHE=d
    return d


def main():
    v8.v3.load_finsaber_all=corrected_finsaber
    v8.OUT=OUT
    v8.main()
    j=json.loads(OUT.read_text(encoding='utf-8'))
    j['datedCIKOverrides']=DATED_CIK_OVERRIDES
    j['method']=j.get('method','')+' Historical issuer CIKs are date-corrected for known ticker continuities/reorganizations listed in datedCIKOverrides; these corrections affect identity/SEC facts only, not prices or strategy parameters.'
    OUT.write_text(json.dumps(j,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print('V9_FINAL',json.dumps({'continuousStart90Pct':j.get('continuousStart90Pct'),'sourceCounts':j.get('sourceCounts'),'validation':j.get('validationAgainstRecentExact'),'performance':j.get('performance')},ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
