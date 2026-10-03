#!/usr/bin/env python3
"""Fixed-universe weighting ablation; no new downloads or signal lookahead.

SPMO-style variants borrow only the score transform and optional security caps.
QUQU's 6-1 signal, volatility overlay and existing selected universe stay fixed.
"""
import argparse, copy, csv, json, math
from pathlib import Path
import numpy as np
import ququ_cadence_analysis as audit
import ququ_v2_ablation as core


def redistribute(raw, limits):
    assert sum(limits.values()) >= 1 - 1e-12
    left, remaining, out = 1., dict(raw), {}
    while remaining:
        total = sum(remaining.values())
        proposed = {t: left * x / total for t, x in remaining.items()}
        saturated = [t for t, x in proposed.items() if x > limits[t] + 1e-14]
        if not saturated:
            out.update(proposed)
            break
        for t in saturated:
            out[t] = limits[t]
            left -= limits[t]
            del remaining[t]
    assert abs(sum(out.values()) - 1) < 1e-10
    assert all(w <= limits[t] + 1e-10 for t, w in out.items())
    return out


def main():
    ap = argparse.ArgumentParser()
    for key in ['inputs', 'caps', 'ablation', 'reference', 'outdir']:
        ap.add_argument('--' + key, required=True)
    args = ap.parse_args()
    read = lambda f: json.loads(Path(f).read_text())
    inp, caps, ablation, result = map(read, [args.inputs, args.caps, args.ablation, args.reference])
    result = copy.deepcopy(result)
    months = [r['month'] for r in result['variants']['QUQU-v2-6M']['rows']]
    meta = {r['month']: r for r in ablation['months']}
    formulas = {
        'A-SqrtFloat-M1': 'sqrt(FloatCap) * relative adjusted 6-1 gross momentum; 20% cap',
        'B-Float-M3': 'FloatCap * relative adjusted 6-1 gross momentum^3; 20% cap',
        'C-Float-M1': 'FloatCap * relative adjusted 6-1 gross momentum; 20% cap',
        'D-SPMOScore-20': 'FloatCap * S&P piecewise score of original QUQU winsorized z; 20% cap',
        'E-SPMOScore-9': 'Same S&P score; security cap min(9%, 3 * FloatCap weight in selected QUQU basket)',
    }
    target_sets = {n: {} for n in formulas}
    recovered, errors, top_errors, floors = {}, [], [], []
    for m in months:
        original = inp['targets'][m]
        assert max(original.values()) < .20 - 1e-10, 'Cannot invert a capped weight'
        cap = {t: caps['months'][m][t]['floatCap'] for t in original}
        # Original raw_i = sqrt(F_i) * (g_i / median(g))^3.
        # Uncapped weights retain ratios exactly; median normalization removes
        # their unknown common scale. The archived median restores absolute g.
        q = {t: (original[t] / math.sqrt(cap[t])) ** (1/3) for t in original}
        median_q = float(np.median(list(q.values())))
        relative = {t: x / median_q for t, x in q.items()}
        md = meta[m]['variants']['+ Winsorization']
        diag = meta[m]['diagnostics']['winsor']
        gross = {t: x * md['signalMedianGross'] for t, x in relative.items()}
        floors.extend((m, t) for t, g in gross.items() if g <= 1.00001e-6)
        assert min(gross.values()) > 1.00001e-6, 'Gross floor destroys z inversion'
        z = {t: float(np.clip((g - 1 - diag['mean']) / diag['std'], -3, 3)) if diag['std'] else 0. for t, g in gross.items()}
        scores = {t: 1 + x if x >= 0 else 1 / (1 - x) for t, x in z.items()}
        reconstructed = core.cap_and_redistribute({t: math.sqrt(cap[t]) * relative[t]**3 for t in original})
        errors.append(max(abs(reconstructed[t] - original[t]) for t in original))
        top_errors.extend(abs(original[r['ticker']] - r['weight']) for r in md['topHoldings'])
        recovered[m] = {t: {'relativeMomentum': relative[t], 'grossMomentum': gross[t], 'winsorZ': z[t], 'SPScore': scores[t]} for t in original}
        raws = {
            'A-SqrtFloat-M1': {t: math.sqrt(cap[t]) * relative[t] for t in original},
            'B-Float-M3': {t: cap[t] * relative[t]**3 for t in original},
            'C-Float-M1': {t: cap[t] * relative[t] for t in original},
            'D-SPMOScore-20': {t: cap[t] * scores[t] for t in original},
            'E-SPMOScore-9': {t: cap[t] * scores[t] for t in original},
        }
        for n, raw in raws.items():
            limits = {t: min(.09, 3 * cap[t] / sum(cap.values())) if n == 'E-SPMOScore-9' else .20 for t in original}
            w = redistribute(raw, limits)
            assert set(w) == set(original)
            target_sets[n][m] = w
    assert max(errors) < 1e-12 and max(top_errors) < 1e-10
    for n, targets in target_sets.items():
        variant = {**inp, 'targets': targets, 'corporateActions': {}}
        phases = {str(k): (lambda rows: {'metrics': audit.metrics(rows), 'rows': rows})(audit.simulate(months, variant, 6, k, True)) for k in range(6)}
        result['variants'][n] = phases['0']
        result['phaseResults'][n] = phases
        values = [p['metrics']['CAGRpct'] for p in phases.values()]
        result['phaseSensitivity'][n] = {'CAGRminPct': min(values), 'CAGRmedianPct': float(np.median(values)), 'CAGRmaxPct': max(values), 'validPhases': 6}
        result['costSensitivity'][n] = {str(bp): audit.metrics(audit.net_rows(phases['0']['rows'], bp)) for bp in [0, 10, 25, 50]}
        result['targets'][n] = targets
        result['allocationDiagnostics'][n] = {}
        for m, w in targets.items():
            rr = caps['months'][m]
            result['allocationDiagnostics'][n][m] = {'holdings': len(w), 'maxWeightPct': max(w.values())*100, 'top10WeightPct': sum(sorted(w.values(), reverse=True)[:10])*100,
                'currentSharesProxyWeightPct': sum(w[t] for t in w if rr[t]['capMethod']=='current-shares-proxy')*100,
                'missingFloatProxyWeightPct': sum(w[t] for t in w if rr[t]['floatMethod']=='missing-assume-1.0')*100}
        result['comparisons'][n] = {b: {'CAGRgapPp': phases['0']['metrics']['CAGRpct'] - result['variants'][b]['metrics']['CAGRpct']} for b in ['QQQ', 'SPMO']}
    result['formulas'].update(formulas)
    result['consistency'].update({'recoveredWeightMaxError': max(errors), 'archivedTopWeightMaxError': max(top_errors), 'grossFloors': len(floors)})
    result['SPMOAdaptation'] = {'source': 'https://www.spglobal.com/spdji/en/documents/methodologies/methodology-sp-momentum-indices.pdf',
        'scope': 'S&P positive score transform and historical security cap only; no S&P500 selection, no 12-1 signal, no full volatility division. Original QUQU clipped z is used, based on its selected liquid basket. E cap denominator is the selected QUQU basket, not S&P500. Pre-September-2026 security-level cap used; no new company aggregation.',
        'score': 'z clipped to [-3,3]; s=1+z if z>=0, else 1/(1-z)',
        'recovery': 'Exact uncapped-weight inversion using cached float cap, archived gross median and original pre-winsor mean/std. No re-standardization of already clipped values.'}
    out = Path(args.outdir); out.mkdir(parents=True, exist_ok=True)
    (out/'ququ_weighting_comparison.json').write_text(json.dumps(result, indent=2, allow_nan=False))
    (out/'recovered_signals.json').write_text(json.dumps(recovered, allow_nan=False))
    with (out/'ququ_weighting_summary.csv').open('w') as f:
        w=csv.DictWriter(f, fieldnames=['strategy']+list(next(iter(result['variants'].values()))['metrics'])); w.writeheader()
        for n,v in result['variants'].items(): w.writerow({'strategy':n, **v['metrics']})
    with (out/'ququ_weighting_monthly.csv').open('w') as f:
        w=csv.writer(f);w.writerow(['month']+list(result['variants']))
        for i,m in enumerate(months):w.writerow([m]+[v['rows'][i]['return']*100 for v in result['variants'].values()])
    print(json.dumps({'metrics': {n:v['metrics'] for n,v in result['variants'].items()}, 'validation': result['consistency'], 'phaseSensitivity':result['phaseSensitivity']}, indent=2))


if __name__ == '__main__':
    main()
