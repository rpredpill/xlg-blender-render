#!/usr/bin/env python3
from __future__ import annotations

import json
import math
from datetime import datetime, timezone, date
from pathlib import Path
from statistics import median, pstdev

import numpy as np

MIN_PRIOR_PERCENTILE = 8
LEADERSHIP_PERCENTILE = 50.0
EXTREME_PERCENTILE = 90.0


def next_month(ym_or_date: str) -> str:
    d = date.fromisoformat(ym_or_date[:10] if len(ym_or_date) >= 10 else ym_or_date + '-01')
    if d.month == 12:
        return f'{d.year + 1}-01'
    return f'{d.year}-{d.month + 1:02d}'


def dispersion(rows: list[dict]) -> float:
    moms = [float(r['momentum']) for r in rows if isinstance(r.get('momentum'), (int, float)) and math.isfinite(float(r['momentum']))]
    if len(moms) < 80:
        raise RuntimeError(f'only {len(moms)} momentum rows')
    return float(pstdev(moms))


def percentile_rank_prior(prior_disps: list[float], current_disp: float) -> float | None:
    if len(prior_disps) < MIN_PRIOR_PERCENTILE:
        return None
    return 100.0 * sum(1 for x in prior_disps if x <= current_disp) / len(prior_disps)


def decision(current_disp: float, prior_disps: list[float]) -> dict:
    med = float(median(prior_disps)) if prior_disps else None
    prev = float(prior_disps[-1]) if prior_disps else None
    pct = percentile_rank_prior(prior_disps, current_disp)
    falling = bool(prev is not None and current_disp < prev)

    # First eight observations are only a warm-up for the causal percentile.
    # During warm-up, preserve the prior expanding-median leadership split and
    # do not allow the mean-reversion overlay to fire.
    if pct is None:
        leadership = True if med is None else current_disp > med
        overlay = False
        selected = 'QUQU' if leadership else 'QQQ'
        mode = 'WARMUP_MEDIAN'
    else:
        leadership = bool(pct >= LEADERSHIP_PERCENTILE)
        overlay = bool(pct >= EXTREME_PERCENTILE and falling)
        selected = 'QQQE' if overlay else ('QUQU' if leadership else 'QQQ')
        mode = 'MR90'

    return {
        'momentumDispersion': current_disp,
        'priorMedianDispersion': med,
        'priorDispersion': prev,
        'priorObservationCount': len(prior_disps),
        'leadershipPercentile': pct,
        'leadershipStrong': leadership,
        'falling': falling,
        'meanReversionOverlay': overlay,
        'decisionMode': mode,
        'selectedSleeve': selected,
    }


def compact_ququ_rows(rows: list[dict], include_returns: bool = True) -> list[dict]:
    out = []
    for r in rows:
        x = {
            'ticker': str(r.get('ticker', '')).upper(),
            'weight': float(r['weight']),
            'momentum': float(r.get('momentum', 0.0)),
            'marketCap': float(r.get('marketCap', 0.0)),
        }
        if include_returns and isinstance(r.get('monthlyReturn'), (int, float)):
            x['monthlyReturn'] = float(r['monthlyReturn'])
        out.append(x)
    return out


def etf_rows(ticker: str, return_pct: float | None = None) -> list[dict]:
    r = {'ticker': ticker, 'weight': 1.0, 'momentum': None, 'marketCap': None}
    if isinstance(return_pct, (int, float)):
        r['monthlyReturn'] = float(return_pct) / 100.0
    return [r]


def metrics(returns_pct: list[float]) -> dict:
    a = np.asarray(returns_pct, dtype=float) / 100.0
    eq = np.concatenate([[1.0], np.cumprod(1.0 + a)])
    peaks = np.maximum.accumulate(eq)
    dd = eq / peaks - 1.0
    total = float(eq[-1] - 1.0)
    cagr = float(eq[-1] ** (12.0 / len(a)) - 1.0) if len(a) else 0.0
    mdd = float(dd.min()) if len(dd) else 0.0
    return {
        'months': int(len(a)),
        'totalReturnPct': total * 100.0,
        'CAGRpct': cagr * 100.0,
        'MDDpct': mdd * 100.0,
        'Calmar': (cagr / abs(mdd)) if mdd < 0 else None,
        'endingIndex': float(eq[-1] * 100.0),
    }


def main() -> None:
    qhist = json.loads(Path('ququ-history.json').read_text(encoding='utf-8'))
    latest = json.loads(Path('ququ-latest.json').read_text(encoding='utf-8'))
    bench = json.loads(Path('benchmark-history.json').read_text(encoding='utf-8'))

    qmonths = qhist.get('months') or {}
    bmonths = bench.get('months') or {}
    history_months = sorted(m for m in qmonths if isinstance(qmonths[m], dict))

    prior_disps: list[float] = []
    out_months: dict[str, dict] = {}
    completed_returns: list[float] = []

    for m in history_months:
        rec = qmonths[m]
        rows = rec.get('rows') or []
        if len(rows) < 80:
            continue
        d = dispersion(rows)
        dec = decision(d, prior_disps)
        bench_row = bmonths.get(m) or {}
        qqq_ret = bench_row.get('QQQ')
        qqqe_ret = bench_row.get('QQQE')
        ququ_ret = rec.get('portfolioReturn')

        if dec['selectedSleeve'] == 'QUQU':
            target_rows = compact_ququ_rows(rows, include_returns=True)
            port = float(ququ_ret) if isinstance(ququ_ret, (int, float)) else None
            universe_count = int(rec.get('universeCount') or len(rows))
            coverage = float(rec.get('coverageRatio') or 1.0)
            missing_weight = float(rec.get('missingReturnWeight') or 0.0)
        elif dec['selectedSleeve'] == 'QQQE':
            target_rows = etf_rows('QQQE', float(qqqe_ret) if isinstance(qqqe_ret, (int, float)) else None)
            port = float(qqqe_ret) if isinstance(qqqe_ret, (int, float)) else None
            universe_count = 1
            coverage = 1.0
            missing_weight = 0.0
        else:
            target_rows = etf_rows('QQQ', float(qqq_ret) if isinstance(qqq_ret, (int, float)) else None)
            port = float(qqq_ret) if isinstance(qqq_ret, (int, float)) else None
            universe_count = 1
            coverage = 1.0
            missing_weight = 0.0

        out = {
            'allocationMonth': m,
            'signalMonth': rec.get('signalMonth'),
            'signalDate': rec.get('signalDate'),
            'generatedAt': rec.get('generatedAt'),
            'selectedSleeve': dec['selectedSleeve'],
            'decision': dec,
            'rule': 'MR90: percentile <50 => QQQ; >=50 => QUQU; >=90 and falling => QQQE',
            'universeCount': universe_count,
            'coverageRatio': coverage,
            'missingReturnWeight': missing_weight,
            'rows': target_rows,
        }
        if port is not None:
            out['portfolioReturn'] = port
            completed_returns.append(port)
        if rec.get('lastDate') is not None:
            out['lastDate'] = rec.get('lastDate')
        out_months[m] = out
        prior_disps.append(d)

    recent_date = str((latest.get('signal') or {}).get('recentDate') or '')
    if not recent_date:
        raise RuntimeError('QUQU latest missing signal.recentDate')
    allocation_month = next_month(recent_date)
    current_rows = latest.get('rows') or []
    if len(current_rows) != 100:
        raise RuntimeError(f'QUQU latest {len(current_rows)}/100')

    prior_month_disps = []
    for m in sorted(k for k in qmonths if k < allocation_month):
        rows = (qmonths[m] or {}).get('rows') or []
        if len(rows) >= 80:
            prior_month_disps.append(dispersion(rows))
    cur_disp = dispersion(current_rows)
    cur_dec = decision(cur_disp, prior_month_disps)

    if cur_dec['selectedSleeve'] == 'QUQU':
        latest_rows = compact_ququ_rows(current_rows, include_returns=False)
    else:
        latest_rows = etf_rows(cur_dec['selectedSleeve'])

    latest_payload = {
        'strategy': 'QUU',
        'universe': 'Nasdaq-100 / QQQ / QQQE regime switch',
        'generatedAt': datetime.now(timezone.utc).isoformat(),
        'sourceGeneratedAt': latest.get('generatedAt'),
        'signal': {
            'recentDate': recent_date,
            'allocationMonth': allocation_month,
            'rule': 'MR90: leadership percentile <50 => QQQ; >=50 => QUQU; >=90 with falling dispersion => QQQE',
        },
        'decision': cur_dec,
        'selectedSleeve': cur_dec['selectedSleeve'],
        'holdings': len(latest_rows),
        'weightSum': sum(float(r['weight']) for r in latest_rows),
        'maxWeight': max(float(r['weight']) for r in latest_rows),
        'top10Weight': sum(sorted((float(r['weight']) for r in latest_rows), reverse=True)[:10]),
        'rows': latest_rows,
    }

    current_record = {
        'allocationMonth': allocation_month,
        'signalMonth': recent_date[:7],
        'signalDate': recent_date,
        'generatedAt': latest_payload['generatedAt'],
        'selectedSleeve': cur_dec['selectedSleeve'],
        'decision': cur_dec,
        'rule': latest_payload['signal']['rule'],
        'universeCount': len(latest_rows),
        'coverageRatio': 1.0,
        'missingReturnWeight': 0.0,
        'rows': latest_rows,
    }
    old_current = out_months.get(allocation_month) or {}
    if isinstance(old_current.get('portfolioReturn'), (int, float)):
        current_record['portfolioReturn'] = float(old_current['portfolioReturn'])
        if old_current.get('lastDate') is not None:
            current_record['lastDate'] = old_current.get('lastDate')
    out_months[allocation_month] = current_record

    history_payload = {
        'strategy': 'QUU',
        'universe': 'Nasdaq-100 / QQQ / QQQE regime switch',
        'description': 'QUU monthly holdings and returns. Uses QUQU during strong leadership, QQQ during weak leadership, and QQQE when extreme leadership begins mean-reverting.',
        'generatedAt': latest_payload['generatedAt'],
        'rules': {
            'minimumPriorMonths': MIN_PRIOR_PERCENTILE,
            'leadership': 'Prior-only Nasdaq-100 cross-sectional 6-1 momentum-dispersion percentile >=50 => QUQU; below 50 => QQQ',
            'meanReversionOverlay': 'Prior-only percentile >=90 AND current dispersion < immediately prior dispersion => QQQE for that allocation month',
            'reset': 'Reevaluate from scratch every month; no cooldown or multi-month hold rule',
            'warmup': 'Before eight prior observations exist, use expanding prior median for the QUQU/QQQ split and disable the QQQE overlay',
        },
        'metricsCompletedMonths': metrics(completed_returns) if completed_returns else None,
        'months': {k: out_months[k] for k in sorted(out_months)},
    }

    Path('quu-latest.json').write_text(json.dumps(latest_payload, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    Path('quu-history.json').write_text(json.dumps(history_payload, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({
        'latest': {'allocationMonth': allocation_month, 'selectedSleeve': cur_dec['selectedSleeve'], 'decision': cur_dec, 'holdings': len(latest_rows)},
        'historyMonths': len(out_months),
        'metrics': history_payload['metricsCompletedMonths'],
    }, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
