#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import math
import re
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

CAP = 0.20
ANCHOR_MONTH = "2026-04"
NIKKEI_URL = "https://indexes.nikkei.co.jp/en/nkave/index/component?idx=nk225"
TOPIX_URLS = [
    "https://www.jpx.co.jp/automation/markets/indices/topix/files/topixweight_j.csv",
    "https://www.jpx.co.jp/english/markets/indices/topix/tvdivq0000001vg2-att/topixweight_j.csv",
]
HEADERS = {"User-Agent": "Mozilla/5.0 (Nani strategy research; public data)"}
JP_ANCHORS = [
    "7203", "6758", "9984", "8306", "6501", "6861", "7974", "6098", "9983", "8035",
    "6857", "8058", "8031", "8001", "8316", "8411", "7267", "6902", "4063", "9432",
    "9433", "4502", "4568", "7751", "6367", "6273", "6954", "6981", "8766", "8591",
]


def number_like(v) -> bool:
    try:
        return math.isfinite(float(v))
    except Exception:
        return False


def month_add(ym: str, n: int) -> str:
    y, m = map(int, ym.split("-"))
    k = y * 12 + (m - 1) + n
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


def months_between(a: str, b: str) -> int:
    ay, am = map(int, a.split("-")); by, bm = map(int, b.split("-"))
    return (by - ay) * 12 + bm - am


def month_end(ym: str) -> pd.Timestamp:
    return pd.Period(ym, freq="M").end_time.tz_localize("UTC")


def now_month() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


def cap_and_redistribute(raw: dict[str, float], cap: float = CAP) -> dict[str, float]:
    free = set(raw)
    out: dict[str, float] = {}
    remaining = 1.0
    while free:
        total = sum(raw[s] for s in free)
        if not math.isfinite(total) or total <= 0:
            raise RuntimeError("Nani raw weight sum <= 0")
        over = [s for s in free if remaining * raw[s] / total > cap + 1e-12]
        if not over:
            for s in free:
                out[s] = remaining * raw[s] / total
            break
        for s in over:
            out[s] = cap
            remaining -= cap
            free.remove(s)
    return out


def get_nikkei_members() -> list[str]:
    html = requests.get(NIKKEI_URL, headers=HEADERS, timeout=45)
    html.raise_for_status()
    codes: list[str] = []
    try:
        for table in pd.read_html(io.StringIO(html.text)):
            table.columns = [" ".join(map(str, c)).strip() if isinstance(c, tuple) else str(c).strip() for c in table.columns]
            code_col = next((c for c in table.columns if c.lower() == "code" or "code" in c.lower()), None)
            if code_col is None:
                continue
            for v in table[code_col].astype(str):
                code = v.strip().upper()
                if re.fullmatch(r"(?:\d{4}|\d{3}[A-Z])", code):
                    codes.append(code)
    except Exception:
        pass
    if len(set(codes)) < 220:
        for m in re.finditer(r">\s*((?:\d{4}|\d{3}[A-Z]))\s*<", html.text, flags=re.I):
            codes.append(m.group(1).upper())
    codes = list(dict.fromkeys(codes))
    if not 220 <= len(codes) <= 230:
        raise RuntimeError(f"Nikkei 225 membership parse produced {len(codes)} codes")
    return codes


def load_topix_weights() -> tuple[str, dict[str, float], str]:
    last_error = None
    for url in TOPIX_URLS:
        try:
            r = requests.get(url, headers=HEADERS, timeout=45)
            r.raise_for_status()
            content = r.content
            df = None
            for enc in ("cp932", "shift_jis", "utf-8-sig"):
                try:
                    df = pd.read_csv(io.BytesIO(content), encoding=enc)
                    break
                except Exception:
                    pass
            if df is None or df.empty:
                raise RuntimeError("TOPIX weight CSV empty")
            code_col = next((c for c in df.columns if "コード" in str(c) or str(c).strip().lower() == "code"), None)
            weight_col = next((c for c in df.columns if "ウエイト" in str(c) or "weight" in str(c).lower()), None)
            date_col = next((c for c in df.columns if "日付" in str(c) or "date" in str(c).lower()), None)
            if code_col is None or weight_col is None:
                raise RuntimeError(f"TOPIX columns not found: {list(df.columns)}")
            out = {}
            for _, row in df.iterrows():
                code = str(row.get(code_col, "")).strip().upper().split(".")[0]
                if not re.fullmatch(r"(?:\d{4}|\d{3}[A-Z])", code):
                    continue
                txt = str(row.get(weight_col, "")).strip().replace("%", "").replace(",", "")
                try:
                    w = float(txt) / 100.0
                except Exception:
                    continue
                if math.isfinite(w) and w > 0:
                    out[code] = w
            if len(out) < 1500:
                raise RuntimeError(f"Only {len(out)} TOPIX weights parsed")
            dt = None
            if date_col is not None:
                vals = [str(x).strip() for x in df[date_col].dropna().tolist() if str(x).strip()]
                if vals:
                    raw = vals[0]
                    m = re.search(r"(20\d{2})[^0-9]?(\d{2})[^0-9]?(\d{2})", raw)
                    if m:
                        dt = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            return dt or month_end(month_add(now_month(), -1)).date().isoformat(), out, url
        except Exception as e:
            last_error = e
    raise RuntimeError(f"TOPIX weight download failed: {last_error}")


def extract_field(frame: pd.DataFrame, field: str, ticker: str) -> pd.Series:
    if frame is None or frame.empty:
        return pd.Series(dtype=float)
    if isinstance(frame.columns, pd.MultiIndex):
        for key in ((field, ticker), (ticker, field)):
            if key in frame.columns:
                return pd.to_numeric(frame[key], errors="coerce").dropna()
        return pd.Series(dtype=float)
    if field in frame.columns:
        return pd.to_numeric(frame[field], errors="coerce").dropna()
    return pd.Series(dtype=float)


def last_at(frame: pd.DataFrame, ticker: str, field: str, target: pd.Timestamp) -> float | None:
    s = extract_field(frame, field, ticker)
    if s.empty:
        return None
    idx = pd.to_datetime(s.index, utc=True)
    mask = idx <= target
    if not mask.any():
        return None
    v = float(s.iloc[np.flatnonzero(mask)[-1]])
    return v if math.isfinite(v) and v > 0 else None


def download_prices(symbols: list[str], start: str, end: str) -> pd.DataFrame:
    data = yf.download(
        tickers=symbols,
        start=start,
        end=end,
        auto_adjust=True,
        actions=False,
        progress=False,
        group_by="column",
        threads=True,
    )
    if data is None or data.empty:
        raise RuntimeError("Yahoo price download returned no data")
    return data


def fetch_info(symbol: str) -> dict:
    try:
        info = yf.Ticker(symbol).info or {}
    except Exception:
        return {"symbol": symbol}
    def num(k):
        try:
            v = float(info.get(k))
            return v if math.isfinite(v) and v > 0 else None
        except Exception:
            return None
    return {
        "symbol": symbol,
        "floatShares": num("floatShares"),
        "sharesOutstanding": num("sharesOutstanding") or num("impliedSharesOutstanding"),
        "marketCap": num("marketCap"),
        "currentPrice": num("currentPrice") or num("regularMarketPrice"),
    }


def build_float_share_proxies(ndx, jp_codes, prices, ququ, topix_date, topix_weights):
    proxy: dict[str, float] = {}
    diag = {"usFromQuqu": 0, "usInfoFallback": 0, "jpTopixImplied": 0, "jpInfoFallback": 0}
    qs = pd.Timestamp(ququ.get("signalDate") or month_end(month_add(now_month(), -2)))
    ququ_signal = qs.tz_localize("UTC") if qs.tzinfo is None else qs.tz_convert("UTC")
    qmap = {str(r.get("ticker", "")).upper(): r for r in (ququ.get("rows") or [])}
    missing_us = []
    for s in ndx:
        qr = qmap.get(s)
        px = last_at(prices, s.replace('.', '-'), "Close", ququ_signal)
        try: fc = float(qr.get("floatCap")) if qr else None
        except Exception: fc = None
        if fc and px and fc > 0 and px > 0:
            proxy[s] = fc / px
            diag["usFromQuqu"] += 1
        else:
            missing_us.append(s)
    if missing_us:
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(fetch_info, s.replace('.', '-')): s for s in missing_us}
            for fut in as_completed(futs):
                s = futs[fut]; fs = fut.result().get("floatShares")
                if fs:
                    proxy[s] = float(fs); diag["usInfoFallback"] += 1
    have_us = sum(1 for s in ndx if s in proxy)
    if have_us < max(95, len(ndx) - 3):
        miss = [s for s in ndx if s not in proxy]
        raise RuntimeError(f"US float-share proxy coverage too low: {have_us}/{len(ndx)} missing={miss}")

    topix_ts = pd.Timestamp(topix_date)
    if topix_ts.tzinfo is None: topix_ts = topix_ts.tz_localize("UTC")
    anchors = [c for c in JP_ANCHORS if c in jp_codes and c in topix_weights]
    anchor_info = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fetch_info, f"{c}.T"): c for c in anchors}
        for fut in as_completed(futs):
            anchor_info[futs[fut]] = fut.result()
    totals = []; anchor_rows = []
    for code in anchors:
        info = anchor_info.get(code) or {}
        fs = info.get("floatShares")
        px = last_at(prices, f"{code}.T", "Close", topix_ts)
        w = topix_weights.get(code)
        if fs and px and w and w > 0:
            total = float(fs) * float(px) / float(w)
            if math.isfinite(total) and total > 1e12:
                totals.append(total)
                anchor_rows.append({"code": code, "floatShares": fs, "price": px, "topixWeight": w, "impliedTopixFloatCapJPY": total})
    if len(totals) < 3:
        raise RuntimeError(f"Only {len(totals)} valid JP float anchors; need >=3")
    med = float(statistics.median(totals))
    abs_dev = [abs(x - med) for x in totals]
    mad = float(statistics.median(abs_dev)) if abs_dev else 0.0
    filtered = [x for x in totals if mad == 0 or abs(x - med) <= 4 * mad]
    if len(filtered) >= 3:
        med = float(statistics.median(filtered))
    diag["jpTopixFloatCapJPY"] = med
    diag["jpAnchorCount"] = len(totals)
    diag["jpAnchorRows"] = anchor_rows
    missing_jp = []
    for code in jp_codes:
        w = topix_weights.get(code)
        px = last_at(prices, f"{code}.T", "Close", topix_ts)
        if w and px and w > 0 and px > 0:
            proxy[f"{code}.T"] = (med * w) / px
            diag["jpTopixImplied"] += 1
        else:
            missing_jp.append(code)
    if missing_jp:
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(fetch_info, f"{c}.T"): c for c in missing_jp}
            for fut in as_completed(futs):
                c = futs[fut]; fs = fut.result().get("floatShares")
                if fs:
                    proxy[f"{c}.T"] = float(fs); diag["jpInfoFallback"] += 1
    have_jp = sum(1 for c in jp_codes if f"{c}.T" in proxy)
    if have_jp < 220:
        miss = [c for c in jp_codes if f"{c}.T" not in proxy]
        raise RuntimeError(f"JP float-share proxy coverage too low: {have_jp}/{len(jp_codes)} missing={miss[:20]}")
    return proxy, diag


def fx_at(prices: pd.DataFrame, target: pd.Timestamp) -> float:
    v = last_at(prices, "JPY=X", "Close", target)
    if not v or v < 50 or v > 300:
        raise RuntimeError(f"USDJPY invalid at {target}: {v}")
    return float(v)


def build_target(allocation_month, symbols, prices, float_shares, signal_recent=None):
    signal_month = signal_recent or month_add(allocation_month, -1)
    early_month = month_add(signal_month, -5)
    recent_ts = month_end(signal_month); early_ts = month_end(early_month)
    fx = fx_at(prices, recent_ts)
    rows = []; gross_values = []
    for meta in symbols:
        s = meta["ticker"]; yf_s = meta["yf"]
        recent = last_at(prices, yf_s, "Close", recent_ts)
        early = last_at(prices, yf_s, "Close", early_ts)
        fs = float_shares.get(s)
        if not recent or not fs:
            continue
        gross = (recent / early) if early else None
        if gross and math.isfinite(gross) and gross > 0:
            gross_values.append(gross)
        rows.append({**meta, "recentPrice": recent, "earlyPrice": early, "floatSharesProxy": fs, "grossMomentum": gross})
    if len(rows) < 315:
        raise RuntimeError(f"Only {len(rows)} Nani symbols have current price+float")
    if len(gross_values) < 300:
        raise RuntimeError(f"Only {len(gross_values)} Nani symbols have 6-1 history")
    neutral = float(np.median(gross_values)); raw = {}
    for r in rows:
        g = r["grossMomentum"] if r["grossMomentum"] and r["grossMomentum"] > 0 else neutral
        r["momentumFallback"] = r["grossMomentum"] is None
        r["grossMomentum"] = float(g); r["momentum"] = float(g - 1.0); r["relativeMomentum"] = float(g / neutral)
        local_fc = float(r["floatSharesProxy"] * r["recentPrice"])
        r["floatCapLocal"] = local_fc
        r["fxJPYperUSD"] = fx if r["country"] == "JP" else 1.0
        r["floatCap"] = local_fc / fx if r["country"] == "JP" else local_fc
        r["rawWeightScore"] = r["floatCap"] * (r["relativeMomentum"] ** 3)
        raw[r["ticker"]] = r["rawWeightScore"]
    weights = cap_and_redistribute(raw); out = []
    for r in rows:
        if r["ticker"] in weights:
            r["weight"] = float(weights[r["ticker"]]); out.append(r)
    out.sort(key=lambda x: x["weight"], reverse=True)
    for i, r in enumerate(out, 1): r["weightRank"] = i
    total = sum(r["weight"] for r in out); mx = max(r["weight"] for r in out)
    if abs(total - 1.0) > 1e-9 or mx > CAP + 1e-9:
        raise RuntimeError(f"Nani weight validation failed sum={total} max={mx}")
    return {"allocationMonth": allocation_month, "signalMonth": signal_month, "signalDate": recent_ts.date().isoformat(), "earlyDate": early_ts.date().isoformat(), "momentumMedianGross": neutral, "fxJPYperUSD": fx, "rows": out, "weightSum": total, "maxWeight": mx, "top10Weight": sum(r["weight"] for r in out[:10])}


def month_return(month, rows, prices):
    start = pd.Period(month, freq="M").start_time.tz_localize("UTC"); end = month_end(month)
    details = []; valid_weight = 0.0; gross_port = 0.0
    for r in rows:
        s = r["yf"]; oser = extract_field(prices, "Open", s); cser = extract_field(prices, "Close", s)
        ret = None
        if not oser.empty and not cser.empty:
            oi = pd.to_datetime(oser.index, utc=True); ci = pd.to_datetime(cser.index, utc=True)
            omask = (oi >= start) & (oi <= end); cmask = (ci >= start) & (ci <= end)
            if omask.any() and cmask.any():
                op = float(oser.iloc[np.flatnonzero(omask)[0]]); cl = float(cser.iloc[np.flatnonzero(cmask)[-1]])
                ret = cl / op - 1.0 if op > 0 and cl > 0 else None
        w = float(r["weight"])
        if ret is not None and math.isfinite(ret):
            valid_weight += w; gross_port += w * (1.0 + ret)
        details.append({"ticker": r["ticker"], "yf": r["yf"], "country": r["country"], "weight": w, "monthlyReturn": ret})
    missing = max(0.0, 1.0 - valid_weight)
    if missing > 0.02:
        raise RuntimeError(f"{month} missing return weight {missing:.2%}")
    gross_port += missing; port = gross_port - 1.0
    for r in details:
        if r["monthlyReturn"] is None: r["monthlyReturn"] = 0.0
    end_raw = {r["ticker"]: float(r["weight"]) * (1.0 + float(r["monthlyReturn"])) for r in details}
    denom = sum(end_raw.values()); end_weights = {s: v / denom for s, v in end_raw.items()}
    return {"portfolioReturn": port, "rows": details, "endWeights": end_weights, "missingReturnWeight": missing, "coverageRatio": 1.0 - missing, "lastDate": end.date().isoformat()}


def compact_target(t):
    return [{"ticker": r["ticker"], "yf": r["yf"], "country": r["country"], "weight": float(r["weight"]), "momentum": float(r["momentum"]), "relativeMomentum": float(r["relativeMomentum"]), "floatCap": float(r["floatCap"]), "floatSharesProxy": float(r["floatSharesProxy"])} for r in t["rows"]]


def apply_weights_to_meta(weights, meta_map):
    rows = []
    for s, w in weights.items():
        m = meta_map.get(s) or {"ticker": s, "yf": s, "country": "JP" if s.endswith(".T") else "US", "localTicker": s.replace(".T", "")}
        rows.append({"ticker": s, "yf": m["yf"], "country": m["country"], "weight": float(w)})
    return rows


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--site", default="."); args = ap.parse_args(); site = Path(args.site)
    quu = json.loads((site / "quu-latest.json").read_text(encoding="utf-8")); ququ = json.loads((site / "ququ-latest.json").read_text(encoding="utf-8"))
    ndx = list(dict.fromkeys(str(r.get("ticker", "")).upper().replace("-", ".") for r in (quu.get("rows") or []) if r.get("ticker")))
    if not 95 <= len(ndx) <= 105: raise RuntimeError(f"Nasdaq-100 source has {len(ndx)} rows")
    jp_codes = get_nikkei_members(); topix_date, topix_weights, topix_url = load_topix_weights()
    metas = [{"ticker": s, "yf": s.replace(".", "-"), "country": "US", "localTicker": s} for s in ndx]
    metas += [{"ticker": f"{c}.T", "yf": f"{c}.T", "country": "JP", "localTicker": c} for c in jp_codes]
    metas = list({m["ticker"]: m for m in metas}.values()); meta_map = {m["ticker"]: m for m in metas}
    current = now_month(); last_completed = month_add(current, -1); start_month = month_add(ANCHOR_MONTH, -6)
    start = (pd.Period(start_month, freq="M").start_time - pd.Timedelta(days=7)).date().isoformat(); end = (datetime.now(timezone.utc) + timedelta(days=4)).date().isoformat()
    prices = download_prices([m["yf"] for m in metas] + ["JPY=X"], start, end)
    float_shares, float_diag = build_float_share_proxies(ndx, jp_codes, prices, ququ, topix_date, topix_weights)

    hist_path = site / "nani-history.json"
    try: history = json.loads(hist_path.read_text(encoding="utf-8")) if hist_path.exists() else {}
    except Exception: history = {}
    old_months = history.get("months") if isinstance(history.get("months"), dict) else {}
    months = {k: v for k, v in old_months.items() if isinstance(v, dict) and number_like(v.get("portfolioReturn"))}
    current_rows = None; prev_end_weights = None; m = ANCHOR_MONTH
    while m <= last_completed:
        rec = old_months.get(m) if isinstance(old_months.get(m), dict) else None
        if rec and number_like(rec.get("portfolioReturn")):
            months[m] = rec; prev_end_weights = rec.get("endWeights") if isinstance(rec.get("endWeights"), dict) else None
            if prev_end_weights: current_rows = apply_weights_to_meta({k: float(v) for k, v in prev_end_weights.items()}, meta_map)
            m = month_add(m, 1); continue
        if months_between(ANCHOR_MONTH, m) % 6 == 0 or current_rows is None:
            target = build_target(m, metas, prices, float_shares); current_rows = compact_target(target)
        elif prev_end_weights:
            current_rows = apply_weights_to_meta({k: float(v) for k, v in prev_end_weights.items()}, meta_map)
        result = month_return(m, current_rows, prices)
        months[m] = {"allocationMonth": m, "signalMonth": month_add(m, -1) if months_between(ANCHOR_MONTH, m) % 6 == 0 else None, "rebalance": months_between(ANCHOR_MONTH, m) % 6 == 0, "portfolioReturn": float(result["portfolioReturn"]), "lastDate": result["lastDate"], "rows": result["rows"], "endWeights": result["endWeights"], "coverageRatio": result["coverageRatio"], "missingReturnWeight": result["missingReturnWeight"], "validatedFreeFloat": True, "universeCount": len(metas), "backfillMethod": "current-universe/current-float-share proxy; official TOPIX weights for Japan"}
        prev_end_weights = result["endWeights"]; current_rows = apply_weights_to_meta(prev_end_weights, meta_map); m = month_add(m, 1)

    if months_between(ANCHOR_MONTH, current) % 6 == 0:
        latest_signal = build_target(current, metas, prices, float_shares); latest_rows = compact_target(latest_signal); current_rebalance = True
    else:
        if last_completed in months and isinstance(months[last_completed].get("endWeights"), dict): latest_rows = apply_weights_to_meta({k: float(v) for k, v in months[last_completed]["endWeights"].items()}, meta_map)
        else: latest_rows = compact_target(build_target(ANCHOR_MONTH, metas, prices, float_shares))
        last_reb = current
        while months_between(ANCHOR_MONTH, last_reb) % 6 != 0: last_reb = month_add(last_reb, -1)
        latest_signal = build_target(last_reb, metas, prices, float_shares); current_rebalance = False
    months[current] = {"allocationMonth": current, "signalMonth": latest_signal["signalMonth"], "signalDate": latest_signal["signalDate"], "earlyDate": latest_signal["earlyDate"], "rebalance": current_rebalance, "rows": latest_rows, "validatedFreeFloat": True, "universeCount": len(metas)}
    history_payload = {"strategy": "Nani", "version": "1.0", "universe": "Nasdaq-100 ∪ Nikkei 225", "description": "Semiannual Nani target weights plus completed month-end return records.", "rules": {"weighting": "Float-Adjusted Market Cap × Relative Momentum^3", "rebalance": "6 months", "singleNameCap": CAP}, "dataCaveat": "Historical records use current free-float-share proxies on historical prices; Japan proxies are anchored to official JPX TOPIX free-float weights. This is not fully point-in-time historical float data.", "months": {k: months[k] for k in sorted(months)}}
    hist_path.write_text(json.dumps(history_payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")

    sig_by = {r["ticker"]: r for r in latest_signal["rows"]}; latest_out = []
    for r in latest_rows:
        z = dict(r); sr = sig_by.get(r["ticker"])
        if sr:
            for k in ["momentum", "grossMomentum", "relativeMomentum", "floatCap", "floatCapLocal", "floatSharesProxy", "rawWeightScore", "recentPrice", "earlyPrice", "fxJPYperUSD"]:
                if k in sr: z[k] = sr[k]
        latest_out.append(z)
    latest_out.sort(key=lambda x: x["weight"], reverse=True)
    for i, r in enumerate(latest_out, 1): r["weightRank"] = i
    total = sum(float(r["weight"]) for r in latest_out)
    latest_payload = {"strategy": "Nani", "version": "1.0", "ready": True, "generatedAt": datetime.now(timezone.utc).isoformat(), "universe": "Nasdaq-100 ∪ Nikkei 225", "holdings": len(latest_out), "allocationMonth": current, "rebalanceMonth": latest_signal["allocationMonth"], "signalMonth": latest_signal["signalMonth"], "signalDate": latest_signal["signalDate"], "earlyDate": latest_signal["earlyDate"], "rules": {"universe": "Nasdaq-100 ∪ Nikkei 225", "momentum": "6-1 gross momentum relative to the cross-sectional median", "weighting": "Float-Adjusted Market Cap × Relative Momentum^3", "baseCurrency": "USD", "singleNameCap": CAP, "capRedistribution": "proportional", "rebalance": "semiannual (6 months)"}, "sources": {"nasdaq100": "site quu-latest.json Nasdaq-100 universe", "nikkei225": NIKKEI_URL, "japanFreeFloatWeights": topix_url, "pricesAndAnchorFloatShares": "Yahoo Finance via yfinance", "usFreeFloatProxy": "site ququ-latest.json floatCap divided by contemporaneous adjusted close"}, "diagnostics": {**float_diag, "topixWeightDate": topix_date, "nasdaqCount": len(ndx), "nikkeiCount": len(jp_codes), "unionCount": len(metas), "momentumMedianGross": latest_signal["momentumMedianGross"], "fxJPYperUSD": latest_signal["fxJPYperUSD"]}, "freeDataCaveat": "Free data uses latest/current float-share proxies rather than fully point-in-time historical float shares. Japan is cross-checked and scaled from official JPX TOPIX free-float weights.", "weightSum": total, "maxWeight": max(float(r["weight"]) for r in latest_out), "top10Weight": sum(float(r["weight"]) for r in latest_out[:10]), "rows": latest_out}
    if len(latest_out) < 315 or abs(total - 1.0) > 1e-8 or latest_payload["maxWeight"] > CAP + 1e-8: raise RuntimeError(f"latest validation failed n={len(latest_out)} sum={total} max={latest_payload['maxWeight']}")
    (site / "nani-latest.json").write_text(json.dumps(latest_payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"strategy": "Nani", "allocationMonth": current, "rebalanceMonth": latest_signal["allocationMonth"], "holdings": len(latest_out), "maxWeight": latest_payload["maxWeight"], "top10Weight": latest_payload["top10Weight"], "completedHistoryMonths": len([1 for v in months.values() if number_like(v.get("portfolioReturn"))]), "topixWeightDate": topix_date, "jpAnchors": float_diag.get("jpAnchorCount")}, indent=2))


if __name__ == "__main__":
    main()
