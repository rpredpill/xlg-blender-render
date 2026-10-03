"""Public FLOW simulation snapshots. No orders, credentials or account records."""
import datetime as dt, json, pathlib

def build_forward(series, dates, ranking, root):
    root=pathlib.Path(root)
    path=root/'joy-forward-state.json'
    old=json.loads(path.read_text()) if path.exists() else {'allocations':[]}
    asof=dates[-1]
    qstart=f'{asof[:4]}-{((int(asof[5:7])-1)//3)*3+1:02d}-01'
    allocations=old['allocations']
    if not allocations or allocations[-1]['quarter']!=qstart:
        signal=max(d for d in dates if d<qstart)
        rank=ranking(signal)
        scores={r['ticker']:r for r in rank}
        prior=allocations[-1]['rows'] if allocations else []
        keep=[r['ticker'] for r in prior if r['ticker'] in scores and scores[r['ticker']]['rank']<=20]
        chosen=(keep+[r['ticker'] for r in rank if r['ticker'] not in keep])[:10]
        if len(chosen)!=10:raise RuntimeError('Forward allocation needs 10 eligible stocks')
        total=sum(scores[s]['score'] for s in chosen)
        execution=min(d for d in dates if d>=qstart)
        rows=[{'ticker':s,'weight':scores[s]['score']/total} for s in chosen]
        allocations.append({'quarter':qstart,'signalDate':signal,'executionDate':execution,'rows':rows})
    first=allocations[0]['executionDate']
    calendar=[d for d in dates if first<=d<=asof]
    nav=1.;weights={};daily=[];month_records={};month_nav=1.;month_weights={};month_bench={};prices={};prev=None
    allocation_by={a['executionDate']:a for a in allocations}
    for date in calendar:
        month=date[:7]
        if prev is None or prev[:7]!=month:
            month_nav=nav
            month_weights=dict(weights)
            month_bench={s:series[s][prev]['adj'] if prev else series[s][date]['adj'] for s in ['SPY','QQQ']}
            month_base_date=prev or date
        if prev:
            relatives={s:series[s][date]['adj']/series[s][prev]['adj'] for s in weights}
            grow=sum(weights[s]*relatives[s] for s in weights)
            nav*=grow;weights={s:weights[s]*relatives[s]/grow for s in weights}
        if date in allocation_by:
            target={r['ticker']:r['weight'] for r in allocation_by[date]['rows']}
            post=nav
            for _ in range(40):post=nav-.001*sum(abs(target.get(s,0)*post-weights.get(s,0)*nav) for s in target.keys()|weights.keys())
            nav=post;weights=target
            if not month_weights:month_weights=dict(weights)
        rows=[]
        for s,w in sorted(weights.items(),key=lambda x:-x[1]):
            # Individual returns describe the complete current month independently of portfolio turnover.
            base=series[s][month_base_date]['adj']
            rows.append({'ticker':s,'weight':w,'ret':(series[s][date]['adj']/base-1)*100,'price':series[s][date]['close'],'priceDate':date})
        benchmark={s:(series[s][date]['adj']/month_bench[s]-1)*100 for s in ['SPY','QQQ']}
        h={'month':month,'port':(nav/month_nav-1)*100,'firstDate':month_base_date,'lastDate':date,'rows':rows,'benchmarks':benchmark,'benchmarkPrices':{s:{'price':series[s][date]['close'],'priceDate':date} for s in ['SPY','QQQ']},'provenance':'public-forward-simulation','complete':month!=asof[:7]}
        month_records[month]=h
        daily.append({'date':date,'nav':nav,'SPY':series['SPY'][date]['adj']/series['SPY'][first]['adj'],'QQQ':series['QQQ'][date]['adj']/series['QQQ'][first]['adj']})
        prev=date
    last=allocations[-1]
    output={'strategy':'환희','ready':True,'generatedAt':dt.datetime.now(dt.UTC).isoformat(),'start':first,'asOf':asof,'signalDate':last['signalDate'],'allocationDate':last['executionDate'],'currentMonth':asof[:7],'months':month_records,'daily':daily,'cost':.001,'provenance':'public-forward-simulation','note':'First allocation initialized from the preceding quarter-end score; no 2025 bridge or Paper account data.'}
    # No mutation until every price and calculation succeeds.
    path.write_text(json.dumps({'allocations':allocations},ensure_ascii=False,indent=2))
    (root/'joy-latest.json').write_text(json.dumps(output,ensure_ascii=False,indent=2))
    return output
