(()=>{
"use strict";
const SIG="nani-ndx100-nk225-v2";
const HISTORY_URL="./nani-history.json";
const key=kind=>`somx.${kind}.v2.${SIG}`;
let syncing=false;
function read(kind){try{return JSON.parse(localStorage.getItem(key(kind))||"{}")||{}}catch{return{}}}
function write(kind,value){try{localStorage.setItem(key(kind),JSON.stringify(value))}catch{}}
function validRows(rows){return Array.isArray(rows)&&rows.length>0&&rows.every(r=>r&&r.ticker&&Number.isFinite(Number(r.weight))&&Number(r.weight)>0)}
async function syncNaniHistory(){
  if(syncing)return;
  if(globalThis.SOMXStrategy?.getMode?.()!=="nani")return;
  syncing=true;
  try{
    const r=await fetch(`${HISTORY_URL}?v=${Date.now()}`,{cache:"no-store"});
    if(!r.ok)throw new Error(`Nani history HTTP ${r.status}`);
    const j=await r.json(),months=j?.months||{};
    const hh=read("holdings"),wh=read("weights"),mh={};let changed=false,returnsChanged=false;
    for(const [month,record] of Object.entries(months)){
      if(record?.provenance!=="forward-live-v2")continue;
      const rows=record?.rows;if(!validRows(rows))continue;
      const sum=rows.reduce((a,x)=>a+Number(x.weight),0);if(Math.abs(sum-1)>1e-6)continue;
      const holdings=rows.map(x=>String(x.ticker).trim().toUpperCase());
      const weights=Object.fromEntries(rows.map(x=>[String(x.ticker).trim().toUpperCase(),Number(x.weight)]));
      hh[month]=holdings;wh[month]=weights;changed=true;
      if(Number.isFinite(Number(record.portfolioReturn))){
        mh[month]={
          month,port:Number(record.portfolioReturn)*100,lastDate:record.lastDate||null,holdings,weights,
          rows:rows.map(x=>({ticker:String(x.ticker).trim().toUpperCase(),ret:Number.isFinite(Number(x.monthlyReturn))?Number(x.monthlyReturn)*100:0,weight:Number(x.weight),country:x.country||null})),
          forwardLive:true,universeCount:Number(record.universeCount||rows.length),coverageRatio:Number(record.coverageRatio||1),
          signalMonth:record.signalMonth||null,allocationMonth:record.allocationMonth||null
        };
        returnsChanged=true;
      }
    }
    if(changed){write("holdings",hh);write("weights",wh)}
    write("history",mh);
    window.dispatchEvent(new CustomEvent("somx:nani-history-synced",{detail:{months:Object.keys(months).sort()}}));
    if(returnsChanged)window.dispatchEvent(new Event("somx:historychange"));
  }catch(e){console.warn("Nani history sync",e)}finally{syncing=false}
}
window.addEventListener("load",syncNaniHistory,{once:true});
window.addEventListener("somx:strategy-active",syncNaniHistory);
globalThis.NaniHistory={sync:syncNaniHistory};
})();