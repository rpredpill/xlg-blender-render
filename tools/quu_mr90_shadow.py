#!/usr/bin/env python3
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

OUT = Path('quu-mr90-shadow.json')
START_MONTH = '2026-10'
FROZEN_AT = '2026-10-02'
RULE_VERSION = 'mr90-v1-frozen-2026-10-02'
MIN_PRIOR = 8
LEADERSHIP_PERCENTILE = 50.0
EXTREME_PERCENTILE = 90.0


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def finite(x) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(float(x))


def load(path: str):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def percentile_vs_prior(prior: list[float], current: float) -> float:
    if len(prior) < MIN_PRIOR:
        raise RuntimeError(f'Need at least {MIN_PRIOR} prior dispersions, got {len(prior)}')
    return 100.0 * sum(x <= current for x in prior) / len(prior)


def metrics(returns: list[float]):
    if not returns:
        return {
            'months': 0,
            'totalReturnPct': None,
            'CAGRpct': None,
            'MDDpct': None,
            'Calmar': None,
            'endingIndex': 100.0,
        }
    eq = 1.0
    peak = 1.0
    mdd = 0.0
    for r in returns:
        eq *= 1.0 + float(r) / 100.0
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1.0)
    n = len(returns)
    cagr = (eq ** (12.0 / n) - 1.0) * 100.0
    mdd_pct = mdd * 100.0
    calmar = cagr / abs(mdd_pct) if mdd_pct < 0 else None
    return {
        'months': n,
        'totalReturnPct': (eq - 1.0) * 100.0,
        'CAGRpct': cagr,
        'MDDpct': mdd_pct,
        'Calmar': calmar,
        'endingIndex': eq * 100.0,
    }


def main():
    latest = load('quu-latest.json')
    quu_hist = load('quu-history.json')
    ququ_hist = load('ququ-history.json')
    bench = load('benchmark-history.json')

    allocation_month = str(latest['signal']['allocationMonth'])
    current_dispersion = float(latest['decision']['momentumDispersion'])
    prior_dispersion = latest['decision'].get('priorDispersion')

    prior = []
    for month, rec in sorted((quu_hist.get('months') or {}).items()):
        if month >= allocation_month:
            continue
        d = (rec.get('decision') or {}).get('momentumDispersion')
        if finite(d):
            prior.append(float(d))

    if not finite(prior_dispersion):
        prior_dispersion = prior[-1] if prior else None
    if not finite(prior_dispersion):
        raise RuntimeError('Missing prior dispersion')

    pctl = percentile_vs_prior(prior, current_dispersion)
    falling = current_dispersion < float(prior_dispersion)
    leadership = pctl >= LEADERSHIP_PERCENTILE
    overlay = pctl >= EXTREME_PERCENTILE and falling
    sleeve = 'QQQ' if (overlay or not leadership) else 'QUQU'

    if OUT.exists():
        try:
            out = json.loads(OUT.read_text(encoding='utf-8'))
        except Exception:
            out = {}
    else:
        out = {}

    # The rule metadata is intentionally immutable once this shadow starts.
    expected_rule = {
        'version': RULE_VERSION,
        'frozenAt': FROZEN_AT,
        'startMonth': START_MONTH,
        'minimumPriorMonths': MIN_PRIOR,
        'leadershipPercentile': LEADERSHIP_PERCENTILE,
        'extremePercentile': EXTREME_PERCENTILE,
        'leadership': 'Prior-only dispersion percentile >= 50 => QUQU, otherwise QQQ.',
        'overlay': 'Prior-only dispersion percentile >= 90 AND current dispersion < immediately prior dispersion => QQQ for that allocation month.',
        'reset': 'Reevaluate from scratch every month; no cooldown and no multi-month hold rule.',
        'decisionLock': 'A month decision is written once and never retrospectively changed. Realized-return fields may be refreshed from canonical history corrections.',
        'lookahead': 'None in decision classification.',
    }
    existing_rule = out.get('frozenRule')
    if existing_rule and existing_rule != expected_rule:
        raise RuntimeError('Frozen MR90 rule metadata changed; refusing to continue')

    out.setdefault('strategy', 'QUU-MR90 Shadow')
    out.setdefault('status', 'EXPERIMENTAL_SHADOW_OOS')
    out.setdefault('createdAt', now_iso())
    out['frozenRule'] = expected_rule
    out['updatedAt'] = now_iso()
    out.setdefault('decisions', {})
    decisions = out['decisions']

    if allocation_month >= START_MONTH and allocation_month not in decisions:
        decisions[allocation_month] = {
            'allocationMonth': allocation_month,
            'signalMonth': latest['signal']['recentDate'][:7],
            'signalDate': latest['signal']['recentDate'],
            'lockedAt': now_iso(),
            'sourceGeneratedAt': latest.get('generatedAt'),
            'dispersion': current_dispersion,
            'priorDispersion': float(prior_dispersion),
            'priorObservationCount': len(prior),
            'leadershipPercentile': pctl,
            'leadershipStrong': leadership,
            'falling': falling,
            'mr90Overlay': overlay,
            'selectedSleeve': sleeve,
            'liveQUUSelectedSleeveAtLock': latest.get('selectedSleeve'),
            'realizedReturnPct': None,
            'returnSource': None,
        }

    # Populate outcomes only after canonical monthly-return data exists.
    qqu_months = ququ_hist.get('months') or {}
    quu_months = quu_hist.get('months') or {}
    bench_months = bench.get('months') or {}
    for month, rec in sorted(decisions.items()):
        if month < START_MONTH:
            raise RuntimeError(f'Unexpected pre-OOS decision {month}')
        ququ_ret = (qqu_months.get(month) or {}).get('portfolioReturn')
        qqq_ret = (bench_months.get(month) or {}).get('QQQ')
        live_quu_ret = (quu_months.get(month) or {}).get('portfolioReturn')

        rec['comparators'] = {
            'QUQU': float(ququ_ret) if finite(ququ_ret) else None,
            'QQQ': float(qqq_ret) if finite(qqq_ret) else None,
            'liveQUU': float(live_quu_ret) if finite(live_quu_ret) else None,
        }
        chosen = ququ_ret if rec['selectedSleeve'] == 'QUQU' else qqq_ret
        if finite(chosen):
            rec['realizedReturnPct'] = float(chosen)
            rec['returnSource'] = 'ququ-history.json:portfolioReturn' if rec['selectedSleeve'] == 'QUQU' else 'benchmark-history.json:QQQ'
            rec['returnUpdatedAt'] = now_iso()

    ordered = {m: decisions[m] for m in sorted(decisions)}
    out['decisions'] = ordered

    completed_months = [m for m, r in ordered.items() if finite(r.get('realizedReturnPct'))]
    out['oos'] = {
        'firstDecisionMonth': min(ordered) if ordered else None,
        'latestDecisionMonth': max(ordered) if ordered else None,
        'decisionMonths': len(ordered),
        'completedMonths': len(completed_months),
        'completedMonthList': completed_months,
        'MR90': metrics([float(ordered[m]['realizedReturnPct']) for m in completed_months]),
    }

    # Comparator metrics are calculated only on the same completed OOS months.
    for label in ['liveQUU', 'QUQU', 'QQQ']:
        same = [m for m in completed_months if finite((ordered[m].get('comparators') or {}).get(label))]
        out['oos'][label] = {
            'sameMonthCount': len(same),
            'metrics': metrics([float(ordered[m]['comparators'][label]) for m in same]),
        }

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({
        'allocationMonth': allocation_month,
        'dispersion': current_dispersion,
        'percentile': pctl,
        'falling': falling,
        'selectedSleeve': sleeve,
        'newDecisionLocked': allocation_month in ordered,
        'oos': out['oos'],
    }, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
