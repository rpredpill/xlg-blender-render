#!/usr/bin/env python3
"""Reconstruct and backtest QQQ ∩ SPMO, 2021-09 to 2026-09.

Method:
- Rebalance on the 3rd Friday of March and September.
- QQQ weights: first QQQ public N-PORT report after each rebalance (normally month-end).
- SPMO weights/constituents: first SPMO public N-PORT report after each rebalance
  (normally May/November due to its fiscal-quarter N-PORT cadence).
- Back-cast each reported holding weight to the rebalance date with
      w_target ~= w_report * ETF_total_return(target->report)
                           / stock_total_return(target->report)
  then form only the CUSIP intersection, raw weight = QQQ + SPMO, normalize to 100%.
- Daily total-return backtest uses Yahoo adjusted closes.

This is a historical reconstruction proxy, not a claim that the later-filed N-PORT
holdings were publicly knowable on the rebalance date. Corporate actions or off-cycle
index changes between rebalance and N-PORT report can also create small reconstruction
error; diagnostics are emitted.
"""

from __future__ import annotations

import calendar
import html
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests
import yfinance as yf

UA = "qqq-spmo-intersection-research/1.0 contact=no-reply@github.com"
SEC_HEADERS = {"User-Agent": UA, "Accept-Encoding": "gzip, deflate", "Host": "www.sec.gov"}
DATA_SEC_HEADERS = {"User-Agent": UA, "Accept-Encoding": "gzip, deflate", "Host": "data.sec.gov"}
SPMO_TRUST_CIK = "1378872"
SPMO_SERIES = "S000050154"
QQQ_CIK = "0001067839"
OUTDIR = Path("research/backtest_output")
OUTDIR.mkdir(parents=True, exist_ok=True)

session = requests.Session()


@dataclass
class Holding:
    cusip: str
    name: str
    title: str
    pct: float


@dataclass
class Snapshot:
    fund: str
    accession: str
    report_date: pd.Timestamp
    filing_date: Optional[pd.Timestamp]
    holdings: Dict[str, Holding]


def get(url: str, *, data_sec: bool = False, timeout: int = 45, tries: int = 5) -> requests.Response:
    headers = DATA_SEC_HEADERS if data_sec else SEC_HEADERS
    last = None
    for i in range(tries):
        try:
            r = session.get(url, headers=headers, timeout=timeout)
            if r.status_code in (403, 429, 500, 502, 503, 504):
                last = RuntimeError(f"HTTP {r.status_code} {url}")
                time.sleep(1.5 * (i + 1))
                continue
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            time.sleep(1.2 * (i + 1))
    raise RuntimeError(f"GET failed: {url}: {last}")


def tag_text(block: str, tag: str) -> str:
    m = re.search(rf"<(?:[A-Za-z0-9_]+:)?{re.escape(tag)}(?:\s[^>]*)?>([\s\S]*?)</(?:[A-Za-z0-9_]+:)?{re.escape(tag)}>", block)
    if not m:
        return ""
    return html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()


def parse_nport(xml: str, fund: str, accession: str, filing_date: Optional[pd.Timestamp]) -> Snapshot:
    rep = tag_text(xml, "repPdDate")
    if not rep:
        raise ValueError(f"No repPdDate: {fund} {accession}")
    holdings: Dict[str, Holding] = {}
    for m in re.finditer(r"<(?:[A-Za-z0-9_]+:)?invstOrSec>([\s\S]*?)</(?:[A-Za-z0-9_]+:)?invstOrSec>", xml):
        b = m.group(1)
        cusip = tag_text(b, "cusip").upper().replace(" ", "")
        pct_s = tag_text(b, "pctVal")
        if not cusip or not pct_s:
            continue
        try:
            pct = float(pct_s)
        except ValueError:
            continue
        if not math.isfinite(pct) or pct <= 0:
            continue
        name = tag_text(b, "name")
        title = tag_text(b, "title")
        # CUSIP should uniquely identify share class. If a filing repeats one, sum it.
        if cusip in holdings:
            old = holdings[cusip]
            holdings[cusip] = Holding(cusip, old.name or name, old.title or title, old.pct + pct)
        else:
            holdings[cusip] = Holding(cusip, name, title, pct)
    return Snapshot(fund, accession, pd.Timestamp(rep), filing_date, holdings)


def archive_url(cik: str, accession: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/primary_doc.xml"


def load_qqq_snapshots() -> List[Snapshot]:
    url = f"https://data.sec.gov/submissions/CIK{QQQ_CIK}.json"
    js = get(url, data_sec=True).json()
    recent = js["filings"]["recent"]
    rows = []
    for form, acc, filed in zip(recent["form"], recent["accessionNumber"], recent["filingDate"]):
        if form != "NPORT-P":
            continue
        rows.append((acc, pd.Timestamp(filed)))
    snaps = []
    for acc, fd in rows:
        try:
            xml = get(archive_url(QQQ_CIK, acc)).text
            s = parse_nport(xml, "QQQ", acc, fd)
            if pd.Timestamp("2021-06-01") <= s.report_date <= pd.Timestamp("2026-06-30"):
                snaps.append(s)
            time.sleep(0.12)
        except Exception as e:
            print(f"WARN QQQ {acc}: {e}", file=sys.stderr)
    snaps.sort(key=lambda x: x.report_date)
    return snaps


def spmo_accessions_atom() -> List[Tuple[str, Optional[pd.Timestamp]]]:
    # SEC supports querying a series ID directly; this avoids mixing the many series in the trust.
    url = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
           f"&CIK={SPMO_SERIES}&type=NPORT-P&dateb=&owner=include&count=100&output=atom")
    txt = get(url).text
    out = []
    for entry in re.findall(r"<entry>([\s\S]*?)</entry>", txt):
        accm = re.search(r"<accession-number>([^<]+)</accession-number>", entry)
        if not accm:
            continue
        acc = accm.group(1).strip()
        # Atom filing-date can appear in a filing-date tag or in the summary.
        fdm = re.search(r"<filing-date>(\d{4}-\d{2}-\d{2})</filing-date>", entry)
        if not fdm:
            fdm = re.search(r"Filing Date:\s*</b>\s*(\d{4}-\d{2}-\d{2})", entry)
        fd = pd.Timestamp(fdm.group(1)) if fdm else None
        out.append((acc, fd))
    return out


def load_spmo_snapshots() -> List[Snapshot]:
    rows = spmo_accessions_atom()
    if not rows:
        raise RuntimeError("No SPMO NPORT-P accessions from SEC Atom")
    snaps = []
    for acc, fd in rows:
        try:
            xml = get(archive_url(SPMO_TRUST_CIK, acc)).text
            s = parse_nport(xml, "SPMO", acc, fd)
            if pd.Timestamp("2021-08-01") <= s.report_date <= pd.Timestamp("2026-08-31"):
                snaps.append(s)
            time.sleep(0.12)
        except Exception as e:
            print(f"WARN SPMO {acc}: {e}", file=sys.stderr)
    snaps.sort(key=lambda x: x.report_date)
    return snaps


def third_friday(year: int, month: int) -> pd.Timestamp:
    c = calendar.Calendar(firstweekday=calendar.MONDAY)
    fridays = [d for d in c.itermonthdates(year, month) if d.month == month and d.weekday() == 4]
    return pd.Timestamp(fridays[2])


def targets() -> List[pd.Timestamp]:
    ds = []
    for y in range(2021, 2027):
        for m in (3, 9):
            d = third_friday(y, m)
            if pd.Timestamp("2021-09-01") <= d <= pd.Timestamp("2026-03-31"):
                ds.append(d)
    return ds


def choose_after(snaps: List[Snapshot], target: pd.Timestamp, max_days: int) -> Snapshot:
    cand = [s for s in snaps if s.report_date >= target and (s.report_date - target).days <= max_days]
    if not cand:
        raise RuntimeError(f"No snapshot within {max_days}d after {target.date()}")
    return min(cand, key=lambda s: s.report_date)


def map_cusips_openfigi(items: Dict[str, str]) -> Dict[str, Optional[str]]:
    """Map CUSIP->US ticker. items is cusip->issuer/title hint."""
    keys = sorted(items)
    ans: Dict[str, Optional[str]] = {k: None for k in keys}
    api = "https://api.openfigi.com/v3/mapping"
    headers = {"Content-Type": "application/json", "User-Agent": UA}
    for off in range(0, len(keys), 10):
        batch_keys = keys[off:off+10]
        payload = [{"idType": "ID_CUSIP", "idValue": k, "exchCode": "US"} for k in batch_keys]
        for attempt in range(7):
            r = session.post(api, headers=headers, json=payload, timeout=45)
            if r.status_code == 429:
                wait = 15 + attempt * 10
                print(f"OpenFIGI 429, wait {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            break
        else:
            data = [{} for _ in batch_keys]
        for k, row in zip(batch_keys, data):
            candidates = row.get("data") or []
            # Favor Common Stock and US exchange records.
            candidates = sorted(candidates, key=lambda x: (
                0 if str(x.get("securityType2", "")).lower() == "common stock" else 1,
                0 if x.get("exchCode") in {"US", "UN", "UW", "UQ", "UA", "UP"} else 1,
            ))
            if candidates:
                t = candidates[0].get("ticker")
                if t:
                    ans[k] = str(t).replace("/", ".")
        if off + 10 < len(keys):
            time.sleep(3.0)
    return ans


def yahoo_search_ticker(name: str) -> Optional[str]:
    try:
        url = "https://query1.finance.yahoo.com/v1/finance/search"
        r = session.get(url, params={"q": name, "quotesCount": 10, "newsCount": 0},
                        headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        if not r.ok:
            return None
        for q in r.json().get("quotes", []):
            if q.get("quoteType") == "EQUITY" and q.get("exchange") in {"NMS", "NGM", "NCM", "NYQ", "PCX"}:
                return q.get("symbol")
    except Exception:
        return None
    return None


def yahoo_symbol(t: str) -> str:
    # Yahoo uses hyphen for class suffixes (BRK.B -> BRK-B). QQQ overlap rarely needs this,
    # but make it generic.
    return t.replace(".", "-")


def dl_prices(tickers: Iterable[str], start: str, end: str) -> pd.DataFrame:
    ts = sorted(set(yahoo_symbol(x) for x in tickers if x))
    # End is exclusive in yfinance.
    end_ex = (pd.Timestamp(end) + pd.Timedelta(days=3)).strftime("%Y-%m-%d")
    data = yf.download(ts, start=start, end=end_ex, auto_adjust=False, actions=False,
                       progress=False, group_by="column", threads=True, timeout=30)
    if data.empty:
        raise RuntimeError("yfinance returned no price data")
    if len(ts) == 1:
        if "Adj Close" in data.columns:
            out = data[["Adj Close"]].rename(columns={"Adj Close": ts[0]})
        else:
            out = data[["Close"]].rename(columns={"Close": ts[0]})
    else:
        field = "Adj Close" if "Adj Close" in data.columns.get_level_values(0) else "Close"
        out = data[field].copy()
    out.index = pd.to_datetime(out.index).tz_localize(None)
    out = out.sort_index().replace([np.inf, -np.inf], np.nan).ffill()
    return out


def nearest_price(px: pd.DataFrame, ticker: str, d: pd.Timestamp, days: int = 7) -> Optional[float]:
    t = yahoo_symbol(ticker)
    if t not in px.columns:
        return None
    s = px[t].dropna()
    if s.empty:
        return None
    # Prefer exact date, otherwise nearest session within days.
    ix = s.index.get_indexer([d], method="nearest")[0]
    if ix < 0:
        return None
    dd = s.index[ix]
    if abs((dd - d).days) > days:
        return None
    v = float(s.iloc[ix])
    return v if v > 0 and math.isfinite(v) else None


def main() -> None:
    print("Loading SEC N-PORT snapshots...")
    qqq_snaps = load_qqq_snapshots()
    spmo_snaps = load_spmo_snapshots()
    print("QQQ reports:", [(str(s.report_date.date()), s.accession) for s in qqq_snaps])
    print("SPMO reports:", [(str(s.report_date.date()), s.accession) for s in spmo_snaps])

    rebal_dates = targets()
    pairs = []
    cusip_hints: Dict[str, str] = {}
    for d in rebal_dates:
        q = choose_after(qqq_snaps, d, 25)   # quarter-end after third Friday
        s = choose_after(spmo_snaps, d, 85)  # May/Nov report after Mar/Sep rebalance
        inter = sorted(set(q.holdings) & set(s.holdings))
        if not inter:
            raise RuntimeError(f"Empty QQQ/SPMO CUSIP intersection at {d.date()}")
        for c in inter:
            h = q.holdings[c]
            cusip_hints[c] = h.name or h.title or s.holdings[c].name
        pairs.append((d, q, s, inter))
        print(f"{d.date()} QQQ={q.report_date.date()} SPMO={s.report_date.date()} intersection={len(inter)}")

    print(f"Mapping {len(cusip_hints)} unique intersection CUSIPs via OpenFIGI...")
    cmap = map_cusips_openfigi(cusip_hints)
    unresolved = [c for c, t in cmap.items() if not t]
    if unresolved:
        print(f"OpenFIGI unresolved {len(unresolved)}; Yahoo-search fallback")
        for c in unresolved:
            t = yahoo_search_ticker(cusip_hints[c])
            if t:
                cmap[c] = t
            time.sleep(0.2)
    unresolved = [c for c, t in cmap.items() if not t]
    print("Unresolved CUSIPs:", [(c, cusip_hints[c]) for c in unresolved])

    all_tickers = {"QQQ", "SPMO"}
    for c, t in cmap.items():
        if t:
            all_tickers.add(t)
    print(f"Downloading adjusted daily prices for {len(all_tickers)} symbols...")
    px = dl_prices(all_tickers, "2021-08-01", "2026-09-18")

    # Verify mapped price coverage; retry Yahoo-search for mappings whose ticker has no prices.
    for c, t in list(cmap.items()):
        if not t:
            continue
        yt = yahoo_symbol(t)
        if yt not in px.columns or px[yt].dropna().empty:
            alt = yahoo_search_ticker(cusip_hints[c])
            if alt and yahoo_symbol(alt) != yt:
                cmap[c] = alt
                all_tickers.add(alt)
    missing_symbols = sorted({t for t in cmap.values() if t and yahoo_symbol(t) not in px.columns})
    if missing_symbols:
        try:
            extra = dl_prices(missing_symbols, "2021-08-01", "2026-09-18")
            px = px.join(extra, how="outer", rsuffix="_extra")
            # Resolve duplicate suffix columns if any.
            for col in list(px.columns):
                if str(col).endswith("_extra"):
                    base = str(col)[:-6]
                    if base in px.columns:
                        px[base] = px[base].combine_first(px[col])
                        px = px.drop(columns=[col])
                    else:
                        px = px.rename(columns={col: base})
        except Exception as e:
            print("WARN extra price download:", e)

    # Determine actual trading dates for each scheduled third Friday.
    qqq_s = px[yahoo_symbol("QQQ")].dropna()
    actual_dates: List[pd.Timestamp] = []
    for d in rebal_dates:
        # third Friday should be a session; if holiday, use prior session.
        eligible = qqq_s.index[qqq_s.index <= d]
        if len(eligible) == 0:
            raise RuntimeError(f"No QQQ price before {d}")
        actual_dates.append(eligible[-1])
    end_sched = pd.Timestamp("2026-09-18")
    end_eligible = qqq_s.index[qqq_s.index <= end_sched]
    end_date = end_eligible[-1]

    weights_by_date: Dict[pd.Timestamp, Dict[str, float]] = {}
    rows = []
    map_fail_weight = []

    for (sched, q, s, inter), d in zip(pairs, actual_dates):
        qfund_target = nearest_price(px, "QQQ", d)
        qfund_snap = nearest_price(px, "QQQ", q.report_date)
        sfund_target = nearest_price(px, "SPMO", d)
        sfund_snap = nearest_price(px, "SPMO", s.report_date)
        if None in (qfund_target, qfund_snap, sfund_target, sfund_snap):
            raise RuntimeError(f"Missing ETF price for backcast at {d.date()}")
        q_fret = qfund_snap / qfund_target
        s_fret = sfund_snap / sfund_target

        raw: Dict[str, float] = {}
        diag = []
        omitted_raw = 0.0
        for c in inter:
            t = cmap.get(c)
            qh, sh = q.holdings[c], s.holdings[c]
            if not t:
                omitted_raw += qh.pct + sh.pct
                diag.append((c, None, qh.name, qh.pct, sh.pct, None, None))
                continue
            p0 = nearest_price(px, t, d)
            pq = nearest_price(px, t, q.report_date)
            ps = nearest_price(px, t, s.report_date)
            if None in (p0, pq, ps) or p0 <= 0:
                omitted_raw += qh.pct + sh.pct
                diag.append((c, t, qh.name, qh.pct, sh.pct, None, None))
                continue
            q_stock_ret = pq / p0
            s_stock_ret = ps / p0
            q_w = qh.pct * q_fret / q_stock_ret
            s_w = sh.pct * s_fret / s_stock_ret
            r = q_w + s_w
            if r <= 0 or not math.isfinite(r):
                continue
            raw[t] = raw.get(t, 0.0) + r
            diag.append((c, t, qh.name, qh.pct, sh.pct, q_w, s_w))
        total = sum(raw.values())
        if total <= 0:
            raise RuntimeError(f"No priced intersection at {d.date()}")
        w = {t: v / total for t, v in raw.items()}
        weights_by_date[d] = w
        map_fail_weight.append({"date": str(d.date()), "omitted_raw_pct_proxy": omitted_raw})

        for c, t, nm, qrep, srep, qw, sw in diag:
            if t and t in w:
                rows.append({
                    "rebalance_date": str(d.date()),
                    "scheduled_date": str(sched.date()),
                    "ticker": t,
                    "cusip": c,
                    "name": nm,
                    "qqq_report_date": str(q.report_date.date()),
                    "spmo_report_date": str(s.report_date.date()),
                    "qqq_report_weight_pct": qrep,
                    "spmo_report_weight_pct": srep,
                    "qqq_backcast_weight_pct": qw,
                    "spmo_backcast_weight_pct": sw,
                    "combined_raw_pct": (qw + sw) if qw is not None and sw is not None else None,
                    "final_weight_pct": w[t] * 100,
                })
        print(d.date(), "holdings", len(w), "top", [(t, round(v*100, 2)) for t,v in sorted(w.items(), key=lambda x:-x[1])[:8]])

    hold_df = pd.DataFrame(rows)
    hold_df.to_csv(OUTDIR / "rebalance_holdings.csv", index=False)

    # Daily portfolio NAV, buy-and-hold within each semiannual sleeve, then reset weights.
    nav_parts = []
    chain = 1.0
    date_seq = actual_dates + [end_date]
    for i, d0 in enumerate(actual_dates):
        d1 = date_seq[i+1]
        w = weights_by_date[d0]
        syms = [yahoo_symbol(t) for t in w]
        # Use sessions from QQQ; ffill individual symbols for isolated missing bars.
        idx = qqq_s.loc[d0:d1].index
        block = px.reindex(idx)[syms].ffill()
        # Require a valid start price for every constituent; renormalize only if tiny failures.
        good = [t for t in w if yahoo_symbol(t) in block.columns and pd.notna(block[yahoo_symbol(t)].iloc[0])]
        dropped = set(w) - set(good)
        dropped_w = sum(w[t] for t in dropped)
        if dropped_w > 0.005:
            print(f"WARN {d0.date()} price-start dropped weight {dropped_w:.3%}: {sorted(dropped)}")
        ww = {t: w[t] for t in good}
        z = sum(ww.values())
        ww = {t: v/z for t,v in ww.items()}
        rel = pd.DataFrame(index=idx)
        for t, wt in ww.items():
            col = yahoo_symbol(t)
            srs = block[col].ffill()
            rel[t] = srs / srs.iloc[0]
        sleeve = sum(rel[t] * wt for t, wt in ww.items())
        sleeve = sleeve * chain
        # Avoid duplicate boundary date except first period.
        if i > 0:
            sleeve = sleeve.iloc[1:]
        nav_parts.append(sleeve)
        chain = float(sleeve.iloc[-1])
    nav = pd.concat(nav_parts).sort_index()
    nav.name = "strategy"

    b = qqq_s.loc[nav.index.min():nav.index.max()]
    b = b / b.iloc[0]
    b = b.reindex(nav.index).ffill()
    b.name = "qqq"
    curves = pd.concat([nav, b], axis=1)
    curves.to_csv(OUTDIR / "daily_nav.csv")

    def metrics(srs: pd.Series) -> dict:
        srs = srs.dropna()
        years = (srs.index[-1] - srs.index[0]).days / 365.2425
        total = float(srs.iloc[-1] / srs.iloc[0] - 1)
        cagr = float((srs.iloc[-1] / srs.iloc[0]) ** (1/years) - 1)
        dd = srs / srs.cummax() - 1
        mdd = float(dd.min())
        daily = srs.pct_change().dropna()
        vol = float(daily.std(ddof=1) * math.sqrt(252))
        sharpe0 = float(daily.mean() / daily.std(ddof=1) * math.sqrt(252)) if daily.std(ddof=1) > 0 else float("nan")
        return {"start": str(srs.index[0].date()), "end": str(srs.index[-1].date()),
                "total_return": total, "CAGR": cagr, "MDD": mdd,
                "annualized_vol": vol, "sharpe_rf0": sharpe0,
                "ending_1usd": float(srs.iloc[-1] / srs.iloc[0])}

    # Semiannual period returns.
    period_rows = []
    bounds = actual_dates + [end_date]
    for i in range(len(actual_dates)):
        a, z = bounds[i], bounds[i+1]
        ss = nav.loc[a:z]
        bb = b.loc[a:z]
        period_rows.append({
            "start": str(a.date()), "end": str(z.date()),
            "strategy_return": float(ss.iloc[-1]/ss.iloc[0]-1),
            "qqq_return": float(bb.iloc[-1]/bb.iloc[0]-1),
        })
    pd.DataFrame(period_rows).to_csv(OUTDIR / "period_returns.csv", index=False)

    result = {
        "method": "third-Friday Mar/Sep; NPORT post-rebalance reconstruction via ETF/stock total-return backcast",
        "strategy": metrics(nav),
        "QQQ": metrics(b),
        "rebalance_dates": [str(x.date()) for x in actual_dates],
        "end_date": str(end_date.date()),
        "n_unique_intersection_cusips": len(cusip_hints),
        "unresolved_cusips": [{"cusip": c, "name": cusip_hints[c]} for c in unresolved],
        "mapping_omissions": map_fail_weight,
        "snapshots": [{
            "rebalance": str(d.date()),
            "qqq_report": str(q.report_date.date()), "qqq_accession": q.accession,
            "spmo_report": str(s.report_date.date()), "spmo_accession": s.accession,
            "intersection_cusips": len(inter),
        } for (sched,q,s,inter),d in zip(pairs, actual_dates)],
        "period_returns": period_rows,
    }
    (OUTDIR / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    print("\n=== BACKTEST_RESULT_JSON ===")
    print(json.dumps(result, indent=2))
    print("=== END_BACKTEST_RESULT_JSON ===")


if __name__ == "__main__":
    main()
