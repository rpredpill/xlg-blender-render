#!/usr/bin/env python3
"""QUQU v2 ablation on a monthly point-in-time Nasdaq-listed universe.

Ablation ladder
---------------
0. Composite TotalCap base
1. + FloatCap proxy
2. + 63D volatility adjustment (exponent 0.25)
3. + 63D median-dollar-volume bottom-decile exclusion
4. + cross-sectional ±3 z-score winsorization

The existing Nasdaq-100 QUQU history is reported as a separate legacy benchmark,
not as an ablation step, because changing Nasdaq-100 -> Nasdaq Composite is itself
a material universe change.

Important free-data limitation
------------------------------
Historical total shares are taken from Yahoo historical shares when available.
Free-float uses the latest available Yahoo floatShares / sharesOutstanding ratio
as a proxy and applies it to contemporaneous total market cap.  Missing float
ratios default to 1.0 and are explicitly counted.  This is NOT described as a
fully point-in-time historical free-float series.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

CAP = 0.20
TOP_PCT = 0.10
FLOAT_BUFFER_MULTIPLE = 5
VOL_LOOKBACK = 63
LIQ_LOOKBACK = 63
VOL_EXPONENT = 0.25
WINSOR_Z = 3.0
MIN_SIGNAL_FRESH_DAYS = 12
MIN_VOL_OBS = 40
MIN_LIQ_OBS = 20
MISSING_RETURN_LIMIT = 0.02

VARIANTS = [
    "Composite TotalCap base",
    "+ FloatCap",
    "+ Vol",
    "+ Liquidity",
    "+ Winsorization",
]


def norm_symbol(x: str) -> str:
    return str(x or "").strip().upper().replace("/", ".").replace("-", ".")


def yf_symbol(x: str) -> str:
    return norm_symbol(x).replace(".", "-")


def month_add(ym: str, n: int) -> str:
    return str(pd.Period(ym, freq="M") + n)


def month_end(ym: str) -> pd.Timestamp:
    return pd.Period(ym, freq="M").end_time.normalize()


def next_month_start(ym: str) -> pd.Timestamp:
    return (pd.Period(ym, freq="M") + 1).start_time.normalize()


def cap_and_redistribute(raw: dict[str, float], cap: float = CAP) -> dict[str, float]:
    free = set(raw)
    out = {}
    remaining = 1.0
    while free:
        total = sum(raw[s] for s in free)
        if not math.isfinite(total) or total <= 0:
            raise RuntimeError("raw weight sum <= 0")
        over = [s for s in free if remaining * raw[s] / total > cap + 1e-12]
        if not over:
            for s in free:
                out[s] = remaining * raw[s] / total
            break
        for s in over:
            out[s] = cap
            remaining -= cap
            free.remove(s)
        if remaining < -1e-10:
            raise RuntimeError("cap redistribution underflow")
    return out


def extract_one(data: pd.DataFrame, symbol: str):
    yt = yf_symbol(symbol)
    try:
        if isinstance(data.columns, pd.MultiIndex):
            if yt in data.columns.get_level_values(1):
                d = data.xs(yt, axis=1, level=1, drop_level=True).copy()
            elif yt in data.columns.get_level_values(0):
                d = data.xs(yt, axis=1, level=0, drop_level=True).copy()
            else:
                return None
        else:
            d = data.copy()
        if "Close" not in d.columns:
            return None
        if "Adj Close" not in d.columns:
            d["Adj Close"] = d["Close"]
        if "Open" not in d.columns:
            d["Open"] = np.nan
        if "Volume" not in d.columns:
            d["Volume"] = np.nan
        d = d[["Open", "Close", "Adj Close", "Volume"]].copy()
        d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
        d = d[~d.index.duplicated(keep="last")].sort_index()
        d = d.dropna(how="all")
        return d if not d.empty else None
    except Exception:
        return None


def download_prices(symbols: list[str], start: str, end: str):
    """Bulk-download OHLCV. Retry missing symbols in smaller batches."""
    out = {}

    def run_batches(todo: list[str], size: int, threads):
        for i in range(0, len(todo), size):
            part = todo[i:i + size]
            ypart = [yf_symbol(s) for s in part]
            try:
                data = yf.download(
                    tickers=ypart,
                    start=start,
                    end=end,
                    auto_adjust=False,
                    actions=False,
                    repair=False,
                    progress=False,
                    group_by="column",
                    threads=threads,
                    timeout=25,
                )
            except Exception:
                data = pd.DataFrame()
            if not data.empty:
                for s in part:
                    d = extract_one(data, s)
                    if d is not None and len(d):
                        out[s] = d
            print(
                f"prices pass size={size}: {min(i+len(part), len(todo))}/{len(todo)} loaded={len(out)}",
                flush=True,
            )
            time.sleep(0.05)

    run_batches(symbols, 80, 8)
    missing = [s for s in symbols if s not in out]
    if missing:
        print(f"price retry missing={len(missing)}", flush=True)
        run_batches(missing, 20, 4)
    return out


def last_value(d: pd.DataFrame, col: str, when: pd.Timestamp):
    if d is None or col not in d.columns:
        return None, None
    s = pd.to_numeric(d.loc[d.index <= when, col], errors="coerce").dropna()
    if s.empty:
        return None, None
    v = float(s.iloc[-1])
    dt = s.index[-1]
    if not math.isfinite(v) or v <= 0:
        return None, None
    return v, dt


def month_return(d: pd.DataFrame, ym: str):
    if d is None:
        return None
    start = pd.Period(ym, freq="M").start_time.normalize()
    end = next_month_start(ym)
    g = d[(d.index >= start) & (d.index < end)].copy()
    if g.empty:
        return None
    g = g.dropna(subset=["Close", "Adj Close"])
    if g.empty:
        return None
    first = g.iloc[0]
    last = g.iloc[-1]
    vals = [first.get("Open"), first.get("Close"), first.get("Adj Close"), last.get("Adj Close")]
    if any(pd.isna(x) for x in vals):
        return None
    raw_open, raw_first, adj_first, adj_last = map(float, vals)
    if min(raw_open, raw_first, adj_first, adj_last) <= 0:
        return None
    adj_open = raw_open * (adj_first / raw_first)
    rel = adj_last / adj_open
    if not math.isfinite(rel) or rel <= 0:
        return None
    return rel - 1.0


def trailing_stats(d: pd.DataFrame, signal_date: pd.Timestamp):
    g = d[d.index <= signal_date].tail(max(VOL_LOOKBACK + 1, LIQ_LOOKBACK)).copy()
    if g.empty:
        return None, None, 0, 0

    adj = pd.to_numeric(g["Adj Close"], errors="coerce").dropna()
    rets = adj.pct_change().dropna().tail(VOL_LOOKBACK)
    sigma = float(rets.std(ddof=1)) if len(rets) >= MIN_VOL_OBS else None
    if sigma is not None and (not math.isfinite(sigma) or sigma <= 0):
        sigma = None

    close = pd.to_numeric(g["Close"], errors="coerce")
    volume = pd.to_numeric(g["Volume"], errors="coerce")
    dv = (close * volume).replace([np.inf, -np.inf], np.nan).dropna().tail(LIQ_LOOKBACK)
    adv = float(dv.median()) if len(dv) >= MIN_LIQ_OBS else None
    if adv is not None and (not math.isfinite(adv) or adv < 0):
        adv = None
    return sigma, adv, len(rets), len(dv)


def fetch_share_info(symbol: str, start: str, end: str):
    series = None
    proxy = None
    proxy_method = None
    try:
        q = yf.Ticker(yf_symbol(symbol))
        try:
            s = q.get_shares_full(start=start, end=end)
            if s is not None and len(s):
                s = pd.to_numeric(s, errors="coerce").dropna()
                s = s[(s > 0) & np.isfinite(s)]
                if len(s):
                    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
                    s = s[~s.index.duplicated(keep="last")].sort_index()
                    series = s
                    proxy = float(s.iloc[-1])
                    proxy_method = "Yahoo historical shares latest"
        except Exception:
            pass
        try:
            fi = q.fast_info
            mc = fi.get("market_cap")
            lp = fi.get("last_price")
            if mc and lp and float(mc) > 0 and float(lp) > 0:
                p = float(mc) / float(lp)
                if math.isfinite(p) and p > 0:
                    proxy = p
                    proxy_method = "Yahoo current market-cap/share proxy"
        except Exception:
            pass
    except Exception:
        pass
    return symbol, series, proxy, proxy_method


def load_shares(symbols: list[str], start: str, end: str, workers: int):
    out = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_share_info, s, start, end): s for s in symbols}
        done = 0
        for fut in as_completed(futs):
            s = futs[fut]
            try:
                sym, series, proxy, method = fut.result()
                out[sym] = {"series": series, "proxy": proxy, "proxyMethod": method}
            except Exception:
                out[s] = {"series": None, "proxy": None, "proxyMethod": None}
            done += 1
            if done % 250 == 0 or done == len(symbols):
                hist = sum(1 for x in out.values() if x["series"] is not None)
                proxy = sum(1 for x in out.values() if x["proxy"] is not None)
                print(f"shares {done}/{len(symbols)} historical={hist} proxy={proxy}", flush=True)
    return out


def historical_cap(d: pd.DataFrame, share_info: dict, signal_date: pd.Timestamp):
    raw_close, _ = last_value(d, "Close", signal_date)
    adj_close, _ = last_value(d, "Adj Close", signal_date)
    s = share_info.get("series")
    if s is not None and len(s) and raw_close:
        prior = s.loc[s.index <= signal_date]
        if not prior.empty:
            sh = float(prior.iloc[-1])
            if math.isfinite(sh) and sh > 0:
                return sh * raw_close, "historical-shares"
    proxy = share_info.get("proxy")
    if proxy and adj_close:
        p = float(proxy)
        if math.isfinite(p) and p > 0:
            return p * adj_close, "current-shares-proxy"
    return None, "missing"


def fetch_float_info(symbol: str, share_info: dict):
    ratio = None
    float_shares = None
    outstanding = None
    quote_type = None
    method = None
    try:
        q = yf.Ticker(yf_symbol(symbol))
        info = q.info or {}
        quote_type = str(info.get("quoteType") or "").upper() or None
        fs = info.get("floatShares")
        os_ = info.get("sharesOutstanding")
        if fs is not None:
            float_shares = float(fs)
        if os_ is not None:
            outstanding = float(os_)
        if (not outstanding or outstanding <= 0) and share_info.get("proxy"):
            outstanding = float(share_info["proxy"])
        if float_shares and outstanding and float_shares > 0 and outstanding > 0:
            r = float_shares / outstanding
            if math.isfinite(r) and 0 < r <= 1.25:
                ratio = min(1.0, r)
                method = "latest Yahoo floatShares / sharesOutstanding"
    except Exception:
        pass
    return symbol, ratio, float_shares, outstanding, quote_type, method


def load_float_info(symbols: list[str], shares: dict, workers: int):
    out = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_float_info, s, shares.get(s) or {}): s for s in symbols}
        done = 0
        for fut in as_completed(futs):
            s = futs[fut]
            try:
                sym, ratio, fs, os_, qt, method = fut.result()
                out[sym] = {
                    "ratio": ratio,
                    "floatShares": fs,
                    "sharesOutstanding": os_,
                    "quoteType": qt,
                    "method": method,
                }
            except Exception:
                out[s] = {"ratio": None, "floatShares": None, "sharesOutstanding": None, "quoteType": None, "method": None}
            done += 1
            if done % 200 == 0 or done == len(symbols):
                known = sum(1 for x in out.values() if x.get("ratio") is not None)
                print(f"float {done}/{len(symbols)} known={known}", flush=True)
    return out


def build_month_rows(allocation_month: str, pit_rec: dict, prices: dict, shares: dict):
    signal_month = month_add(allocation_month, -1)
    early_month = month_add(allocation_month, -6)
    signal_date = month_end(signal_month)
    early_date = month_end(early_month)
    rows = []
    cap_methods = {}

    for symbol in pit_rec.get("symbols") or []:
        s = norm_symbol(symbol)
        d = prices.get(s)
        if d is None:
            continue
        recent, recent_dt = last_value(d, "Adj Close", signal_date)
        if recent is None or recent_dt is None:
            continue
        if (signal_date - recent_dt).days > MIN_SIGNAL_FRESH_DAYS:
            continue
        early, _ = last_value(d, "Adj Close", early_date)
        cap, cap_method = historical_cap(d, shares.get(s) or {}, signal_date)
        if cap is None or not math.isfinite(cap) or cap <= 0:
            continue
        sigma, adv, vol_obs, liq_obs = trailing_stats(d, signal_date)
        mr = month_return(d, allocation_month)
        rows.append({
            "ticker": s,
            "recentPrice": recent,
            "earlyPrice": early,
            "grossMomentum": (recent / early) if early and early > 0 else None,
            "marketCap": cap,
            "capMethod": cap_method,
            "sigma63": sigma,
            "adv63": adv,
            "volObs": vol_obs,
            "liqObs": liq_obs,
            "monthlyReturn": mr,
        })
        cap_methods[cap_method] = cap_methods.get(cap_method, 0) + 1

    rows.sort(key=lambda x: x["marketCap"], reverse=True)
    target_k = max(10, int(math.ceil(float(pit_rec.get("count") or len(pit_rec.get("symbols") or [])) * TOP_PCT)))
    return {
        "allocationMonth": allocation_month,
        "signalMonth": signal_month,
        "signalDate": str(signal_date.date()),
        "earlyDate": str(early_date.date()),
        "pitEligibleCount": int(pit_rec.get("count") or len(pit_rec.get("symbols") or [])),
        "pricedCapCount": len(rows),
        "targetK": target_k,
        "capMethods": cap_methods,
        "rows": rows,
    }


def select_total_cap(month: dict):
    k = month["targetK"]
    rows = month["rows"]
    if len(rows) < k:
        raise RuntimeError(f"cap coverage {len(rows)} < targetK {k}")
    return [dict(r) for r in rows[:k]]


def with_float_caps(month: dict, float_info: dict):
    k = month["targetK"]
    rows = month["rows"]
    if len(rows) < k:
        raise RuntimeError(f"cap coverage {len(rows)} < targetK {k}")
    buffer_n = min(len(rows), max(k, FLOAT_BUFFER_MULTIPLE * k))
    buf = []
    known = 0
    for r in rows[:buffer_n]:
        x = dict(r)
        fi = float_info.get(x["ticker"]) or {}
        ratio = fi.get("ratio")
        if ratio is None:
            ratio = 1.0
            x["floatMethod"] = "missing-assume-1.0"
        else:
            ratio = float(ratio)
            known += 1
            x["floatMethod"] = fi.get("method")
        x["floatRatio"] = ratio
        x["floatCap"] = x["marketCap"] * ratio
        x["quoteType"] = fi.get("quoteType")
        buf.append(x)
    buf.sort(key=lambda x: x["floatCap"], reverse=True)
    selected = buf[:k]
    kth = selected[-1]["floatCap"]
    outside_max_total_cap = rows[buffer_n]["marketCap"] if buffer_n < len(rows) else 0.0
    selection_proven = kth >= outside_max_total_cap - 1e-9
    return selected, {
        "bufferN": buffer_n,
        "floatKnownInBuffer": known,
        "floatKnownPctInBuffer": 100.0 * known / buffer_n if buffer_n else None,
        "kthFloatCap": kth,
        "outsideBufferMaxTotalCap": outside_max_total_cap,
        "selectionProvenAgainstOutsideBuffer": bool(selection_proven),
        "selectedFloatKnownCount": sum(1 for x in selected if x["floatMethod"] != "missing-assume-1.0"),
    }


def fill_momentum(rows: list[dict]):
    valid = [float(r["grossMomentum"]) for r in rows if r.get("grossMomentum") and float(r["grossMomentum"]) > 0]
    if not valid:
        raise RuntimeError("no valid momentum observations")
    med = float(np.median(valid))
    out = []
    fallback = 0
    for r in rows:
        x = dict(r)
        g = x.get("grossMomentum")
        if g is None or not math.isfinite(float(g)) or float(g) <= 0:
            g = med
            x["momentumFallback"] = True
            fallback += 1
        else:
            g = float(g)
            x["momentumFallback"] = False
        x["grossMomentumUsed"] = g
        x["excessMomentum"] = g - 1.0
        out.append(x)
    return out, med, fallback


def apply_vol(rows: list[dict]):
    sigmas = [float(r["sigma63"]) for r in rows if r.get("sigma63") and float(r["sigma63"]) > 0]
    if not sigmas:
        raise RuntimeError("no valid volatility observations")
    sigma_med = float(np.median(sigmas))
    out = []
    fallback = 0
    for r in rows:
        x = dict(r)
        sigma = x.get("sigma63")
        if sigma is None or not math.isfinite(float(sigma)) or float(sigma) <= 0:
            sigma = sigma_med
            fallback += 1
            x["volFallback"] = True
        else:
            sigma = float(sigma)
            x["volFallback"] = False
        scale = (sigma_med / sigma) ** VOL_EXPONENT
        x["volScale"] = scale
        x["volAdjustedExcess"] = x["excessMomentum"] * scale
        x["volAdjustedGross"] = max(1e-6, 1.0 + x["volAdjustedExcess"])
        out.append(x)
    return out, sigma_med, fallback


def apply_liquidity(rows: list[dict]):
    if len(rows) < 10:
        raise RuntimeError("too few rows for liquidity decile")
    n_remove = max(1, int(math.floor(len(rows) * 0.10)))
    ordered = sorted(
        rows,
        key=lambda r: (float(r["adv63"]) if r.get("adv63") is not None and math.isfinite(float(r["adv63"])) else -1.0),
    )
    removed = ordered[:n_remove]
    kept = ordered[n_remove:]
    return kept, removed


def apply_winsor(rows: list[dict]):
    vals = np.asarray([float(r["volAdjustedExcess"]) for r in rows], dtype=float)
    mu = float(vals.mean())
    sd = float(vals.std(ddof=0))
    out = []
    clipped = 0
    for r in rows:
        x = dict(r)
        v = float(x["volAdjustedExcess"])
        if sd > 0 and math.isfinite(sd):
            z = (v - mu) / sd
            zc = float(np.clip(z, -WINSOR_Z, WINSOR_Z))
            if abs(zc - z) > 1e-12:
                clipped += 1
            v2 = mu + zc * sd
        else:
            z = 0.0
            zc = 0.0
            v2 = v
        x["winsorZ"] = zc
        x["winsorExcess"] = v2
        x["winsorGross"] = max(1e-6, 1.0 + v2)
        out.append(x)
    return out, {"mean": mu, "std": sd, "clippedCount": clipped}


def weights_and_return(rows: list[dict], cap_field: str, gross_field: str):
    if not rows:
        raise RuntimeError("empty variant")
    gross = [float(r[gross_field]) for r in rows if r.get(gross_field) and float(r[gross_field]) > 0]
    if not gross:
        raise RuntimeError("no positive gross signals")
    med = float(np.median(gross))
    raw = {}
    by_ticker = {}
    for r in rows:
        t = r["ticker"]
        capv = float(r[cap_field])
        g = float(r[gross_field])
        if capv <= 0 or g <= 0:
            continue
        raw[t] = math.sqrt(capv) * ((g / med) ** 3)
        by_ticker[t] = r
    if len(raw) < 5:
        raise RuntimeError(f"only {len(raw)} weightable rows")
    weights = cap_and_redistribute(raw)
    missing_w = sum(weights[t] for t, r in by_ticker.items() if not isinstance(r.get("monthlyReturn"), (int, float)))
    if missing_w > MISSING_RETURN_LIMIT:
        raise RuntimeError(f"missing return weight {missing_w:.2%}")
    ret = sum(
        weights[t] * (float(r["monthlyReturn"]) if isinstance(r.get("monthlyReturn"), (int, float)) else 0.0)
        for t, r in by_ticker.items()
    )
    top = sorted(weights.values(), reverse=True)
    stock_returns = {t: by_ticker[t].get("monthlyReturn") for t in weights}
    return weights, stock_returns, ret, {
        "holdings": len(weights),
        "signalMedianGross": med,
        "maxWeight": max(top),
        "top10Weight": sum(top[:10]),
        "missingReturnWeight": missing_w,
    }


def drifted_turnover(prev_weights: dict | None, prev_stock_returns: dict | None, new_weights: dict):
    if not prev_weights:
        return None
    drift = {}
    total = 0.0
    for t, w in prev_weights.items():
        r = (prev_stock_returns or {}).get(t)
        rr = float(r) if isinstance(r, (int, float)) else 0.0
        v = float(w) * max(0.0, 1.0 + rr)
        drift[t] = v
        total += v
    if total <= 0:
        drift = dict(prev_weights)
        total = sum(drift.values())
    drift = {t: v / total for t, v in drift.items()}
    names = set(drift) | set(new_weights)
    return 0.5 * sum(abs(float(new_weights.get(t, 0.0)) - float(drift.get(t, 0.0))) for t in names)


def perf_metrics(returns: list[float], turnovers: list[float | None]):
    arr = np.asarray(returns, dtype=float)
    eq = np.concatenate([[1.0], np.cumprod(1.0 + arr)])
    peaks = np.maximum.accumulate(eq)
    dd = eq / peaks - 1.0
    n = len(arr)
    cagr = float(eq[-1] ** (12.0 / n) - 1.0) if n else None
    mdd = float(dd.min()) if n else None
    sharpe = None
    if n >= 2:
        sd = float(arr.std(ddof=1))
        if sd > 0:
            sharpe = float(arr.mean() / sd * math.sqrt(12.0))
    ts = [float(x) for x in turnovers if isinstance(x, (int, float))]
    return {
        "months": n,
        "totalReturnPct": float((eq[-1] - 1.0) * 100.0) if n else None,
        "CAGRpct": cagr * 100.0 if cagr is not None else None,
        "MDDpct": mdd * 100.0 if mdd is not None else None,
        "Sharpe0rf": sharpe,
        "avgMonthlyTurnoverPct": float(np.mean(ts) * 100.0) if ts else None,
        "annualizedTurnoverPct": float(np.mean(ts) * 12.0 * 100.0) if ts else None,
        "endingIndex": float(eq[-1] * 100.0) if n else None,
        "positiveMonthRatePct": float(np.mean(arr > 0) * 100.0) if n else None,
        "bestMonthPct": float(arr.max() * 100.0) if n else None,
        "worstMonthPct": float(arr.min() * 100.0) if n else None,
    }


def load_legacy(path: Path, months: list[str]):
    if not path.exists():
        return None
    try:
        j = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    vals = []
    used = []
    for m in months:
        r = (j.get("months") or {}).get(m) or {}
        if isinstance(r.get("portfolioReturn"), (int, float)):
            vals.append(float(r["portfolioReturn"]) / 100.0)
            used.append(m)
    if not vals:
        return None
    return {
        "period": {"start": used[0], "end": used[-1], "months": len(used)},
        "metrics": perf_metrics(vals, []),
        "note": "Existing Nasdaq-100 QUQU; separate benchmark because its universe differs from the v2 ablation universe.",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pit", default="nasdaq-composite-pit.json")
    ap.add_argument("--legacy", default="ququ-history.json")
    ap.add_argument("--start", default="2022-10")
    ap.add_argument("--end", default="2026-09")
    ap.add_argument("--output", default="ququ-v2-ablation.json")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    pit = json.loads(Path(args.pit).read_text(encoding="utf-8"))
    pit_months = pit.get("months") or {}
    months = []
    cur = args.start
    while pd.Period(cur, freq="M") <= pd.Period(args.end, freq="M"):
        sig = month_add(cur, -1)
        if sig in pit_months:
            months.append(cur)
        cur = month_add(cur, 1)
    if len(months) < 12:
        raise RuntimeError(f"only {len(months)} allocation months have PIT signal universes")

    required_signal_months = {month_add(m, -1) for m in months}
    symbols = sorted({norm_symbol(s) for sm in required_signal_months for s in (pit_months[sm].get("symbols") or [])})
    print(f"allocation months={len(months)} {months[0]}..{months[-1]} union symbols={len(symbols)}", flush=True)

    price_start = str((pd.Period(months[0], freq="M") - 8).start_time.date())
    price_end = str((pd.Period(months[-1], freq="M") + 2).start_time.date())
    prices = download_prices(symbols, price_start, price_end)
    price_symbols = sorted(prices)
    print(f"price coverage={len(price_symbols)}/{len(symbols)}", flush=True)
    if len(price_symbols) < 1000:
        raise RuntimeError("price coverage implausibly low")

    shares = load_shares(price_symbols, price_start, price_end, args.workers)

    month_panels = {}
    float_candidates = set()
    for m in months:
        sig = month_add(m, -1)
        panel = build_month_rows(m, pit_months[sig], prices, shares)
        month_panels[m] = panel
        k = panel["targetK"]
        buf_n = min(len(panel["rows"]), max(k, FLOAT_BUFFER_MULTIPLE * k))
        float_candidates.update(r["ticker"] for r in panel["rows"][:buf_n])
        print(
            f"panel {m}: PIT={panel['pitEligibleCount']} capRows={panel['pricedCapCount']} "
            f"k={k} floatBuffer={buf_n}", flush=True
        )

    float_symbols = sorted(float_candidates)
    print(f"float candidate union={len(float_symbols)}", flush=True)
    float_info = load_float_info(float_symbols, shares, args.workers)

    monthly_output = []
    series = {v: [] for v in VARIANTS}
    turnovers = {v: [] for v in VARIANTS}
    prev_weights = {v: None for v in VARIANTS}
    prev_stock_returns = {v: None for v in VARIANTS}
    failed = {}

    for m in months:
        panel = month_panels[m]
        record = {
            "month": m,
            "signalMonth": panel["signalMonth"],
            "signalDate": panel["signalDate"],
            "pitEligibleCount": panel["pitEligibleCount"],
            "pricedCapCount": panel["pricedCapCount"],
            "targetK": panel["targetK"],
            "capMethods": panel["capMethods"],
            "variants": {},
        }
        try:
            base = select_total_cap(panel)
            base, base_mom_med, base_mom_fb = fill_momentum(base)

            flt, float_diag = with_float_caps(panel, float_info)
            flt, flt_mom_med, flt_mom_fb = fill_momentum(flt)
            vol, sigma_med, vol_fb = apply_vol(flt)
            liq, removed_liq = apply_liquidity(vol)
            win, winsor_diag = apply_winsor(liq)

            definitions = [
                ("Composite TotalCap base", base, "marketCap", "grossMomentumUsed"),
                ("+ FloatCap", flt, "floatCap", "grossMomentumUsed"),
                ("+ Vol", vol, "floatCap", "volAdjustedGross"),
                ("+ Liquidity", liq, "floatCap", "volAdjustedGross"),
                ("+ Winsorization", win, "floatCap", "winsorGross"),
            ]

            diagnostics = {
                "baseMomentumMedian": base_mom_med,
                "baseMomentumFallbackCount": base_mom_fb,
                "floatMomentumMedian": flt_mom_med,
                "floatMomentumFallbackCount": flt_mom_fb,
                "float": float_diag,
                "sigmaMedian63": sigma_med,
                "volFallbackCount": vol_fb,
                "liquidityRemovedCount": len(removed_liq),
                "liquidityRemovedSample": [x["ticker"] for x in removed_liq[:20]],
                "winsor": winsor_diag,
            }
            record["diagnostics"] = diagnostics

            for name, rows, cap_field, gross_field in definitions:
                w, stock_r, ret, meta = weights_and_return(rows, cap_field, gross_field)
                to = drifted_turnover(prev_weights[name], prev_stock_returns[name], w)
                series[name].append(ret)
                turnovers[name].append(to)
                prev_weights[name] = w
                prev_stock_returns[name] = stock_r
                record["variants"][name] = {
                    "returnPct": ret * 100.0,
                    "turnoverPct": to * 100.0 if to is not None else None,
                    **meta,
                    "topHoldings": [
                        {"ticker": t, "weight": wt}
                        for t, wt in sorted(w.items(), key=lambda x: x[1], reverse=True)[:25]
                    ],
                }
            monthly_output.append(record)
            print(
                f"ablation {m}: " + " | ".join(f"{v}={record['variants'][v]['returnPct']:+.2f}%" for v in VARIANTS),
                flush=True,
            )
        except Exception as exc:
            failed[m] = str(exc)
            print(f"WARN ablation {m}: {exc}", flush=True)

    # The ladder must be compared on identical successful months.  Because one
    # month is generated atomically for all variants, the arrays are aligned.
    common_n = min(len(series[v]) for v in VARIANTS)
    if common_n < 12:
        raise RuntimeError(f"only {common_n} successful common months; failures={failed}")
    metrics = {v: perf_metrics(series[v][:common_n], turnovers[v][:common_n]) for v in VARIANTS}

    increments = []
    for before, after in zip(VARIANTS[:-1], VARIANTS[1:]):
        a, b = metrics[before], metrics[after]
        increments.append({
            "from": before,
            "to": after,
            "deltaCAGRpctPoints": b["CAGRpct"] - a["CAGRpct"],
            "deltaMDDpctPoints": b["MDDpct"] - a["MDDpct"],
            "deltaSharpe": (b["Sharpe0rf"] - a["Sharpe0rf"]) if b["Sharpe0rf"] is not None and a["Sharpe0rf"] is not None else None,
            "deltaAnnualizedTurnoverPctPoints": (
                b["annualizedTurnoverPct"] - a["annualizedTurnoverPct"]
                if b["annualizedTurnoverPct"] is not None and a["annualizedTurnoverPct"] is not None else None
            ),
        })

    successful_months = [r["month"] for r in monthly_output]
    out = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "strategy": "QUQU v2 ablation",
        "period": {
            "requestedStart": args.start,
            "requestedEnd": args.end,
            "successfulStart": successful_months[0] if successful_months else None,
            "successfulEnd": successful_months[-1] if successful_months else None,
            "successfulMonths": len(successful_months),
        },
        "universe": {
            "name": "Nasdaq Composite proxy from PIT Nasdaq-listed monthly reports",
            "pitSource": pit.get("sourcePage"),
            "pitDatasetGeneratedAt": pit.get("generatedAt"),
            "eligibilityPolicy": pit.get("eligibilityPolicy") or pit.get("caveat"),
            "selection": "Top 10% by contemporaneous capitalization; FloatCap variants rank by float-adjusted cap proxy.",
        },
        "rules": {
            "legacyCore": "sqrt(cap) × (relative 6-1 gross momentum)^3; 20% single-name cap; monthly rebalance",
            "momentum": "P(signal month end) / P(allocation month - 6 month end) - 1; insufficient 6-1 history uses selected cross-sectional median gross momentum",
            "totalCap": "Yahoo historical shares × raw close when available; otherwise split-safe current-share proxy × adjusted close",
            "floatCap": "historical total cap × latest available Yahoo floatShares/sharesOutstanding ratio; missing ratio assumes 1.0 and is audited",
            "floatSelectionBuffer": f"Fetch float data for up to {FLOAT_BUFFER_MULTIPLE}× target count by total-cap rank; verify kth float cap >= max total cap outside buffer",
            "volatility": f"63-trading-day adjusted-close return standard deviation; adjusted excess momentum = excess × (cross-sectional median sigma / stock sigma)^{VOL_EXPONENT}",
            "liquidity": "Remove bottom 10% of FloatCap-selected holdings by trailing 63-trading-day median raw Close × Volume",
            "winsorization": "Cross-sectional z-score of vol-adjusted excess momentum; clip z to ±3 and map back to excess-return units",
            "turnover": "One-way monthly turnover = 0.5 × sum absolute difference between new target and prior target drifted by prior realized stock returns",
            "return": "Adjusted first trading-day open to adjusted last observed close within allocation month; >2% missing-return weight rejects month",
        },
        "freeDataCaveat": (
            "Historical Nasdaq Public Float is a paid Fundamental Data field. This free-data study therefore uses a latest-float-ratio proxy, "
            "reports its coverage, and must not be labeled a fully point-in-time free-float backtest."
        ),
        "metrics": metrics,
        "increments": increments,
        "legacyQUQU": load_legacy(Path(args.legacy), successful_months),
        "failures": failed,
        "months": monthly_output,
    }
    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"period": out["period"], "metrics": metrics, "increments": increments, "failures": failed}, ensure_ascii=False, indent=2, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
