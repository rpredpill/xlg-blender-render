#!/usr/bin/env python3
from __future__ import annotations

import calendar, json, math, re, time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests
import yfinance as yf

OUT = Path('research/backtest_output')
OUT.mkdir(parents=True, exist_ok=True)
S = requests.Session()
UA = 'Mozilla/5.0 qqq-spmo-historical-research/1.0'

# Exact SPMO N-PORT accessions already verified against SEC/GitHub-derived snapshots.
SPMO_KNOWN = {
    '2022-05-31': '0001752724-22-170575',
    '2022-11-30': '0001752724-23-017306',
    '2023-05-31': '0001752724-23-166926',
    '2023-11-30': '0001752724-24-016884',
    '2024-05-31': '0001752724-24-169075',
    '2024-11-30': '0001752724-25-017721',
    '2025-05-31': '0001752724-25-180531',
    '2025-11-30': '0001378872-26-000328',
}
SPMO_CIK = '1378872'
QQQ_CIK_PAD = '0001067839'
QQQ_CIK_ARCHIVE = '1067839'
SPMO_LIVE_2026 = 'https://raw.githubusercontent.com/nimohunter/spmo_track/main/data/holdings/2026-05-25.json'

@dataclass
class Snap:
    fund: str
    report: pd.Timestamp
    source: str
    weights: Dict[str, float]  # CUSIP -> percentage points, or ticker -> pp if key_mode=ticker
    key_mode: str = 'cusip'


def jina(url: str, tries: int = 5) -> str:
    ju = 'https://r.jina.ai/' + url
    err = None
    for i in range(tries):
        try:
            r = S.get(ju, headers={'User-Agent': UA}, timeout=45)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(5 + 3*i); continue
            r.raise_for_status()
            txt = r.text
            marker = 'Markdown Content:'
            if marker in txt:
                txt = txt.split(marker, 1)[1].lstrip('\r\n ')
            return txt
        except Exception as e:
            err = e; time.sleep(3 + 2*i)
    raise RuntimeError(f'Jina failed {url}: {err}')


def jina_json(url: str):
    t = jina(url)
    # Usually exact JSON after the Jina preamble; tolerate code fences.
    t = t.strip()
    if t.startswith('```'):
        t = re.sub(r'^```(?:json)?\s*', '', t)
        t = re.sub(r'\s*```$', '', t)
    a, b = t.find('{'), t.rfind('}')
    if a < 0 or b < a:
        raise RuntimeError('No JSON object returned for ' + url)
    return json.loads(t[a:b+1])


def sec_archive(cik: str, acc: str) -> str:
    return f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace("-", "")}/primary_doc.xml'


def parse_flat_nport(text: str, fund: str, source: str, report_hint: Optional[str] = None) -> Snap:
    # Jina flattens each N-PORT investment into a line like:
    # 037833100 124636013.00000000 NS USD 21338931785.73 10.81669 Long EC CORP US ...
    pat = re.compile(
        r'(?m)^([A-Z0-9]{9})\s+'
        r'[-+0-9.eE]+\s+NS\s+[A-Z]{3}\s+'
        r'[-+0-9.eE]+\s+([-+0-9.eE]+)\s+'
        r'(Long|Short)\s+EC\b'
    )
    weights: Dict[str, float] = {}
    for m in pat.finditer(text):
        cusip, pct_s, side = m.group(1), m.group(2), m.group(3)
        try: pct = float(pct_s)
        except Exception: continue
        if side != 'Long' or not math.isfinite(pct) or pct <= 0: continue
        weights[cusip] = weights.get(cusip, 0.0) + pct
    if len(weights) < 20:
        raise RuntimeError(f'{fund}: parsed only {len(weights)} equity holdings from {source}')
    if report_hint:
        rep = pd.Timestamp(report_hint)
    else:
        ds = re.findall(r'\b20\d{2}-\d{2}-\d{2}\b', text[:5000])
        if not ds: raise RuntimeError(f'{fund}: no report date in {source}')
        rep = pd.Timestamp(ds[0])
    return Snap(fund, rep, source, weights, 'cusip')


def filing_rows(cik_pad: str) -> List[dict]:
    base_url = f'https://data.sec.gov/submissions/CIK{cik_pad}.json'
    js = jina_json(base_url)
    rows: List[dict] = []
    def add(block):
        if not block: return
        n = len(block.get('form', []))
        for i in range(n):
            rows.append({k: (block.get(k, [None]*n)[i] if i < len(block.get(k, [])) else None)
                         for k in ['form','accessionNumber','filingDate','reportDate','primaryDocument']})
    add(js.get('filings', {}).get('recent', {}))
    # Historical chunks are needed for 2021 in high-filing-count trusts.
    for f in js.get('filings', {}).get('files', []):
        name = f.get('name')
        if not name: continue
        try:
            hist = jina_json('https://data.sec.gov/submissions/' + name)
            add(hist)
            time.sleep(3.2)
        except Exception as e:
            print('WARN historical submissions', name, e)
    return rows


def load_qqq(targets: List[pd.Timestamp]) -> Dict[pd.Timestamp, Snap]:
    rows = filing_rows(QQQ_CIK_PAD)
    by_rep = {}
    for r in rows:
        if r.get('form') == 'NPORT-P' and r.get('reportDate'):
            by_rep.setdefault(r['reportDate'], []).append(r)
    out = {}
    for d in targets:
        rep = (d + pd.offsets.MonthEnd(0)).strftime('%Y-%m-%d')
        cand = by_rep.get(rep, [])
        if not cand:
            raise RuntimeError(f'No QQQ NPORT-P metadata for {rep}')
        r = cand[0]
        acc = r['accessionNumber']
        url = sec_archive(QQQ_CIK_ARCHIVE, acc)
        txt = jina(url)
        snap = parse_flat_nport(txt, 'QQQ', url, rep)
        out[d] = snap
        print('QQQ', d.date(), rep, acc, 'n=', len(snap.weights))
        time.sleep(3.2)
    return out


def discover_spmo_2021() -> Tuple[str, str]:
    rows = filing_rows('0001378872')
    cand = [r for r in rows if r.get('form') == 'NPORT-P' and r.get('reportDate') == '2021-11-30']
    print('SPMO 2021-11 candidates:', len(cand))
    if not cand:
        raise RuntimeError('No trust NPORT-P candidates for 2021-11-30')
    for j, r in enumerate(cand, 1):
        acc = r['accessionNumber']
        url = sec_archive(SPMO_CIK, acc)
        try:
            t = jina(url)
        except Exception as e:
            print('WARN candidate', acc, e); continue
        # The series name is present in the flattened N-PORT header.
        if ('S&P 500 Momentum ETF' in t or 'S000050154' in t or 'Momentum ETF (SPMO)' in t):
            print('DISCOVERED SPMO 2021-11 accession', acc, f'candidate {j}/{len(cand)}')
            return acc, t
        time.sleep(3.2)
    raise RuntimeError('Could not identify SPMO among 2021-11 NPORT-P candidates')


def load_spmo(targets: List[pd.Timestamp]) -> Dict[pd.Timestamp, Snap]:
    out = {}
    acc21 = None; text21 = None
    for d in targets:
        y, m = d.year, d.month
        if m == 9:
            rep = f'{y}-11-30'
        else:
            rep = f'{y}-05-31'
        if rep == '2021-11-30':
            if acc21 is None:
                acc21, text21 = discover_spmo_2021()
            url = sec_archive(SPMO_CIK, acc21)
            out[d] = parse_flat_nport(text21, 'SPMO', url, rep)
        elif rep == '2026-05-31':
            # SEC snapshot was not in the maintained public archive when this research was assembled;
            # use the first late-May live holdings snapshot from the same tracker instead.
            r = S.get(SPMO_LIVE_2026, headers={'User-Agent': UA}, timeout=30); r.raise_for_status()
            js = r.json(); sdate = pd.Timestamp(js['asOfDate'])
            w = {str(h['ticker']).replace('/', '.'): float(h['weight']) for h in js['holdings'] if h.get('ticker') and not str(h['ticker']).startswith('?')}
            out[d] = Snap('SPMO', sdate, SPMO_LIVE_2026, w, 'ticker')
            print('SPMO', d.date(), sdate.date(), 'LIVE n=', len(w))
            continue
        else:
            acc = SPMO_KNOWN.get(rep)
            if not acc:
                raise RuntimeError('Missing known SPMO accession ' + rep)
            url = sec_archive(SPMO_CIK, acc)
            txt = jina(url)
            out[d] = parse_flat_nport(txt, 'SPMO', url, rep)
        print('SPMO', d.date(), out[d].report.date(), 'n=', len(out[d].weights), out[d].source)
        time.sleep(3.2)
    return out


def third_friday(y,m):
    fr = [x for x in calendar.Calendar().itermonthdates(y,m) if x.month==m and x.weekday()==4]
    return pd.Timestamp(fr[2])


def target_dates():
    a=[]
    for y in range(2021,2027):
        for m in (3,9):
            d=third_friday(y,m)
            if pd.Timestamp('2021-09-01') <= d <= pd.Timestamp('2026-03-31'): a.append(d)
    return a


def map_cusips(cusips: List[str]) -> Dict[str, Optional[str]]:
    url='https://api.openfigi.com/v3/mapping'
    h={'Content-Type':'application/json','User-Agent':UA}
    out={c:None for c in cusips}
    for k in range(0,len(cusips),10):
        cs=cusips[k:k+10]
        body=[{'idType':'ID_CUSIP','idValue':c,'exchCode':'US'} for c in cs]
        data=None
        for a in range(8):
            r=S.post(url,headers=h,json=body,timeout=30)
            if r.status_code==429:
                time.sleep(10+5*a); continue
            r.raise_for_status(); data=r.json(); break
        if data is None: continue
        for c,row in zip(cs,data):
            arr=row.get('data') or []
            # Common stock first, then any US equity-like mapping.
            arr=sorted(arr,key=lambda z:(0 if str(z.get('securityType2','')).lower()=='common stock' else 1,
                                         0 if z.get('exchCode') in {'US','UN','UW','UQ','UA','UP'} else 1))
            if arr and arr[0].get('ticker'):
                out[c]=str(arr[0]['ticker']).replace('/','.')
        if k+10<len(cusips): time.sleep(3.0)
    return out


def ysym(t): return t.replace('.','-')


def download_prices(tickers: List[str], start='2021-08-01', end='2026-09-22') -> pd.DataFrame:
    result=[]
    ys=sorted({ysym(t) for t in tickers})
    for k in range(0,len(ys),45):
        chunk=ys[k:k+45]
        got=None
        for a in range(4):
            try:
                x=yf.download(chunk,start=start,end=(pd.Timestamp(end)+pd.Timedelta(days=4)).strftime('%Y-%m-%d'),
                              auto_adjust=False,actions=False,progress=False,threads=True,timeout=40)
                if not x.empty: got=x; break
            except Exception as e: print('WARN yfinance retry',a,e); time.sleep(3)
        if got is None: raise RuntimeError('Price download failed for chunk')
        if len(chunk)==1:
            fld='Adj Close' if 'Adj Close' in got.columns else 'Close'
            z=got[[fld]].rename(columns={fld:chunk[0]})
        else:
            lvl=got.columns.get_level_values(0)
            fld='Adj Close' if 'Adj Close' in lvl else 'Close'
            z=got[fld].copy()
        result.append(z)
        time.sleep(1)
    px=pd.concat(result,axis=1)
    px=px.loc[:,~px.columns.duplicated()].sort_index()
    px.index=pd.to_datetime(px.index).tz_localize(None)
    return px.replace([np.inf,-np.inf],np.nan).ffill()


def near(px,t,d,days=8):
    c=ysym(t)
    if c not in px.columns:return None
    s=px[c].dropna()
    if s.empty:return None
    i=s.index.get_indexer([pd.Timestamp(d)],method='nearest')[0]
    if i<0:return None
    if abs((s.index[i]-pd.Timestamp(d)).days)>days:return None
    v=float(s.iloc[i]); return v if math.isfinite(v) and v>0 else None


def metrics(s):
    s=s.dropna(); yrs=(s.index[-1]-s.index[0]).days/365.2425
    r=s.iloc[-1]/s.iloc[0]
    daily=s.pct_change().dropna(); sd=daily.std(ddof=1)
    return {'start':str(s.index[0].date()),'end':str(s.index[-1].date()),'ending_1usd':float(r),
            'total_return':float(r-1),'CAGR':float(r**(1/yrs)-1),
            'MDD':float((s/s.cummax()-1).min()),
            'annualized_vol':float(sd*math.sqrt(252)),
            'sharpe_rf0':float(daily.mean()/sd*math.sqrt(252)) if sd>0 else None}


def main():
    td=target_dates()
    print('TARGETS',[str(x.date()) for x in td])
    q=load_qqq(td)
    sp=load_spmo(td)

    # Map all QQQ CUSIPs; this also maps all standard-period intersections.
    allcus=sorted({c for x in q.values() for c in x.weights})
    print('OpenFIGI mapping',len(allcus),'QQQ CUSIPs')
    cmap=map_cusips(allcus)
    unresolved=[c for c,v in cmap.items() if not v]
    print('UNRESOLVED',len(unresolved),unresolved)

    # Build ticker-level report weights and candidate intersections.
    period_inputs=[]; tickers={'QQQ','SPMO'}
    for d in td:
        qw={}
        for c,w in q[d].weights.items():
            t=cmap.get(c)
            if t: qw[t]=qw.get(t,0)+w
        if sp[d].key_mode=='cusip':
            sw={}
            for c,w in sp[d].weights.items():
                t=cmap.get(c)  # intersection members generally also appear in QQQ map
                if t: sw[t]=sw.get(t,0)+w
        else:
            sw=dict(sp[d].weights)
        inter=sorted(set(qw)&set(sw))
        print('INTER',d.date(),len(inter),inter)
        if not inter: raise RuntimeError('empty intersection '+str(d.date()))
        tickers.update(inter)
        period_inputs.append((d,qw,sw,inter,q[d].report,sp[d].report))

    print('Downloading prices',len(tickers),'symbols')
    px=download_prices(sorted(tickers))
    qpx=px[ysym('QQQ')].dropna()
    actual=[]
    for d in td:
        ix=qpx.index[qpx.index<=d]
        if not len(ix): raise RuntimeError('no QQQ session '+str(d))
        actual.append(ix[-1])
    end0=pd.Timestamp('2026-09-18')
    ix=qpx.index[qpx.index<=end0]; endd=ix[-1]

    wb={}; holding_rows=[]
    for (sched,qw,sw,inter,qr,sr),d in zip(period_inputs,actual):
        q0,qrp=near(px,'QQQ',d),near(px,'QQQ',qr)
        s0,srp=near(px,'SPMO',d),near(px,'SPMO',sr)
        if None in (q0,qrp,s0,srp): raise RuntimeError('missing ETF price '+str(d))
        qret=qrp/q0; sret=srp/s0
        raw={}; omitted=[]
        for t in inter:
            p0,pq,ps=near(px,t,d),near(px,t,qr),near(px,t,sr)
            if None in (p0,pq,ps): omitted.append(t); continue
            qb=qw[t]*qret/(pq/p0)
            sb=sw[t]*sret/(ps/p0)
            raw[t]=qb+sb
        z=sum(raw.values())
        if z<=0: raise RuntimeError('zero weights '+str(d))
        w={t:v/z for t,v in raw.items()}; wb[d]=w
        print('WEIGHTS',d.date(),'n',len(w),'omit',omitted,'TOP',[(t,round(v*100,2)) for t,v in sorted(w.items(),key=lambda x:-x[1])[:12]])
        for t,v in sorted(w.items(),key=lambda x:-x[1]):
            holding_rows.append({'rebalance_date':str(d.date()),'ticker':t,
                'qqq_report_date':str(qr.date()),'spmo_snapshot_date':str(sr.date()),
                'qqq_report_weight_pct':qw[t],'spmo_report_weight_pct':sw[t],
                'final_weight_pct':v*100})
    pd.DataFrame(holding_rows).to_csv(OUT/'rebalance_holdings.csv',index=False)

    parts=[]; chain=1.0; bounds=actual+[endd]
    period_rows=[]
    for i,d0 in enumerate(actual):
        d1=bounds[i+1]; w=wb[d0]
        idx=qpx.loc[d0:d1].index
        rel={}; good={}
        for t,wt in w.items():
            c=ysym(t)
            if c not in px.columns: continue
            s=px.reindex(idx)[c].ffill()
            if s.empty or pd.isna(s.iloc[0]): continue
            good[t]=wt; rel[t]=s/s.iloc[0]
        z=sum(good.values()); good={t:v/z for t,v in good.items()}
        sleeve=sum(rel[t]*wt for t,wt in good.items())*chain
        if i>0:sleeve=sleeve.iloc[1:]
        parts.append(sleeve); chain=float(sleeve.iloc[-1])
    nav=pd.concat(parts).sort_index(); nav.name='strategy'
    bench=qpx.loc[nav.index.min():nav.index.max()]; bench=bench/bench.iloc[0]; bench=bench.reindex(nav.index).ffill(); bench.name='qqq'
    curves=pd.concat([nav,bench],axis=1); curves.to_csv(OUT/'daily_nav.csv')
    for i in range(len(actual)):
        a,z=bounds[i],bounds[i+1]; ss=nav.loc[a:z]; bb=bench.loc[a:z]
        period_rows.append({'start':str(a.date()),'end':str(z.date()),
            'strategy_return':float(ss.iloc[-1]/ss.iloc[0]-1),'qqq_return':float(bb.iloc[-1]/bb.iloc[0]-1)})
    pd.DataFrame(period_rows).to_csv(OUT/'period_returns.csv',index=False)
    summary={'method':'QQQ∩SPMO; Mar/Sep third-Friday; raw weight=backcast(QQQ report weight)+backcast(SPMO snapshot weight), normalized',
             'strategy':metrics(nav),'QQQ':metrics(bench),'rebalance_dates':[str(x.date()) for x in actual],
             'end_date':str(endd.date()),'period_returns':period_rows,'unresolved_qqq_cusips':unresolved,
             'sources':{str(d.date()):{'qqq':q[d].source,'spmo':sp[d].source,'spmo_key_mode':sp[d].key_mode} for d in td}}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print('\n===RESULT===')
    print(json.dumps(summary,indent=2))
    print('===END===')

if __name__=='__main__': main()
