#!/usr/bin/env python3
"""Build monthly point-in-time Nasdaq-listed universes from Nasdaq Trader.

Source page:
  https://www.nasdaqtrader.com/trader.aspx?ID=marketsharedaily

Nasdaq publishes one XLSX per calendar month for "Nasdaq-Listed Securities":
  https://www.nasdaqtrader.com/content/marketstatistics/marketshare/YYYY/NASDAQYYYYMM.xlsx

The report is not a formal Nasdaq Composite constituent file. It is used here as
a survivorship-free monthly Nasdaq-listed security master. Downstream QUQU-v2
code must additionally apply Nasdaq Composite security-type eligibility and a
near-month-end price check before treating a symbol as investable.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

SOURCE_PAGE = "https://www.nasdaqtrader.com/trader.aspx?ID=marketsharedaily"
FILE_TEMPLATE = "https://www.nasdaqtrader.com/content/marketstatistics/marketshare/{year}/NASDAQ{ym_nodash}.xlsx"
UA = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "Chrome/126 Safari/537.36"
}
SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")
SYMBOL_COLUMN_HINTS = (
    "symbol",
    "issue symbol",
    "security symbol",
    "ticker",
    "issue",
)


def month_add(ym: str, n: int) -> str:
    return str(pd.Period(ym, freq="M") + n)


def last_completed_month() -> str:
    now = pd.Timestamp.now(tz="UTC")
    return str(now.to_period("M") - 1)


def clean_symbol(value) -> str | None:
    if value is None or pd.isna(value):
        return None
    s = str(value).strip().upper()
    if not s or s in {
        "SYMBOL", "TOTAL", "TOTALS", "NAN", "NONE",
        "NASDAQ LISTED", "NASDAQ-LISTED SECURITIES",
    }:
        return None
    s = s.replace("/", ".")
    if not SYMBOL_RE.fullmatch(s):
        return None
    return s


def candidate_symbol_columns(df: pd.DataFrame) -> list:
    scored = []
    for col in df.columns:
        name = str(col).strip().lower()
        score = 0
        if name in SYMBOL_COLUMN_HINTS:
            score += 100
        if "symbol" in name:
            score += 80
        if "ticker" in name:
            score += 70

        vals = df[col].dropna().head(500)
        if len(vals):
            valid = sum(clean_symbol(v) is not None for v in vals)
            score += 40.0 * valid / len(vals)
        scored.append((score, col))
    scored.sort(reverse=True, key=lambda x: x[0])
    return [c for score, c in scored if score >= 25]


def parse_workbook(content: bytes) -> tuple[list[str], dict]:
    book = pd.read_excel(io.BytesIO(content), sheet_name=None, engine="openpyxl")
    candidates = []
    diagnostics = {"sheets": []}

    for sheet, raw in book.items():
        frames = [(0, raw)]
        for header_row in range(1, min(12, len(raw))):
            try:
                df = pd.read_excel(
                    io.BytesIO(content),
                    sheet_name=sheet,
                    header=header_row,
                    engine="openpyxl",
                )
                frames.append((header_row, df))
            except Exception:
                continue

        best = None
        for header_row, df in frames:
            if df is None or df.empty:
                continue
            cols = candidate_symbol_columns(df)
            if not cols:
                continue
            col = cols[0]
            syms = [clean_symbol(v) for v in df[col]]
            syms = [s for s in syms if s]
            quality = len(set(syms))
            if best is None or quality > best["quality"]:
                best = {
                    "sheet": str(sheet),
                    "headerRow": header_row,
                    "symbolColumn": str(col),
                    "symbols": syms,
                    "quality": quality,
                    "columns": [str(x) for x in df.columns[:30]],
                }

        diagnostics["sheets"].append({
            "sheet": str(sheet),
            "rows": int(len(raw)),
            "columns": [str(x) for x in raw.columns[:30]],
            "bestUniqueSymbols": int(best["quality"]) if best else 0,
            "bestHeaderRow": int(best["headerRow"]) if best else None,
            "bestSymbolColumn": best["symbolColumn"] if best else None,
        })
        if best:
            candidates.extend(best["symbols"])

    symbols = sorted(set(candidates))
    if len(symbols) < 500:
        raise RuntimeError(
            f"Could not identify a credible symbol column; only {len(symbols)} "
            f"unique symbols. diagnostics={diagnostics}"
        )
    return symbols, diagnostics


def fetch_month(session: requests.Session, ym: str, retries: int = 4):
    p = pd.Period(ym, freq="M")
    url = FILE_TEMPLATE.format(year=p.year, ym_nodash=ym.replace("-", ""))
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            r = session.get(url, headers=UA, timeout=90)
            r.raise_for_status()
            if len(r.content) < 10_000:
                raise RuntimeError(f"response too small ({len(r.content)} bytes)")
            if not r.content.startswith(b"PK"):
                raise RuntimeError(
                    f"not an XLSX/ZIP payload; content-type={r.headers.get('content-type')}"
                )
            symbols, diagnostics = parse_workbook(r.content)
            return {
                "month": ym,
                "url": url,
                "symbols": symbols,
                "count": len(symbols),
                "bytes": len(r.content),
                "diagnostics": diagnostics,
            }
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(2.0 * attempt)
    raise RuntimeError(f"{ym}: {last_error}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2022-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--output", default="nasdaq-composite-pit.json")
    ap.add_argument("--sleep", type=float, default=0.35)
    args = ap.parse_args()

    end = args.end or last_completed_month()
    if pd.Period(args.start, freq="M") > pd.Period(end, freq="M"):
        raise SystemExit("start > end")

    session = requests.Session()
    months = {}
    failures = {}
    cur = args.start
    while pd.Period(cur, freq="M") <= pd.Period(end, freq="M"):
        try:
            rec = fetch_month(session, cur)
            months[cur] = {
                "month": cur,
                "sourceUrl": rec["url"],
                "count": rec["count"],
                "symbols": rec["symbols"],
                "parserDiagnostics": rec["diagnostics"],
            }
            print(f"{cur}: {rec['count']} symbols", flush=True)
        except Exception as exc:
            failures[cur] = str(exc)
            print(f"WARN {cur}: {exc}", flush=True)
        cur = month_add(cur, 1)
        time.sleep(args.sleep)

    if not months:
        raise RuntimeError("No monthly Nasdaq-listed reports could be parsed")

    payload = {
        "dataset": "Nasdaq monthly PIT listed-security universe",
        "purpose": "QUQU-v2 Nasdaq Composite universe reconstruction",
        "sourcePage": SOURCE_PAGE,
        "sourceFileTemplate": FILE_TEMPLATE,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "requestedStart": args.start,
        "requestedEnd": end,
        "firstSuccessfulMonth": min(months),
        "lastSuccessfulMonth": max(months),
        "successfulMonths": len(months),
        "failedMonths": failures,
        "survivorshipPolicy": (
            "Each month is read from Nasdaq Trader's historical Nasdaq-Listed "
            "Securities report for that same month; current membership is never "
            "substituted for a historical month."
        ),
        "caveat": (
            "This is a monthly historical Nasdaq-listed security master, not a "
            "formal Nasdaq Composite constituent file. QUQU-v2 must additionally "
            "apply Composite security-type eligibility and require a price close "
            "near the signal month-end."
        ),
        "months": {k: months[k] for k in sorted(months)},
    }
    out = Path(args.output)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"wrote {out}: {len(months)} months "
        f"{min(months)}..{max(months)} failures={len(failures)}",
        flush=True,
    )


if __name__ == "__main__":
    main()
