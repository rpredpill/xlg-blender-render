(()=>{
"use strict";

const SIG="ququ-ndx100-v1";
const HISTORY_URL="./ququ-history.json";
const key=kind=>`somx.${kind}.v2.${SIG}`;
let syncing=false;

function read(kind){try{return JSON.parse(localStorage.getItem(key(kind))||"{}")||{}}catch{return{}}}
function write(kind,value){try{localStorage.setItem(key(kind),JSON.stringify(value))}catch{}}
function validRows(rows,record){const u=Number(record?.universeCount)||rows?.length||0,cov=u>0?(rows?.length||0)/u:0;return Array.isArray(rows)&&cov>=0.90&&rows.length<=100&&rows.every(r=>r&&r.ticker&&Number.isFinite(Number(r.weight))&&Number(r.weight)>0)}

async function syncQuquHistory({reload=true}={}){
  if(syncing)return;
  if(globalThis.SOMXStrategy?.getMode?.()!=="ququ")return;
  syncing=true;
  try{
    const r=await fetch(`${HISTORY_URL}?v=${Date.now()}`,{cache:"no-store"});
    if(!r.ok)throw new Error(`QUQU history HTTP ${r.status}`);
    const j=await r.json(),months=j?.months||{};
    const hh=read("holdings"),wh=read("weights"),mh=read("history");
    let changed=false,returnsChanged=false;
    for(const [month,record] of Object.entries(months)){
      const rows=record?.rows;
      if(!validRows(rows,record))continue;
      const sum=rows.reduce((a,x)=>a+Number(x.weight),0);
      if(Math.abs(sum-1)>1e-6)continue;
      const holdings=rows.map(x=>String(x.ticker).trim().toUpperCase());
      const weights=Object.fromEntries(rows.map(x=>[String(x.ticker).trim().toUpperCase(),Number(x.weight)]));
      hh[month]=holdings;wh[month]=weights;changed=true;
      if(Number.isFinite(Number(record.portfolioReturn))){
        mh[month]={
          month,
          port:Number(record.portfolioReturn),
          lastDate:record.lastDate||null,
          holdings,
          weights,
          rows:rows.map(x=>({ticker:String(x.ticker).trim().toUpperCase(),ret:Number.isFinite(Number(x.monthlyReturn))?Number(x.monthlyReturn)*100:0,weight:Number(x.weight)})),
          serverBackfill:true,
          coverageRatio:Number(record.coverageRatio||((record.universeCount?rows.length/Number(record.universeCount):1))),
          universeCount:Number(record.universeCount||rows.length),
          missingReturnWeight:Number(record.missingReturnWeight||0),
          historicalCapCount:Number(record.historicalCapCount||0),
          currentSharesProxyCount:Number(record.currentSharesProxyCount||0),
          medianCapFallbackCount:Number(record.medianCapFallbackCount||0)
        };
        returnsChanged=true;
      }
    }
    if(changed){write("holdings",hh);write("weights",wh)}
    if(returnsChanged)write("history",mh);
    window.dispatchEvent(new CustomEvent("somx:ququ-history-synced",{detail:{months:Object.keys(months).sort()}}));
    if(returnsChanged)window.dispatchEvent(new Event("somx:historychange"));
    if(reload&&globalThis.SOMXLive?.applyStrategy&&globalThis.SOMXStrategy?.getMode?.()==="ququ")await globalThis.SOMXLive.applyStrategy(globalThis.SOMXStrategy.getConfig());
  }catch(e){console.warn("QUQU history sync",e)}finally{syncing=false}
}

window.addEventListener("load",()=>syncQuquHistory({reload:true}),{once:true});
window.addEventListener("somx:strategy-active",()=>syncQuquHistory({reload:false}));
globalThis.QUQUHistory={sync:syncQuquHistory};
})();
