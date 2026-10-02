#!/usr/bin/env python3
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

CAP = 0.20


def cap_and_redistribute(raw: dict[str, float], cap: float = CAP) -> dict[str, float]:
    free = set(raw)
    out: dict[str, float] = {}
    remaining = 1.0
    while free:
        total = sum(raw[s] for s in free)
        if not math.isfinite(total) or total <= 0:
            raise RuntimeError('raw sum <= 0')
        over = [s for s in free if remaining * raw[s] / total > cap + 1e-12]
        if not over:
            for s in free:
                out[s] = remaining * raw[s] / total
            break
        for s in over:
            out[s] = cap
            remaining -= cap
            free.remove(s)
    return out


def p2_return(record: dict) -> tuple[float, dict]:
    rows = list(record.get('rows') or [])
    if len(rows) < 90:
        raise RuntimeError(f'not enough rows: {len(rows)}')
    gross = [1.0 + float(r['momentum']) for r in rows if isinstance(r.get('momentum'), (int, float)) and 1.0 + float(r['momentum']) > 0]
    med = float(np.median(gross))
    raw = {}
    eligible = []
    for r in rows:
        t = str(r.get('ticker') or '').strip().upper()
        cap = float(r.get('marketCap') or 0)
        g = 1.0 + float(r.get('momentum') or 0)
        mr = r.get('monthlyReturn')
        if not t or cap <= 0 or g <= 0:
            continue
        score = math.sqrt(cap) * ((g / med) ** 2)
        raw[t] = score
        eligible.append((t, mr))
    if len(raw) < 90:
        raise RuntimeError(f'p2 eligible {len(raw)}')
    w = cap_and_redistribute(raw)
    missing_w = sum(w[t] for t, mr in eligible if not isinstance(mr, (int, float)))
    if missing_w > 0.02:
        raise RuntimeError(f'missing return weight {missing_w:.2%}')
    ret = sum(w[t] * (float(mr) if isinstance(mr, (int, float)) else 0.0) for t, mr in eligible) * 100.0
    top = sorted(w.values(), reverse=True)
    return ret, {'maxWeight': max(top), 'top10Weight': sum(top[:10]), 'missingReturnWeight': missing_w}


def qqq_month_end_closes(start='2021-10-01', end='2026-10-05') -> pd.Series:
    d = yf.download('QQQ', start=start, end=end, auto_adjust=True, actions=False, progress=False, repair=False, threads=False)
    if d.empty:
        raise RuntimeError('QQQ price download empty')
    c = d['Close']
    if isinstance(c, pd.DataFrame):
        c = c.iloc[:, 0]
    c.index = pd.to_datetime(c.index).tz_localize(None)
    return c.resample('ME').last().dropna()


def trend_signal(allocation_month: str, month_closes: pd.Series) -> dict:
    signal_period = pd.Period(allocation_month, freq='M') - 1
    hist = month_closes[month_closes.index.to_period('M') <= signal_period]
    if len(hist) < 10:
        raise RuntimeError(f'{allocation_month}: only {len(hist)} QQQ month closes')
    last10 = hist.iloc[-10:]
    px = float(last10.iloc[-1])
    sma = float(last10.mean())
    return {'on': px > sma, 'signalMonth': str(signal_period), 'qqqClose': px, 'sma10': sma, 'ratio': px / sma}


def metrics(returns_pct: list[float]) -> dict:
    arr = np.asarray(returns_pct, dtype=float) / 100.0
    eq = np.concatenate([[1.0], np.cumprod(1.0 + arr)])
    peaks = np.maximum.accumulate(eq)
    dd = eq / peaks - 1.0
    n = len(arr)
    total = float(eq[-1] - 1.0)
    cagr = float(eq[-1] ** (12.0 / n) - 1.0) if n else float('nan')
    mdd = float(dd.min())
    calmar = cagr / abs(mdd) if mdd < 0 else None
    return {
        'months': n,
        'totalReturnPct': total * 100.0,
        'CAGRpct': cagr * 100.0,
        'MDDpct': mdd * 100.0,
        'Calmar': calmar,
        'positiveMonthRatePct': float((arr > 0).mean() * 100.0),
        'bestMonthPct': float(arr.max() * 100.0),
        'worstMonthPct': float(arr.min() * 100.0),
        'endingIndex': float(eq[-1] * 100.0),
    }


def compound(xs_pct: list[float]) -> float:
    if not xs_pct:
        return 0.0
    return (float(np.prod(1.0 + np.asarray(xs_pct) / 100.0)) - 1.0) * 100.0


def main():
    qhist = json.loads(Path('ququ-history.json').read_text(encoding='utf-8'))
    bench = json.loads(Path('benchmark-history.json').read_text(encoding='utf-8'))
    months = sorted(set(qhist.get('months', {})) & set(bench.get('months', {})))
    months = [m for m in months if isinstance(qhist['months'][m].get('portfolioReturn'), (int, float)) and isinstance(bench['months'][m].get('QQQ'), (int, float))]
    if len(months) < 36:
        raise RuntimeError(f'only {len(months)} common months')

    closes = qqq_month_end_closes(start='2021-10-01', end=str((pd.Period(months[-1], freq='M') + 2).start_time.date()))
    rows = []
    r_p3, r_p2, r_switch, r_qqq, r_spy = [], [], [], [], []
    on_ququ, on_qqq, off_ququ, off_qqq = [], [], [], []

    for m in months:
        rec = qhist['months'][m]
        p3 = float(rec['portfolioReturn'])
        p2, p2meta = p2_return(rec)
        qqq = float(bench['months'][m]['QQQ'])
        spy = float(bench['months'][m]['SPY'])
        sig = trend_signal(m, closes)
        switch = p3 if sig['on'] else qqq
        rows.append({
            'month': m,
            'trendOn': sig['on'],
            'signalMonth': sig['signalMonth'],
            'qqqClose': sig['qqqClose'],
            'qqqSMA10': sig['sma10'],
            'qqqVsSMA': sig['ratio'],
            'QUQU_p3': p3,
            'QUQU_p2': p2,
            'QUQU_QQQ_10M': switch,
            'QQQ': qqq,
            'SPY': spy,
            'p2MaxWeight': p2meta['maxWeight'],
            'p2Top10Weight': p2meta['top10Weight'],
        })
        r_p3.append(p3); r_p2.append(p2); r_switch.append(switch); r_qqq.append(qqq); r_spy.append(spy)
        if sig['on']:
            on_ququ.append(p3); on_qqq.append(qqq)
        else:
            off_ququ.append(p3); off_qqq.append(qqq)

    variants = {
        'QUQU p=3': metrics(r_p3),
        'QUQU p=2': metrics(r_p2),
        'QUQU/QQQ 10M': metrics(r_switch),
        'QQQ': metrics(r_qqq),
        'SPY': metrics(r_spy),
    }
    for name, rs in [('QUQU p=3', r_p3), ('QUQU p=2', r_p2), ('QUQU/QQQ 10M', r_switch)]:
        variants[name]['winRateVsQQQpct'] = float(np.mean(np.asarray(rs) > np.asarray(r_qqq)) * 100.0)
        variants[name]['winRateVsSPYpct'] = float(np.mean(np.asarray(rs) > np.asarray(r_spy)) * 100.0)

    regime = {
        'trendOnMonths': len(on_ququ),
        'trendOffMonths': len(off_ququ),
        'trendOn': {
            'QUQUcompoundPct': compound(on_ququ),
            'QQQcompoundPct': compound(on_qqq),
            'QUQUavgMonthPct': float(np.mean(on_ququ)) if on_ququ else None,
            'QQQavgMonthPct': float(np.mean(on_qqq)) if on_qqq else None,
            'QUQUwinVsQQQpct': float(np.mean(np.asarray(on_ququ) > np.asarray(on_qqq)) * 100.0) if on_ququ else None,
        },
        'trendOff': {
            'QUQUcompoundPct': compound(off_ququ),
            'QQQcompoundPct': compound(off_qqq),
            'QUQUavgMonthPct': float(np.mean(off_ququ)) if off_ququ else None,
            'QQQavgMonthPct': float(np.mean(off_qqq)) if off_qqq else None,
            'QUQUwinVsQQQpct': float(np.mean(np.asarray(off_ququ) > np.asarray(off_qqq)) * 100.0) if off_ququ else None,
        },
    }

    out = {
        'generatedAt': datetime.now(timezone.utc).isoformat(),
        'period': {'start': months[0], 'end': months[-1], 'months': len(months)},
        'rules': {
            'p3': 'Existing QUQU: sqrt(MarketCap) × RelativeMomentum^3, 20% cap',
            'p2': 'sqrt(MarketCap) × RelativeMomentum^2, 20% cap',
            'regime': 'For allocation month t, if prior month-end QQQ adjusted close > prior 10 month-end closes SMA, hold QUQU p=3; otherwise hold QQQ',
            'lookahead': 'Signal uses only information available at the prior month end',
        },
        'variants': variants,
        'regimeBreakdown': regime,
        'months': rows,
    }
    Path('ququ-variant-comparison.json').write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({'period': out['period'], 'variants': variants, 'regimeBreakdown': regime}, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
