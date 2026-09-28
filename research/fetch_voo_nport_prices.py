#!/usr/bin/env python3
from pathlib import Path
from difflib import SequenceMatcher
from urllib.parse import quote
import json, time, re
import requests
import pandas as pd
import yfinance as yf
import xml.etree.ElementTree as ET

OUT = Path('research/voo_data_output')
OUT.mkdir(parents=True, exist_ok=True)
SEC_CIK = '0000036405'
SERIES_TARGET = 'Vanguard 500 Index Fund'
START_REPORT = pd.Timestamp('2021-03-31')
END_REPORT = pd.Timestamp('2026-06-30')
UA = 'rpredpill quantitative research contact github.com/rpredpill'
SEC_HEADERS = {'User-Agent': UA, 'Accept-Encoding': 'gzip, deflate'}
WEB_HEADERS = {'User-Agent': 'Mozilla/5.0'}
S = requests.Session()


def get_json(url, headers=SEC_HEADERS, tries=4):
    for k in range(tries):
        r = S.get(url, headers=headers, timeout=60)
        if r.status_code == 200:
            return r.json()
        time.sleep(1.5 * (k + 1))
    raise RuntimeError(f'GET JSON failed {r.status_code}: {url}')


def get_text(url, headers=SEC_HEADERS, tries=4):
    for k in range(tries):
        r = S.get(url, headers=headers, timeout=60)
        if r.status_code == 200:
            return r.text
        if r.status_code == 404:
            return None
        time.sleep(1.5 * (k + 1))
    return None


def submission_rows():
    root = get_json(f'https://data.sec.gov/submissions/CIK{SEC_CIK}.json')
    blocks = [root.get('filings', {}).get('recent', {})]
    for f in root.get('filings', {}).get('files', []):
        nm = f.get('name')
        if not nm:
            continue
        try:
            blocks.append(get_json('https://data.sec.gov/submissions/' + nm))
            time.sleep(0.12)
        except Exception as e:
            print('history json failed', nm, repr(e))
    rows = []
    for b in blocks:
        if not b or 'accessionNumber' not in b:
            continue
        n = len(b['accessionNumber'])
        for i in range(n):
            form = b.get('form', [''] * n)[i]
            report = b.get('reportDate', [''] * n)[i]
            if form != 'NPORT-P' or not report:
                continue
            dt = pd.Timestamp(report)
            if START_REPORT <= dt <= END_REPORT:
                rows.append({
                    'accession': b['accessionNumber'][i],
                    'filed': b.get('filingDate', [''] * n)[i],
                    'report': report,
                    'primaryDocument': b.get('primaryDocument', [''] * n)[i],
                })
    # de-duplicate because SEC historical blocks can overlap recent
    df = pd.DataFrame(rows).drop_duplicates('accession').sort_values(['report','accession'])
    return df


def find_primary_xml(accession, primary_hint=''):
    acc = accession.replace('-', '')
    base = f'https://www.sec.gov/Archives/edgar/data/{int(SEC_CIK)}/{acc}/'
    candidates = []
    if primary_hint and str(primary_hint).lower().endswith('.xml'):
        candidates.append(primary_hint)
    candidates += ['primary_doc.xml']
    for fn in candidates:
        txt = get_text(base + fn)
        if txt and '<edgarSubmission' in txt:
            return txt, base + fn
    try:
        idx = get_json(base + 'index.json')
        for item in idx.get('directory', {}).get('item', []):
            fn = item.get('name','')
            if fn.lower().endswith('.xml'):
                txt = get_text(base + fn)
                if txt and '<edgarSubmission' in txt:
                    return txt, base + fn
    except Exception as e:
        print('index fail', accession, repr(e))
    return None, None


def text_any(node, tag):
    el = node.find('.//{*}' + tag)
    return (el.text or '').strip() if el is not None and el.text else ''


def parse_voo_xml(txt, accession, filed, src):
    try:
        root = ET.fromstring(txt)
    except Exception as e:
        print('xml parse fail', accession, repr(e))
        return None
    series = text_any(root, 'seriesName')
    if SERIES_TARGET.lower() not in series.lower():
        return None
    report = text_any(root, 'repPdDate') or text_any(root, 'repPdEnd')
    net_assets = text_any(root, 'netAssets')
    invs = []
    for x in root.findall('.//{*}invstOrSec'):
        name = text_any(x, 'name')
        title = text_any(x, 'title')
        cusip = text_any(x, 'cusip')
        val = text_any(x, 'valUSD')
        pct = text_any(x, 'pctVal')
        asset = text_any(x, 'assetCat')
        isin = ''
        ie = x.find('.//{*}isin')
        if ie is not None:
            isin = (ie.attrib.get('value') or (ie.text or '')).strip()
        try:
            valf = float(val)
            pctf = float(pct)
        except Exception:
            continue
        if valf <= 0 or pctf <= 0:
            continue
        invs.append({
            'report': report, 'filed': filed, 'accession': accession,
            'series': series, 'source': src, 'name': name, 'title': title,
            'cusip': cusip, 'isin': isin, 'assetCat': asset,
            'valUSD': valf, 'weight': pctf,
        })
    if not invs:
        return None
    return {'report': report, 'filed': filed, 'accession': accession,
            'series': series, 'netAssets': net_assets, 'source': src,
            'holdings': invs}


def norm(s):
    s = (s or '').lower()
    s = s.replace('&', ' and ')
    s = re.sub(r'\b(class|cl|common|stock|shares|share|incorporated|corporation|corp|company|co|plc|ltd|adr)\b', ' ', s)
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()

# Overrides for recurring ambiguous names/classes.
OVERRIDES = {
    'apple': 'AAPL', 'microsoft': 'MSFT', 'amazon com': 'AMZN', 'nvidia': 'NVDA',
    'meta platforms': 'META', 'facebook': 'META', 'tesla': 'TSLA', 'broadcom': 'AVGO',
    'jpmorgan chase': 'JPM', 'visa': 'V', 'mastercard': 'MA', 'eli lilly': 'LLY',
    'exxon mobil': 'XOM', 'unitedhealth group': 'UNH', 'costco wholesale': 'COST',
    'walmart': 'WMT', 'johnson and johnson': 'JNJ', 'procter and gamble': 'PG',
    'oracle': 'ORCL', 'home depot': 'HD', 'abbvie': 'ABBV', 'bank of america': 'BAC',
    'coca cola': 'KO', 'netflix': 'NFLX', 'salesforce': 'CRM', 'merck': 'MRK',
    'chevron': 'CVX', 'advanced micro devices': 'AMD', 'cisco systems': 'CSCO',
    'accenture': 'ACN', 'general electric': 'GE', 'international business machines': 'IBM',
    'mcdonald': 'MCD', 'thermo fisher scientific': 'TMO', 'wells fargo': 'WFC',
    'linde': 'LIN', 'philip morris international': 'PM', 'abbott laboratories': 'ABT',
    'goldman sachs group': 'GS', 'caterpillar': 'CAT', 'qualcomm': 'QCOM',
    'walt disney': 'DIS', 'rtx': 'RTX', 'raytheon technologies': 'RTX',
    'verizon communications': 'VZ', 'at and t': 'T', 'intuitive surgical': 'ISRG',
    'servicenow': 'NOW', 'intuit': 'INTU', 'texas instruments': 'TXN',
    'american express': 'AXP', 's and p global': 'SPGI', 'booking holdings': 'BKNG',
    'pepsico': 'PEP', 'amgen': 'AMGN', 'danaher': 'DHR', 'blackrock': 'BLK',
    'pfizer': 'PFE', 'nextera energy': 'NEE', 'lowe': 'LOW', 'micron technology': 'MU',
    'comcast': 'CMCSA', 'uber technologies': 'UBER', 'palantir technologies': 'PLTR',
    'charles schwab': 'SCHW', 'union pacific': 'UNP', 'honeywell international': 'HON',
    'applied materials': 'AMAT', 'lockheed martin': 'LMT', 'starbucks': 'SBUX',
    'boeing': 'BA', 'deere': 'DE', 'stryker': 'SYK', 'boston scientific': 'BSX',
    'progressive': 'PGR', 'automatic data processing': 'ADP', 'analog devices': 'ADI',
    'marsh and mclennan': 'MMC', 'gilead sciences': 'GILD', 'vertex pharmaceuticals': 'VRTX',
    'prologis': 'PLD', 'eaton': 'ETN', 'southern': 'SO', 'duke energy': 'DUK',
    'chipotle mexican grill': 'CMG', 'kkr': 'KKR', 'crowdstrike holdings': 'CRWD',
}


def override_symbol(name, title):
    n = norm((title or '') + ' ' + (name or ''))
    # Alphabet and Berkshire require class handling.
    if 'alphabet' in n:
        raw = ((title or '') + ' ' + (name or '')).lower()
        return 'GOOGL' if ('class a' in raw or ' cl a' in raw) else 'GOOG'
    if 'berkshire hathaway' in n:
        return 'BRK-B'
    for key, sym in OVERRIDES.items():
        if key in n:
            return sym
    return None


def yahoo_search_symbol(name, title):
    ov = override_symbol(name, title)
    if ov:
        return ov, 'override', 1.0
    query = title or name
    try:
        url = 'https://query1.finance.yahoo.com/v1/finance/search?q=' + quote(query) + '&quotesCount=10&newsCount=0'
        j = get_json(url, headers=WEB_HEADERS, tries=3)
        candidates = []
        target = norm(query)
        for q in j.get('quotes', []):
            if q.get('quoteType') != 'EQUITY':
                continue
            sym = q.get('symbol','')
            if not sym or sym.endswith('=F') or '^' in sym:
                continue
            nm = q.get('longname') or q.get('shortname') or ''
            score = SequenceMatcher(None, target, norm(nm)).ratio()
            exch = q.get('exchange','')
            if exch in ('NYQ','NMS','NGM','NCM','ASE','PCX'):
                score += 0.05
            candidates.append((score,sym,nm))
        if candidates:
            candidates.sort(reverse=True)
            sc,sym,nm = candidates[0]
            return sym.replace('.', '-'), 'yahoo', sc
    except Exception as e:
        print('search fail', query, repr(e))
    return None, 'unmapped', 0.0


def main():
    subs = submission_rows()
    subs.to_csv(OUT/'nport_candidates.csv', index=False)
    print('NPORT candidates', len(subs))
    snaps=[]
    seen_reports=set()
    # newest first lets us avoid repeated same-report duplicates once VOO is found
    for _,r in subs.sort_values(['report','filed'], ascending=False).iterrows():
        report=str(r['report'])
        if report in seen_reports:
            continue
        txt,src=find_primary_xml(r['accession'], r.get('primaryDocument',''))
        if not txt:
            continue
        parsed=parse_voo_xml(txt,r['accession'],r['filed'],src)
        if parsed:
            seen_reports.add(report)
            snaps.append(parsed)
            print('VOO', parsed['report'], 'holdings', len(parsed['holdings']))
        time.sleep(0.12)

    if not snaps:
        raise RuntimeError('No VOO N-PORT snapshots found')
    snaps=sorted(snaps,key=lambda x:x['report'])
    meta=pd.DataFrame([{k:v for k,v in s.items() if k!='holdings'} for s in snaps])
    meta.to_csv(OUT/'voo_snapshots.csv', index=False)
    h=pd.concat([pd.DataFrame(s['holdings']) for s in snaps], ignore_index=True)
    h['rank']=h.groupby('report')['weight'].rank(method='first',ascending=False).astype(int)
    h.to_csv(OUT/'voo_holdings_all.csv', index=False)

    # Mapping work is limited to names that ever enter Top 80, which safely covers all planned Top-N variations.
    top=h[h['rank']<=80].copy()
    keys=(top[['name','title','cusip','isin']].drop_duplicates().reset_index(drop=True))
    maps=[]
    for i,r in keys.iterrows():
        sym,method,score=yahoo_search_symbol(r['name'],r['title'])
        maps.append({**r.to_dict(),'ticker':sym,'map_method':method,'map_score':score})
        if i%20==0: print('mapped',i,'/',len(keys))
        time.sleep(0.05)
    mp=pd.DataFrame(maps)
    mp.to_csv(OUT/'ticker_map.csv', index=False)
    top=top.merge(mp[['name','title','cusip','isin','ticker','map_method','map_score']],on=['name','title','cusip','isin'],how='left')
    top.to_csv(OUT/'voo_top80_mapped.csv', index=False)

    tickers=sorted(set(top['ticker'].dropna().astype(str)))
    tickers += ['VOO']
    tickers=sorted(set(tickers))
    price_rows=[]
    status={}
    for i,t in enumerate(tickers):
        try:
            d=yf.download(t,start='2021-03-01',end='2026-09-29',auto_adjust=False,actions=False,progress=False,threads=False,timeout=60)
            if d.empty:
                status[t]='empty'; continue
            if isinstance(d.columns,pd.MultiIndex):
                c=d['Close'].iloc[:,0]
                a=d['Adj Close'].iloc[:,0] if 'Adj Close' in d.columns.get_level_values(0) else c
            else:
                c=d['Close']; a=d['Adj Close'] if 'Adj Close' in d.columns else c
            z=pd.DataFrame({'date':d.index,'ticker':t,'close':c.values,'adj_close':a.values}).dropna(subset=['close'])
            price_rows.append(z)
            status[t]=f'ok:{len(z)}'
        except Exception as e:
            status[t]='error:'+repr(e)
        if i%20==0: print('prices',i,'/',len(tickers))
    prices=pd.concat(price_rows,ignore_index=True) if price_rows else pd.DataFrame(columns=['date','ticker','close','adj_close'])
    prices.to_csv(OUT/'prices_long.csv', index=False)

    coverage=[]
    for report,g in top.groupby('report'):
        top50=g[g['rank']<=50]
        coverage.append({
            'report':report,
            'top50_count':len(top50),
            'top50_mapped':int(top50.ticker.notna().sum()),
            'top50_mapped_weight':float(top50.loc[top50.ticker.notna(),'weight'].sum()),
            'top50_total_weight':float(top50.weight.sum()),
        })
    pd.DataFrame(coverage).to_csv(OUT/'coverage.csv',index=False)
    (OUT/'status.json').write_text(json.dumps({
        'snapshots':len(snaps),
        'reports':[s['report'] for s in snaps],
        'mapped_unique':int(mp.ticker.notna().sum()),
        'mapping_rows':len(mp),
        'price_status':status,
    },indent=2),encoding='utf-8')

    print('snapshots',len(snaps))
    print('reports',[s['report'] for s in snaps])
    print('map coverage',mp.ticker.notna().mean())
    print('price tickers',prices.ticker.nunique())

if __name__=='__main__':
    main()
