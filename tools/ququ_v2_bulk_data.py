#!/usr/bin/env python3
"""Bulk data adapters for QUQU v2.

Price source
------------
paperswithbacktest/Stocks-Daily-Price public Hugging Face parquet shards.
The shards contain symbol/date/open/close/adj_close/volume and are queried with
DuckDB HTTP range reads so the backtest does not issue one Yahoo request per
symbol.

Shares source
-------------
1. defeatbeta/yahoo-finance-data historical shares-outstanding parquet.
2. speb/financial-data latest valuation snapshots as an explicit *current*
   shares proxy: implied shares = market_cap / current_price.

Float source
------------
speb/financial-data latest valuation snapshot float_shares.  We derive a
current free-float ratio from the same snapshot's implied shares.  Ratios above
1.05 or <=0 are rejected rather than silently clamped because ADR/share-unit
mismatches can be extreme.  Valid 1.00..1.05 observations are capped at 1.00.

The adapters expose the same function signatures expected by
ququ_v2_ablation.py so the strategy math remains separate from data sourcing.
"""
from __future__ import annotations

import math

import duckdb
import numpy as np
import pandas as pd

PRICE_BASE = "https://huggingface.co/datasets/paperswithbacktest/Stocks-Daily-Price/resolve/main/data/"
PRICE_URLS = [PRICE_BASE + f"train-{i:05d}-of-00004.parquet" for i in range(4)]
SHARES_URL = "https://huggingface.co/datasets/defeatbeta/yahoo-finance-data/resolve/main/data/US/stock_shares_outstanding.parquet"
VALUATION_URL = "https://huggingface.co/datasets/speb/financial-data/resolve/main/raw/stock_valuation_snapshot.parquet"
MAX_FLOAT_RATIO = 1.05


def norm_symbol(x: str) -> str:
    return str(x or "").strip().upper().replace("/", ".").replace("-", ".")


def _connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("INSTALL httpfs")
    con.execute("LOAD httpfs")
    con.execute("SET threads=4")
    return con


def _wanted_table(con: duckdb.DuckDBPyConnection, symbols: list[str]) -> list[str]:
    syms = sorted({norm_symbol(s) for s in symbols if norm_symbol(s)})
    con.execute("CREATE TEMP TABLE wanted(symbol VARCHAR PRIMARY KEY)")
    if syms:
        con.executemany("INSERT INTO wanted VALUES (?)", [(s,) for s in syms])
    return syms


def load_prices_hf(symbols: list[str], start: str, end: str):
    """Return core-compatible dict[symbol] -> daily OHLCV DataFrame.

    ``end`` follows yfinance semantics in the existing engine (exclusive).
    """
    con = _connect()
    wanted = _wanted_table(con, symbols)
    src = "read_parquet([" + ",".join(repr(u) for u in PRICE_URLS) + "], union_by_name=true)"
    query = f"""
        SELECT
            upper(p.symbol) AS symbol,
            try_cast(p.date AS DATE) AS date,
            p.open AS Open,
            p.close AS Close,
            p.adj_close AS "Adj Close",
            p.volume AS Volume
        FROM {src} p
        INNER JOIN wanted w ON upper(p.symbol)=w.symbol
        WHERE try_cast(p.date AS DATE) >= ?
          AND try_cast(p.date AS DATE) < ?
        ORDER BY symbol, date
    """
    df = con.execute(query, [str(start), str(end)]).fetchdf()
    con.close()

    out = {}
    if df.empty:
        print(f"HF prices: 0/{len(wanted)} symbols", flush=True)
        return out

    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.tz_localize(None).dt.normalize()
    df = df.dropna(subset=["symbol", "date", "Close", "Adj Close"])
    for symbol, g in df.groupby("symbol", sort=False):
        d = g[["date", "Open", "Close", "Adj Close", "Volume"]].copy()
        d = d.set_index("date")
        d = d[~d.index.duplicated(keep="last")].sort_index()
        for c in ["Open", "Close", "Adj Close", "Volume"]:
            d[c] = pd.to_numeric(d[c], errors="coerce")
        if not d.empty:
            out[str(symbol).upper()] = d
    print(
        f"HF prices: loaded={len(out)}/{len(wanted)} rows={len(df)} "
        f"range={df['date'].min().date()}..{df['date'].max().date()}",
        flush=True,
    )
    return out


def load_current_valuation(symbols: list[str]) -> dict[str, dict]:
    """Load latest current valuation snapshot for requested symbols only."""
    con = _connect()
    wanted = _wanted_table(con, symbols)
    query = f"""
        SELECT symbol, report_date, market_cap, current_price, float_shares
        FROM (
            SELECT
                upper(v.symbol) AS symbol,
                v.report_date,
                cast(v.market_cap AS DOUBLE) AS market_cap,
                cast(v.current_price AS DOUBLE) AS current_price,
                cast(v.float_shares AS DOUBLE) AS float_shares,
                row_number() OVER (
                    PARTITION BY upper(v.symbol)
                    ORDER BY v.report_date DESC
                ) AS rn
            FROM read_parquet('{VALUATION_URL}') v
            INNER JOIN wanted w ON upper(v.symbol)=w.symbol
        )
        WHERE rn=1
    """
    df = con.execute(query).fetchdf()
    con.close()

    out = {}
    for row in df.itertuples(index=False):
        symbol = str(row.symbol).upper()
        market_cap = float(row.market_cap) if row.market_cap is not None and pd.notna(row.market_cap) else None
        current_price = float(row.current_price) if row.current_price is not None and pd.notna(row.current_price) else None
        float_shares = float(row.float_shares) if row.float_shares is not None and pd.notna(row.float_shares) else None
        implied = None
        if market_cap and current_price and math.isfinite(market_cap) and math.isfinite(current_price) and market_cap > 0 and current_price > 0:
            implied = market_cap / current_price
            if not math.isfinite(implied) or implied <= 0:
                implied = None
        ratio = None
        if implied and float_shares and math.isfinite(float_shares) and float_shares > 0:
            raw_ratio = float_shares / implied
            if math.isfinite(raw_ratio) and 0 < raw_ratio <= MAX_FLOAT_RATIO:
                ratio = min(1.0, raw_ratio)
        out[symbol] = {
            "reportDate": str(row.report_date) if row.report_date is not None else None,
            "marketCap": market_cap,
            "currentPrice": current_price,
            "impliedShares": implied,
            "floatShares": float_shares,
            "floatRatio": ratio,
        }
    implied_n = sum(1 for x in out.values() if x.get("impliedShares"))
    float_n = sum(1 for x in out.values() if x.get("floatRatio") is not None)
    print(
        f"valuation snapshot: rows={len(out)}/{len(wanted)} impliedShares={implied_n} "
        f"validFloatRatio={float_n}",
        flush=True,
    )
    return out


def load_shares_hf(symbols: list[str], start: str | None = None, end: str | None = None, workers: int | None = None):
    """Return core-compatible historical shares map from one public parquet."""
    con = _connect()
    wanted = _wanted_table(con, symbols)
    query = f"""
        SELECT
            upper(s.symbol) AS symbol,
            try_cast(s.report_date AS DATE) AS report_date,
            cast(s.shares_outstanding AS DOUBLE) AS shares_outstanding
        FROM read_parquet('{SHARES_URL}') s
        INNER JOIN wanted w ON upper(s.symbol)=w.symbol
        WHERE s.shares_outstanding IS NOT NULL
          AND s.shares_outstanding > 0
          AND try_cast(s.report_date AS DATE) IS NOT NULL
        ORDER BY symbol, report_date
    """
    df = con.execute(query).fetchdf()
    con.close()

    out = {
        s: {"series": None, "proxy": None, "proxyMethod": None, "source": "missing"}
        for s in wanted
    }
    if df.empty:
        print(f"HF shares: 0/{len(wanted)} symbols", flush=True)
        return out

    df["report_date"] = pd.to_datetime(df["report_date"], errors="coerce").dt.tz_localize(None).dt.normalize()
    df["shares_outstanding"] = pd.to_numeric(df["shares_outstanding"], errors="coerce")
    df = df.dropna(subset=["symbol", "report_date", "shares_outstanding"])
    df = df[(df["shares_outstanding"] > 0) & np.isfinite(df["shares_outstanding"])]

    for symbol, g in df.groupby("symbol", sort=False):
        g = g.sort_values("report_date").drop_duplicates("report_date", keep="last")
        s = pd.Series(
            g["shares_outstanding"].astype(float).to_numpy(),
            index=pd.DatetimeIndex(g["report_date"]),
            dtype=float,
        )
        if len(s):
            out[str(symbol).upper()] = {
                "series": s,
                "proxy": float(s.iloc[-1]),
                "proxyMethod": "HF defeatbeta/YCharts latest shares",
                "source": "HF historical shares outstanding",
            }

    hist = sum(1 for x in out.values() if x.get("series") is not None)
    print(
        f"HF shares: historical={hist}/{len(wanted)} ({hist/len(wanted):.1%}) rows={len(df)}",
        flush=True,
    )
    return out


def load_shares_hf_plus_proxy(symbols: list[str], start: str | None = None, end: str | None = None, workers: int | None = None):
    """Historical shares first; current implied shares only as explicit fallback."""
    out = load_shares_hf(symbols, start, end, workers)
    valuation = load_current_valuation(symbols)
    added_proxy = 0
    for symbol in sorted({norm_symbol(s) for s in symbols}):
        info = out.setdefault(
            symbol,
            {"series": None, "proxy": None, "proxyMethod": None, "source": "missing"},
        )
        if info.get("proxy"):
            continue
        v = valuation.get(symbol) or {}
        implied = v.get("impliedShares")
        if implied and math.isfinite(float(implied)) and float(implied) > 0:
            info["proxy"] = float(implied)
            info["proxyMethod"] = "speb current marketCap/currentPrice implied shares"
            info["source"] = (
                "HF historical shares + speb current implied-share fallback"
                if info.get("series") is not None
                else "speb current implied-share fallback"
            )
            added_proxy += 1
    any_proxy = sum(1 for x in out.values() if x.get("proxy"))
    hist = sum(1 for x in out.values() if x.get("series") is not None)
    print(
        f"combined shares: historical={hist}/{len(out)} anyProxy={any_proxy}/{len(out)} "
        f"spebAdded={added_proxy}",
        flush=True,
    )
    return out


def load_float_info_bulk(symbols: list[str], shares: dict, workers: int | None = None):
    """Core-compatible latest float-ratio map with strict unit validation."""
    valuation = load_current_valuation(symbols)
    out = {}
    from_same_snapshot = 0
    from_share_proxy = 0
    rejected = 0
    for symbol in sorted({norm_symbol(s) for s in symbols}):
        v = valuation.get(symbol) or {}
        ratio = v.get("floatRatio")
        method = None
        outstanding = v.get("impliedShares")
        float_shares = v.get("floatShares")
        if ratio is not None:
            ratio = float(ratio)
            method = "speb latest floatShares / same-snapshot implied shares"
            from_same_snapshot += 1
        elif float_shares and math.isfinite(float(float_shares)) and float(float_shares) > 0:
            # Secondary check against the core share proxy.  Accept only if the
            # resulting ratio is economically valid; otherwise mark missing.
            share_proxy = (shares.get(symbol) or {}).get("proxy")
            if share_proxy and math.isfinite(float(share_proxy)) and float(share_proxy) > 0:
                raw = float(float_shares) / float(share_proxy)
                if math.isfinite(raw) and 0 < raw <= MAX_FLOAT_RATIO:
                    ratio = min(1.0, raw)
                    outstanding = float(share_proxy)
                    method = "speb latest floatShares / core latest shares proxy"
                    from_share_proxy += 1
                else:
                    rejected += 1
        out[symbol] = {
            "ratio": ratio,
            "floatShares": float(float_shares) if float_shares is not None and pd.notna(float_shares) else None,
            "sharesOutstanding": float(outstanding) if outstanding is not None and pd.notna(outstanding) else None,
            "quoteType": None,
            "method": method,
            "snapshotDate": v.get("reportDate"),
        }
    known = sum(1 for x in out.values() if x.get("ratio") is not None)
    print(
        f"bulk float: known={known}/{len(out)} sameSnapshot={from_same_snapshot} "
        f"shareProxy={from_share_proxy} rejectedUnitMismatch={rejected}",
        flush=True,
    )
    return out


def merge_share_maps(primary: dict, fallback: dict) -> dict:
    """Merge fallback share histories without overwriting richer primary rows."""
    out = dict(primary)
    for symbol, fb in (fallback or {}).items():
        s = norm_symbol(symbol)
        cur = out.get(s) or {}
        cur_series = cur.get("series")
        fb_series = (fb or {}).get("series")
        if cur_series is None or not len(cur_series):
            out[s] = fb
            continue
        if fb_series is None or not len(fb_series):
            continue
        combined = pd.concat([fb_series, cur_series])
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        x = dict(cur)
        x["series"] = combined
        if not x.get("proxy") and len(combined):
            x["proxy"] = float(combined.iloc[-1])
            x["proxyMethod"] = (fb or {}).get("proxyMethod")
        x["source"] = f"{cur.get('source','primary')} + {(fb or {}).get('source','fallback')}"
        out[s] = x
    return out
