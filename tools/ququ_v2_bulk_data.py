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
defeatbeta/yahoo-finance-data public historical shares-outstanding parquet.
This module intentionally exposes the same dictionary shape expected by
ququ_v2_ablation.historical_cap().  Additional SEC-derived coverage can be
merged into the returned map by the SEC adapter without changing the strategy
engine.
"""
from __future__ import annotations

import math
from collections import defaultdict

import duckdb
import numpy as np
import pandas as pd

PRICE_BASE = "https://huggingface.co/datasets/paperswithbacktest/Stocks-Daily-Price/resolve/main/data/"
PRICE_URLS = [PRICE_BASE + f"train-{i:05d}-of-00004.parquet" for i in range(4)]
SHARES_URL = "https://huggingface.co/datasets/defeatbeta/yahoo-finance-data/resolve/main/data/US/stock_shares_outstanding.parquet"


def norm_symbol(x: str) -> str:
    return str(x or "").strip().upper().replace("/", ".").replace("-", ".")


def _connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    # httpfs is bundled/downloadable by DuckDB and enables HTTPS parquet range
    # reads. LOAD may succeed without INSTALL on cached runner images, so keep
    # both calls explicit for deterministic fresh runners.
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


def load_shares_hf(symbols: list[str], start: str | None = None, end: str | None = None, workers: int | None = None):
    """Return core-compatible historical shares map from one public parquet.

    ``series`` is indexed by report_date and therefore can be sampled on-or-
    before a signal date by the existing historical_cap() function. ``proxy``
    is the latest observed shares value and is retained only as the engine's
    explicit split-safe fallback when a historical observation is unavailable.
    """
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
        f"HF shares: historical={hist}/{len(wanted)} ({hist/len(wanted):.1%}) "
        f"rows={len(df)}",
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
        # Combine by availability date. Primary wins same-date collisions because
        # its YCharts series is usually denser and directly observed.
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
