(()=>{
"use strict";
const SIG="snpi-sp500-v1";
const HISTORY_URL="./snpi-history.json";
const key=kind=>`somx.${kind}.v2.${SIG}`;
let syncing=false;
function read(kind){try{return JSON.parse(localStorage.getItem(key(kind))||"{}")||{}}catch{return{}}}
function write(kind,value){try{localStorage.setItem(key(kind),JSON.stringify(value))}catch{}}
function validRows(rows){return Array.isArray(rows)&&rows.length>=495&&rows.every(r=>r&&r.ticker&&Number.isFinite(Number(r.weight))&&Number(r.weight)>0)}
async function syncSnpiHistory({reload=true}={}){
  if(syncing)return;
  if(globalThis.SOMXStrategy?.getMode?.()!=="snpi")return;
  syncing=true;
  try{
    const r=await fetch(`${HISTORY_URL}?v=${Date.now()}`,{cache:"no-store"});
    if(!r.ok)throw new Error(`SNPI history HTTP ${r.status}`);
    const j=await r.json(),months=j?.months||{};
    const hh=read("holdings"),wh=read("weights");let changed=false;
    for(const [month,record] of Object.entries(months)){
      const rows=record?.rows;if(!validRows(rows))continue;
      const sum=rows.reduce((a,x)=>a+Number(x.weight),0);if(Math.abs(sum-1)>1e-6)continue;
      hh[month]=rows.map(x=>String(x.ticker).trim().toUpperCase());
      wh[month]=Object.fromEntries(rows.map(x=>[String(x.ticker).trim().toUpperCase(),Number(x.weight)]));
      changed=true;
    }
    if(changed){write("holdings",hh);write("weights",wh)}
    window.dispatchEvent(new CustomEvent("somx:snpi-history-synced",{detail:{months:Object.keys(months).sort()}}));
    if(reload&&globalThis.SOMXLive?.applyStrategy&&globalThis.SOMXStrategy?.getMode?.()==="snpi")await globalThis.SOMXLive.applyStrategy(globalThis.SOMXStrategy.getConfig());
  }catch(e){console.warn("SNPI history sync",e)}finally{syncing=false}
}
window.addEventListener("load",()=>syncSnpiHistory({reload:true}),{once:true});
window.addEventListener("somx:strategy-active",()=>syncSnpiHistory({reload:false}));
globalThis.SNPIHistory={sync:syncSnpiHistory};
})();
