#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

WIKI_NDX = "https://en.wikipedia.org/wiki/Nasdaq-100"
UA = "Mozilla/5.0 (compatible; NaniMetadata/1.0; +https://github.com/)"


def norm_ticker(s: str) -> str:
    return str(s or "").strip().upper().replace("/", ".").replace("-", ".")


def clean_name(v) -> str | None:
    s = re.sub(r"\s+", " ", str(v or "")).strip()
    if not s or s.lower() in {"nan", "none", "-"}:
        return None
    return s


def flatten_col(c) -> str:
    if isinstance(c, tuple):
        c = " ".join(str(x) for x in c if str(x).lower() != "nan")
    return re.sub(r"\s+", " ", str(c)).strip().lower()


def fetch_tables(url: str):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    return pd.read_html(StringIO(r.text))


def ndx_names() -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for df in fetch_tables(WIKI_NDX):
            cols = {flatten_col(c): c for c in df.columns}
            ticker_col = next((orig for key, orig in cols.items() if key in {"ticker", "ticker symbol", "symbol"} or "ticker" in key), None)
            name_col = next((orig for key, orig in cols.items() if key in {"company", "company name", "security"} or "company" in key), None)
            if ticker_col is None or name_col is None:
                continue
            for _, row in df.iterrows():
                t = norm_ticker(row.get(ticker_col))
                n = clean_name(row.get(name_col))
                if t and n:
                    out[t] = n
    except Exception as e:
        print(f"NDX names warning: {e}")
    return out


def nikkei_names(url: str) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for df in fetch_tables(url):
            cols = {flatten_col(c): c for c in df.columns}
            code_col = next((orig for key, orig in cols.items() if key == "code" or key.endswith(" code") or "code" in key), None)
            name_col = next((orig for key, orig in cols.items() if "company" in key and "name" in key), None)
            if name_col is None:
                name_col = next((orig for key, orig in cols.items() if key in {"company", "name"}), None)
            if code_col is None or name_col is None:
                continue
            for _, row in df.iterrows():
                m = re.search(r"\b(\d{4})\b", str(row.get(code_col) or ""))
                n = clean_name(row.get(name_col))
                if m and n:
                    out[f"{m.group(1)}.T"] = n
    except Exception as e:
        print(f"Nikkei names warning: {e}")
    return out


def yahoo_name(yf_symbol: str) -> str | None:
    try:
        info = yf.Ticker(yf_symbol).get_info()
        return clean_name(info.get("shortName") or info.get("longName"))
    except Exception as e:
        print(f"Yahoo name warning {yf_symbol}: {e}")
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default=".")
    args = ap.parse_args()
    site = Path(args.site)

    latest = json.loads((site / "nani-latest.json").read_text(encoding="utf-8"))
    rows = latest.get("rows") or []
    tickers = [norm_ticker(r.get("ticker")) for r in rows if r.get("ticker")]
    if not tickers:
        raise RuntimeError("Nani latest has no rows")

    out_path = site / "nani-company-names.json"
    try:
        old = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
        names = {norm_ticker(k): clean_name(v) for k, v in (old.get("names") or {}).items() if clean_name(v)}
    except Exception:
        names = {}

    names.update(ndx_names())
    nikkei_url = (latest.get("sources") or {}).get("nikkei225SourceUniverse") or "https://indexes.nikkei.co.jp/en/nkave/index/component?idx=nk225"
    names.update(nikkei_names(nikkei_url))

    row_by_ticker = {norm_ticker(r.get("ticker")): r for r in rows}
    missing = [t for t in tickers if not names.get(t)]
    for t in missing:
        r = row_by_ticker[t]
        yfs = str(r.get("yf") or t).strip()
        n = yahoo_name(yfs)
        if n:
            names[t] = n

    selected = {t: names[t] for t in tickers if names.get(t)}
    still_missing = [t for t in tickers if t not in selected]
    payload = {
        "strategy": "Nani",
        "version": "1.0",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "count": len(selected),
        "expected": len(tickers),
        "coverageRatio": len(selected) / len(tickers),
        "missing": still_missing,
        "names": dict(sorted(selected.items())),
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ["count", "expected", "coverageRatio", "missing"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
