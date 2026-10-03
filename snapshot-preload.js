(()=>{
"use strict";
try{
  const ACTIVE="somx.strategy.active.v1",CAP=0.20;
  let mode=localStorage.getItem(ACTIVE);
  if(mode==="core"||mode==="custom"){mode="snpi";localStorage.setItem(ACTIVE,"snpi")}
  if(!["snpi","snpy","ququ","quu"].includes(mode))return;
  const file=mode==="quu"?"quu-latest.json":mode==="ququ"?"ququ-latest.json":"snpi-latest.json";
  const sig=mode==="snpi"?"snpi-sp500-v1":mode==="snpy"?"snpy-sp500-top100-v1":mode==="quu"?"quu-ndx100-rollover-v1":"ququ-nasdaq-composite-v2";
  const min=mode==="snpi"?495:mode==="quu"?1:100;
  const xhr=new XMLHttpRequest();
  xhr.open("GET",`./${file}?v=${Date.now()}`,false);
  xhr.setRequestHeader("Cache-Control","no-cache");
  xhr.send(null);
  if(xhr.status<200||xhr.status>=300)return;
  const j=JSON.parse(xhr.responseText||"{}");
  if(mode==="ququ"&&j.strategy!=="QUQU v2")return;
  if(!Array.isArray(j.rows)||(mode!=="snpy"&&j.rows.length<min))return;

  const capAndRedistribute=raw=>{
    const free=new Set(Object.keys(raw)),out={};let remaining=1;
    while(free.size){let total=0;for(const s of free)total+=raw[s];if(!(total>0))return null;
      const over=[...free].filter(s=>remaining*raw[s]/total>CAP+1e-12);
      if(!over.length){for(const s of free)out[s]=remaining*raw[s]/total;break}
      for(const s of over){out[s]=CAP;remaining-=CAP;free.delete(s)}
    }
    return out;
  };
  const deriveSnpy=rows=>{
    const ranked=rows.map(r=>{
      const ticker=String(r.ticker||"").trim().toUpperCase(),cap=Number(r.marketCap),gross=1+Number(r.momentum),stored=Number(r.rawWeightScore);
      const raw=Number.isFinite(stored)&&stored>0?stored:(cap>0&&gross>0?Math.sqrt(cap)*(gross**3):NaN);
      return{ticker,raw};
    }).filter(r=>r.ticker&&Number.isFinite(r.raw)&&r.raw>0).sort((a,b)=>b.raw-a.raw).slice(0,100);
    if(ranked.length!==100)return null;
    const raw=Object.fromEntries(ranked.map(r=>[r.ticker,r.raw])),w=capAndRedistribute(raw);if(!w)return null;
    return ranked.map(r=>({ticker:r.ticker,weight:w[r.ticker]})).sort((a,b)=>b.weight-a.weight);
  };

  let rows;
  if(mode==="snpy")rows=deriveSnpy(j.rows);
  else rows=j.rows.map(r=>({ticker:String(r.ticker||"").trim().toUpperCase(),weight:Number(r.weight)}));
  if(!Array.isArray(rows)||rows.length<min||rows.some(r=>!r.ticker||!Number.isFinite(r.weight)||r.weight<=0))return;
  const sum=rows.reduce((a,r)=>a+r.weight,0);if(Math.abs(sum-1)>1e-6)return;

  const strategy=globalThis.SOMXStrategy;
  if(strategy?.getConfig){
    const originalGetConfig=strategy.getConfig.bind(strategy);
    strategy.getConfig=()=>{
      const c=originalGetConfig();
      if(c?.mode===mode){c.holdings=rows.length;c.entryRank=rows.length;c.exitRank=rows.length}
      return c;
    };
  }
  document.addEventListener("DOMContentLoaded",()=>{
    if(globalThis.SOMXStrategy?.getMode?.()!==mode)return;
    for(const id of ["st-holdings","st-entry","st-exit"]){const el=document.getElementById(id);if(el)el.value=String(rows.length)}
  },{once:true});

  const p=new Intl.DateTimeFormat("en-CA",{timeZone:"America/New_York",year:"numeric",month:"2-digit"}).formatToParts(new Date());
  const ym=`${p.find(x=>x.type==="year").value}-${p.find(x=>x.type==="month").value}`;
  const allocationYm=mode==="ququ"&&/^\d{4}-\d{2}$/.test(String(j.allocationMonth||""))?String(j.allocationMonth):ym;
  const holdings=rows.map(r=>r.ticker),targetWeights=Object.fromEntries(rows.map(r=>[r.ticker,r.weight]));
  const key=kind=>`somx.${kind}.v2.${sig}`;
  let hh={},wh={};try{hh=JSON.parse(localStorage.getItem(key("holdings"))||"{}")||{}}catch{}try{wh=JSON.parse(localStorage.getItem(key("weights"))||"{}")||{}}catch{}

  // Keep the production allocation month for provenance/history, but the live
  // dashboard state must always be anchored to the current month. Otherwise
  // app.js tries to catch QUQU up with the generic S&P 500 momentum engine.
  hh[allocationYm]=[...holdings];wh[allocationYm]={...targetWeights};
  hh[ym]=[...holdings];wh[ym]={...targetWeights};
  const st={
    rebalanceMonth:ym,strategyAnchor:ym,initialized:true,holdings,
    statuses:Object.fromEntries(holdings.map(s=>[s,"IN"])),targetWeights,
    basePrices:{},updatedAt:new Date().toISOString(),snapshotAt:j.generatedAt||null,
    snapshotAllocationMonth:allocationYm
  };
  localStorage.setItem(key("state"),JSON.stringify(st));
  localStorage.setItem(key("holdings"),JSON.stringify(hh));
  localStorage.setItem(key("weights"),JSON.stringify(wh));
}catch(e){console.warn("snapshot preload",e)}
})();