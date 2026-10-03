"""FLOW public signals. No credentials, orders, or account data are published."""
import asyncio, datetime as dt, io, json, pathlib, urllib.request
import aiohttp
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent
HEADERS = {'User-Agent': 'Mozilla/5.0'}

async def build():
    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    html = urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=40).read().decode()
    universe = pd.read_html(io.StringIO(html))[0]
    symbols = sorted(universe['Symbol'].astype(str).tolist())
    sem = asyncio.Semaphore(10)
    async with aiohttp.ClientSession(headers=HEADERS, trust_env=True, timeout=aiohttp.ClientTimeout(total=50)) as session:
        async def get(symbol):
            async with sem:
                for attempt in range(3):
                    try:
                        async with session.get('https://query1.finance.yahoo.com/v8/finance/chart/'+symbol.replace('.', '-')+'?range=6mo&interval=1d') as response:
                            response.raise_for_status()
                            r = (await response.json(content_type=None))['chart']['result'][0]
                        dates = pd.to_datetime(r['timestamp'],unit='s',utc=True).tz_convert('America/New_York').strftime('%Y-%m-%d')
                        q = r['indicators']['quote'][0]
                        values = pd.Series(q['close'],index=dates,dtype=float)*pd.Series(q['volume'],index=dates,dtype=float)
                        return symbol,values.where(values>0)
                    except Exception:
                        if attempt==2: return symbol,None
                        await asyncio.sleep(attempt+1)
        spy = (await get('SPY'))[1]
        if spy is None: raise RuntimeError('Trading calendar unavailable')
        ny_now = dt.datetime.now(dt.UTC).astimezone(__import__('zoneinfo').ZoneInfo('America/New_York'))
        today = ny_now.strftime('%Y-%m-%d')
        completed = (spy.dropna().index <= today) if (ny_now.hour,ny_now.minute)>=(16,15) else (spy.dropna().index < today)
        dates = spy.dropna().index[completed][-63:]
        if len(dates)!=63: raise RuntimeError('Need 63 completed sessions')
        asof = dates[-1]
        results = await asyncio.gather(*(get(s) for s in symbols))
    rows=[]; excluded=[]
    for s,v in results:
        window = v.reindex(dates) if v is not None else None
        if window is None or window.isna().any(): excluded.append(s);continue
        rows.append({'ticker':s,'score':float(window.median())})
    rows.sort(key=lambda r:(-r['score'],r['ticker']))
    for i,r in enumerate(rows): r['rank']=i+1
    # Publish all eligible scores: each private paper account applies its own Top20 buffer.
    total=sum(r['score'] for r in rows[:10])
    top=[dict(r,weight=r['score']/total) for r in rows[:10]]
    age=(dt.date.fromisoformat(today)-dt.date.fromisoformat(asof)).days
    ready=len(rows)/len(symbols)>=.98 and age<=4 and len(top)==10
    data={'strategy':'FLOW','version':1,'ready':ready,'generatedAt':dt.datetime.now(dt.UTC).isoformat(),
          'signalDate':asof,'membershipRetrievedAt':dt.datetime.now(dt.UTC).isoformat(),'membershipSource':url,
          'priceSource':'Yahoo Finance daily close × volume; consolidated vendor data, not Alpaca IEX',
          'universeCount':len(symbols),'eligibleCount':len(rows),'excluded':excluded,
          'rule':'63-session median traded value; Top10 / retain Top20; score-proportional; quarterly; no leverage or ETF holdings',
          'rows':top,'ranking':rows,'initialSignal':True,'coverageGuard':.98}
    (ROOT/'flow-latest.json').write_text(json.dumps(data,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in data.items() if k not in ['ranking']},ensure_ascii=False))

if __name__=='__main__': asyncio.run(build())
