#!/usr/bin/env python3
import json, requests
from pathlib import Path

URL='https://raw.githubusercontent.com/rpredpill/xlg-blender-render/somx-pages/quu-history.json'
obj=requests.get(URL,timeout=30).json()
out={
  'generatedAt':obj.get('generatedAt'),
  'rules':obj.get('rules'),
  'metricsCompletedMonths':obj.get('metricsCompletedMonths'),
  'months':{}
}
for m,x in sorted(obj.get('months',{}).items()):
    if '2022-10' <= m <= '2025-12':
        out['months'][m]={
          'selectedSleeve':x.get('selectedSleeve'),
          'portfolioReturn':x.get('portfolioReturn'),
          'coverageRatio':x.get('coverageRatio'),
          'decision':x.get('decision')
        }
Path('quu-exact-overlap-compact.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
print('months',len(out['months']))
