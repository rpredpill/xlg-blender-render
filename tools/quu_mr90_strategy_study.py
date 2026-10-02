#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

SRC = Path('quu-leadership-percentile-study.json')
OVERFIT = Path('quu-overfit-validation.json')
PROXY = Path('quu-pre2018-proxy-validation.json')
OUT = Path('quu-mr90-strategy-study.json')


def metrics(returns):
    eq = 1.0
    peak = 1.0
    mdd = 0.0
    for r in returns:
        eq *= 1.0 + float(r) / 100.0
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1.0)
    n = len(returns)
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


def summarize_sleeves(rows, sleeves):
    rets = []
    transitions = 0
    prev = None
    for r, s in zip(rows, sleeves):
        rets.append(float(r['riskReturn']) if s == 'RISK' else float(r['QQQ']))
        if prev is not None and s != prev:
            transitions += 1
        prev = s
    out = metrics(rets)
    out['riskMonths'] = sum(s == 'RISK' for s in sleeves)
    out['qqqMonths'] = sum(s == 'QQQ' for s in sleeves)
    out['sleeveTransitions'] = transitions
    return out


def intervention_diagnostics(rows, baseline_sleeves, overlay_sleeves):
    idxs = [i for i, (b, o) in enumerate(zip(baseline_sleeves, overlay_sleeves)) if b == 'RISK' and o == 'QQQ']
    interventions = []
    overlay_returns = [float(r['riskReturn']) if s == 'RISK' else float(r['QQQ']) for r, s in zip(rows, overlay_sleeves)]
    for i in idxs:
        r = rows[i]
        saved = float(r['QQQ']) - float(r['riskReturn'])
        interventions.append({
            'month': r['month'],
            'percentile': float(r['percentile']),
            'falling': bool(r['falling']),
            'riskReturnPct': float(r['riskReturn']),
            'QQQReturnPct': float(r['QQQ']),
            'savedPctPoints': saved,
        })
    loo = []
    for i in idxs:
        test = list(overlay_returns)
        test[i] = float(rows[i]['riskReturn'])
        loo.append({'removedIntervention': rows[i]['month'], 'metrics': metrics(test)})
    largest = max(interventions, key=lambda x: x['savedPctPoints']) if interventions else None
    return {
        'count': len(interventions),
        'interventions': interventions,
        'sumSavedPctPoints': sum(x['savedPctPoints'] for x in interventions),
        'positiveSavedCount': sum(x['savedPctPoints'] > 0 for x in interventions),
        'negativeSavedCount': sum(x['savedPctPoints'] < 0 for x in interventions),
        'largestSavedIntervention': largest,
        'leaveOneInterventionOut': loo,
    }


def analyze(sample, tukey_trigger_months):
    rows = sample['rows']
    risk = ['RISK'] * len(rows)
    qqq = ['QQQ'] * len(rows)
    baseline = ['RISK' if float(r['percentile']) >= 50.0 else 'QQQ' for r in rows]
    mr90 = [
        'QQQ' if (float(r['percentile']) >= 90.0 and bool(r['falling']))
        else ('RISK' if float(r['percentile']) >= 50.0 else 'QQQ')
        for r in rows
    ]
    tukey = [
        'QQQ' if (r['month'] in tukey_trigger_months)
        else ('RISK' if float(r['percentile']) >= 50.0 else 'QQQ')
        for r in rows
    ]

    return {
        'period': {'start': rows[0]['month'], 'end': rows[-1]['month'], 'months': len(rows)},
        'commonWindowNote': 'Only months with >=8 prior dispersion observations are used so MR90 percentile is fully causal and all strategies share the identical evaluation window.',
        'strategies': {
            'RiskSleeveOnly': summarize_sleeves(rows, risk),
            'QQQ': summarize_sleeves(rows, qqq),
            'LeadershipBaseline': summarize_sleeves(rows, baseline),
            'ExistingQUU_Tukey_commonWindow': summarize_sleeves(rows, tukey),
            'MR90': summarize_sleeves(rows, mr90),
        },
        'tukey': intervention_diagnostics(rows, baseline, tukey),
        'mr90': intervention_diagnostics(rows, baseline, mr90),
        'sameInterventionMonths': [r['month'] for i, r in enumerate(rows) if baseline[i] == 'RISK' and mr90[i] == 'QQQ'] == [r['month'] for i, r in enumerate(rows) if baseline[i] == 'RISK' and tukey[i] == 'QQQ'],
    }


def main():
    src = json.loads(SRC.read_text(encoding='utf-8'))
    overfit = json.loads(OVERFIT.read_text(encoding='utf-8'))
    proxy = json.loads(PROXY.read_text(encoding='utf-8'))

    exact_tukey = {x['month'] for x in overfit['headline'].get('rolloverTriggers', [])}
    proxy_tukey = set(proxy['headline'].get('triggers', []))

    exact = analyze(src['exact2022_2026'], exact_tukey)
    pre = analyze(src['pre2018Proxy'], proxy_tukey)

    out = {
        'purpose': 'Compare a prespecified mean-reversion overlay (MR90) with QUQU/proxy, QQQ, Leadership baseline, and the frozen Tukey QUU rule. No threshold optimization.',
        'mr90Rule': {
            'leadership': 'Use risk sleeve when prior-only dispersion percentile >=50; otherwise QQQ.',
            'overlay': 'If prior-only dispersion percentile >=90 and dispersion is falling versus immediately prior month, use QQQ for that month; reevaluate from scratch next month.',
            'thresholdSelection': '90th percentile was prespecified as a representative extreme threshold; it was not selected by return maximization.',
            'lookahead': 'None.',
        },
        'exact2022_2026_commonWindow': exact,
        'pre2018Proxy_commonWindow': pre,
        'limitations': [
            'Exact recent sample is short and contains very few MR90 interventions.',
            'Pre-2018 risk sleeve is RelativeMomentum^3 without reliable historical market-cap weighting, not exact QUQU.',
            'The common-window comparison starts only after 8 prior dispersion observations; it is intentionally not the same as the full 48-month/79-month headline backtests.',
            'MR90 is a research variant and does not modify the live QUU rule.',
        ],
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
