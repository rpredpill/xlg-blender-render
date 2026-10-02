(()=>{
"use strict";
const SIG="snpy-sp500-top100-v1";
const HISTORY_URL="./snpi-history.json";
const CAP=0.20;
const key=kind=>`somx.${kind}.v2.${SIG}`;
let syncing=false;
function read(kind){try{return JSON.parse(localStorage.getItem(key(kind))||"{}")||{}}catch{return{}}}
function write(kind,value){try{localStorage.setItem(key(kind),JSON.stringify(value))}catch{}}
function capAndRedistribute(raw){
  const free=new Set(Object.keys(raw)),out={};let remaining=1;
  while(free.size){
    let total=0;for(const s of free)total+=raw[s];
    if(!(total>0))throw new Error("SNPY raw weight sum <= 0");
    const over=[...free].filter(s=>remaining*raw[s]/total>CAP+1e-12);
    if(!over.length){for(const s of free)out[s]=remaining*raw[s]/total;break}
    for(const s of over){out[s]=CAP;remaining-=CAP;free.delete(s)}
  }
  return out;
}
function sourceCoverage(record){const rows=record?.rows||[],u=Number(record?.universeCount)||rows.length;return u>0?rows.length/u:0}
function derive(record){
  const source=Array.isArray(record?.rows)?record.rows:[];
  if(sourceCoverage(record)<0.90||source.length<100)return null;
  const ranked=source.map(x=>{
    const cap=Number(x.marketCap),gross=1+Number(x.momentum),ticker=String(x.ticker||"").trim().toUpperCase();
    const raw=cap>0&&gross>0?Math.sqrt(cap)*(gross**3):NaN;
    return{...x,ticker,raw};
  }).filter(x=>x.ticker&&Number.isFinite(x.raw)&&x.raw>0).sort((a,b)=>b.raw-a.raw).slice(0,100);
  if(ranked.length!==100)return null;
  const raw=Object.fromEntries(ranked.map(x=>[x.ticker,x.raw])),w=capAndRedistribute(raw);
  const rows=ranked.map((x,i)=>({...x,weight:w[x.ticker],weightRank:i+1})).sort((a,b)=>b.weight-a.weight);
  const missingReturnWeight=rows.reduce((a,x)=>a+(Number.isFinite(Number(x.monthlyReturn))?0:Number(x.weight)),0);
  if(missingReturnWeight>0.02)return null;
  const portfolioReturn=100*rows.reduce((a,x)=>a+Number(x.weight)*(Number.isFinite(Number(x.monthlyReturn))?Number(x.monthlyReturn):0),0);
  return{rows,portfolioReturn,missingReturnWeight};
}
async function syncSnpyHistory({reload=true}={}){
  if(syncing)return;
  if(globalThis.SOMXStrategy?.getMode?.()!=="snpy")return;
  syncing=true;
  try{
    const r=await fetch(`${HISTORY_URL}?v=${Date.now()}`,{cache:"no-store"});
    if(!r.ok)throw new Error(`SNPY source history HTTP ${r.status}`);
    const j=await r.json(),months=j?.months||{};
    const hh=read("holdings"),wh=read("weights"),mh=read("history");let changed=false,returnsChanged=false;
    for(const [month,record] of Object.entries(months)){
      const d=derive(record);if(!d)continue;
      const holdings=d.rows.map(x=>x.ticker),weights=Object.fromEntries(d.rows.map(x=>[x.ticker,Number(x.weight)]));
      const sum=Object.values(weights).reduce((a,x)=>a+x,0);if(Math.abs(sum-1)>1e-6)continue;
      hh[month]=holdings;wh[month]=weights;changed=true;
      mh[month]={
        month,port:d.portfolioReturn,lastDate:record.lastDate||null,holdings,weights,
        rows:d.rows.map(x=>({ticker:x.ticker,ret:Number.isFinite(Number(x.monthlyReturn))?Number(x.monthlyReturn)*100:0,weight:Number(x.weight)})),
        serverBackfill:true,derivedFrom:"SNPI",selectionRule:"Top100 by SNPI raw score",
        sourceCoverageRatio:sourceCoverage(record),universeCount:100,missingReturnWeight:d.missingReturnWeight,
        historicalCapCount:Number(record.historicalCapCount||0),currentSharesProxyCount:Number(record.currentSharesProxyCount||0),medianCapFallbackCount:Number(record.medianCapFallbackCount||0)
      };
      returnsChanged=true;
    }
    if(changed){write("holdings",hh);write("weights",wh)}
    if(returnsChanged)write("history",mh);
    window.dispatchEvent(new CustomEvent("somx:snpy-history-synced",{detail:{months:Object.keys(months).sort()}}));
    if(returnsChanged)window.dispatchEvent(new Event("somx:historychange"));
    if(reload&&globalThis.SOMXLive?.applyStrategy&&globalThis.SOMXStrategy?.getMode?.()==="snpy")await globalThis.SOMXLive.applyStrategy(globalThis.SOMXStrategy.getConfig());
  }catch(e){console.warn("SNPY history sync",e)}finally{syncing=false}
}
window.addEventListener("load",()=>syncSnpyHistory({reload:true}),{once:true});
window.addEventListener("somx:strategy-active",()=>syncSnpyHistory({reload:false}));
globalThis.SNPYHistory={sync:syncSnpyHistory};
})();
