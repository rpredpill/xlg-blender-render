#!/usr/bin/env python3
from __future__ import annotations

import json, math
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import nani_backtest_5y as B

OUT = Path('research/nani_variants_vs_qqq_5y.json')
MR_THRESHOLD = -0.10


def nikkei_members_at(asof: date, current: set[str]) -> set[str]:
    members = set(current)
    for ds, adds, removes in sorted(B.NIKKEI_EVENTS, reverse=True):
        eff = date.fromisoformat(ds)
        if eff > asof:
            members.difference_update(adds)
            members.update(removes)
    if not 224 <= len(members) <= 225:
        raise RuntimeError(f'Nikkei reconstructed count {len(members)} at {asof}')
    return members


def spearman(xs, ys):
    if len(xs) < 20 or len(xs) != len(ys):
        return None
    x = pd.Series(xs).rank(method='average')
    y = pd.Series(ys).rank(method='average')
    rho = x.corr(y)
    return float(rho) if pd.notna(rho) else None


def normalize(raw: dict[str,float], bucket=0.5):
    z = sum(v for v in raw.values() if math.isfinite(v) and v > 0)
    if z <= 0:
        raise RuntimeError('raw sum <= 0')
    return {k: bucket*v/z for k,v in raw.items() if math.isfinite(v) and v > 0}


def port_return(selected, exponent, med, fx0, fx1):
    raw_us, raw_jp, by = {}, {}, {}
    for r in selected:
        rel = r['gross'] / med
        raw = r['floatCap'] * (rel ** exponent)
        by[r['ticker']] = r
        (raw_us if r['country']=='US' else raw_jp)[r['ticker']] = raw
    w = {**normalize(raw_us), **normalize(raw_jp)}
    ret = 0.0
    for t, wt in w.items():
        r = by[t]
        if r['country']=='US': rr = r['p1']/r['p0'] - 1
        else: rr = (r['p1']/fx1)/(r['p0']/fx0) - 1
        ret += wt*rr
    return float(ret), w


def main():
    months=[]; m=B.START_MONTH
    while m<=B.END_MONTH:
        months.append(m); m=B.month_add(m,1)
    assert len(months)==60

    current_jp=B.parse_current_nikkei(); ndx_hist=B.load_ndx_history()
    jp_by={m:nikkei_members_at(B.month_start_date(m),current_jp) for m in months}
    us_by={m:B.ndx_members_at(B.month_start_date(m),ndx_hist) for m in months}
    all_jp=sorted(set().union(*jp_by.values())); all_us=sorted(set().union(*us_by.values()))
    yf_us={s:B.yf_symbol_us(s) for s in all_us}; yf_jp={c:f'{c}.T' for c in all_jp}
    all_yf=sorted(set(yf_us.values())|set(yf_jp.values())|{'JPY=X','QQQ'})
    close=B.download_close(all_yf)
    proxies, methods=B.build_float_proxies(sorted(set(yf_us.values())|set(yf_jp.values())))

    names=['base_m3','mr_off','float_only','m2','qqq']
    rets={k:[] for k in names}; values={k:100.0 for k in names}; curve=[]; details=[]
    missing_total=0; mr_months=[]

    for m in months:
        sig=B.prev_month_end(m); early=B.month_end(B.month_add(m,-6)); end=B.month_end(m)
        one_back=B.month_end(B.month_add(m,-2)); two_back=B.month_end(B.month_add(m,-3))
        fx0=B.last_close(close,'JPY=X',sig); fx1=B.last_close(close,'JPY=X',end)
        if not fx0 or not fx1: raise RuntimeError(f'FX missing {m}')
        candidates=[]; missing=[]
        for s in sorted(us_by[m]):
            ys=yf_us[s]; p0=B.last_close(close,ys,sig); pe=B.last_close(close,ys,early); p1=B.last_close(close,ys,end); pm1=B.last_close(close,ys,one_back); pm2=B.last_close(close,ys,two_back); sh=proxies.get(ys)
            if not all([p0,pe,p1,pm1,pm2,sh]): missing.append((s,'US')); continue
            candidates.append({'ticker':s,'country':'US','p0':p0,'pe':pe,'p1':p1,'pm1':pm1,'pm2':pm2,'floatCap':sh*p0,'gross':p0/pe})
        jp=[]
        for c in sorted(jp_by[m]):
            ys=yf_jp[c]; p0=B.last_close(close,ys,sig); pe=B.last_close(close,ys,early); p1=B.last_close(close,ys,end); pm1=B.last_close(close,ys,one_back); pm2=B.last_close(close,ys,two_back); sh=proxies.get(ys)
            if not all([p0,pe,p1,pm1,pm2,sh]): missing.append((c,'JP')); continue
            jp.append({'ticker':f'{c}.T','country':'JP','p0':p0,'pe':pe,'p1':p1,'pm1':pm1,'pm2':pm2,'floatCap':sh*p0,'gross':p0/pe})
        jp.sort(key=lambda r:r['floatCap'],reverse=True); jp=jp[:100]
        selected=candidates+jp
        if len(jp)<95 or len(candidates)<92: raise RuntimeError(f'Coverage low {m} JP={len(jp)} US={len(candidates)}')
        med=float(np.median([r['gross'] for r in selected]))

        r_prev=[]; r_recent=[]
        for r in selected:
            r_prev.append(r['pm1']/r['pm2']-1)
            r_recent.append(r['p0']/r['pm1']-1)
        rho=spearman(r_prev,r_recent)
        mr=bool(rho is not None and rho<=MR_THRESHOLD)
        if mr: mr_months.append(m)

        base,_=port_return(selected,3,med,fx0,fx1)
        m2,_=port_return(selected,2,med,fx0,fx1)
        fl,_=port_return(selected,0,med,fx0,fx1)
        mrret=fl if mr else base
        q0=B.last_close(close,'QQQ',sig); q1=B.last_close(close,'QQQ',end)
        if not q0 or not q1: raise RuntimeError(f'QQQ missing {m}')
        qr=q1/q0-1
        month_rets={'base_m3':base,'mr_off':mrret,'float_only':fl,'m2':m2,'qqq':float(qr)}
        row={'month':m,'meanReversionRho':rho,'meanReversion':mr}
        for k,v in month_rets.items():
            rets[k].append(float(v)); values[k]*=1+v; row[k]=values[k]
        curve.append(row)
        details.append({'month':m,'rho':rho,'meanReversion':mr,'returns':month_rets,'jpSelected':len(jp),'usSelected':len(candidates),'missingCount':len(missing)})
        missing_total+=len(missing)

    metrics={k:B.metrics(v) for k,v in rets.items()}
    payload={
        'generatedAt':datetime.now(timezone.utc).isoformat(),
        'period':{'startMonth':B.START_MONTH,'endMonth':B.END_MONTH,'months':60},
        'rules':{
            'universe':'PIT Nasdaq-100 + reconstructed PIT Nikkei 225; Japan float-cap top100',
            'countryBuckets':'Japan 50% / US 50%',
            'base_m3':'float-cap proxy × relative 6-1 momentum^3',
            'mr_off':f'base_m3, but if prior two monthly cross-sectional returns have Spearman rho <= {MR_THRESHOLD}, use float-cap only that month',
            'float_only':'float-cap proxy only',
            'm2':'float-cap proxy × relative 6-1 momentum^2',
            'qqq':'QQQ adjusted-price benchmark',
            'rebalance':'monthly, prior month-end signal',
        },
        'caveat':'Historical free-float shares are not fully point-in-time; current/available float-share proxies are held constant through history. Research proxy only.',
        'metrics':metrics,
        'endingValue100':values,
        'meanReversionMonths':mr_months,
        'meanReversionMonthCount':len(mr_months),
        'equityCurve':curve,
        'monthly':details,
        'diagnostics':{
            'uniqueNikkeiTickers':len(all_jp),'uniqueNasdaqTickers':len(all_us),'priceSymbols':len(all_yf),
            'floatProxyCoverage':len(proxies),'floatProxyMethods':{str(k):int(v) for k,v in pd.Series(list(methods.values())).value_counts().items()},
            'candidateMissingOccurrences':missing_total,
        }
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({'metrics':metrics,'endingValue100':values,'meanReversionMonths':mr_months},indent=2))

if __name__=='__main__': main()
