#!/usr/bin/env python3
import json
from pathlib import Path

q=json.loads(Path('quu-history.json').read_text(encoding='utf-8'))
b=json.loads(Path('benchmark-history.json').read_text(encoding='utf-8'))
qm=q.get('months') or {}
bm=b.get('months') or {}
months=sorted(set(qm)&set(bm))
rows=[]
qqq_idx=100.0
quu_idx=100.0
for m in months:
    qr=qm[m].get('portfolioReturn')
    br=(bm[m] or {}).get('QQQ')
    if not isinstance(qr,(int,float)) or not isinstance(br,(int,float)):
        continue
    quu_idx*=1+float(qr)/100.0
    qqq_idx*=1+float(br)/100.0
    rows.append({
        'month':m,
        'QQQ':qqq_idx,
        'QUU':quu_idx,
        'QQQ_monthlyReturnPct':float(br),
        'QUU_monthlyReturnPct':float(qr),
        'QUU_sleeve':qm[m].get('selectedSleeve')
    })
out={
    'startIndex':100.0,
    'months':len(rows),
    'start':rows[0]['month'] if rows else None,
    'end':rows[-1]['month'] if rows else None,
    'rows':rows,
    'final':rows[-1] if rows else None,
    'quuMetrics':q.get('metricsCompletedMonths')
}
Path('qqq-quu-chart-data.json').write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
print(json.dumps({'months':len(rows),'final':out['final']},ensure_ascii=False))
