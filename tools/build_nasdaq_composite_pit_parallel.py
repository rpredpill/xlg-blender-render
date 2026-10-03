#!/usr/bin/env python3
"""Parallel month wrapper for build_nasdaq_composite_pit.py.

The underlying monthly parser/classifier remains exactly the same; this module
only executes independent calendar months concurrently and assembles the result
in chronological order.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

import build_nasdaq_composite_pit as base


def fetch_one(ym: str):
    # requests.Session is not intentionally shared between worker threads.
    with requests.Session() as session:
        return base.fetch_month(session, ym)


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
        futs = {ex.submit(fetch_one, ym): ym for ym in requested}
        for fut in as_completed(futs):
            ym = futs[fut]
            try:
                rec = fut.result()
                months[ym] = compact_record(ym, rec)
                print(
                    f"{ym}: raw={rec['rawCount']} eligible={rec['count']} "
                    f"etf={rec['excludedETFCount']} suffix={rec['excludedSuffixCount']} "
                    f"etfUnknown={rec['etfFlagUnknownCount']}",
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
        "buildMode": f"parallel-month-fetch-{workers}-workers",
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
    print(
        f"wrote {out}: {len(ordered)} months {min(ordered)}..{max(ordered)} "
        f"failures={len(failures)} workers={workers}",
        flush=True,
    )


if __name__ == "__main__":
    main()
