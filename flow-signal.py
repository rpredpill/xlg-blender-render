"""FLOW public signals. No credentials, orders, or account data are published."""
import asyncio, datetime as dt, io, json, pathlib, urllib.request
import aiohttp
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent
HEADERS = {'User-Agent': 'Mozilla/5.0'}

def rank_flow(results, symbols, dates):
    if len(dates) != 21:
        raise ValueError('FLOW requires 21 completed sessions')
    rows, excluded = [], []
    for symbol, record in results:
        if symbol not in symbols:
            continue
        window = record['dollars'].reindex(dates) if record else None
        if window is None or window.isna().any() or (window <= 0).any():
            excluded.append(symbol)
            continue
        rows.append({'ticker': symbol, 'score': float(window.mean())})
    rows.sort(key=lambda r: (-r['score'], r['ticker']))
    for i, row in enumerate(rows):
        row['rank'] = i + 1
    return rows, excluded

async def build():
    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    html = urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=40).read().decode()
    universe = pd.read_html(io.StringIO(html))[0]
    symbols = sorted(universe['Symbol'].astype(str).tolist())
    prior_file=ROOT/'joy-forward-state.json'
    start_epoch=None
    if prior_file.exists():
        initial=json.loads(prior_file.read_text())['allocations'][0]['executionDate']
        start_epoch=int(dt.datetime.combine(dt.date.fromisoformat(initial)-dt.timedelta(days=160),dt.time(),dt.UTC).timestamp())
    query=('period1='+str(start_epoch)+'&period2='+str(int(dt.datetime.now(dt.UTC).timestamp()))+'&interval=1d') if start_epoch else 'range=6mo&interval=1d'
    sem = asyncio.Semaphore(10)
    async with aiohttp.ClientSession(headers=HEADERS, trust_env=True, timeout=aiohttp.ClientTimeout(total=50)) as session:
        async def get(symbol):
            async with sem:
                for attempt in range(3):
                    try:
                        async with session.get('https://query2.finance.yahoo.com/v8/finance/chart/'+symbol.replace('.', '-')+'?'+query) as response:
                            response.raise_for_status()
                            r = (await response.json(content_type=None))['chart']['result'][0]
                        dates = pd.to_datetime(r['timestamp'],unit='s',utc=True).tz_convert('America/New_York').strftime('%Y-%m-%d')
                        q = r['indicators']['quote'][0]
                        values = pd.Series(q['close'],index=dates,dtype=float)*pd.Series(q['volume'],index=dates,dtype=float)
                        adj=r['indicators'].get('adjclose',[{'adjclose':q['close']}])[0]['adjclose']
                        points={str(d):{'close':float(c),'adj':float(a)} for d,c,a in zip(dates,q['close'],adj) if c is not None and a is not None and c>0 and a>0}
                        return symbol,{'dollars':values.where(values>0),'prices':points}
                    except Exception:
                        if attempt==2: return symbol,None
                        await asyncio.sleep(attempt+1)
        spy_data = (await get('SPY'))[1]
        spy = spy_data['dollars'] if spy_data else None
        if spy is None: raise RuntimeError('Trading calendar unavailable')
        ny_now = dt.datetime.now(dt.UTC).astimezone(__import__('zoneinfo').ZoneInfo('America/New_York'))
        today = ny_now.strftime('%Y-%m-%d')
        completed = (spy.dropna().index <= today) if (ny_now.hour,ny_now.minute)>=(16,15) else (spy.dropna().index < today)
        all_dates = list(spy.dropna().index[completed])
        dates = all_dates[-21:]
        if len(dates)!=21: raise RuntimeError('Need 21 completed sessions')
        asof = dates[-1]
        prior_path=ROOT/'joy-forward-state.json'
        prior_symbols=[]
        if prior_path.exists():
            prior_state=json.loads(prior_path.read_text())
            prior_symbols=[r['ticker'] for a in prior_state['allocations'] for r in a['rows']]+list(prior_state.get('continuationSeed',{}).get('weights',{}))
        results = await asyncio.gather(*(get(s) for s in sorted(set(symbols+prior_symbols+['QQQ']))))
    rows, excluded = rank_flow(results, symbols, dates)
    # FLOW v2: current Top5, no retention buffer, equal weights.
    top=[dict(r,weight=.2) for r in rows[:5]]
    age=(dt.date.fromisoformat(today)-dt.date.fromisoformat(asof)).days
    ready=len(rows)/len(symbols)>=.98 and age<=4 and len(top)==5
    data={'strategy':'FLOW','version':2,'ruleId':'FLOW_MEAN21_TOP5_EQUAL_SEMIANNUAL_V2','ready':ready,'generatedAt':dt.datetime.now(dt.UTC).isoformat(),
          'signalDate':asof,'membershipRetrievedAt':dt.datetime.now(dt.UTC).isoformat(),'membershipSource':url,
          'priceSource':'Yahoo Finance daily close × volume; consolidated vendor data, not Alpaca IEX',
          'universeCount':len(symbols),'eligibleCount':len(rows),'excluded':excluded,
          'rule':'21-session arithmetic mean traded value; current Top5 without retention buffer; equal 20% weights; semiannual January/July first-trading-day rebalance; no leverage or ETF holdings',
          'rows':top,'ranking':rows,'initialSignal':True,'coverageGuard':.98}
    (ROOT/'flow-latest.json').write_text(json.dumps(data,ensure_ascii=False,indent=2))
    if not ready:raise RuntimeError('Latest FLOW signal is not ready; preserve prior simulation snapshot')
    def rank_at(day):
        window_dates=[d for d in all_dates if d<=day][-63:]
        ranked=[]
        for symbol,record in results:
            if symbol not in symbols or record is None:continue
            win=record['dollars'].reindex(window_dates)
            if len(window_dates)==63 and win.notna().all():ranked.append({'ticker':symbol,'score':float(win.median())})
        ranked.sort(key=lambda x:(-x['score'],x['ticker']))
        for i,x in enumerate(ranked):x['rank']=i+1
        if len(ranked)/len(symbols)<.98:raise RuntimeError('Forward signal coverage below 98%')
        return ranked
    from importlib.util import spec_from_file_location,module_from_spec
    spec=spec_from_file_location('joy_forward',ROOT/'joy-forward.py');module=module_from_spec(spec);spec.loader.exec_module(module)
    price_series={s:r['prices'] for s,r in results if r is not None}
    price_series['SPY']=spy_data['prices']
    forward=module.build_forward(price_series,all_dates,rank_at,ROOT)
    print(json.dumps({'signalDate':asof,'eligibleCount':len(rows),'forwardStart':forward['start'],'forwardAsOf':forward['asOf']},ensure_ascii=False))

if __name__=='__main__': asyncio.run(build())

