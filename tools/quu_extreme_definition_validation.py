#!/usr/bin/env python3
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

MIN_HISTORY = 8


def metrics(rs_pct: list[float]) -> dict:
    a = np.asarray(rs_pct, dtype=float) / 100.0
    if len(a) == 0:
        return {'months': 0, 'totalReturnPct': None, 'CAGRpct': None, 'MDDpct': None, 'Calmar': None, 'endingIndex': None}
    eq = np.concatenate([[1.0], np.cumprod(1.0 + a)])
    peaks = np.maximum.accumulate(eq)
    dd = eq / peaks - 1.0
    cagr = float(eq[-1] ** (12.0 / len(a)) - 1.0)
    mdd = float(dd.min())
    return {
        'months': int(len(a)),
        'totalReturnPct': float((eq[-1] - 1.0) * 100.0),
        'CAGRpct': cagr * 100.0,
        'MDDpct': mdd * 100.0,
        'Calmar': cagr / abs(mdd) if mdd < 0 else None,
        'endingIndex': float(eq[-1] * 100.0),
    }


def threshold(prior: list[float], method: str) -> float | None:
    if len(prior) < MIN_HISTORY:
        return None
    a = np.asarray(prior, dtype=float)
    if method == 'tukey_1.5iqr':
        q1 = float(np.quantile(a, 0.25)); q3 = float(np.quantile(a, 0.75))
        return q3 + 1.5 * (q3 - q1)
    if method == 'expanding_p90':
        return float(np.quantile(a, 0.90))
    if method == 'mean_plus_1.5sd':
        return float(np.mean(a) + 1.5 * np.std(a, ddof=0))
    if method == 'median_plus_2.5mad':
        med = float(np.median(a)); mad = float(np.median(np.abs(a - med)))
        return med + 2.5 * mad
    raise KeyError(method)


def category_stats(rows: list[dict]) -> dict:
    if not rows:
        return {'months': 0, 'avgExcessPctPoints': None, 'medianExcessPctPoints': None, 'QUQUwinRatePct': None, 'monthsList': []}
    x = np.asarray([r['excess'] for r in rows], dtype=float)
    return {
        'months': len(rows),
        'avgExcessPctPoints': float(np.mean(x)),
        'medianExcessPctPoints': float(np.median(x)),
        'QUQUwinRatePct': float(np.mean(x > 0) * 100.0),
        'monthsList': [r['month'] for r in rows],
    }


def analyze(rows: list[dict], label: str) -> dict:
    methods = ['tukey_1.5iqr', 'expanding_p90', 'mean_plus_1.5sd', 'median_plus_2.5mad']
    by_method = {}
    for method in methods:
        prior: list[float] = []
        strategy_rs: list[float] = []
        events: list[dict] = []
        for r in rows:
            d = float(r['dispersion'])
            p = prior[-1] if prior else None
            leadership_thr = float(np.median(prior)) if prior else None
            leadership = True if leadership_thr is None else d > leadership_thr
            ext_thr = threshold(prior, method)
            extreme = bool(ext_thr is not None and d > ext_thr)
            falling = bool(p is not None and d < p)
            rollover = extreme and falling
            sleeve_ququ = leadership and not rollover
            chosen = float(r['riskReturn']) if sleeve_ququ else float(r['QQQ'])
            strategy_rs.append(chosen)
            events.append({
                'month': r['month'],
                'dispersion': d,
                'priorDispersion': p,
                'leadershipThreshold': leadership_thr,
                'extremeThreshold': ext_thr,
                'leadership': leadership,
                'extreme': extreme,
                'falling': falling,
                'rollover': rollover,
                'riskReturn': float(r['riskReturn']),
                'QQQ': float(r['QQQ']),
                'excess': float(r['riskReturn']) - float(r['QQQ']),
            })
            prior.append(d)

        er = [e for e in events if e['extreme'] and not e['falling']]
        ef = [e for e in events if e['extreme'] and e['falling']]
        nr = [e for e in events if (not e['extreme']) and not e['falling']]
        nf = [e for e in events if (not e['extreme']) and e['falling']]
        by_method[method] = {
            'strategy': metrics(strategy_rs),
            'extremeRisingOrFlat': category_stats(er),
            'extremeFallingRollover': category_stats(ef),
            'nonExtremeRisingOrFlat': category_stats(nr),
            'nonExtremeFalling': category_stats(nf),
            'rolloverEvents': [e for e in ef],
        }

    # Definition-robustness summary: do all methods agree on the sign pattern?
    signs = {}
    for method, x in by_method.items():
        up = x['extremeRisingOrFlat']['avgExcessPctPoints']
        down = x['extremeFallingRollover']['avgExcessPctPoints']
        signs[method] = {
            'extremeRisingPositive': None if up is None else bool(up > 0),
            'extremeFallingNegative': None if down is None else bool(down < 0),
            'rolloverEventCount': x['extremeFallingRollover']['months'],
        }

    return {'label': label, 'months': len(rows), 'methods': by_method, 'signAgreement': signs}


def load_exact() -> list[dict]:
    qh = json.loads(Path('ququ-history.json').read_text(encoding='utf-8'))
    bh = json.loads(Path('benchmark-history.json').read_text(encoding='utf-8'))
    months = sorted(set(qh.get('months', {})) & set(bh.get('months', {})))
    out = []
    for m in months:
        rec = qh['months'][m]
        if not isinstance(rec.get('portfolioReturn'), (int, float)) or not isinstance(bh['months'][m].get('QQQ'), (int, float)):
            continue
        vals = [float(r['momentum']) for r in (rec.get('rows') or []) if isinstance(r.get('momentum'), (int, float))]
        if len(vals) < 90:
            continue
        out.append({
            'month': m,
            'dispersion': float(np.std(np.asarray(vals, dtype=float), ddof=0)),
            'riskReturn': float(rec['portfolioReturn']),
            'QQQ': float(bh['months'][m]['QQQ']),
        })
    return out


def load_proxy() -> list[dict]:
    p = Path('quu-pre2018-proxy-validation.json')
    if not p.exists():
        return []
    j = json.loads(p.read_text(encoding='utf-8'))
    out = []
    for r in j.get('months', []):
        if all(isinstance(r.get(k), (int, float)) for k in ['dispersion', 'proxyReturn', 'QQQ']):
            out.append({'month': r['month'], 'dispersion': float(r['dispersion']), 'riskReturn': float(r['proxyReturn']), 'QQQ': float(r['QQQ'])})
    return out


def main() -> None:
    exact = load_exact()
    if len(exact) < 36:
        raise RuntimeError(f'exact months only {len(exact)}')
    proxy = load_proxy()
    out = {
        'generatedAt': datetime.now(timezone.utc).isoformat(),
        'purpose': 'Robustness check only. Thresholds are prespecified canonical alternatives; no threshold is selected or optimized by return.',
        'definitions': {
            'leadership': 'Current dispersion > expanding median of prior dispersions',
            'direction': 'Falling when current dispersion < immediately prior dispersion',
            'extremeMethods': {
                'tukey_1.5iqr': 'Q3 + 1.5×IQR of prior dispersions',
                'expanding_p90': '90th percentile of prior dispersions',
                'mean_plus_1.5sd': 'Prior mean + 1.5×population standard deviation',
                'median_plus_2.5mad': 'Prior median + 2.5×median absolute deviation',
            },
            'rollover': 'Extreme AND falling',
            'allocation': 'QUQU/proxy only when leadership strong and not rollover; otherwise QQQ',
            'minimumHistoryForExtreme': MIN_HISTORY,
            'lookahead': 'All thresholds use prior months only.',
        },
        'exact2022_2026': analyze(exact, 'Exact QUQU market-cap-weighted history'),
        'pre2018Proxy': analyze(proxy, '2011-2018 PIT RelativeMomentum^3 proxy; not exact QUQU') if len(proxy) >= 36 else {'available': False, 'months': len(proxy)},
    }
    Path('quu-extreme-definition-validation.json').write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    compact = {
        'exact': {m: {'up': x['extremeRisingOrFlat'], 'down': x['extremeFallingRollover'], 'strategy': x['strategy']} for m, x in out['exact2022_2026']['methods'].items()},
        'proxy': None if not out['pre2018Proxy'].get('methods') else {m: {'up': x['extremeRisingOrFlat'], 'down': x['extremeFallingRollover']} for m, x in out['pre2018Proxy']['methods'].items()},
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
