#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import yfinance as yf

SRC = Path('quu-leadership-percentile-study.json')
OUT = Path('quu-breadth-confirmation-study.json')


def metrics(returns):
    vals = [float(x) for x in returns]
    eq = 1.0
    peak = 1.0
    mdd = 0.0
    for r in vals:
        eq *= 1.0 + r / 100.0
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1.0)
    n = len(vals)
    cagr = (eq ** (12.0 / n) - 1.0) * 100.0 if n else None
    mdd_pct = mdd * 100.0 if n else None
    calmar = cagr / abs(mdd_pct) if n and mdd_pct < 0 else None
    return {
        'months': n,
        'totalReturnPct': (eq - 1.0) * 100.0 if n else None,
        'CAGRpct': cagr,
        'MDDpct': mdd_pct,
        'Calmar': calmar,
        'endingIndex': eq * 100.0 if n else None,
    }


def monthly_returns(symbol: str):
    df = yf.download(
        symbol,
        start='2011-01-01',
        end='2026-10-05',
        interval='1d',
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    if df is None or df.empty:
        raise RuntimeError(f'No price history for {symbol}')
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if 'Open' not in df.columns or 'Close' not in df.columns:
        raise RuntimeError(f'Missing Open/Close for {symbol}: {list(df.columns)}')
    df = df[['Open', 'Close']].dropna().copy()
    df.index = pd.to_datetime(df.index)
    out = {}
    for period, g in df.groupby(df.index.to_period('M')):
        if g.empty:
            continue
        first_open = float(g.iloc[0]['Open'])
        last_close = float(g.iloc[-1]['Close'])
        if first_open > 0:
            out[str(period)] = (last_close / first_open - 1.0) * 100.0
    return out


def prev_month(ym: str) -> str:
    p = pd.Period(ym, freq='M') - 1
    return str(p)


def summary(events):
    if not events:
        return {
            'count': 0,
            'avgRiskReturnPct': None,
            'avgQQQReturnPct': None,
            'avgQQQEReturnPct': None,
            'avgRiskMinusQQQPctPoints': None,
            'avgRiskMinusQQQEPctPoints': None,
            'QQQEwinsVsQQQ': 0,
            'riskWinsVsQQQ': 0,
            'months': [],
        }
    n = len(events)
    return {
        'count': n,
        'avgRiskReturnPct': sum(x['riskReturnPct'] for x in events) / n,
        'avgQQQReturnPct': sum(x['QQQReturnPct'] for x in events) / n,
        'avgQQQEReturnPct': sum(x['QQQEReturnPct'] for x in events) / n,
        'avgRiskMinusQQQPctPoints': sum(x['riskReturnPct'] - x['QQQReturnPct'] for x in events) / n,
        'avgRiskMinusQQQEPctPoints': sum(x['riskReturnPct'] - x['QQQEReturnPct'] for x in events) / n,
        'QQQEwinsVsQQQ': sum(x['QQQEReturnPct'] > x['QQQReturnPct'] for x in events),
        'riskWinsVsQQQ': sum(x['riskReturnPct'] > x['QQQReturnPct'] for x in events),
        'months': [x['month'] for x in events],
    }


def analyze(sample, qqq_monthly, qqqe_monthly):
    rows = sample['rows']
    leadership_returns = []
    current_quu_returns = []
    confirm_only_returns = []
    breadth_route_returns = []
    events = []

    for r in rows:
        month = r['month']
        p = float(r['percentile'])
        falling = bool(r['falling'])
        risk = float(r['riskReturn'])
        qqq = float(r['QQQ'])
        qqqe = qqqe_monthly.get(month)
        if qqqe is None:
            raise RuntimeError(f'QQQE missing for allocation month {month}')

        baseline = risk if p >= 50.0 else qqq
        mr90 = p >= 90.0 and falling

        pm = prev_month(month)
        prior_qqq = qqq_monthly.get(pm)
        prior_qqqe = qqqe_monthly.get(pm)
        breadth = None if prior_qqq is None or prior_qqqe is None else float(prior_qqqe) - float(prior_qqq)
        confirmed = bool(breadth is not None and breadth > 0.0)

        # Current live QUU: MR90 always routes to QQQE.
        current = float(qqqe) if mr90 else baseline
        # Confirmation-only intervention: if breadth is not confirmed, leave LeadershipBaseline untouched.
        confirm_only = float(qqqe) if (mr90 and confirmed) else baseline
        # Breadth-routing candidate: MR90 always defensive; use QQQE only when breadth is confirmed, otherwise QQQ.
        breadth_route = (float(qqqe) if confirmed else qqq) if mr90 else baseline

        leadership_returns.append(baseline)
        current_quu_returns.append(current)
        confirm_only_returns.append(confirm_only)
        breadth_route_returns.append(breadth_route)

        if mr90:
            if breadth is None:
                raise RuntimeError(f'Prior-month breadth unavailable for MR90 event {month}')
            events.append({
                'month': month,
                'percentile': p,
                'falling': falling,
                'priorMonth': pm,
                'priorQQQReturnPct': float(prior_qqq),
                'priorQQQEReturnPct': float(prior_qqqe),
                'priorBreadthQQQEminusQQQPctPoints': float(breadth),
                'breadthConfirmed': confirmed,
                'riskReturnPct': risk,
                'QQQReturnPct': qqq,
                'QQQEReturnPct': float(qqqe),
                'bestThisMonth': max([('RISK', risk), ('QQQ', qqq), ('QQQE', float(qqqe))], key=lambda x: x[1])[0],
            })

    confirmed_events = [x for x in events if x['breadthConfirmed']]
    unconfirmed_events = [x for x in events if not x['breadthConfirmed']]

    return {
        'period': {'start': rows[0]['month'], 'end': rows[-1]['month'], 'months': len(rows)},
        'strategies': {
            'LeadershipBaseline': metrics(leadership_returns),
            'CurrentQUU_MR90_QQQE': metrics(current_quu_returns),
            'MR90_QQQE_only_if_breadth_confirmed': metrics(confirm_only_returns),
            'MR90_breadth_route_QQQE_else_QQQ': metrics(breadth_route_returns),
        },
        'mr90Events': {
            'count': len(events),
            'confirmedCount': len(confirmed_events),
            'unconfirmedCount': len(unconfirmed_events),
            'confirmed': summary(confirmed_events),
            'unconfirmed': summary(unconfirmed_events),
            'rows': events,
        },
    }


def main():
    src = json.loads(SRC.read_text(encoding='utf-8'))
    qqq = monthly_returns('QQQ')
    qqqe = monthly_returns('QQQE')

    exact = analyze(src['exact2022_2026'], qqq, qqqe)
    pre = analyze(src['pre2018Proxy'], qqq, qqqe)
    all_events = exact['mr90Events']['rows'] + pre['mr90Events']['rows']
    confirmed = [x for x in all_events if x['breadthConfirmed']]
    unconfirmed = [x for x in all_events if not x['breadthConfirmed']]

    out = {
        'purpose': 'Test whether already-known prior-month Nasdaq-100 equal-weight breadth confirms MR90 mean reversion. No threshold optimization.',
        'definition': {
            'mr90': 'Prior-only leadership percentile >=90 AND dispersion falling versus immediately prior dispersion.',
            'breadth': 'Previous calendar month QQQE return minus QQQ return. This is fully known before the allocation month begins.',
            'confirmed': 'Previous-month QQQE - QQQ > 0 percentage points.',
            'lookahead': 'None. Allocation-month returns are outcomes only.',
        },
        'candidateRules': {
            'CurrentQUU_MR90_QQQE': 'MR90 => QQQE; otherwise LeadershipBaseline.',
            'ConfirmOnly': 'MR90 AND prior-month breadth >0 => QQQE; otherwise LeadershipBaseline.',
            'BreadthRoute': 'MR90 AND breadth >0 => QQQE; MR90 AND breadth <=0 => QQQ; otherwise LeadershipBaseline.',
        },
        'priceMethod': 'Yahoo Finance auto-adjusted daily prices; monthly return = first trading-day Open to last trading-day Close.',
        'exact2022_2026': exact,
        'pre2018Proxy': pre,
        'combinedEventOnly': {
            'count': len(all_events),
            'confirmedCount': len(confirmed),
            'unconfirmedCount': len(unconfirmed),
            'confirmed': summary(confirmed),
            'unconfirmed': summary(unconfirmed),
            'events': sorted(all_events, key=lambda x: x['month']),
        },
        'limitations': [
            'MR90 event count remains extremely small, so any apparent breadth-confirmation effect can be unstable.',
            'Pre-2018 risk sleeve is Momentum^3 proxy rather than exact market-cap-weighted QUQU.',
            'The zero threshold for QQQE-QQQ is prespecified and not optimized.',
            'This study does not modify live QUU.',
        ],
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
