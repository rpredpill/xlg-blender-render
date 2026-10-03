#!/usr/bin/env python3
"""Build monthly point-in-time Nasdaq-listed universes from Nasdaq Trader.

Source page:
  https://www.nasdaqtrader.com/trader.aspx?ID=marketsharedaily

Nasdaq publishes one XLSX per calendar month for "Nasdaq-Listed Securities":
  https://www.nasdaqtrader.com/content/marketstatistics/marketshare/YYYY/NASDAQYYYYMM.xlsx

The workbook is a historical Nasdaq-listed security master, not an official
Nasdaq Composite constituent file.  We use the workbook's PIT ETF flag and a
conservative suffix rule to remove obvious ineligible security types.  The
QUQU-v2 engine must still require usable equity prices and capitalization data.
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
# Nasdaq fifth-character issue types that are clearly outside Composite equity
# eligibility.  We only apply these when the corresponding root symbol exists
# in the same PIT workbook, avoiding false exclusions of legitimate 5-char roots.
# L is intentionally NOT included because symbols such as GOOGL exist alongside
# GOOG and must not be classified from the last character alone.
ROOT_LINKED_INELIGIBLE_SUFFIXES = {
    "C",  # exchange-traded managed fund / NextShares
    "G", "H", "I",  # convertible bonds
    "M", "N", "O", "P",  # preferred classes
    "R",  # rights
    "T",  # with warrants/rights
    "U",  # units
    "V",  # when-issued / when-distributed
    "W",  # warrants
    "X",  # Nasdaq Fund Network instrument
    "Z",  # miscellaneous / preferred when-issued
}


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


def yn_flag(value) -> bool | None:
    if value is None or pd.isna(value):
        return None
    # Nasdaq monthly XLSX files use numeric 0.0/1.0 for ETF FLAG in at
    # least the 2022-12 sample. Handle numeric values before string parsing.
    try:
        x = float(value)
        if x == 1.0:
            return True
        if x == 0.0:
            return False
    except (TypeError, ValueError):
        pass
    s = str(value).strip().upper()
    if s in {"Y", "YES", "1", "1.0", "TRUE"}:
        return True
    if s in {"N", "NO", "0", "0.0", "FALSE"}:
        return False
    return None


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


def find_etf_column(df: pd.DataFrame):
    for col in df.columns:
        name = re.sub(r"\s+", " ", str(col)).strip().lower()
        if "etf" in name and ("flag" in name or name == "etf"):
            return col
    return None


def parse_workbook(content: bytes) -> tuple[list[dict], dict]:
    book = pd.read_excel(io.BytesIO(content), sheet_name=None, engine="openpyxl")
    records: dict[str, dict] = {}
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
            symbol_col = cols[0]
            etf_col = find_etf_column(df)
            rows = []
            for _, row in df.iterrows():
                sym = clean_symbol(row.get(symbol_col))
                if not sym:
                    continue
                rows.append({
                    "symbol": sym,
                    "etf": yn_flag(row.get(etf_col)) if etf_col is not None else None,
                })
            quality = len({x["symbol"] for x in rows})
            if best is None or quality > best["quality"]:
                best = {
                    "sheet": str(sheet),
                    "headerRow": header_row,
                    "symbolColumn": str(symbol_col),
                    "etfColumn": str(etf_col) if etf_col is not None else None,
                    "records": rows,
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
            "bestEtfColumn": best["etfColumn"] if best else None,
        })
        if best:
            for rec in best["records"]:
                old = records.get(rec["symbol"])
                if old is None:
                    records[rec["symbol"]] = rec
                elif old.get("etf") is None and rec.get("etf") is not None:
                    records[rec["symbol"]] = rec

    rows = [records[s] for s in sorted(records)]
    if len(rows) < 500:
        raise RuntimeError(
            f"Could not identify a credible symbol column; only {len(rows)} "
            f"unique symbols. diagnostics={diagnostics}"
        )
    return rows, diagnostics


def classify_month(rows: list[dict]):
    raw_symbols = {r["symbol"] for r in rows}
    eligible = []
    excluded_etf = []
    excluded_suffix = []
    etf_unknown = []

    for r in rows:
        s = r["symbol"]
        if r.get("etf") is True:
            excluded_etf.append(s)
            continue
        if r.get("etf") is None:
            etf_unknown.append(s)

        # A root-linked 5th-character suffix is strong evidence of a subordinate
        # security.  Example: AACI + AACIU/AACIW.  Do not classify on the suffix
        # alone because legitimate root symbols can be five characters.
        if len(s) == 5 and s[:4] in raw_symbols and s[-1] in ROOT_LINKED_INELIGIBLE_SUFFIXES:
            excluded_suffix.append(s)
            continue
        eligible.append(s)

    return {
        "symbols": sorted(eligible),
        "rawCount": len(raw_symbols),
        "count": len(eligible),
        "excludedETFCount": len(excluded_etf),
        "excludedSuffixCount": len(excluded_suffix),
        "etfFlagUnknownCount": len(etf_unknown),
        "excludedETFSample": sorted(excluded_etf)[:30],
        "excludedSuffixSample": sorted(excluded_suffix)[:30],
    }


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
            rows, diagnostics = parse_workbook(r.content)
            classified = classify_month(rows)
            return {
                "month": ym,
                "url": url,
                "bytes": len(r.content),
                "diagnostics": diagnostics,
                **classified,
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
                "rawCount": rec["rawCount"],
                "count": rec["count"],
                "excludedETFCount": rec["excludedETFCount"],
                "excludedSuffixCount": rec["excludedSuffixCount"],
                "etfFlagUnknownCount": rec["etfFlagUnknownCount"],
                "excludedETFSample": rec["excludedETFSample"],
                "excludedSuffixSample": rec["excludedSuffixSample"],
                "symbols": rec["symbols"],
                "parserDiagnostics": rec["diagnostics"],
            }
            print(
                f"{cur}: raw={rec['rawCount']} eligible={rec['count']} "
                f"etf={rec['excludedETFCount']} suffix={rec['excludedSuffixCount']} "
                f"etfUnknown={rec['etfFlagUnknownCount']}",
                flush=True,
            )
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
        "eligibilityPolicy": (
            "Use the workbook's PIT ETF flag to exclude ETFs. Also exclude obvious "
            "root-linked subordinate issue suffixes C/G/H/I/M/N/O/P/R/T/U/V/W/X/Z. "
            "Final QUQU-v2 eligibility additionally requires usable equity prices "
            "and capitalization data."
        ),
        "caveat": (
            "The public monthly workbook is not the paid Nasdaq Fundamental Data "
            "security master and therefore does not expose the complete historical "
            "Issue Type/Class or Public Float fields. Eligibility and free-float "
            "coverage must be audited downstream."
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
