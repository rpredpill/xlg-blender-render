#!/usr/bin/env python3
"""Run the main backfill engine with extra recovery for legacy/delisted Yahoo symbols."""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from datetime import datetime, timezone
from urllib.parse import quote

import pandas as pd
import requests

ENGINE = "/tmp/backfill_snpi_ququ.py"

# Same-company ticker changes only. Acquisitions/mergers are deliberately NOT aliased.
ALIASES = {
    "ANTM": "ELV",   # Anthem -> Elevance Health
    "ABC": "COR",    # AmerisourceBergen -> Cencora
    "BLL": "BALL",   # Ball ticker change
    "BK": "BNY",     # Bank of New York Mellon ticker change
    "CDAY": "DAY",   # Ceridian -> Dayforce
    "FBHS": "FBIN",  # Fortune Brands Home & Security -> Fortune Brands Innovations
    "FI": "FISV",    # Fiserv ticker returned to FISV
    "FLT": "CPAY",   # FleetCor -> Corpay
    "MMC": "MRSH",   # Marsh McLennan ticker change
    "NLOK": "GEN",   # NortonLifeLock -> Gen Digital
    "PKI": "RVTY",   # PerkinElmer -> Revvity
    "RE": "EG",      # Everest Re -> Everest Group
}


def load_engine():
    spec = importlib.util.spec_from_file_location("snpi_backfill_engine", ENGINE)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load backfill engine")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def epoch_utc(day: str) -> int:
    return int(pd.Timestamp(day, tz="UTC").timestamp())


def yahoo_chart(symbol: str, start: str, end: str):
    """Fetch Yahoo chart JSON directly; this often survives quote-summary delisting failures."""
    p1 = epoch_utc(start)
    p2 = epoch_utc(end) + 86400
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + quote(symbol, safe="-.")
        + f"?period1={p1}&period2={p2}&interval=1d&events=div%2Csplits&includeAdjustedClose=true"
    )
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        if r.status_code != 200:
            return None
        payload = r.json()
        result = ((payload.get("chart") or {}).get("result") or [None])[0]
        if not result:
            return None
        ts = result.get("timestamp") or []
        quote0 = (((result.get("indicators") or {}).get("quote") or [{}])[0])
        adj0 = (((result.get("indicators") or {}).get("adjclose") or [{}])[0])
        opens = quote0.get("open") or []
        closes = quote0.get("close") or []
        adjs = adj0.get("adjclose") or closes
        n = min(len(ts), len(opens), len(closes), len(adjs))
        rows = []
        for i in range(n):
            o, c, a = opens[i], closes[i], adjs[i]
            if c is None:
                continue
            rows.append((pd.to_datetime(ts[i], unit="s", utc=True).tz_localize(None).normalize(), o, c, a if a is not None else c))
        if not rows:
            return None
        d = pd.DataFrame(rows, columns=["Date", "Open", "Close", "Adj Close"]).set_index("Date")
        for col in ("Open", "Close", "Adj Close"):
            d[col] = pd.to_numeric(d[col], errors="coerce")
        d = d[~d.index.duplicated(keep="last")].sort_index().dropna(subset=["Close"])
        return d if not d.empty else None
    except Exception:
        return None


def main():
    mod = load_engine()
    base_download = mod.download_prices

    def recovered_download(symbols, start, end):
        out = base_download(symbols, start, end)
        missing = [s for s in symbols if s not in out]
        recovered_direct = []
        recovered_alias = []
        for s in missing:
            d = yahoo_chart(mod.yf_symbol(s), start, end)
            if d is not None:
                out[s] = d
                recovered_direct.append(s)
                continue
            alias = ALIASES.get(s)
            if alias:
                d = yahoo_chart(mod.yf_symbol(alias), start, end)
                if d is not None:
                    out[s] = d
                    recovered_alias.append(f"{s}->{alias}")
        still = [s for s in symbols if s not in out]
        print("legacy raw-chart recovered:", recovered_direct, flush=True)
        print("same-company alias recovered:", recovered_alias, flush=True)
        print(f"final price coverage={len(out)}/{len(symbols)}; unresolved={still}", flush=True)
        mod.LEGACY_PRICE_RECOVERY = {
            "direct": recovered_direct,
            "aliases": recovered_alias,
            "unresolved": still,
        }
        return out

    original_build = mod.build_strategy

    def annotated_build(*args, **kwargs):
        payload = original_build(*args, **kwargs)
        payload.setdefault("backfill", {})["legacyPriceRecovery"] = getattr(mod, "LEGACY_PRICE_RECOVERY", {})
        return payload

    mod.download_prices = recovered_download
    mod.build_strategy = annotated_build
    mod.main()


if __name__ == "__main__":
    main()
