#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import pstdev

import numpy as np
import pandas as pd

# Frozen PIT membership / Yahoo recovery engine.
spec = importlib.util.spec_from_file_location('maxpit','/tmp/quu_max_pit_validation.py')
if spec is None or spec.loader is None:
    raise RuntimeError('cannot load max PIT module')
mp = importlib.util.module_from_spec(spec)
sys.modules['maxpit'] = mp
spec.loader.exec_module(mp)
base = mp.base

# Native adjusted WIKI loader for old/delisted names.
spec2 = importlib.util.spec_from_file_location('preproxy','/tmp/quu_pre2018_proxy_validation.py')
if spec2 is None or spec2.loader is None:
    raise RuntimeError('cannot load pre-2018 proxy module')
pre = importlib.util.module_from_spec(spec2)
sys.modules['preproxy'] = pre
spec2.loader.exec_module(pre)

BUILD_START = '2011-08'   # provides prior-only percentile history before invest start
INVEST_START = '2012-04'  # QQQE live history available; 8 prior monthly observations exist
END = '2026-09'
CAP = 0.20
MIN_COVERAGE = 0.90
OUT = Path('quu-long-proxy-2012-2026.json')


def metrics(rs):
    a = np.asarray(rs, dtype=float) / 100.0
    if not len(a): return None
    eq = np.concatenate([[1.0], np.cumprod(1.0 + a)])
    peak = np.maximum.accumulate(eq)
    dd = eq / peak - 1.0
    cagr = float(eq[-1] ** (12.0 / len(a)) - 1.0)
    mdd = float(dd.min())
    return {
        'months': int(len(a)),
        'totalReturnPct': float((eq[-1]-1.0)*100.0),
        'CAGRpct': cagr*100.0,
        'MDDpct': mdd*100.0,
        'Calmar': cagr/abs(mdd) if mdd < 0 else None,
        'endingIndex': float(eq[-1]*100.0),
    }


def cap_and_redistribute(raw, cap=CAP):
    free=set(raw); out={}; remaining=1.0
    while free:
        total=sum(raw[s] for s in free)
        if not(total>0): raise RuntimeError('raw sum <=0')
        over=[s for s in free if remaining*raw[s]/total > cap+1e-12]
        if not over:
            for s in free: out[s]=remaining*raw[s]/total
            break
        for s in over:
            out[s]=cap; remaining-=cap; free.remove(s)
    return out


def adjusted_month_return(prices, symbol, ym):
    d = prices.get(symbol)
    if d is None or d.empty: return None
    start = pd.Period(ym, freq='M').start_time.normalize()
    end = base.next_month_start(ym)
    g = d[(d.index >= start) & (d.index < end)].copy()
    if g.empty: return None

    # WIKI historical rows have native Adj Open.
    if 'Adj Open' in g.columns:
        gw = g.dropna(subset=['Adj Open','Adj Close'])
        if not gw.empty and gw.index[0] == g.dropna(subset=['Adj Close']).index[0]:
            ao=float(gw.iloc[0]['Adj Open']); ac=float(gw.iloc[-1]['Adj Close'])
            if ao>0 and ac>0 and math.isfinite(ao) and math.isfinite(ac):
                return ac/ao-1.0

    # Yahoo: derive adjusted open using the same-day adjustment factor.
    gy = g.dropna(subset=['Open','Close','Adj Close'])
    if gy.empty: return None
    fr=gy.iloc[0]; lr=gy.iloc[-1]
    o=float(fr['Open']); c=float(fr['Close']); ac0=float(fr['Adj Close']); ac1=float(lr['Adj Close'])
    if not(o>0 and c>0 and ac0>0 and ac1>0): return None
    adj_open=o*(ac0/c)
    if not(adj_open>0 and math.isfinite(adj_open)): return None
    return ac1/adj_open-1.0


def build_month(membership, prices, month):
    signal_date = base.month_end(base.month_add(month,-1))
    recent = signal_date
    early = base.month_end(base.month_add(month,-6))
    universe = base.members_at(membership, signal_date)
    rows=[]; rejected=[]
    for s in universe:
        if s not in prices: continue
        a=base.close_on_or_before(prices,s,recent,'Adj Close')
        b=base.close_on_or_before(prices,s,early,'Adj Close')
        mr=adjusted_month_return(prices,s,month)
        if not(a and b and b>0 and mr is not None): continue
        gross=float(a/b); ret=float(mr)
        # Data-quality guard only: exclude identity/split pathologies, not normal market moves.
        if not(0.10 <= gross <= 6.0) or not(-0.95 <= ret <= 5.0):
            rejected.append({'ticker':s,'gross':gross,'monthlyReturn':ret}); continue
        rows.append({'ticker':s,'gross':gross,'momentum':gross-1.0,'monthlyReturn':ret})
    coverage=len(rows)/max(1,len(universe))
    if coverage < MIN_COVERAGE:
        raise RuntimeError(f'coverage {len(rows)}/{len(universe)}={coverage:.2%}; rejected={rejected[:5]}')
    med=float(np.median([r['gross'] for r in rows]))
    if not med>0: raise RuntimeError('median gross <=0')
    raw={r['ticker']:(r['gross']/med)**3 for r in rows}
    w=cap_and_redistribute(raw)
    proxy=100.0*sum(w[r['ticker']]*r['monthlyReturn'] for r in rows)
    disp=float(pstdev([r['momentum'] for r in rows]))
    return {
        'month':month,'universeCount':len(universe),'holdingsCount':len(rows),
        'coverageRatio':coverage,'rejectedIdentityRows':rejected,
        'dispersion':disp,'proxyReturn':float(proxy),
    }


def percentile_prior(prior, current):
    if not prior: return None
    return 100.0*sum(1 for x in prior if x <= current)/len(prior)


def build_rule(month_rows, qqq, qqqe):
    prior=[]; out=[]
    for x in month_rows:
        d=float(x['dispersion']); prev=prior[-1] if prior else None
        p=percentile_prior(prior,d)
        falling=bool(prev is not None and d < prev)
        if len(prior) < 8:
            # Frozen live warmup convention: median split, overlay disabled.
            med=float(np.median(prior)) if prior else None
            strong=True if med is None else d >= med
            overlay=False
            mode='WARMUP_MEDIAN'
        else:
            strong=bool(p is not None and p >= 50.0)
            overlay=bool(p is not None and p >= 90.0 and falling)
            mode='MR90'
        if overlay:
            sleeve='QQQE'; r=float(qqqe[x['month']])
        elif strong:
            sleeve='PROXY'; r=float(x['proxyReturn'])
        else:
            sleeve='QQQ'; r=float(qqq[x['month']])
        y=dict(x)
        y.update({'priorObservationCount':len(prior),'leadershipPercentile':p,'falling':falling,
                  'leadershipStrong':strong,'meanReversionOverlay':overlay,'decisionMode':mode,
                  'sleeve':sleeve,'QQQ':float(qqq[x['month']]),'QQQE':float(qqqe[x['month']]),'QUUProxy':r})
        out.append(y); prior.append(d)
    return out


def cumulative(rows, key):
    eq=100.0; out=[]
    for x in rows:
        eq*=1.0+float(x[key])/100.0
        out.append(eq)
    return out


def corr(a,b):
    if len(a)<2: return None
    return float(np.corrcoef(np.asarray(a,float),np.asarray(b,float))[0,1])


def main():
    membership=base.load_membership(base.NDX100_URL)
    begin=base.month_end(base.month_add(BUILD_START,-6)); end_signal=base.month_end(base.month_add(END,-1))
    symbols=set(base.members_at(membership,begin))
    for dt,tickers in membership:
        if begin < dt <= end_signal: symbols.update(tickers)
    symbols.update(base.members_at(membership,end_signal)); symbols=sorted(symbols)
    print('PIT union',len(symbols),flush=True)

    start_day=str((pd.Period(BUILD_START,freq='M')-7).start_time.date())
    end_day='2026-10-03'
    prices=base.download_prices(symbols,start_day,end_day)
    recovery=mp.recover_yahoo_prices(prices,symbols,start_day,end_day)
    wiki=pre.load_wiki_adjusted(symbols,start_day)
    merge=mp.merge_wiki(prices,wiki)
    print('prices',len(prices),'wiki',len(wiki),'recovered',len(recovery['direct']),len(recovery['aliases']),flush=True)

    bench=base.download_prices(['QQQ','QQQE'],'2011-01-01',end_day)
    if 'QQQ' not in bench or 'QQQE' not in bench: raise RuntimeError('QQQ/QQQE missing')

    qqq={}; qqqe={}; good=[]; failures={}; m=BUILD_START
    while m<=END:
        try:
            a=adjusted_month_return(bench,'QQQ',m); b=adjusted_month_return(bench,'QQQE',m)
            if a is None: raise RuntimeError('QQQ return missing')
            # QQQE can be absent before launch; only required from INVEST_START onward.
            if m>=INVEST_START and b is None: raise RuntimeError('QQQE return missing')
            qqq[m]=float(a*100.0); qqqe[m]=float(b*100.0) if b is not None else None
            x=build_month(membership,prices,m); good.append(x)
            print('OK',m,f"cov={x['coverageRatio']:.1%}",f"proxy={x['proxyReturn']:+.2f}%",flush=True)
        except Exception as e:
            failures[m]=str(e); print('FAIL',m,e,flush=True)
        m=base.month_add(m,1)

    # Require one continuous series covering the investment window.
    by={x['month']:x for x in good}
    missing=[]; m=INVEST_START
    invest=[]
    while m<=END:
        if m not in by: missing.append(m)
        else: invest.append(by[m])
        m=base.month_add(m,1)
    if missing:
        raise RuntimeError(f'missing investment months: {missing[:20]} total={len(missing)}')

    # Rule needs pre-investment dispersion history, so run from BUILD_START, then slice.
    all_cont=[]; m=BUILD_START
    while m<=END:
        if m not in by: raise RuntimeError(f'prior-history gap {m}: {failures.get(m)}')
        all_cont.append(by[m]); m=base.month_add(m,1)
    # Before QQQE launch overlay cannot fire; set harmless QQQ fallback value for engine warmup.
    qqqe_for_rule={k:(v if v is not None else qqq[k]) for k,v in qqqe.items()}
    ruled_all=build_rule(all_cont,qqq,qqqe_for_rule)
    rows=[x for x in ruled_all if x['month']>=INVEST_START]

    proxy_cum=cumulative(rows,'QUUProxy'); qqq_cum=cumulative(rows,'QQQ')
    for x,a,b in zip(rows,proxy_cum,qqq_cum):
        x['QUUProxyIndex']=a; x['QQQIndex']=b

    # Exact overlap validation from published live QUU history.
    exact_path=Path('quu-history.json')
    overlap=None
    if exact_path.exists():
        exact=json.loads(exact_path.read_text(encoding='utf-8')).get('months') or {}
        common=[x for x in rows if x['month'] in exact and isinstance(exact[x['month']].get('portfolioReturn'),(int,float))]
        if common:
            er=[float(exact[x['month']]['portfolioReturn']) for x in common]
            pr=[float(x['QUUProxy']) for x in common]
            overlap={
                'start':common[0]['month'],'end':common[-1]['month'],'months':len(common),
                'monthlyReturnCorrelation':corr(er,pr),
                'exactMetrics':metrics(er),'proxyMetrics':metrics(pr),
                'meanAbsoluteMonthlyDifferencePctPoints':float(np.mean(np.abs(np.asarray(er)-np.asarray(pr)))),
            }

    # Quarterly chart points, plus first and final monthly observations.
    chart=[]
    for i,x in enumerate(rows):
        mm=int(x['month'][5:7])
        if i==0 or mm in (3,6,9,12) or i==len(rows)-1:
            chart.append({'month':x['month'],'QQQ':x['QQQIndex'],'QUUProxy':x['QUUProxyIndex']})

    out={
        'generatedAt':datetime.now(timezone.utc).isoformat(),
        'purpose':'Continuous long-history proxy for current QUU logic. Historical risk sleeve is RelativeMomentum^3 with 20% cap because clean PIT market caps for delisted Nasdaq-100 constituents are unavailable. This is not exact QUQU before the exact live-history window.',
        'period':{'buildStart':BUILD_START,'investmentStart':INVEST_START,'end':END,'months':len(rows)},
        'rule':{
            'riskSleeve':'PIT Nasdaq-100 RelativeMomentum^3, 20% cap (proxy; no historical market-cap term)',
            'leadership':'prior-only dispersion percentile >=50 => risk proxy; below 50 => QQQ',
            'meanReversion':'prior-only dispersion percentile >=90 AND falling => QQQE',
            'warmup':'first 8 prior observations use expanding median split; QQQE overlay disabled',
            'parametersRetuned':False,
        },
        'dataQuality':{
            'minimumCoverageRatio':min(float(x['coverageRatio']) for x in rows),
            'averageCoverageRatio':float(np.mean([x['coverageRatio'] for x in rows])),
            'failedMonthsOutsideInvestmentOrNone':failures,
            'yahooRecovery':recovery,'wikiMerge':merge,
            'identityGuard':'exclude only gross 6-1 outside 0.10..6.0 or monthly return outside -95%..+500%',
        },
        'metrics':{
            'QUUProxy':metrics([x['QUUProxy'] for x in rows]),
            'QQQ':metrics([x['QQQ'] for x in rows]),
            'AlwaysRiskProxy':metrics([x['proxyReturn'] for x in rows]),
        },
        'sleeveCounts':{s:sum(1 for x in rows if x['sleeve']==s) for s in ['PROXY','QQQ','QQQE']},
        'mr90Months':[{'month':x['month'],'percentile':x['leadershipPercentile'],'falling':x['falling'],'proxyReturn':x['proxyReturn'],'QQQ':x['QQQ'],'QQQE':x['QQQE']} for x in rows if x['meanReversionOverlay']],
        'exactOverlap':overlap,
        'chartQuarterly':chart,
        'months':rows,
    }
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({'period':out['period'],'quality':{'min':out['dataQuality']['minimumCoverageRatio'],'avg':out['dataQuality']['averageCoverageRatio']},'metrics':out['metrics'],'sleeves':out['sleeveCounts'],'mr90':out['mr90Months'],'overlap':overlap},ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__': main()
