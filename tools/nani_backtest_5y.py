#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import math
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

START_MONTH = "2021-10"
END_MONTH = "2026-09"
PRICE_START = "2021-03-01"
PRICE_END = "2026-10-02"
NDX_HISTORY = "https://raw.githubusercontent.com/thuningxu/sp500nq100/main/nasdaq100_components_history.csv"
NIKKEI_URL = "https://indexes.nikkei.co.jp/en/nkave/index/component?idx=nk225"
HEADERS = {"User-Agent": "Mozilla/5.0 Nani 5y research backtest"}

# Effective-date component changes. Current Nikkei list is used as the anchor and
# events are reversed to reconstruct prior month-start memberships.
NIKKEI_EVENTS = [
    ("2021-10-01", ["6861", "6981", "7974"], ["3105", "5901", "9412"]),
    ("2021-12-29", [], ["9062"]),
    ("2022-01-05", ["9147"], []),
    ("2022-04-04", ["8591"], ["8303"]),
    ("2022-09-29", ["6594"], ["8355"]),
    ("2022-10-03", ["6273", "7741"], ["3103", "6703"]),
    ("2022-10-04", ["5831"], ["1333"]),
    ("2023-04-03", ["4661", "6723", "9201"], ["3101", "5703", "5707"]),
    ("2023-10-02", ["4385", "6920", "9843"], ["5202", "7003", "8628"]),
    ("2024-04-01", ["3092", "6146", "6526"], ["2531", "5232", "5541"]),
    ("2024-10-01", ["4307", "7453"], ["3863", "4631"]),
    ("2025-04-01", ["6532"], ["9301"]),
    ("2025-07-04", ["6963"], ["9613"]),
    ("2025-10-01", ["3697"], ["7762"]),
    ("2025-11-11", ["4062"], ["6594"]),
    ("2026-04-01", ["285A", "543A", "7532"], ["6674", "6952", "7205"]),
    # Announced 2026-09-04, effective 2026-10-01. Included only so the Oct-2026
    # current anchor can be reversed back to Sep-2026 and earlier.
    ("2026-10-01", ["5016", "6525", "9697"], ["543A", "4902", "7004"]),
]

NDX_2026_EVENTS = [
    (date(2026, 6, 22), {"ALAB", "CRWV", "NBIS", "RKLB", "TER"}, {"CHTR", "CTSH", "INSM", "VRSK", "ZS"}),
    (date(2026, 6, 29), {"HONA"}, set()),
    (date(2026, 7, 7), {"SPCX"}, set()),
]

YF_ALIAS = {
    "FB": "META",
}


def month_add(ym: str, n: int) -> str:
    y, m = map(int, ym.split("-"))
    k = y * 12 + m - 1 + n
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


def month_start_date(ym: str) -> date:
    return pd.Period(ym, freq="M").start_time.date()


def prev_month_end(ym: str) -> pd.Timestamp:
    return pd.Period(month_add(ym, -1), freq="M").end_time


def month_end(ym: str) -> pd.Timestamp:
    return pd.Period(ym, freq="M").end_time


def yf_symbol_us(s: str) -> str:
    s = YF_ALIAS.get(s, s)
    return s.replace(".", "-")


def parse_current_nikkei() -> set[str]:
    r = requests.get(NIKKEI_URL, headers=HEADERS, timeout=60)
    r.raise_for_status()
    codes: list[str] = []
    for table in pd.read_html(io.StringIO(r.text)):
        for col in table.columns:
            label = " ".join(map(str, col)) if isinstance(col, tuple) else str(col)
            if "code" not in label.lower():
                continue
            for v in table[col].astype(str):
                x = v.strip().upper()
                if (len(x) == 4 and x.isdigit()) or (len(x) == 4 and x[:3].isdigit() and x[3].isalpha()):
                    codes.append(x)
    out = set(codes)
    if not 220 <= len(out) <= 230:
        raise RuntimeError(f"Nikkei current parse count {len(out)}")
    return out


def nikkei_members_at(asof: date, current: set[str]) -> set[str]:
    members = set(current)
    for ds, adds, removes in sorted(NIKKEI_EVENTS, reverse=True):
        eff = date.fromisoformat(ds)
        if eff > asof:
            # Reverse the event.
            members.difference_update(adds)
            members.update(removes)
    if len(members) != 225:
        raise RuntimeError(f"Nikkei reconstructed count {len(members)} at {asof}")
    return members


def load_ndx_history() -> pd.DataFrame:
    r = requests.get(NDX_HISTORY, headers=HEADERS, timeout=60)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    return df.dropna(subset=["date"]).sort_values("date")


def ndx_members_at(asof: date, hist: pd.DataFrame) -> set[str]:
    rows = hist[hist["date"] <= asof]
    if rows.empty:
        raise RuntimeError(f"No NDX row at {asof}")
    row = rows.iloc[-1]
    members = {x.strip().upper().replace("-", ".") for x in str(row["tickers"]).split(",") if x.strip()}
    for eff, adds, removes in NDX_2026_EVENTS:
        if asof >= eff:
            members.difference_update(removes)
            members.update(adds)
    if not 95 <= len(members) <= 110:
        raise RuntimeError(f"NDX reconstructed count {len(members)} at {asof}")
    return members


def download_close(symbols: list[str]) -> pd.DataFrame:
    parts = []
    for i in range(0, len(symbols), 60):
        chunk = symbols[i:i+60]
        d = yf.download(chunk, start=PRICE_START, end=PRICE_END, auto_adjust=True, actions=False,
                        progress=False, group_by="column", threads=True)
        if d is None or d.empty:
            continue
        if isinstance(d.columns, pd.MultiIndex):
            if "Close" in d.columns.get_level_values(0):
                c = d["Close"].copy()
            else:
                # yfinance occasionally reverses levels.
                c = pd.DataFrame({s: d[(s, "Close")] for s in chunk if (s, "Close") in d.columns})
        else:
            # Single-symbol chunk, unlikely here.
            c = d[["Close"]].rename(columns={"Close": chunk[0]})
        parts.append(c)
    if not parts:
        raise RuntimeError("No prices downloaded")
    out = pd.concat(parts, axis=1)
    out = out.loc[:, ~out.columns.duplicated()]
    out.index = pd.to_datetime(out.index).tz_localize(None)
    return out.sort_index()


def last_close(close: pd.DataFrame, sym: str, t: pd.Timestamp) -> float | None:
    if sym not in close.columns:
        return None
    s = pd.to_numeric(close[sym], errors="coerce").dropna()
    s = s[s.index <= t]
    if s.empty:
        return None
    v = float(s.iloc[-1])
    return v if math.isfinite(v) and v > 0 else None


def first_share_fallback(sym: str) -> float | None:
    try:
        ser = yf.Ticker(sym).get_shares_full(start=PRICE_START, end=PRICE_END)
        if ser is not None and len(ser):
            vals = pd.to_numeric(ser, errors="coerce").dropna()
            if len(vals):
                v = float(vals.iloc[-1])
                if math.isfinite(v) and v > 0:
                    return v
    except Exception:
        return None
    return None


def fetch_float_proxy(sym: str) -> tuple[str, float | None, str]:
    try:
        info = yf.Ticker(sym).info or {}
    except Exception:
        info = {}
    for key in ("floatShares", "sharesOutstanding", "impliedSharesOutstanding"):
        try:
            v = float(info.get(key))
            if math.isfinite(v) and v > 0:
                return sym, v, key
        except Exception:
            pass
    try:
        mc = float(info.get("marketCap")); px = float(info.get("currentPrice") or info.get("regularMarketPrice"))
        if math.isfinite(mc) and math.isfinite(px) and mc > 0 and px > 0:
            return sym, mc / px, "marketCap/currentPrice"
    except Exception:
        pass
    return sym, first_share_fallback(sym), "get_shares_full"


def build_float_proxies(symbols: list[str]) -> tuple[dict[str, float], dict[str, str]]:
    vals: dict[str, float] = {}
    methods: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(fetch_float_proxy, s): s for s in symbols}
        for fut in as_completed(futs):
            s, v, method = fut.result()
            if v and math.isfinite(v) and v > 0:
                vals[s] = float(v); methods[s] = method
    return vals, methods


def metrics(rets: list[float]) -> dict:
    a = np.array(rets, dtype=float)
    curve = np.cumprod(1 + a)
    total = float(curve[-1] - 1)
    cagr = float(curve[-1] ** (12 / len(a)) - 1)
    peak = np.maximum.accumulate(curve)
    dd = curve / peak - 1
    vol = float(np.std(a, ddof=1) * math.sqrt(12)) if len(a) > 1 else 0.0
    mean = float(np.mean(a) * 12)
    sharpe = mean / vol if vol > 0 else None
    return {
        "totalReturn": total,
        "cagr": cagr,
        "mddMonthEnd": float(np.min(dd)),
        "annualizedVol": vol,
        "sharpeRf0": sharpe,
        "positiveMonthRate": float(np.mean(a > 0)),
        "bestMonth": float(np.max(a)),
        "worstMonth": float(np.min(a)),
    }


def main():
    months = []
    m = START_MONTH
    while m <= END_MONTH:
        months.append(m); m = month_add(m, 1)
    assert len(months) == 60

    current_jp = parse_current_nikkei()
    ndx_hist = load_ndx_history()
    jp_by_month = {m: nikkei_members_at(month_start_date(m), current_jp) for m in months}
    us_by_month = {m: ndx_members_at(month_start_date(m), ndx_hist) for m in months}

    all_jp = sorted(set().union(*jp_by_month.values()))
    all_us = sorted(set().union(*us_by_month.values()))
    yf_map_us = {s: yf_symbol_us(s) for s in all_us}
    yf_map_jp = {c: f"{c}.T" for c in all_jp}
    all_yf = sorted(set(yf_map_us.values()) | set(yf_map_jp.values()) | {"JPY=X", "QQQ"})

    close = download_close(all_yf)
    # Fundamentals proxy: current/available float shares held constant through history.
    proxies, proxy_methods = build_float_proxies(sorted(set(yf_map_us.values()) | set(yf_map_jp.values())))

    nani_rets = []
    qqq_rets = []
    curve_rows = []
    monthly_details = []
    n_value = q_value = 100.0
    skipped_total = 0

    for m in months:
        sig_end = prev_month_end(m)
        early_end = month_end(month_add(m, -6))
        hold_end = month_end(m)
        fx0 = last_close(close, "JPY=X", sig_end)
        fx1 = last_close(close, "JPY=X", hold_end)
        if not fx0 or not fx1:
            raise RuntimeError(f"FX missing for {m}")

        us_members = sorted(us_by_month[m])
        jp_members = sorted(jp_by_month[m])
        candidates = []
        missing = []

        for s in us_members:
            ys = yf_map_us[s]
            p0 = last_close(close, ys, sig_end); pe = last_close(close, ys, early_end); p1 = last_close(close, ys, hold_end)
            sh = proxies.get(ys)
            if not p0 or not pe or not p1 or not sh:
                missing.append((s, "US")); continue
            candidates.append({"ticker": s, "yf": ys, "country": "US", "p0": p0, "pe": pe, "p1": p1,
                               "floatSharesProxy": sh, "floatCap": sh * p0, "gross": p0 / pe})

        jp_all = []
        for c in jp_members:
            ys = yf_map_jp[c]
            p0 = last_close(close, ys, sig_end); pe = last_close(close, ys, early_end); p1 = last_close(close, ys, hold_end)
            sh = proxies.get(ys)
            if not p0 or not pe or not p1 or not sh:
                missing.append((c, "JP")); continue
            jp_all.append({"ticker": f"{c}.T", "yf": ys, "country": "JP", "p0": p0, "pe": pe, "p1": p1,
                           "floatSharesProxy": sh, "floatCap": sh * p0, "gross": p0 / pe})

        jp_all.sort(key=lambda r: r["floatCap"], reverse=True)
        jp_selected = jp_all[:100]
        selected = [x for x in candidates if x["country"] == "US"] + jp_selected
        if len(jp_selected) < 95 or len([x for x in selected if x["country"] == "US"]) < 92:
            raise RuntimeError(f"Coverage too low {m}: JP={len(jp_selected)} US={len([x for x in selected if x['country']=='US'])} missing={missing[:20]}")

        grosses = [r["gross"] for r in selected if r["gross"] and math.isfinite(r["gross"]) and r["gross"] > 0]
        med = float(np.median(grosses))
        raw_us = {}; raw_jp = {}
        by_ticker = {}
        for r in selected:
            rel = r["gross"] / med
            raw = r["floatCap"] * rel**3
            r["relativeMomentum"] = rel; r["raw"] = raw
            by_ticker[r["ticker"]] = r
            (raw_us if r["country"] == "US" else raw_jp)[r["ticker"]] = raw

        def norm(raw, bucket):
            z = sum(raw.values())
            return {k: bucket * v / z for k, v in raw.items()}
        weights = {**norm(raw_us, 0.5), **norm(raw_jp, 0.5)}

        port = 0.0
        for t, w in weights.items():
            r = by_ticker[t]
            if r["country"] == "US":
                rr = r["p1"] / r["p0"] - 1
            else:
                # USD return: JPY equity return plus USDJPY translation.
                rr = (r["p1"] / fx1) / (r["p0"] / fx0) - 1
            port += w * rr
        nani_rets.append(float(port))

        q0 = last_close(close, "QQQ", sig_end); q1 = last_close(close, "QQQ", hold_end)
        if not q0 or not q1:
            raise RuntimeError(f"QQQ missing {m}")
        qr = q1 / q0 - 1
        qqq_rets.append(float(qr))
        n_value *= 1 + port; q_value *= 1 + qr
        skipped_total += len(missing)
        top = sorted(weights.items(), key=lambda kv: kv[1], reverse=True)[:10]
        curve_rows.append({"month": m, "nani": n_value, "qqq": q_value})
        monthly_details.append({
            "month": m, "naniReturn": port, "qqqReturn": qr,
            "jpSelected": len(jp_selected), "usSelected": len(raw_us),
            "jpWeight": sum(v for k,v in weights.items() if k.endswith('.T')),
            "usWeight": sum(v for k,v in weights.items() if not k.endswith('.T')),
            "momentumMedianGross": med,
            "missingCandidates": [{"ticker": a, "country": b} for a,b in missing],
            "top10": [{"ticker": t, "weight": w} for t,w in top],
        })

    payload = {
        "strategy": "Nani v3 research backtest",
        "benchmark": "QQQ",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "period": {"startMonth": START_MONTH, "endMonth": END_MONTH, "months": len(months),
                   "startSignalDate": "2021-09-30", "endDate": "2026-09-30"},
        "rules": {
            "universe": "Point-in-time Nasdaq-100 + reconstructed point-in-time Nikkei 225; Japan sleeve selects float-cap top 100",
            "signal": "6-1 gross momentum relative to selected-union cross-sectional median",
            "rawWeight": "Float-cap proxy × Relative Momentum^3",
            "countryBuckets": "Japan 50% / US 50%",
            "rebalance": "monthly at prior month-end close for research backtest",
            "singleNameCap": None,
            "baseCurrency": "USD; Japan monthly returns translated with USDJPY",
        },
        "caveat": "Constituent history is point-in-time/reconstructed, but historical free-float shares are not fully PIT. Current/available float-share or shares-outstanding proxies are held constant through history. Treat this as a research proxy backtest, not an official index-quality backtest.",
        "nani": metrics(nani_rets),
        "qqq": metrics(qqq_rets),
        "endingValue100": {"nani": n_value, "qqq": q_value},
        "equityCurve": curve_rows,
        "monthly": monthly_details,
        "diagnostics": {
            "uniqueNikkeiTickers": len(all_jp), "uniqueNasdaqTickers": len(all_us),
            "priceSymbols": len(all_yf), "floatProxyCoverage": len(proxies),
            "floatProxyMethods": dict(pd.Series(list(proxy_methods.values())).value_counts()),
            "candidateMissingOccurrences": skipped_total,
        },
        "sources": {
            "ndxHistory": NDX_HISTORY,
            "nikkeiCurrent": NIKKEI_URL,
            "nikkeiChanges": "Nikkei official component-change history through 2026-04 plus 2026-10 official-announcement changes",
            "prices": "Yahoo Finance via yfinance, auto-adjusted",
            "fx": "Yahoo Finance JPY=X",
        }
    }
    out = Path("research/nani_vs_qqq_5y.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"nani": payload["nani"], "qqq": payload["qqq"], "endingValue100": payload["endingValue100"], "diagnostics": payload["diagnostics"]}, indent=2))


if __name__ == "__main__":
    main()
