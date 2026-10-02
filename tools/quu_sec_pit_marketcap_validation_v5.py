#!/usr/bin/env python3
from __future__ import annotations

import importlib.util, json, sys
from pathlib import Path

spec=importlib.util.spec_from_file_location('secv4','/tmp/quu_sec_pit_marketcap_validation_v4.py')
if spec is None or spec.loader is None: raise RuntimeError('cannot load v4')
v4=importlib.util.module_from_spec(spec); sys.modules['secv4']=v4; spec.loader.exec_module(v4)

OUT=Path('quu-sec-pit-marketcap-validation-v5.json')
ORIG=v4.enhanced_cik_map
VERIFIED={
    'CTRX':'0001363851', # Catamaran Corporation
    'HANS':'0000865752', # Hansen Natural / Monster Beverage
    'LMCA':'0001560385', # Liberty Media Corp
    'STRZA':'0001507934',# Starz
    'VIP':'0001468091',  # VimpelCom Ltd
    'VMED':'0001270400', # Virgin Media Inc
    'WCRX':'0001323854', # Warner Chilcott plc
}

def map_v5(finsaber):
    m,s=ORIG(finsaber)
    for t,c in VERIFIED.items():
        m[t]=c; s[t]='manual-sec-verified-historical'
    return m,s


def main():
    v4.enhanced_cik_map=map_v5
    v4.OUT=OUT
    v4.main()
    j=json.loads(OUT.read_text(encoding='utf-8'))
    j['verifiedHistoricalCIKMap']=VERIFIED
    j['method']=j.get('method','')+' Historical ticker-to-CIK gaps are filled only with manually SEC-verified issuer mappings listed in verifiedHistoricalCIKMap.'
    OUT.write_text(json.dumps(j,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print('V5_FINAL',json.dumps({'continuousStart90Pct':j.get('continuousStart90Pct'),'mapping':j.get('mapping'),'validation':j.get('validationAgainstRecentExact'),'performance':j.get('performance')},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
