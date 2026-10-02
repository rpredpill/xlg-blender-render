#!/usr/bin/env python3
from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import mean, median

SRC = Path('quu-leadership-percentile-study.json')
OUT = Path('quu-state-matrix-study.json')
STATES = ['Low+Rising','Low+Falling','High+Rising','High+Falling']


def safe_mean(xs):
    return float(mean(xs)) if xs else None


def safe_median(xs):
    return float(median(xs)) if xs else None


def state_name(row):
    high = float(row['percentile']) >= 50.0
    falling = bool(row['falling'])
    return ('High' if high else 'Low') + ('+Falling' if falling else '+Rising')


def annotate_drawdowns(rows):
    # Drawdown of the risk sleeve itself (QUQU in exact sample, momentum^3 proxy pre-2018),
    # starting at 1.0 at the first classified month. This is descriptive, not a trading rule.
    eq = 1.0
    peak = 1.0
    min_dd = 0.0
    out = []
    for r in rows:
        x = dict(r)
        ret = float(x['riskReturn']) / 100.0
        eq *= 1.0 + ret
        peak = max(peak, eq)
        dd = eq / peak - 1.0
        new_dd_low = dd < min_dd - 1e-12
        if new_dd_low:
            min_dd = dd
        x['riskEquityIndex'] = eq * 100.0
        x['riskDrawdownPct'] = dd * 100.0
        x['newDrawdownLowEvent'] = bool(new_dd_low)
        out.append(x)
    return out


def summarize_state(rows):
    if not rows:
        return {'months': 0}
    excess = [float(r['excess']) for r in rows]
    risk = [float(r['riskReturn']) for r in rows]
    qqq = [float(r['QQQ']) for r in rows]
    dds = [float(r['riskDrawdownPct']) for r in rows]
    n = len(rows)
    return {
        'months': n,
        'avgExcessPctPoints': safe_mean(excess),
        'medianExcessPctPoints': safe_median(excess),
        'QUQUorProxyWinRatePct': 100.0 * sum(x > 0 for x in excess) / n,
        'avgRiskReturnPct': safe_mean(risk),
        'avgQQQReturnPct': safe_mean(qqq),
        'riskPositiveMonthRatePct': 100.0 * sum(x > 0 for x in risk) / n,
        'riskLossLeMinus5PctRatePct': 100.0 * sum(x <= -5.0 for x in risk) / n,
        'riskLossLeMinus10PctRatePct': 100.0 * sum(x <= -10.0 for x in risk) / n,
        'underperformQQQLeMinus5ppRatePct': 100.0 * sum(x <= -5.0 for x in excess) / n,
        'drawdownLeMinus10PctMonthRatePct': 100.0 * sum(x <= -10.0 for x in dds) / n,
        'newDrawdownLowEvents': sum(bool(r['newDrawdownLowEvent']) for r in rows),
        'worstRiskReturnPct': min(risk),
        'worstExcessPctPoints': min(excess),
        'monthsList': [r['month'] for r in rows],
    }


def analyze(sample):
    rows = annotate_drawdowns(sample['rows'])
    buckets = {s: [] for s in STATES}
    for r in rows:
        buckets[state_name(r)].append(r)
    stats = {s: summarize_state(buckets[s]) for s in STATES}
    hr = stats['High+Rising']
    hf = stats['High+Falling']
    lr = stats['Low+Rising']
    lf = stats['Low+Falling']
    contrasts = {
        'highDirectionAvgExcessSpreadPctPoints': (hr.get('avgExcessPctPoints') - hf.get('avgExcessPctPoints')) if hr.get('avgExcessPctPoints') is not None and hf.get('avgExcessPctPoints') is not None else None,
        'lowDirectionAvgExcessSpreadPctPoints': (lr.get('avgExcessPctPoints') - lf.get('avgExcessPctPoints')) if lr.get('avgExcessPctPoints') is not None and lf.get('avgExcessPctPoints') is not None else None,
        'highRisingVsLowRisingAvgExcessSpreadPctPoints': (hr.get('avgExcessPctPoints') - lr.get('avgExcessPctPoints')) if hr.get('avgExcessPctPoints') is not None and lr.get('avgExcessPctPoints') is not None else None,
        'highFallingVsLowFallingAvgExcessSpreadPctPoints': (hf.get('avgExcessPctPoints') - lf.get('avgExcessPctPoints')) if hf.get('avgExcessPctPoints') is not None and lf.get('avgExcessPctPoints') is not None else None,
    }
    return {
        'months': len(rows),
        'stateDefinition': 'High iff prior-only dispersion percentile >=50; Rising iff current dispersion >= immediately prior dispersion; Falling otherwise.',
        'states': stats,
        'contrasts': contrasts,
        'rows': [{
            'month': r['month'], 'percentile': r['percentile'], 'falling': r['falling'],
            'state': state_name(r), 'riskReturn': r['riskReturn'], 'QQQ': r['QQQ'], 'excess': r['excess'],
            'riskDrawdownPct': r['riskDrawdownPct'], 'newDrawdownLowEvent': r['newDrawdownLowEvent']
        } for r in rows],
    }


def main():
    src = json.loads(SRC.read_text(encoding='utf-8'))
    exact = analyze(src['exact2022_2026'])
    proxy = analyze(src['pre2018Proxy'])
    out = {
        'purpose': 'Describe QUQU relative performance by the fixed 2D Leadership State = (Level, Direction). No threshold or return optimization.',
        'definitions': {
            'level': 'High when current momentum-dispersion percentile versus prior months only is >=50; Low otherwise.',
            'direction': 'Rising when current dispersion >= immediately prior month; Falling otherwise.',
            'largeLossDiagnostics': 'Monthly risk-sleeve loss <=-5% / <=-10%, underperformance versus QQQ <=-5 percentage points, and risk-sleeve drawdown <=-10%. These are diagnostics only, not rules.',
            'lookahead': 'None. State labels use signal information available before the allocation month.',
        },
        'exact2022_2026': exact,
        'pre2018Proxy': proxy,
        'crossSampleSignCheck': {
            s: {
                'exactAvgExcessSign': None if exact['states'][s].get('avgExcessPctPoints') is None else ('positive' if exact['states'][s]['avgExcessPctPoints'] > 0 else 'negative' if exact['states'][s]['avgExcessPctPoints'] < 0 else 'zero'),
                'proxyAvgExcessSign': None if proxy['states'][s].get('avgExcessPctPoints') is None else ('positive' if proxy['states'][s]['avgExcessPctPoints'] > 0 else 'negative' if proxy['states'][s]['avgExcessPctPoints'] < 0 else 'zero'),
            } for s in STATES
        },
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({
        'exactStates': exact['states'],
        'exactContrasts': exact['contrasts'],
        'proxyStates': proxy['states'],
        'proxyContrasts': proxy['contrasts'],
        'signs': out['crossSampleSignCheck'],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
