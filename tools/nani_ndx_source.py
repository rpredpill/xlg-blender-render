#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests

NASDAQ_ARTICLE = "https://www.nasdaq.com/articles/global-indexes/nasdaq-100-index"
HISTORY_CSV = "https://raw.githubusercontent.com/thuningxu/sp500nq100/main/nasdaq100_components_history.csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (Nani strategy constituent validation)"}


def norm_symbol(x: str) -> str:
    return str(x or "").strip().upper().replace("-", ".")


def valid_symbol(x: str) -> bool:
    return bool(re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", x))


def scrape_nasdaq_article() -> list[str]:
    r = requests.get(NASDAQ_ARTICLE, headers=HEADERS, timeout=45)
    r.raise_for_status()
    symbols: list[str] = []
    for table in pd.read_html(io.StringIO(r.text)):
        # Nasdaq's current article is a simple company/symbol table, but keep
        # this parser tolerant of changing column labels.
        for col in table.columns:
            label = " ".join(map(str, col)) if isinstance(col, tuple) else str(col)
            if "symbol" not in label.lower() and "ticker" not in label.lower():
                continue
            for v in table[col].tolist():
                s = norm_symbol(v)
                if valid_symbol(s):
                    symbols.append(s)
    symbols = list(dict.fromkeys(symbols))
    if not 95 <= len(symbols) <= 110:
        raise RuntimeError(f"Nasdaq article parser returned {len(symbols)} symbols")
    required = {"AAPL", "MSFT", "NVDA", "SPCX", "HONA"}
    if not required.issubset(symbols):
        raise RuntimeError(f"Nasdaq article missing required current names: {sorted(required-set(symbols))}")
    return symbols


def fallback_history(asof: date) -> list[str]:
    r = requests.get(HISTORY_CSV, headers=HEADERS, timeout=45)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    if df.empty or "date" not in df.columns or "tickers" not in df.columns:
        raise RuntimeError("NDX history CSV schema changed")
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    eligible = df[df["date"].notna() & (df["date"] <= asof)]
    if eligible.empty:
        raise RuntimeError("No NDX history row available")
    row = eligible.sort_values("date").iloc[-1]
    symbols = {norm_symbol(x) for x in str(row["tickers"]).split(",") if valid_symbol(norm_symbol(x))}

    # Official Nasdaq changes after the public history file's latest 2026 row.
    events = [
        (date(2026, 6, 22), {"ALAB", "CRWV", "NBIS", "RKLB", "TER"}, {"CHTR", "CTSH", "INSM", "VRSK", "ZS"}),
        # Honeywell Aerospace entered NDX as a spin-off constituent; HON remained.
        (date(2026, 6, 29), {"HONA"}, set()),
        # Nasdaq announced SPCX as an NDX addition without a named deletion.
        (date(2026, 7, 7), {"SPCX"}, set()),
        # Announced Oct. 1; effective before market open Oct. 9, 2026.
        (date(2026, 10, 9), {"MRNA"}, {"WBD"}),
    ]
    for effective, adds, removes in events:
        if asof >= effective:
            symbols.difference_update(removes)
            symbols.update(adds)
    out = sorted(symbols)
    if not 95 <= len(out) <= 110:
        raise RuntimeError(f"Fallback NDX count implausible: {len(out)}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="nani-ndx-source.json")
    args = ap.parse_args()
    today = datetime.now(timezone.utc).date()
    source = NASDAQ_ARTICLE
    try:
        symbols = scrape_nasdaq_article()
        method = "Nasdaq current constituent article"
    except Exception as primary_error:
        symbols = fallback_history(today)
        source = HISTORY_CSV
        method = f"public NDX history + official 2026 Nasdaq changes; primary failed: {primary_error}"

    payload = {
        "asOf": today.isoformat(),
        "count": len(symbols),
        "source": source,
        "method": method,
        "symbols": symbols,
    }
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"asOf": payload["asOf"], "count": payload["count"], "source": source, "method": method, "sample": symbols[:12]}, indent=2))


if __name__ == "__main__":
    main()
