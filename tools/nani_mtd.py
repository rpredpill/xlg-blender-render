#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd


def load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def compute_mtd(L, month: str, rows: list[dict], prices: pd.DataFrame) -> dict:
    start = pd.Period(month, freq="M").start_time.tz_localize("UTC")
    now = pd.Timestamp(datetime.now(timezone.utc))
    details = []
    valid_weight = 0.0
    gross_port = 0.0
    last_dates: list[pd.Timestamp] = []

    for r in rows:
        yf_ticker = r.get("yf") or r.get("ticker")
        oser = L.extract_field(prices, "Open", yf_ticker)
        cser = L.extract_field(prices, "Close", yf_ticker)
        ret = None
        last_date = None
        if not oser.empty and not cser.empty:
            oi = pd.to_datetime(oser.index, utc=True)
            ci = pd.to_datetime(cser.index, utc=True)
            omask = (oi >= start) & (oi <= now)
            cmask = (ci >= start) & (ci <= now)
            if omask.any() and cmask.any():
                op = float(oser.iloc[np.flatnonzero(omask)[0]])
                close_pos = np.flatnonzero(cmask)[-1]
                cl = float(cser.iloc[close_pos])
                if op > 0 and cl > 0 and math.isfinite(op) and math.isfinite(cl):
                    ret = cl / op - 1.0
                    last_date = ci[close_pos]
                    last_dates.append(last_date)

        w = float(r.get("weight", 0.0))
        if ret is not None and math.isfinite(ret):
            valid_weight += w
            gross_port += w * (1.0 + ret)
        details.append({
            "ticker": r.get("ticker"),
            "country": r.get("country"),
            "weight": w,
            "monthlyReturn": ret,
            "lastDate": last_date.date().isoformat() if last_date is not None else None,
        })

    missing = max(0.0, 1.0 - valid_weight)
    if valid_weight <= 0 or missing > 0.02:
        return {
            "ready": False,
            "month": month,
            "coverageRatio": valid_weight,
            "missingReturnWeight": missing,
            "rows": details,
            "displayOnly": True,
            "officialHistory": False,
        }

    gross_port += missing
    for r in details:
        if r["monthlyReturn"] is None:
            r["monthlyReturn"] = 0.0

    return {
        "ready": True,
        "month": month,
        "asOfDate": max(last_dates).date().isoformat() if last_dates else None,
        "portfolioReturn": gross_port - 1.0,
        "coverageRatio": 1.0 - missing,
        "missingReturnWeight": missing,
        "rows": details,
        "displayOnly": True,
        "officialHistory": False,
        "definition": "Current-month display only: first available trading-day open to latest available close using the locked monthly Nani target.",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default=".")
    ap.add_argument("--legacy", default="/tmp/nani_live_legacy.py")
    args = ap.parse_args()

    site = Path(args.site)
    L = load_module("nani_legacy_mtd", args.legacy)
    latest_path = site / "nani-latest.json"
    hist_path = site / "nani-history.json"
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    hist = json.loads(hist_path.read_text(encoding="utf-8"))

    if latest.get("strategy") != "Nani" or latest.get("version") != "4.0":
        raise RuntimeError("Nani v4 latest required")

    month = str(latest.get("allocationMonth") or L.now_month())
    record = (hist.get("months") or {}).get(month) or {}
    locked_rows = record.get("rows") if record.get("provenance") == "forward-live-v4" else None
    rows = locked_rows if isinstance(locked_rows, list) and locked_rows else latest.get("rows") or []
    if not rows:
        raise RuntimeError("No Nani rows available for MTD")

    # Keep the UI target locked to the month-start forward snapshot rather than letting
    # a daily MTD refresh silently alter holdings/weights.
    latest["rows"] = rows
    latest["holdings"] = len(rows)
    weights = [float(r.get("weight", 0.0)) for r in rows]
    latest["weightSum"] = sum(weights)
    latest["maxWeight"] = max(weights) if weights else None
    latest["top10Weight"] = sum(sorted(weights, reverse=True)[:10])

    start = pd.Period(month, freq="M").start_time.date().isoformat()
    end = (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat()
    symbols = list(dict.fromkeys(str(r.get("yf") or r.get("ticker")) for r in rows if r.get("yf") or r.get("ticker")))
    prices = L.download_prices(symbols, start, end)
    latest["currentMonthMTD"] = compute_mtd(L, month, rows, prices)
    latest["mtdUpdatedAt"] = datetime.now(timezone.utc).isoformat()
    latest["mtdTargetPolicy"] = "Locked to the forward-live-v4 rows archived for the allocation month when available."

    latest_path.write_text(json.dumps(latest, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    mtd = latest["currentMonthMTD"]
    print(json.dumps({
        "month": month,
        "ready": mtd.get("ready"),
        "asOfDate": mtd.get("asOfDate"),
        "portfolioReturn": mtd.get("portfolioReturn"),
        "coverageRatio": mtd.get("coverageRatio"),
        "holdings": len(rows),
    }, indent=2))


if __name__ == "__main__":
    main()
