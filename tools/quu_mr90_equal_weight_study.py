#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import yfinance as yf

SRC = Path('quu-leadership-percentile-study.json')
OUT = Path('quu-mr90-equal-weight-study.json')


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
        start='2012-01-01',
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


def analyze(sample, qqqe_monthly):
    rows = sample['rows']
    baseline_returns = []
    mr90_qqq_returns = []
    mr90_qqqe_returns = []
    qqqe_all = []
    interventions = []

    for r in rows:
        month = r['month']
        p = float(r['percentile'])
        falling = bool(r['falling'])
        risk_ret = float(r['riskReturn'])
        qqq_ret = float(r['QQQ'])
        qqqe_ret = qqqe_monthly.get(month)

        baseline = risk_ret if p >= 50.0 else qqq_ret
        trigger = p >= 90.0 and falling
        mr_qqq = qqq_ret if trigger else baseline
        if trigger:
            if qqqe_ret is None:
                raise RuntimeError(f'QQQE return missing for MR90 intervention month {month}')
            mr_qqqe = float(qqqe_ret)
            interventions.append({
                'month': month,
                'percentile': p,
                'falling': falling,
                'riskReturnPct': risk_ret,
                'QQQReturnPct': qqq_ret,
                'QQQEReturnPct': float(qqqe_ret),
                'QQQEsavedVsRiskPctPoints': float(qqqe_ret) - risk_ret,
                'QQQEsavedVsQQQPctPoints': float(qqqe_ret) - qqq_ret,
                'betterDefensiveSleeve': 'QQQE' if float(qqqe_ret) > qqq_ret else ('QQQ' if float(qqqe_ret) < qqq_ret else 'TIE'),
            })
        else:
            mr_qqqe = baseline

        baseline_returns.append(baseline)
        mr90_qqq_returns.append(mr_qqq)
        mr90_qqqe_returns.append(mr_qqqe)
        if qqqe_ret is not None:
            qqqe_all.append(float(qqqe_ret))

    return {
        'period': {'start': rows[0]['month'], 'end': rows[-1]['month'], 'months': len(rows)},
        'strategies': {
            'LeadershipBaseline': metrics(baseline_returns),
            'MR90_QQQ': metrics(mr90_qqq_returns),
            'MR90_QQQE': metrics(mr90_qqqe_returns),
        },
        'interventions': {
            'count': len(interventions),
            'rows': interventions,
            'QQQEwinsVsQQQ': sum(x['QQQEsavedVsQQQPctPoints'] > 0 for x in interventions),
            'QQQwinsVsQQQE': sum(x['QQQEsavedVsQQQPctPoints'] < 0 for x in interventions),
            'ties': sum(x['QQQEsavedVsQQQPctPoints'] == 0 for x in interventions),
            'avgQQQEminusQQQPctPoints': (sum(x['QQQEsavedVsQQQPctPoints'] for x in interventions) / len(interventions)) if interventions else None,
            'sumQQQEminusQQQPctPoints': sum(x['QQQEsavedVsQQQPctPoints'] for x in interventions),
            'avgQQQEminusRiskPctPoints': (sum(x['QQQEsavedVsRiskPctPoints'] for x in interventions) / len(interventions)) if interventions else None,
        },
    }


def main():
    src = json.loads(SRC.read_text(encoding='utf-8'))
    qqqe = monthly_returns('QQQE')

    exact = analyze(src['exact2022_2026'], qqqe)
    pre = analyze(src['pre2018Proxy'], qqqe)

    all_events = exact['interventions']['rows'] + pre['interventions']['rows']
    combined = {
        'count': len(all_events),
        'QQQEwinsVsQQQ': sum(x['QQQEsavedVsQQQPctPoints'] > 0 for x in all_events),
        'QQQwinsVsQQQE': sum(x['QQQEsavedVsQQQPctPoints'] < 0 for x in all_events),
        'ties': sum(x['QQQEsavedVsQQQPctPoints'] == 0 for x in all_events),
        'avgQQQEminusQQQPctPoints': (sum(x['QQQEsavedVsQQQPctPoints'] for x in all_events) / len(all_events)) if all_events else None,
        'medianQQQEminusQQQPctPoints': float(pd.Series([x['QQQEsavedVsQQQPctPoints'] for x in all_events]).median()) if all_events else None,
        'events': sorted(all_events, key=lambda x: x['month']),
    }

    out = {
        'purpose': 'Test Nasdaq-100 equal weight (QQQE) as the defensive sleeve only when the already-frozen MR90 condition fires. MR90 signal definition is unchanged; this study changes only the intervention sleeve.',
        'rule': {
            'baseline': 'Prior-only leadership percentile >=50 => risk sleeve (QUQU in exact sample / Momentum^3 proxy pre-2018); otherwise QQQ.',
            'mr90Trigger': 'Prior-only leadership percentile >=90 AND dispersion falling versus immediately prior month.',
            'MR90_QQQ': 'Use QQQ on MR90 trigger months.',
            'MR90_QQQE': 'Use QQQE on the exact same MR90 trigger months; all other months identical to LeadershipBaseline.',
            'thresholdOptimization': 'None. 50/90 and falling are unchanged from the frozen MR90 study.',
        },
        'priceMethod': 'Yahoo Finance auto-adjusted daily prices; monthly return = adjusted first trading-day Open to adjusted last trading-day Close.',
        'exact2022_2026': exact,
        'pre2018Proxy': pre,
        'combinedInterventionOnly': combined,
        'limitations': [
            'Only five MR90 interventions exist across the two samples, so event counts are very small.',
            'Pre-2018 risk sleeve is Momentum^3 proxy, not exact market-cap-weighted QUQU.',
            'QQQE is evaluated as a replacement sleeve only on already-defined MR90 intervention months; no attempt is made to optimize an equal-weight threshold or holding period.',
            'This is a research study and does not modify live QUU or the frozen MR90 OOS shadow rule.',
        ],
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
