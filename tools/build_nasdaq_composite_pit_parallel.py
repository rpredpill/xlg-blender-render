#!/usr/bin/env python3
"""Fast parallel month wrapper for build_nasdaq_composite_pit.py.

Known Nasdaq monthly workbooks use sheet ``NASDAQ`` with row 0 as the header.
We parse that layout once per month.  If the layout is absent or implausible,
we fall back to the slower multi-header parser in build_nasdaq_composite_pit.py.
Eligibility/classification is always delegated to the same base functions.
"""
from __future__ import annotations

import argparse
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

import build_nasdaq_composite_pit as base


def fast_fetch_one(ym: str, retries: int = 4):
    period = pd.Period(ym, freq="M")
    url = base.FILE_TEMPLATE.format(year=period.year, ym_nodash=ym.replace("-", ""))
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            with requests.Session() as session:
                r = session.get(url, headers=base.UA, timeout=90)
                r.raise_for_status()
            if len(r.content) < 10_000:
                raise RuntimeError(f"response too small ({len(r.content)} bytes)")
            if not r.content.startswith(b"PK"):
                raise RuntimeError(
                    f"not an XLSX/ZIP payload; content-type={r.headers.get('content-type')}"
                )

            try:
                df = pd.read_excel(
                    io.BytesIO(r.content),
                    sheet_name="NASDAQ",
                    header=0,
                    engine="openpyxl",
                )
                symbol_cols = base.candidate_symbol_columns(df)
                if not symbol_cols:
                    raise RuntimeError("fast path: symbol column not found")
                symbol_col = symbol_cols[0]
                etf_col = base.find_etf_column(df)
                rows = []
                for _, row in df.iterrows():
                    sym = base.clean_symbol(row.get(symbol_col))
                    if not sym:
                        continue
                    rows.append({
                        "symbol": sym,
                        "etf": base.yn_flag(row.get(etf_col)) if etf_col is not None else None,
                    })
                unique_count = len({x["symbol"] for x in rows})
                if unique_count < 500:
                    raise RuntimeError(
                        f"fast path: only {unique_count} unique symbols"
                    )
                classified = base.classify_month(rows)
                diagnostics = {
                    "parser": "fast-known-layout",
                    "fallbackUsed": False,
                    "sheets": [{
                        "sheet": "NASDAQ",
                        "rows": int(len(df)),
                        "columns": [str(x) for x in df.columns[:30]],
                        "bestUniqueSymbols": unique_count,
                        "bestHeaderRow": 0,
                        "bestSymbolColumn": str(symbol_col),
                        "bestEtfColumn": str(etf_col) if etf_col is not None else None,
                    }],
                }
                return {
                    "month": ym,
                    "url": url,
                    "bytes": len(r.content),
                    "diagnostics": diagnostics,
                    **classified,
                }
            except Exception as fast_exc:
                # The base parser handles alternate sheets/header offsets.  Use a
                # fresh session so a fast-path parse problem cannot corrupt state.
                with requests.Session() as fallback_session:
                    rec = base.fetch_month(fallback_session, ym, retries=1)
                rec["diagnostics"] = {
                    **(rec.get("diagnostics") or {}),
                    "parser": "base-fallback",
                    "fallbackUsed": True,
                    "fastPathError": str(fast_exc),
                }
                return rec
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.5 * attempt)
    raise RuntimeError(f"{ym}: {last_error}")


def month_range(start: str, end: str) -> list[str]:
    out = []
    cur = start
    while pd.Period(cur, freq="M") <= pd.Period(end, freq="M"):
        out.append(cur)
        cur = base.month_add(cur, 1)
    return out


def compact_record(ym: str, rec: dict) -> dict:
    return {
        "month": ym,
        "sourceUrl": rec["url"],
        "rawCount": int(rec["rawCount"]),
        "count": int(rec["count"]),
        "excludedETFCount": int(rec["excludedETFCount"]),
        "excludedSuffixCount": int(rec["excludedSuffixCount"]),
        "etfFlagUnknownCount": int(rec["etfFlagUnknownCount"]),
        "excludedETFSample": rec["excludedETFSample"],
        "excludedSuffixSample": rec["excludedSuffixSample"],
        "symbols": rec["symbols"],
        "parserDiagnostics": rec["diagnostics"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2022-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--output", default="nasdaq-composite-pit.json")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    end = args.end or base.last_completed_month()
    if pd.Period(args.start, freq="M") > pd.Period(end, freq="M"):
        raise SystemExit("start > end")
    workers = max(1, min(int(args.workers), 12))
    requested = month_range(args.start, end)

    months: dict[str, dict] = {}
    failures: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fast_fetch_one, ym): ym for ym in requested}
        for fut in as_completed(futs):
            ym = futs[fut]
            try:
                rec = fut.result()
                months[ym] = compact_record(ym, rec)
                print(
                    f"{ym}: raw={rec['rawCount']} eligible={rec['count']} "
                    f"etf={rec['excludedETFCount']} suffix={rec['excludedSuffixCount']} "
                    f"etfUnknown={rec['etfFlagUnknownCount']} "
                    f"parser={rec['diagnostics'].get('parser')}",
                    flush=True,
                )
            except Exception as exc:
                failures[ym] = str(exc)
                print(f"WARN {ym}: {exc}", flush=True)

    if not months:
        raise RuntimeError("No monthly Nasdaq-listed reports could be parsed")

    ordered = {k: months[k] for k in sorted(months)}
    payload = {
        "dataset": "Nasdaq monthly PIT listed-security universe",
        "purpose": "QUQU-v2 Nasdaq Composite universe reconstruction",
        "sourcePage": base.SOURCE_PAGE,
        "sourceFileTemplate": base.FILE_TEMPLATE,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "requestedStart": args.start,
        "requestedEnd": end,
        "firstSuccessfulMonth": min(ordered),
        "lastSuccessfulMonth": max(ordered),
        "successfulMonths": len(ordered),
        "failedMonths": {k: failures[k] for k in sorted(failures)},
        "buildMode": f"parallel-fast-path-{workers}-workers-with-base-fallback",
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
        "months": ordered,
    }
    out = Path(args.output)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    fallback_count = sum(
        1 for r in ordered.values()
        if (r.get("parserDiagnostics") or {}).get("fallbackUsed")
    )
    print(
        f"wrote {out}: {len(ordered)} months {min(ordered)}..{max(ordered)} "
        f"failures={len(failures)} workers={workers} fallbacks={fallback_count}",
        flush=True,
    )


if __name__ == "__main__":
    main()
