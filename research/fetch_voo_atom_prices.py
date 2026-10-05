#!/usr/bin/env python3
from pathlib import Path
from urllib.parse import urlencode, quote
from difflib import SequenceMatcher
import urllib.request, urllib.error, xml.etree.ElementTree as ET
import pandas as pd, numpy as np, json, time, re
import requests, yfinance as yf

OUT=Path('research/voo_data_output'); OUT.mkdir(parents=True,exist_ok=True)
CIK='36405'; TARGET='Vanguard 500 Index Fund'
START_FILE=pd.Timestamp('2021-04-01')
UA='Mozilla/5.0'
ATOM_NS={'a':'http://www.w3.org/2005/Atom'}

def fetch_bytes(url,tries=4):
    last=None
    for k in range(tries):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Encoding':'identity'})
            with urllib.request.urlopen(req,timeout=45) as r: return r.read()
        except Exception as e:
            last=e; time.sleep(1.5*(k+1))
    raise last

def tx(node,tag):
    e=node.find('.//{*}'+tag)
    return (e.text or '').strip() if e is not None and e.text else ''

def atom_jobs():
    jobs=[]
    for start in range(0,1000,100):
        q=urlencode({'action':'getcompany','CIK':CIK,'type':'NPORT-P','owner':'exclude','count':'100','start':str(start),'output':'atom'})
        url='https://www.sec.gov/cgi-bin/browse-edgar?'+q
        print('ATOM',start,flush=True)
        b=fetch_bytes(url)
        (OUT/f'atom_{start}.xml').write_bytes(b)
        root=ET.fromstring(b)
        entries=root.findall('a:entry',ATOM_NS)
        if not entries: break
        oldest=None
        for e in entries:
            acc=tx(e,'accession-number'); filed=tx(e,'filing-date')
            if not acc or not filed: continue
            dt=pd.Timestamp(filed); oldest=dt if oldest is None else min(oldest,dt)
            if dt>=START_FILE: jobs.append({'accession':acc,'filed':filed})
        if oldest is not None and oldest<START_FILE: break
        time.sleep(.4)
    return pd.DataFrame(jobs).drop_duplicates('accession').sort_values('filed')

def parse_voo(job):
    acc=job['accession']; acc0=acc.replace('-','')
    url=f'https://www.sec.gov/Archives/edgar/data/{CIK}/{acc0}/primary_doc.xml'
    try: b=fetch_bytes(url)
    except Exception as e:
        print('XMLFAIL',acc,repr(e),flush=True); return None
    try: root=ET.fromstring(b)
    except Exception as e:
        print('PARSEFAIL',acc,repr(e),flush=True); return None
    series=tx(root,'seriesName')
    if TARGET.lower() not in series.lower(): return None
    report=tx(root,'repPdDate') or tx(root,'repPdEnd')
    hs=[]
    for x in root.findall('.//{*}invstOrSec'):
        if tx(x,'assetCat') not in ('EC',''): continue
        try: w=float(tx(x,'pctVal')); v=float(tx(x,'valUSD'))
        except: continue
        if w<=0 or v<=0: continue
        ids=x.find('.//{*}identifiers'); isin=''
        if ids is not None:
            ie=ids.find('{*}isin'); isin=ie.get('value','') if ie is not None else ''
        hs.append({'report':report,'filed':job['filed'],'accession':acc,'source':url,'name':tx(x,'name'),'title':tx(x,'title'),'cusip':tx(x,'cusip'),'isin':isin,'weight':w,'valUSD':v})
    print('VOO',report,len(hs),flush=True)
    return hs

def norm(s):
    s=(s or '').lower().replace('&',' and ')
    s=re.sub(r'[^a-z0-9]+',' ',s)
    return re.sub(r'\s+',' ',s).strip()

OV={
'apple':'AAPL','microsoft':'MSFT','amazon com':'AMZN','nvidia':'NVDA','meta platforms':'META','facebook':'META','tesla':'TSLA','broadcom':'AVGO','jpmorgan chase':'JPM','visa':'V','mastercard':'MA','eli lilly':'LLY','exxon mobil':'XOM','unitedhealth':'UNH','costco':'COST','walmart':'WMT','johnson and johnson':'JNJ','procter and gamble':'PG','oracle':'ORCL','home depot':'HD','abbvie':'ABBV','bank of america':'BAC','coca cola':'KO','netflix':'NFLX','salesforce':'CRM','merck':'MRK','chevron':'CVX','advanced micro devices':'AMD','cisco systems':'CSCO','accenture':'ACN','general electric':'GE','international business machines':'IBM','mcdonald':'MCD','thermo fisher':'TMO','wells fargo':'WFC','linde':'LIN','philip morris':'PM','abbott laboratories':'ABT','goldman sachs':'GS','caterpillar':'CAT','qualcomm':'QCOM','walt disney':'DIS','raytheon technologies':'RTX','rtx':'RTX','verizon':'VZ','at and t':'T','intuitive surgical':'ISRG','servicenow':'NOW','intuit':'INTU','texas instruments':'TXN','american express':'AXP','s and p global':'SPGI','booking holdings':'BKNG','pepsico':'PEP','amgen':'AMGN','danaher':'DHR','blackrock':'BLK','pfizer':'PFE','nextera energy':'NEE','lowe':'LOW','micron technology':'MU','comcast':'CMCSA','uber technologies':'UBER','palantir':'PLTR','charles schwab':'SCHW','union pacific':'UNP','honeywell':'HON','applied materials':'AMAT','lockheed martin':'LMT','starbucks':'SBUX','boeing':'BA','deere':'DE','stryker':'SYK','boston scientific':'BSX','progressive':'PGR','automatic data processing':'ADP','analog devices':'ADI','marsh and mclennan':'MMC','gilead sciences':'GILD','vertex pharmaceuticals':'VRTX','prologis':'PLD','eaton':'ETN','southern':'SO','duke energy':'DUK','chipotle':'CMG','kkr':'KKR','crowdstrike':'CRWD','capital one':'COF','conocophillips':'COP','3m':'MMM','cvs health':'CVS','target':'TGT','cigna':'CI','mondelez':'MDLZ','cme group':'CME','aon':'AON','colgate palmolive':'CL','regeneron':'REGN','air products':'APD','illinois tool works':'ITW','moody':'MCO','equinix':'EQIX','lam research':'LRCX','kla':'KLAC','marvell technology':'MRVL','fortinet':'FTNT','palo alto networks':'PANW'}

def map_symbol(name,title):
    raw=((title or '')+' '+(name or '')).lower(); n=norm(raw)
    if 'alphabet' in n: return ('GOOGL' if 'class a' in raw else 'GOOG'),'override',1.0
    if 'berkshire hathaway' in n: return 'BRK-B','override',1.0
    for k,v in OV.items():
        if k in n: return v,'override',1.0
    try:
        u='https://query1.finance.yahoo.com/v1/finance/search?q='+quote(title or name)+'&quotesCount=10&newsCount=0'
        j=requests.get(u,headers={'User-Agent':UA},timeout=30).json(); target=norm(title or name); cand=[]
        for q in j.get('quotes',[]):
            if q.get('quoteType')!='EQUITY': continue
            sym=q.get('symbol',''); nm=q.get('longname') or q.get('shortname') or ''
            if not sym: continue
            sc=SequenceMatcher(None,target,norm(nm)).ratio()
            if q.get('exchange') in ('NYQ','NMS','NGM','NCM','ASE','PCX'): sc+=.05
            cand.append((sc,sym.replace('.','-')))
        if cand:
            cand.sort(reverse=True); return cand[0][1],'yahoo',cand[0][0]
    except Exception as e: print('MAPFAIL',name,repr(e),flush=True)
    return None,'unmapped',0.0

def main():
    jobs=atom_jobs(); jobs.to_csv(OUT/'nport_candidates.csv',index=False); print('jobs',len(jobs),flush=True)
    snaps=[]
    for _,j in jobs.sort_values('filed',ascending=False).iterrows():
        hs=parse_voo(j)
        if hs: snaps.append(hs)
        time.sleep(.13)
    if not snaps: raise RuntimeError('no VOO snapshots')
    h=pd.concat([pd.DataFrame(x) for x in snaps],ignore_index=True)
    h=h.sort_values(['report','weight'],ascending=[True,False]); h['rank']=h.groupby('report').cumcount()+1
    h.to_csv(OUT/'voo_holdings_all.csv',index=False)
    top=h[h['rank']<=80].copy()
    keys=top[['name','title','cusip','isin']].drop_duplicates().reset_index(drop=True)
    maps=[]
    for i,r in keys.iterrows():
        t,m,s=map_symbol(r['name'],r['title']); maps.append({**r.to_dict(),'ticker':t,'map_method':m,'map_score':s})
        if i%20==0: print('map',i,len(keys),flush=True)
        time.sleep(.05)
    mp=pd.DataFrame(maps); mp.to_csv(OUT/'ticker_map.csv',index=False)
    top=top.merge(mp,on=['name','title','cusip','isin'],how='left'); top.to_csv(OUT/'voo_top80_mapped.csv',index=False)
    tickers=sorted(set(top.ticker.dropna().astype(str))|{'VOO'})
    prs=[]; status={}
    for i,t in enumerate(tickers):
        try:
            d=yf.download(t,start='2021-03-01',end='2026-09-29',auto_adjust=False,progress=False,threads=False,timeout=60)
            if d.empty: status[t]='empty'; continue
            if isinstance(d.columns,pd.MultiIndex):
                c=d['Close'].iloc[:,0]; a=d['Adj Close'].iloc[:,0] if 'Adj Close' in d.columns.get_level_values(0) else c
            else:
                c=d['Close']; a=d['Adj Close'] if 'Adj Close' in d.columns else c
            prs.append(pd.DataFrame({'date':d.index,'ticker':t,'close':c.values,'adj_close':a.values}).dropna(subset=['close']))
            status[t]=f'ok:{len(d)}'
        except Exception as e: status[t]='error:'+repr(e)
        if i%20==0: print('price',i,len(tickers),flush=True)
    p=pd.concat(prs,ignore_index=True) if prs else pd.DataFrame(columns=['date','ticker','close','adj_close']); p.to_csv(OUT/'prices_long.csv',index=False)
    cov=[]
    for report,g in top.groupby('report'):
        g=g[g['rank']<=50]; cov.append({'report':report,'top50_count':len(g),'mapped':int(g.ticker.notna().sum()),'mapped_weight':float(g.loc[g.ticker.notna(),'weight'].sum()),'total_weight':float(g.weight.sum())})
    pd.DataFrame(cov).to_csv(OUT/'coverage.csv',index=False)
    (OUT/'status.json').write_text(json.dumps({'reports':sorted(h.report.unique().tolist()),'snapshots':int(h.report.nunique()),'mapping_rows':len(mp),'mapped':int(mp.ticker.notna().sum()),'price_status':status},indent=2),encoding='utf-8')
    print('DONE snapshots',h.report.nunique(),'mapped',mp.ticker.notna().mean(),'prices',p.ticker.nunique(),flush=True)

if __name__=='__main__': main()
