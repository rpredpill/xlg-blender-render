#!/usr/bin/env python3
from __future__ import annotations

import csv
import importlib.util
import io
import json
import math
import random
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from statistics import pstdev
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests

BASE_PATH = Path('/tmp/backfill_snpi_ququ.py')
spec = importlib.util.spec_from_file_location('quu_base', BASE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError('cannot load backfill base')
base = importlib.util.module_from_spec(spec)
sys.modules['quu_base'] = base
spec.loader.exec_module(base)

START = '2007-08'
END = '2026-09'
CAP = 0.20
WIKI_CUTOFF = pd.Timestamp('2018-03-27')
KAGGLE_WIKI_URL = 'https://www.kaggle.com/api/v1/datasets/download/marketneutral/quandl-wiki-prices-us-equites'
UA = {'User-Agent': 'Mozilla/5.0'}

# Same-company ticker changes only. Acquisitions/mergers are deliberately excluded.
ALIASES = {
    'NLOK': 'GEN',      # NortonLifeLock -> Gen Digital
    'CTRP': 'TCOM',     # Ctrip -> Trip.com
    'PCLN': 'BKNG',     # Priceline -> Booking Holdings
    'FB': 'META',       # Facebook -> Meta Platforms
    'RIMM': 'BB',       # Research In Motion -> BlackBerry
    'WLTW': 'WTW',      # Willis Towers Watson ticker change
    'FI': 'FISV',       # Fiserv ticker cycle / same company
}


def metrics(rs):
    a = np.asarray(rs, dtype=float) / 100.0
    if not len(a):
        return None
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


def cap_and_redistribute(raw, cap=CAP):
    free = set(raw)
    out = {}
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


def epoch_utc(day: str) -> int:
    return int(pd.Timestamp(day, tz='UTC').timestamp())


def yahoo_chart(symbol: str, start: str, end: str):
    p1 = epoch_utc(start)
    p2 = epoch_utc(end) + 86400
    url = (
        'https://query1.finance.yahoo.com/v8/finance/chart/'
        + quote(symbol, safe='-.')
        + f'?period1={p1}&period2={p2}&interval=1d&events=div%2Csplits&includeAdjustedClose=true'
    )
    try:
        r = requests.get(url, headers=UA, timeout=12)
        if r.status_code != 200:
            return None
        payload = r.json()
        result = ((payload.get('chart') or {}).get('result') or [None])[0]
        if not result:
            return None
        ts = result.get('timestamp') or []
        quote0 = (((result.get('indicators') or {}).get('quote') or [{}])[0])
        adj0 = (((result.get('indicators') or {}).get('adjclose') or [{}])[0])
        opens = quote0.get('open') or []
        closes = quote0.get('close') or []
        adjs = adj0.get('adjclose') or closes
        n = min(len(ts), len(opens), len(closes), len(adjs))
        rows = []
        for i in range(n):
            o, c, a = opens[i], closes[i], adjs[i]
            if c is None:
                continue
            rows.append((pd.to_datetime(ts[i], unit='s', utc=True).tz_localize(None).normalize(), o, c, a if a is not None else c))
        if not rows:
            return None
        d = pd.DataFrame(rows, columns=['Date', 'Open', 'Close', 'Adj Close']).set_index('Date')
        for col in ('Open', 'Close', 'Adj Close'):
            d[col] = pd.to_numeric(d[col], errors='coerce')
        d = d[~d.index.duplicated(keep='last')].sort_index().dropna(subset=['Close'])
        return d if not d.empty else None
    except Exception:
        return None


def recover_yahoo_prices(prices, symbols, start, end):
    missing = [s for s in symbols if s not in prices]
    direct, aliased = [], []
    for i, s in enumerate(missing, 1):
        d = yahoo_chart(base.yf_symbol(s), start, end)
        if d is not None:
            prices[s] = d
            direct.append(s)
        else:
            alias = ALIASES.get(s)
            if alias:
                d = yahoo_chart(base.yf_symbol(alias), start, end)
                if d is not None:
                    prices[s] = d
                    aliased.append(f'{s}->{alias}')
        if i % 20 == 0 or i == len(missing):
            print(f'raw Yahoo recovery {i}/{len(missing)}; direct={len(direct)} alias={len(aliased)}', flush=True)
    return {'direct': direct, 'aliases': aliased, 'stillMissing': [s for s in symbols if s not in prices]}


def _wiki_csv_member(z: zipfile.ZipFile):
    names = z.namelist()
    direct = next((n for n in names if n.upper().endswith('WIKI_PRICES.CSV')), None)
    if direct:
        return ('direct', direct)
    nested = next((n for n in names if 'WIKI' in n.upper() and n.upper().endswith('.ZIP')), None)
    if nested:
        return ('nested', nested)
    return (None, None)


def load_wiki_prices(symbols, start='2007-01-01'):
    """Public Quandl WIKI mirror; frozen at 2018-03-27 and includes delisted stocks."""
    archive = Path('/tmp/quandl-wiki-prices-us-equites.zip')
    if not archive.exists():
        print('Downloading public Quandl WIKI archive...', flush=True)
        with requests.get(KAGGLE_WIKI_URL, headers=UA, timeout=120, stream=True) as r:
            r.raise_for_status()
            with archive.open('wb') as f:
                for chunk in r.iter_content(1024 * 1024):
                    if chunk:
                        f.write(chunk)
        print('WIKI archive MB', round(archive.stat().st_size / 1e6, 1), flush=True)

    want = set(symbols)
    frames = []
    usecols = ['ticker', 'date', 'open', 'close', 'adj_close']
    with zipfile.ZipFile(archive) as outer:
        kind, member = _wiki_csv_member(outer)
        if not member:
            raise RuntimeError(f'WIKI_PRICES.csv not found; members={outer.namelist()[:20]}')
        if kind == 'direct':
            raw = outer.open(member)
            close_raw = True
        else:
            nested_path = Path('/tmp/wiki-prices-inner.zip')
            if not nested_path.exists():
                nested_path.write_bytes(outer.read(member))
            inner = zipfile.ZipFile(nested_path)
            csv_name = next((n for n in inner.namelist() if n.upper().endswith('WIKI_PRICES.CSV')), None)
            if not csv_name:
                inner.close()
                raise RuntimeError('nested WIKI_PRICES.csv missing')
            raw = inner.open(csv_name)
            close_raw = True
        try:
            for idx, chunk in enumerate(pd.read_csv(raw, usecols=usecols, chunksize=500000), 1):
                x = chunk[chunk['ticker'].astype(str).str.upper().isin(want)].copy()
                if not x.empty:
                    x['date'] = pd.to_datetime(x['date'], errors='coerce')
                    x = x[(x['date'] >= pd.Timestamp(start)) & (x['date'] <= WIKI_CUTOFF)]
                    if not x.empty:
                        frames.append(x)
                if idx % 10 == 0:
                    print('WIKI chunks', idx, 'matched rows', sum(len(f) for f in frames), flush=True)
        finally:
            if close_raw:
                raw.close()
            if kind == 'nested':
                inner.close()

    if not frames:
        raise RuntimeError('no WIKI rows matched NDX symbols')
    allx = pd.concat(frames, ignore_index=True)
    out = {}
    for s, g in allx.groupby(allx['ticker'].astype(str).str.upper()):
        d = pd.DataFrame({
            'Open': pd.to_numeric(g['open'], errors='coerce').to_numpy(),
            'Close': pd.to_numeric(g['close'], errors='coerce').to_numpy(),
            'Adj Close': pd.to_numeric(g['adj_close'], errors='coerce').to_numpy(),
        }, index=pd.to_datetime(g['date']).dt.normalize())
        d = d[~d.index.duplicated(keep='last')].sort_index().dropna(subset=['Close'])
        if not d.empty:
            out[s] = d
    print('WIKI symbols recovered', len(out), 'rows', len(allx), flush=True)
    return out


def merge_wiki(prices, wiki):
    added, extended = [], []
    for s, wd in wiki.items():
        if s not in prices:
            prices[s] = wd
            added.append(s)
            continue
        # WIKI appended last so it is authoritative for its frozen historical window.
        merged = pd.concat([prices[s], wd]).sort_index()
        merged = merged[~merged.index.duplicated(keep='last')]
        prices[s] = merged
        extended.append(s)
    return {'newSymbols': added, 'extendedSymbols': extended}


def normalize_production_coverage(rec):
    rows = list(rec.get('rows') or [])
    universe_count = max(1, int(rec.get('universeCount') or len(rows)))
    coverage = len(rows) / universe_count
    if coverage < 0.90:
        raise RuntimeError(f'PIT price coverage {len(rows)}/{universe_count}={coverage:.2%}')

    # Production QUQU definition: if PIT membership has >100 recoverable securities,
    # keep the 100 largest by contemporaneous market cap. If only 90-99 are recoverable,
    # retain them and explicitly record coverage instead of inventing survivors.
    if len(rows) > 100:
        rows = sorted(rows, key=lambda x: float(x.get('marketCap') or 0.0), reverse=True)[:100]

    if len(rows) < 90:
        raise RuntimeError(f'only {len(rows)} recoverable rows')

    gross = [1.0 + float(x.get('momentum') or 0.0) for x in rows]
    med = float(np.median(gross))
    raw = {}
    for x in rows:
        cap = float(x.get('marketCap') or 0.0)
        g = 1.0 + float(x.get('momentum') or 0.0)
        if not (cap > 0 and g > 0 and med > 0):
            raise RuntimeError('bad cap/momentum')
        raw[x['ticker']] = math.sqrt(cap) * ((g / med) ** 3)
    w = cap_and_redistribute(raw)
    for x in rows:
        x['weight'] = float(w[x['ticker']])
    rows.sort(key=lambda x: x['weight'], reverse=True)

    missing_w = sum(x['weight'] for x in rows if x.get('monthlyReturn') is None)
    if missing_w > 0.02:
        raise RuntimeError(f'missing return weight {missing_w:.2%}')
    port = 100.0 * sum(x['weight'] * (float(x['monthlyReturn']) if x.get('monthlyReturn') is not None else 0.0) for x in rows)

    out = dict(rec)
    out['rows'] = rows
    out['holdingsCount'] = len(rows)
    out['coverageRatio'] = coverage
    out['missingReturnWeight'] = float(missing_w)
    out['portfolioReturn'] = float(port)
    out['maxWeight'] = max(x['weight'] for x in rows)
    out['top10Weight'] = sum(x['weight'] for x in rows[:10])
    out['coveragePolicy'] = 'PIT membership; require >=90% recoverable historical price coverage. If >100 priced securities, retain top100 by contemporaneous market cap.'
    return out


def dispersion(rec):
    xs = [float(x['momentum']) for x in rec['rows'] if isinstance(x.get('momentum'), (int, float)) and math.isfinite(float(x['momentum']))]
    if len(xs) < 80:
        raise RuntimeError(f'dispersion rows {len(xs)}')
    return float(pstdev(xs))


def tukey(xs, k=1.5):
    if len(xs) < 8:
        return None
    a = np.asarray(xs, dtype=float)
    q1 = float(np.quantile(a, 0.25))
    q3 = float(np.quantile(a, 0.75))
    return q3 + k * (q3 - q1)


def qtile(xs, q):
    return float(np.quantile(np.asarray(xs, dtype=float), q)) if xs else None


def run_rule(records, qqq, leadership_q=0.5, tukey_k=1.5, min_drop=0.0):
    prior = []
    rows = []
    for month, rec in records:
        d = dispersion(rec)
        threshold = qtile(prior, leadership_q)
        fence = tukey(prior, tukey_k)
        prev = prior[-1] if prior else None
        leadership = True if threshold is None else d > threshold
        drop = ((prev - d) / prev) if prev and prev > 0 else None
        extreme = bool(fence is not None and d > fence)
        rollover = bool(extreme and prev is not None and d < prev and (drop is None or drop >= min_drop))
        sleeve = 'QUQU' if leadership and not rollover else 'QQQ'
        qr = float(rec['portfolioReturn'])
        br = float(qqq[month])
        rows.append({
            'month': month, 'dispersion': d, 'leadershipThreshold': threshold,
            'tukeyUpper': fence, 'priorDispersion': prev, 'leadership': leadership,
            'extreme': extreme, 'rollover': rollover, 'sleeve': sleeve,
            'QUQU': qr, 'QQQ': br, 'QUU': qr if sleeve == 'QUQU' else br,
            'coverageRatio': float(rec.get('coverageRatio') or 1.0),
            'holdingsCount': int(rec.get('holdingsCount') or len(rec.get('rows') or [])),
            'medianCapFallbackCount': int(rec.get('medianCapFallbackCount') or 0),
        })
        prior.append(d)
    return rows


def contiguous_suffix(valid_months, end_month):
    s = set(valid_months)
    cur = end_month
    out = []
    while cur in s:
        out.append(cur)
        cur = base.month_add(cur, -1)
    return list(reversed(out))


def summarize_rows(rows):
    return {
        'QUQU': metrics([x['QUQU'] for x in rows]),
        'QQQ': metrics([x['QQQ'] for x in rows]),
        'LeadershipBaselineNoRollover': metrics([x['QUQU'] if x['leadership'] else x['QQQ'] for x in rows]),
        'QUU': metrics([x['QUU'] for x in rows]),
        'rolloverTriggers': [x for x in rows if x['rollover']],
        'qqqSleeveMonths': [x['month'] for x in rows if x['sleeve'] == 'QQQ'],
    }


def main():
    membership = base.load_membership(base.NDX100_URL)
    latest_signal = base.month_end(base.month_add(END, -1))
    begin = base.month_end(base.month_add(START, -6))
    symbols = set(base.members_at(membership, begin))
    for dt, tickers in membership:
        if begin < dt <= latest_signal:
            symbols.update(tickers)
    symbols.update(base.members_at(membership, latest_signal))
    symbols.add('QQQ')
    symbols = sorted(symbols)
    print('NDX union symbols', len(symbols), flush=True)

    price_start = str((pd.Period(START, freq='M') - 7).start_time.date())
    price_end = str((pd.Period(END, freq='M') + 2).start_time.date())
    prices = base.download_prices(symbols, price_start, price_end)
    yahoo_recovery = recover_yahoo_prices(prices, symbols, price_start, price_end)
    print('Yahoo after raw/alias coverage', len(prices), '/', len(symbols), flush=True)

    wiki = load_wiki_prices([s for s in symbols if s != 'QQQ'], start=price_start)
    wiki_recovery = merge_wiki(prices, wiki)
    print('hybrid price coverage', len(prices), '/', len(symbols), flush=True)

    shares = base.load_shares([s for s in symbols if s != 'QQQ'], price_start, price_end)

    valid = {}
    failures = {}
    qqq = {}
    m = START
    while m <= END:
        q = base.month_return(prices, 'QQQ', m)
        if q:
            qqq[m] = float(q['return']) * 100.0
        try:
            rec = normalize_production_coverage(base.strategy_month('QUQU-MAX-PIT', membership, prices, shares, m))
            if m not in qqq:
                raise RuntimeError('QQQ return unavailable')
            valid[m] = rec
            print(
                f"OK {m} QUQU={rec['portfolioReturn']:+.2f}% holdings={rec['holdingsCount']} "
                f"coverage={rec['coverageRatio']:.1%} histCap={rec.get('historicalCapCount')} "
                f"proxy={rec.get('currentSharesProxyCount')} medianCap={rec.get('medianCapFallbackCount')}", flush=True
            )
        except Exception as e:
            failures[m] = str(e)
            print('FAIL', m, e, flush=True)
        m = base.month_add(m, 1)

    suffix = contiguous_suffix(sorted(valid), END)
    if len(suffix) < 48:
        raise RuntimeError(f'final contiguous PIT suffix only {len(suffix)} months: {suffix[:1]}..{suffix[-1:] if suffix else []}')
    records = [(m, valid[m]) for m in suffix]
    base_rows = run_rule(records, qqq)
    headline = summarize_rows(base_rows)

    sensitivity = []
    for k in [1.0, 1.25, 1.5, 1.75, 2.0, 2.5]:
        r = run_rule(records, qqq, tukey_k=k)
        sensitivity.append({'dimension': 'tukey_k', 'value': k, 'metrics': metrics([x['QUU'] for x in r]), 'rolloverMonths': [x['month'] for x in r if x['rollover']]})
    for q in [0.40, 0.45, 0.50, 0.55, 0.60]:
        r = run_rule(records, qqq, leadership_q=q)
        sensitivity.append({'dimension': 'leadership_quantile', 'value': q, 'metrics': metrics([x['QUU'] for x in r]), 'rolloverMonths': [x['month'] for x in r if x['rollover']]})
    for d in [0.0, 0.05, 0.10, 0.20]:
        r = run_rule(records, qqq, min_drop=d)
        sensitivity.append({'dimension': 'rollover_min_drop', 'value': d, 'metrics': metrics([x['QUU'] for x in r]), 'rolloverMonths': [x['month'] for x in r if x['rollover']]})

    split_defs = [
        ('2007-08_to_2011-12', '2007-08', '2011-12'),
        ('2012_to_2016', '2012-01', '2016-12'),
        ('2017_to_2021', '2017-01', '2021-12'),
        ('2022_to_2026-09', '2022-01', '2026-09'),
    ]
    subperiods = {}
    for name, a, b in split_defs:
        rr = [x for x in base_rows if a <= x['month'] <= b]
        if rr:
            subperiods[name] = summarize_rows(rr)

    trigger_months = [x['month'] for x in base_rows if x['rollover']]
    event_dependence = []
    for t in trigger_months:
        rs = [x['QUQU'] if x['month'] == t else x['QUU'] for x in base_rows]
        event_dependence.append({'removedTrigger': t, 'metrics': metrics(rs)})

    leadership_months = [x for x in base_rows if x['leadership']]
    k = len(trigger_months)
    actual_end = headline['QUU']['endingIndex']
    rng = random.Random(20261002)
    mc = []
    if k and len(leadership_months) >= k:
        for _ in range(10000):
            chosen = {x['month'] for x in rng.sample(leadership_months, k)}
            rs = [(x['QQQ'] if x['month'] in chosen else (x['QUQU'] if x['leadership'] else x['QQQ'])) for x in base_rows]
            mc.append(metrics(rs)['endingIndex'])
    pct = (100.0 * sum(v <= actual_end for v in mc) / len(mc)) if mc else None

    data_quality = {
        'attemptedStart': START,
        'attemptedEnd': END,
        'attemptedMonths': len(pd.period_range(START, END, freq='M')),
        'validMonths': len(valid),
        'failedMonths': len(failures),
        'finalContiguousStart': suffix[0],
        'finalContiguousEnd': suffix[-1],
        'finalContiguousMonths': len(suffix),
        'minimumCoverageRatio': min(float(valid[m].get('coverageRatio') or 1.0) for m in suffix),
        'minimumHoldingsCount': min(int(valid[m].get('holdingsCount') or 0) for m in suffix),
        'maxMedianCapFallbackCount': max(int(valid[m].get('medianCapFallbackCount') or 0) for m in suffix),
        'minHistoricalCapCount': min(int(valid[m].get('historicalCapCount') or 0) for m in suffix),
        'maxCurrentSharesProxyCount': max(int(valid[m].get('currentSharesProxyCount') or 0) for m in suffix),
        'failuresBeforeContiguousStart': {k: failures[k] for k in sorted(failures) if k < suffix[0]},
        'priceSources': {
            'Yahoo': 'yfinance adjusted prices plus raw Yahoo chart recovery',
            'Wiki': 'Quandl WIKI public Kaggle mirror through 2018-03-27; includes delisted equities',
            'sameCompanyAliases': yahoo_recovery['aliases'],
            'unresolvedYahooSymbols': yahoo_recovery['stillMissing'],
            'wikiNewSymbolCount': len(wiki_recovery['newSymbols']),
            'wikiExtendedSymbolCount': len(wiki_recovery['extendedSymbols']),
        },
        'marketCapCaveat': 'Historical Yahoo shares are used when available; otherwise current-share proxies or cross-sectional median caps are used. Pre-2018 delisted names can therefore have approximate market caps even when prices are recovered from WIKI. Counts are reported; long-history results are research-grade, not licensed PIT fundamentals.',
    }

    out = {
        'generatedAt': datetime.now(timezone.utc).isoformat(),
        'fixedRule': {
            'leadership': 'dispersion > expanding prior median',
            'extreme': 'dispersion > expanding prior Q3 + 1.5×IQR',
            'rollover': 'extreme AND dispersion < previous month dispersion',
            'allocation': 'QUQU when leadership strong and no rollover; otherwise QQQ',
            'parametersFrozen': True,
        },
        'membershipSourceStart': '2007-02-01',
        'earliestAttemptedAllocationMonth': START,
        'dataQuality': data_quality,
        'headline': headline,
        'sensitivity': sensitivity,
        'subperiods': subperiods,
        'eventDependence': event_dependence,
        'randomSameCountSwitchSanity': {
            'trials': len(mc), 'actualEndingIndex': actual_end,
            'percentileVsRandomSameCountSwitches': pct,
            'randomMedianEndingIndex': float(np.median(mc)) if mc else None,
            'random95thEndingIndex': float(np.quantile(mc, 0.95)) if mc else None,
        },
        'failures': failures,
        'months': base_rows,
    }
    Path('quu-max-pit-validation.json').write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({
        'dataQuality': data_quality,
        'headline': headline,
        'randomSameCountSwitchSanity': out['randomSameCountSwitchSanity'],
    }, ensure_ascii=False, indent=2, allow_nan=False), flush=True)


if __name__ == '__main__':
    main()
