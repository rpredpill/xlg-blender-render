#!/usr/bin/env python3
from __future__ import annotations
import importlib.util, json, sys
from pathlib import Path

spec=importlib.util.spec_from_file_location('secv9','/tmp/quu_sec_pit_marketcap_validation_v9.py')
if spec is None or spec.loader is None: raise RuntimeError('cannot load v9')
v9=importlib.util.module_from_spec(spec); sys.modules['secv9']=v9; spec.loader.exec_module(v9)

OUT=Path('quu-sec-pit-marketcap-validation-v10-sensitivity85.json')

def main():
    v9.v8.MIN_COVERAGE=.85
    v9.OUT=OUT
    v9.main()
    j=json.loads(OUT.read_text(encoding='utf-8'))
    j['sensitivityOnly']=True
    j['coverageThreshold']=0.85
    j['method']=j.get('method','')+' Sensitivity-only run: price and market-cap coverage threshold lowered from 90% to 85%; missing caps within the accepted month are filled with that month cross-sectional median cap exactly as in the base engine. Live QUU is unchanged.'
    OUT.write_text(json.dumps(j,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print('V10_85_FINAL',json.dumps({'continuousStart':j.get('continuousStart90Pct'),'validation':j.get('validationAgainstRecentExact'),'performance':j.get('performance')},ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
