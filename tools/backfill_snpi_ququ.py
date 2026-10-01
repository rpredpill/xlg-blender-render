#!/usr/bin/env python3
"""Backfill SNPI / QUQU monthly targets and realized monthly returns.

Default research window: 2022-10 through the last completed calendar month.
Universe membership is point-in-time. Momentum and returns use Yahoo adjusted
prices. Historical market cap uses contemporaneous Yahoo shares outstanding
when available; otherwise it falls back to a split-safe current-share proxy.
Every fallback is recorded in the output JSON.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

SP500_URL = "https://raw.githubusercontent.com/thuningxu/sp500nq100/main/sp500_components_history.csv"
NDX100_URL = "https://raw.githubusercontent.com/thuningxu/sp500nq100/main/nasdaq100_components_history.csv"
UA = {"User-Agent": "Mozilla/5.0"}
CAP = 0.20
RULE = "Raw weight = sqrt(Market Cap) × (6-1 gross momentum / median gross momentum)^3; 20% cap"


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


def load_membership(url: str):
    r = requests.get(url, headers=UA, timeout=60)
    r.raise_for_status()
    rows = []
    for rec in csv.DictReader(io.StringIO(r.text)):
        dt = pd.Timestamp(rec["date"]).normalize()
        tickers = [norm_symbol(x) for x in rec["tickers"].split(",") if norm_symbol(x)]
        rows.append((dt, tickers))
    rows.sort(key=lambda x: x[0])
    if not rows:
        raise RuntimeError(f"membership source empty: {url}")
    return rows


def members_at(rows, when: pd.Timestamp):
    best = None
    for dt, tickers in rows:
        if dt <= when:
            best = tickers
        else:
            break
    if best is None:
        raise RuntimeError(f"no membership snapshot for {when.date()}")
    return list(best)


def cap_and_redistribute(raw: dict[str, float], cap: float = CAP):
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


def extract_one(data: pd.DataFrame, ticker: str):
    yt = yf_symbol(ticker)
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
        d = d[["Open", "Close", "Adj Close"]].copy()
        d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
        d = d[~d.index.duplicated(keep="last")].sort_index()
        d = d.dropna(how="all")
        return d if not d.empty else None
    except Exception:
        return None


def download_prices(symbols: list[str], start: str, end: str):
    out = {}
    for i in range(0, len(symbols), 70):
        part = symbols[i:i + 70]
        ypart = [yf_symbol(s) for s in part]
        data = yf.download(
            tickers=ypart,
            start=start,
            end=end,
            auto_adjust=False,
            actions=False,
            repair=False,
            progress=False,
            group_by="column",
            threads=False,
        )
        for s in part:
            d = extract_one(data, s)
            if d is not None:
                out[s] = d
        print(f"prices {min(i+len(part), len(symbols))}/{len(symbols)}; loaded={len(out)}", flush=True)
        time.sleep(0.15)

    missing = [s for s in symbols if s not in out]
    for j, s in enumerate(missing):
        try:
            d = yf.Ticker(yf_symbol(s)).history(
                start=start, end=end, auto_adjust=False, actions=False, repair=False
            )
            if not d.empty:
                if "Adj Close" not in d.columns:
                    d["Adj Close"] = d["Close"]
                d = d[["Open", "Close", "Adj Close"]].copy()
                d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
                d = d[~d.index.duplicated(keep="last")].sort_index()
                out[s] = d
        except Exception:
            pass
        if (j + 1) % 10 == 0:
            print(f"individual price fallbacks {j+1}/{len(missing)}", flush=True)
        time.sleep(0.10)
    return out


def close_on_or_before(prices, symbol: str, when: pd.Timestamp, col: str):
    d = prices.get(symbol)
    if d is None or col not in d.columns:
        return None
    s = pd.to_numeric(d.loc[d.index <= when, col], errors="coerce").dropna()
    if s.empty:
        return None
    v = float(s.iloc[-1])
    return v if math.isfinite(v) and v > 0 else None


def month_return(prices, symbol: str, ym: str):
    d = prices.get(symbol)
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
    raw_open = float(first.get("Open")) if pd.notna(first.get("Open")) else None
    raw_close_first = float(first.get("Close")) if pd.notna(first.get("Close")) else None
    adj_close_first = float(first.get("Adj Close")) if pd.notna(first.get("Adj Close")) else None
    adj_close_last = float(last.get("Adj Close")) if pd.notna(last.get("Adj Close")) else None
    if not raw_open or not raw_close_first or not adj_close_first or not adj_close_last:
        return None
    if raw_open <= 0 or raw_close_first <= 0 or adj_close_first <= 0 or adj_close_last <= 0:
        return None
    adj_open = raw_open * (adj_close_first / raw_close_first)
    rel = adj_close_last / adj_open
    if not math.isfinite(rel) or rel <= 0:
        return None
    return {
        "return": rel - 1.0,
        "firstDate": str(g.index[0].date()),
        "lastDate": str(g.index[-1].date()),
    }


def fetch_share_info(symbol: str, start: str, end: str):
    yt = yf_symbol(symbol)
    series = None
    current_proxy = None
    method = None
    try:
        q = yf.Ticker(yt)
        try:
            s = q.get_shares_full(start=start, end=end)
            if s is not None and len(s):
                s = pd.to_numeric(s, errors="coerce").dropna()
                if len(s):
                    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
                    s = s[~s.index.duplicated(keep="last")].sort_index()
                    series = s
                    current_proxy = float(s.iloc[-1])
                    method = "Yahoo historical shares outstanding"
        except Exception:
            pass
        if not current_proxy or not math.isfinite(current_proxy) or current_proxy <= 0:
            try:
                fi = q.fast_info
                mc = fi.get("market_cap")
                lp = fi.get("last_price")
                if mc and lp and float(mc) > 0 and float(lp) > 0:
                    current_proxy = float(mc) / float(lp)
                    method = "Yahoo current market-cap/share proxy"
            except Exception:
                pass
        if not current_proxy or not math.isfinite(current_proxy) or current_proxy <= 0:
            try:
                inf = q.info
                sh = inf.get("sharesOutstanding")
                if sh and float(sh) > 0:
                    current_proxy = float(sh)
                    method = "Yahoo current shares proxy"
            except Exception:
                pass
    except Exception:
        pass
    return symbol, series, current_proxy, method


def load_shares(symbols: list[str], start: str, end: str):
    out = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(fetch_share_info, s, start, end): s for s in symbols}
        done = 0
        for fut in as_completed(futs):
            s = futs[fut]
            try:
                sym, series, proxy, method = fut.result()
                out[sym] = {"series": series, "proxy": proxy, "method": method}
            except Exception:
                out[s] = {"series": None, "proxy": None, "method": None}
            done += 1
            if done % 50 == 0 or done == len(symbols):
                hist = sum(1 for x in out.values() if x["series"] is not None)
                proxy = sum(1 for x in out.values() if x["proxy"] is not None)
                print(f"shares {done}/{len(symbols)}; historical={hist}; anyProxy={proxy}", flush=True)
    return out


def historical_cap(prices, shares, symbol: str, signal_date: pd.Timestamp):
    raw_close = close_on_or_before(prices, symbol, signal_date, "Close")
    adj_close = close_on_or_before(prices, symbol, signal_date, "Adj Close")
    info = shares.get(symbol) or {}
    s = info.get("series")
    if s is not None and len(s) and raw_close:
        prior = s.loc[s.index <= signal_date]
        if not prior.empty:
            sh = float(prior.iloc[-1])
            if math.isfinite(sh) and sh > 0:
                return sh * raw_close, "historical-shares"
    proxy = info.get("proxy")
    if proxy and math.isfinite(float(proxy)) and float(proxy) > 0 and adj_close:
        return float(proxy) * adj_close, "current-shares-proxy"
    return None, "missing"


def strategy_month(name: str, membership, prices, shares, allocation_month: str):
    signal_month = month_add(allocation_month, -1)
    early_month = month_add(allocation_month, -6)
    signal_date = month_end(signal_month)
    early_date = month_end(early_month)
    universe = members_at(membership, signal_date)

    rows = []
    for ticker in universe:
        recent = close_on_or_before(prices, ticker, signal_date, "Adj Close")
        if recent is None:
            continue
        early = close_on_or_before(prices, ticker, early_date, "Adj Close")
        cap, cap_method = historical_cap(prices, shares, ticker, signal_date)
        rows.append({
            "ticker": ticker,
            "recentPrice": recent,
            "earlyPrice": early,
            "marketCap": cap,
            "capMethod": cap_method,
        })

    if len(rows) < max(90, int(len(universe) * 0.90)):
        raise RuntimeError(f"{name} {allocation_month}: recent-price coverage {len(rows)}/{len(universe)}")

    gross = [r["recentPrice"] / r["earlyPrice"] for r in rows if r["earlyPrice"] and r["earlyPrice"] > 0]
    if not gross:
        raise RuntimeError(f"{name} {allocation_month}: no 6-1 momentum observations")
    neutral_gross = float(np.median(gross))

    cap_values = [r["marketCap"] for r in rows if r["marketCap"] and r["marketCap"] > 0]
    if not cap_values:
        raise RuntimeError(f"{name} {allocation_month}: no market cap observations")
    median_cap = float(np.median(cap_values))

    raw = {}
    mom_values = []
    for r in rows:
        if r["earlyPrice"] and r["earlyPrice"] > 0:
            mg = r["recentPrice"] / r["earlyPrice"]
            r["momentumFallback"] = None
        else:
            mg = neutral_gross
            r["momentumFallback"] = "cross-sectional median gross momentum"
        r["momentumGross"] = mg
        r["momentum"] = mg - 1.0
        mom_values.append(mg)
        if not r["marketCap"] or r["marketCap"] <= 0:
            r["marketCap"] = median_cap
            r["capMethod"] = "cross-sectional-median-cap"

    mom_med = float(np.median(mom_values))
    for r in rows:
        rel_mom = r["momentumGross"] / mom_med
        r["relativeMomentum"] = rel_mom
        r["rawWeightScore"] = math.sqrt(r["marketCap"]) * (rel_mom ** 3)
        raw[r["ticker"]] = r["rawWeightScore"]

    weights = cap_and_redistribute(raw)
    for r in rows:
        r["weight"] = weights[r["ticker"]]
        mr = month_return(prices, r["ticker"], allocation_month)
        r["monthlyReturn"] = mr["return"] if mr else None
        r["firstDate"] = mr["firstDate"] if mr else None
        r["lastDate"] = mr["lastDate"] if mr else None

    rows.sort(key=lambda x: x["weight"], reverse=True)
    missing_return_weight = sum(r["weight"] for r in rows if r["monthlyReturn"] is None)
    if missing_return_weight > 0.02:
        raise RuntimeError(f"{name} {allocation_month}: missing return weight {missing_return_weight:.2%}")

    port = sum(r["weight"] * (r["monthlyReturn"] if r["monthlyReturn"] is not None else 0.0) for r in rows)
    cap_hist = sum(1 for r in rows if r["capMethod"] == "historical-shares")
    cap_proxy = sum(1 for r in rows if r["capMethod"] == "current-shares-proxy")
    cap_median = sum(1 for r in rows if r["capMethod"] == "cross-sectional-median-cap")
    mom_fallback = sum(1 for r in rows if r["momentumFallback"])
    max_weight = max(r["weight"] for r in rows)
    top10 = sum(r["weight"] for r in rows[:10])
    last_dates = [r["lastDate"] for r in rows if r["lastDate"]]

    compact = [{
        "ticker": r["ticker"],
        "weight": r["weight"],
        "momentum": r["momentum"],
        "marketCap": r["marketCap"],
        "monthlyReturn": r["monthlyReturn"],
        "capMethod": r["capMethod"],
        "momentumFallback": r["momentumFallback"],
    } for r in rows]

    return {
        "allocationMonth": allocation_month,
        "signalMonth": signal_month,
        "signalDate": str(signal_date.date()),
        "earlyDate": str(early_date.date()),
        "rule": RULE,
        "universeCount": len(universe),
        "pricedCount": len(rows),
        "holdingsCount": len(rows),
        "maxWeight": max_weight,
        "top10Weight": top10,
        "portfolioReturn": port * 100.0,
        "lastDate": max(last_dates) if last_dates else None,
        "missingReturnWeight": missing_return_weight,
        "historicalCapCount": cap_hist,
        "currentSharesProxyCount": cap_proxy,
        "medianCapFallbackCount": cap_median,
        "momentumFallbackCount": mom_fallback,
        "rows": compact,
    }


def build_strategy(name, universe_name, membership, prices, shares, start_month, end_month, existing_path: Path):
    months = {}
    if existing_path.exists():
        try:
            old = json.loads(existing_path.read_text(encoding="utf-8"))
            if isinstance(old.get("months"), dict):
                months.update(old["months"])
        except Exception:
            pass

    current = start_month
    failures = {}
    while current <= end_month:
        try:
            rec = strategy_month(name, membership, prices, shares, current)
            months[current] = rec
            print(
                f"{name} {current}: return={rec['portfolioReturn']:+.2f}% holdings={rec['holdingsCount']} "
                f"histCap={rec['historicalCapCount']} proxy={rec['currentSharesProxyCount']} "
                f"missingRetW={rec['missingReturnWeight']:.3%}", flush=True
            )
        except Exception as e:
            failures[current] = str(e)
            print(f"WARN {name} {current}: {e}", flush=True)
        current = month_add(current, 1)

    ordered = {k: months[k] for k in sorted(months)}
    return {
        "strategy": name,
        "universe": universe_name,
        "description": "Monthly target holdings/weights plus realized monthly returns. allocationMonth is the month these weights are held.",
        "backfill": {
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "requestedStart": start_month,
            "requestedEnd": end_month,
            "membershipMethod": "point-in-time full-membership snapshots",
            "priceMethod": "Yahoo Finance adjusted prices; adjusted first-day open to adjusted last-day close for monthly return",
            "marketCapMethod": "historical Yahoo shares outstanding × raw close when available; split-safe current-share proxy × adjusted close otherwise",
            "marketCapCaveat": "Fallback share proxies ignore historical issuance/buybacks; per-month fallback counts are recorded.",
            "missingReturnRule": "Only if missing-return weight <=2%, missing return is treated as cash (0%) and the exact weight is recorded; otherwise the month is rejected.",
            "failures": failures,
        },
        "months": ordered,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2022-10")
    ap.add_argument("--end", default=None, help="allocation month YYYY-MM; default last completed month")
    ap.add_argument("--output-dir", default=".")
    args = ap.parse_args()

    today = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    default_end = str(today.to_period("M") - 1)
    end_month = args.end or default_end
    if pd.Period(args.start, freq="M") > pd.Period(end_month, freq="M"):
        raise SystemExit("start > end")

    sp = load_membership(SP500_URL)
    ndx = load_membership(NDX100_URL)
    earliest_signal = month_end(month_add(args.start, -1))
    latest_signal = month_end(month_add(end_month, -1))
    all_symbols = set()
    for membership in (sp, ndx):
        begin = month_end(month_add(args.start, -6))
        all_symbols.update(members_at(membership, begin))
        for dt, tickers in membership:
            if begin < dt <= latest_signal:
                all_symbols.update(tickers)
        all_symbols.update(members_at(membership, earliest_signal))
        all_symbols.update(members_at(membership, latest_signal))
    symbols = sorted(all_symbols)
    print(f"union symbols={len(symbols)}", flush=True)

    price_start = str((pd.Period(args.start, freq="M") - 7).start_time.date())
    price_end = str((pd.Period(end_month, freq="M") + 2).start_time.date())
    prices = download_prices(symbols, price_start, price_end)
    print(f"price coverage={len(prices)}/{len(symbols)}", flush=True)

    shares = load_shares(symbols, price_start, price_end)

    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    snpi_path = outdir / "snpi-history.json"
    ququ_path = outdir / "ququ-history.json"

    snpi = build_strategy("SNPI", "S&P 500", sp, prices, shares, args.start, end_month, snpi_path)
    ququ = build_strategy("QUQU", "Nasdaq-100", ndx, prices, shares, args.start, end_month, ququ_path)

    snpi_path.write_text(json.dumps(snpi, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    ququ_path.write_text(json.dumps(ququ, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")

    def summary(payload):
        months = payload["months"]
        realized = [m for m, r in months.items() if isinstance(r, dict) and isinstance(r.get("portfolioReturn"), (int, float))]
        return len(realized), (realized[0] if realized else None), (realized[-1] if realized else None)

    print("SNPI summary", summary(snpi), flush=True)
    print("QUQU summary", summary(ququ), flush=True)


if __name__ == "__main__":
    main()
