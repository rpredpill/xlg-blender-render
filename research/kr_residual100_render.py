import os, json, gc
from pathlib import Path
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from http.server import BaseHTTPRequestHandler, HTTPServer

START = pd.Timestamp("2021-01-01")
END = pd.Timestamp("2026-09-25")
YEARS = range(2020, 2027)
LOOKBACK, SKIP, L6, L3, NPC = 252, 21, 126, 63, 5
PARENT_N, HOLD_N, ENTRY_RANK, KEEP_RANK = 500, 100, 80, 120
ABS_CAP, MCAP_MULT = 0.09, 3.0
SLIP_BPS, SELL_TAX_BPS = 2.0, 20.0

def year_path(y): return Path("data") / f"marcap-{y}.parquet"
def schema_names(y): return pq.ParquetFile(year_path(y)).schema.names

def ordinary_mask(d):
    market = d["Market"].astype(str).isin(["KOSPI","KOSDAQ","KOSDAQ GLOBAL"])
    name = d["Name"].fillna("").astype(str)
    dept = d["Dept"].fillna("").astype(str) if "Dept" in d.columns else pd.Series("", index=d.index)
    bad = name.str.contains("스팩", regex=False)
    bad |= name.str.contains(r"(우$|우B$|우C$|우\(전환\)|우선주$)", regex=True)
    bad |= dept.str.contains("관리종목|정리매매", regex=True)
    return market & ~bad

def load_year_basic(y, need_cols):
    cols0 = schema_names(y)
    d = pd.read_parquet(year_path(y), columns=[c for c in need_cols if c in cols0], engine="pyarrow")
    d["Date"] = pd.to_datetime(d["Date"])
    d["Code"] = d["Code"].astype(str).str.zfill(6)
    return d

def build_rebalance_info():
    info, union, names, markets = {}, set(), {}, {}
    for y in YEARS:
        d = load_year_basic(y, ["Date","Code","Name","Market","Dept","Marcap"])
        d = d.loc[ordinary_mask(d)].copy()
        d["Marcap"] = pd.to_numeric(d["Marcap"], errors="coerce")
        d = d.dropna(subset=["Marcap"])
        dates = pd.DatetimeIndex(sorted(d["Date"].unique()))
        for q in [1,2,3,4]:
            qd = dates[dates.quarter == q]
            if len(qd) == 0: continue
            rd = qd[0]
            if rd < START or rd > END: continue
            day = d[d["Date"] == rd].sort_values("Marcap", ascending=False).head(PARENT_N)
            codes = day["Code"].tolist()
            info[pd.Timestamp(rd)] = {"codes":codes, "mcap":dict(zip(day["Code"], day["Marcap"].astype(float)))}
            union.update(codes)
            names.update(dict(zip(day["Code"], day["Name"].astype(str))))
            markets.update(dict(zip(day["Code"], day["Market"].astype(str))))
        del d; gc.collect()
    return dict(sorted(info.items())), union, names, markets

def build_returns(union):
    frames, names, markets = [], {}, {}
    for y in YEARS:
        cols0 = schema_names(y)
        rr = "ChagesRatio" if "ChagesRatio" in cols0 else ("ChangesRatio" if "ChangesRatio" in cols0 else ("ChangeRatio" if "ChangeRatio" in cols0 else None))
        if rr is None: raise RuntimeError(f"No change-ratio column in {y}: {cols0}")
        d = load_year_basic(y, ["Date","Code","Name","Market",rr])
        d = d[d["Code"].isin(union)].copy()
        d["Ret"] = pd.to_numeric(d[rr], errors="coerce") / 100.0
        d = d[(d["Date"] >= pd.Timestamp("2020-01-01")) & (d["Date"] <= END)]
        names.update(dict(zip(d["Code"], d["Name"].astype(str))))
        markets.update(dict(zip(d["Code"], d["Market"].astype(str))))
        frames.append(d.pivot(index="Date", columns="Code", values="Ret").astype("float32"))
        del d; gc.collect()
    ret = pd.concat(frames).sort_index()
    ret = ret[~ret.index.duplicated(keep="last")]
    return ret, names, markets

def load_kospi():
    arr = []
    for y in YEARS:
        u = f"https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/master/data/index/year_ks11/{y}.csv"
        d = pd.read_csv(u); d["Date"] = pd.to_datetime(d["Date"]); arr.append(d[["Date","Close"]])
    return pd.concat(arr).drop_duplicates("Date").sort_values("Date").set_index("Date")["Close"].astype(float)

def zscore(s):
    s=s.replace([np.inf,-np.inf],np.nan); sd=s.std(ddof=1)
    return (s-s.mean())/sd if np.isfinite(sd) and sd>0 else s*np.nan

def mom(mat, length):
    w=mat.iloc[-length:-SKIP]
    return w.sum(axis=0,skipna=False)/w.std(axis=0,ddof=1)

def residual_matrix(X, market_ret):
    X=X.dropna(axis=1,how="any")
    m=market_ret.reindex(X.index); good=m.notna(); X=X.loc[good]; m=m.loc[good]
    if X.shape[0]<200 or X.shape[1]<HOLD_N: return pd.DataFrame(index=X.index)
    A=X.to_numpy(dtype=np.float64); mv=m.to_numpy(dtype=np.float64)
    Ac=A-A.mean(axis=0); mc=mv-mv.mean(); den=float(mc@mc)
    beta=(mc[:,None]*Ac).sum(axis=0)/max(den,1e-15); R=Ac-mc[:,None]*beta
    sd=R.std(axis=0,ddof=1); sd[sd==0]=np.nan
    Z=np.nan_to_num(R/sd,nan=0.0); k=min(NPC,Z.shape[0]-2,Z.shape[1]-1)
    if k<=0: return pd.DataFrame(R,index=X.index,columns=X.columns)
    U,S,Vt=np.linalg.svd(Z,full_matrices=False); F=U[:,:k]*S[:k]; F1=np.column_stack([np.ones(F.shape[0]),F])
    B=np.linalg.lstsq(F1,R,rcond=None)[0]; E=R-F1@B
    return pd.DataFrame(E,index=X.index,columns=X.columns)

def score_at(date, ret, kospi, info, residual=False):
    if date not in ret.index: return pd.Series(dtype=float),pd.Series(dtype=float)
    loc=ret.index.get_loc(date)
    if not isinstance(loc,(int,np.integer)) or loc<LOOKBACK: return pd.Series(dtype=float),pd.Series(dtype=float)
    parent=info[date]["codes"]; hist=ret.iloc[loc-LOOKBACK+1:loc+1].reindex(columns=parent).dropna(axis=1,how="any")
    if hist.shape[1]<HOLD_N: return pd.Series(dtype=float),pd.Series(dtype=float)
    M=residual_matrix(hist,kospi.pct_change().reindex(hist.index)) if residual else hist
    if M.shape[1]<HOLD_N: return pd.Series(dtype=float),pd.Series(dtype=float)
    sc=(0.70*zscore(mom(M,L6))+0.30*zscore(mom(M,L3))).dropna().sort_values(ascending=False)
    pm=pd.Series(info[date]["mcap"],dtype=float).reindex(parent).dropna()
    return sc,pm

def choose_with_buffer(score,incumbents):
    ranks=score.rank(ascending=False,method="first")
    keep=[c for c in incumbents if c in ranks.index and ranks[c]<=KEEP_RANK]
    pref=[c for c in score.index if c not in keep and ranks[c]<=ENTRY_RANK]
    sel=list(dict.fromkeys(keep+pref))[:HOLD_N]
    if len(sel)<HOLD_N:
        for c in score.index:
            if c not in sel: sel.append(c)
            if len(sel)==HOLD_N: break
    return sel[:HOLD_N]

def cap_weights(raw,parent_mcap):
    raw=raw.clip(lower=0).dropna()
    if raw.sum()<=0: raw[:]=1.0
    w=raw/raw.sum(); base=parent_mcap.sum()
    caps=pd.Series(np.minimum(ABS_CAP,MCAP_MULT*(parent_mcap.reindex(w.index).fillna(0)/base)),index=w.index)
    if caps.sum()<1:
        for _ in range(80):
            caps=(caps*1.10).clip(upper=ABS_CAP)
            if caps.sum()>=1: break
    for _ in range(100):
        over=w>caps+1e-12
        if not over.any(): break
        excess=(w[over]-caps[over]).sum(); w[over]=caps[over]
        room=(caps[~over]-w[~over]).clip(lower=0)
        if room.sum()<=1e-15: break
        w[~over]+=excess*room/room.sum()
    return w/w.sum()

def target_weights(score,pm,incumbents):
    sel=choose_with_buffer(score,incumbents); s=score.reindex(sel); mc=pm.reindex(sel)
    shifted=s-s.min()+1e-6; raw=np.sqrt(mc.clip(lower=1.0))*shifted
    return cap_weights(raw,pm)

def run(ret,kospi,info,residual):
    dates=ret.index[(ret.index>=START)&(ret.index<=END)]; rb=set(info.keys())
    w=pd.Series(dtype=float); gross=net=1.0; rows=[]; turns=[]; last_scores=pd.Series(dtype=float)
    for d in dates:
        if len(w):
            rr=ret.loc[d].reindex(w.index).fillna(0.0).astype(float); pr=float((w*rr).sum())
            gross*=1+pr; net*=1+pr
            if 1+pr>0: w=w*(1+rr)/(1+pr)
        if d in rb:
            sc,pm=score_at(d,ret,kospi,info,residual)
            if len(sc)>=HOLD_N:
                tw=target_weights(sc,pm,list(w.index)); idx=w.index.union(tw.index)
                old=w.reindex(idx).fillna(0); new=tw.reindex(idx).fillna(0)
                buys=float((new-old).clip(lower=0).sum()); sells=float((old-new).clip(lower=0).sum())
                net*=1-buys*SLIP_BPS/1e4-sells*(SLIP_BPS+SELL_TAX_BPS)/1e4
                turns.append({"date":str(d.date()),"turnover":(buys+sells)/2,"buys":buys,"sells":sells})
                w=tw.copy(); last_scores=sc.copy()
        rows.append((d,gross,net))
    return pd.DataFrame(rows,columns=["Date","Gross","Net"]).set_index("Date"),turns,w,last_scores

def metrics(s):
    s=s.dropna(); r=s.pct_change().dropna(); yrs=(s.index[-1]-s.index[0]).days/365.25
    cagr=(s.iloc[-1]/s.iloc[0])**(1/max(yrs,1e-9))-1; dd=s/s.cummax()-1
    vol=float(r.std(ddof=1)*np.sqrt(252)); sh=float(r.mean()/r.std(ddof=1)*np.sqrt(252)) if r.std(ddof=1)>0 else None
    trough=dd.idxmin(); peak=s.loc[:trough].idxmax()
    return {"CAGR":float(cagr),"MDD":float(dd.min()),"Vol":vol,"Sharpe":sh,"Multiple":float(s.iloc[-1]/s.iloc[0]),"MDD_peak":str(peak.date()),"MDD_trough":str(trough.date())}

def yearly(s):
    out={}
    for y,g in s.groupby(s.index.year):
        if len(g)>1: out[str(y)]=float(g.iloc[-1]/g.iloc[0]-1)
    return out

def year_stats(s,y):
    x=s[s.index.year==y]
    if len(x)<2:return None
    return {"return":float(x.iloc[-1]/x.iloc[0]-1),"peak_to_end":float(x.iloc[-1]/x.cummax().max()-1),"peak_date":str(x.idxmax().date())}

print("PASS1 top500",flush=True)
info,union,nm1,mk1=build_rebalance_info(); print("rebalances",len(info),"union",len(union),flush=True)
print("PASS2 returns",flush=True)
ret,nm2,mk2=build_returns(union); names={**nm1,**nm2}; markets={**mk1,**mk2}; print("ret",ret.shape,flush=True)
kospi=load_kospi()
print("RAW",flush=True); raw,rt,rw,rs=run(ret,kospi,info,False)
print("RESIDUAL",flush=True); res,st,sw,ss=run(ret,kospi,info,True)
bench=kospi.loc[START:END].dropna(); bench=bench/bench.iloc[0]
monthly=pd.concat([raw["Net"].rename("Raw100"),res["Net"].rename("Residual100"),bench.rename("KOSPI")],axis=1).resample("ME").last().dropna(how="all")
top20=[{"code":c,"name":names.get(c,c),"market":markets.get(c,""),"weight":float(w),"score":float(ss.get(c,np.nan))} for c,w in sw.sort_values(ascending=False).head(20).items()]
out={
"period":[str(START.date()),str(END.date())],
"data":{"rebalances":len(info),"parent_union_unique":len(union),"return_matrix":[int(ret.shape[0]),int(ret.shape[1])]},
"metrics":{"Raw100_gross":metrics(raw["Gross"]),"Raw100_net_sensitivity":metrics(raw["Net"]),"Residual100_gross":metrics(res["Gross"]),"Residual100_net_sensitivity":metrics(res["Net"]),"KOSPI":metrics(bench)},
"yearly":{"Raw100_net":yearly(raw["Net"]),"Residual100_net":yearly(res["Net"]),"KOSPI":yearly(bench)},
"2025":{"Raw100":year_stats(raw["Net"],2025),"Residual100":year_stats(res["Net"],2025),"KOSPI":year_stats(bench,2025)},
"2026":{"Raw100":year_stats(raw["Net"],2026),"Residual100":year_stats(res["Net"],2026),"KOSPI":year_stats(bench,2026)},
"turnover":{"Raw100_avg_quarter":float(np.mean([x["turnover"] for x in rt])) if rt else None,"Residual100_avg_quarter":float(np.mean([x["turnover"] for x in st])) if st else None,"Raw100_rebalances":len(rt),"Residual100_rebalances":len(st)},
"current_residual_top20":top20,
"monthly_nav":[{"date":str(i.date()),**{k:(None if pd.isna(v) else float(v)) for k,v in row.items()}} for i,row in monthly.iterrows()],
"assumptions":{"holdings":100,"parent":"point-in-time KOSPI+KOSDAQ ordinary stocks top500 by market cap","rebalance":"quarterly first trading day","score":"0.70*z(RM6-1)+0.30*z(RM3-1)","residual":"remove KOSPI beta + first 5 PCA components, trailing 252 sessions","weight":"sqrt(mcap)*shifted score","cap":"min(9%,3x parent market-cap weight)","buffer":"keep<=120, new<=80 preferred","net_sensitivity":"2bp one-way slippage + 20bp sell-tax sensitivity; not historical tax reconstruction","stock_returns":"KRX daily ChagesRatio/ChangesRatio price returns","dividends":"not included"}}
RESULT=json.dumps(out,ensure_ascii=False,separators=(",",":")); print("RESULT_JSON="+RESULT,flush=True)

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        b=RESULT.encode("utf-8"); self.send_response(200); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self,*a): pass
port=int(os.environ.get("PORT","10000")); print("serving",port,flush=True); HTTPServer(("0.0.0.0",port),H).serve_forever()
