(()=>{
"use strict";

const SIG="quu-ndx100-rollover-v1";
const HISTORY_URL="./quu-history.json";
const key=kind=>`somx.${kind}.v2.${SIG}`;
let syncing=false,repairing=false,serverMonths={};

function read(kind){try{return JSON.parse(localStorage.getItem(key(kind))||"{}")||{}}catch{return{}}}
function write(kind,value){try{localStorage.setItem(key(kind),JSON.stringify(value))}catch{}}
function validRows(rows){return Array.isArray(rows)&&rows.length>=1&&rows.length<=100&&rows.every(r=>r&&r.ticker&&Number.isFinite(Number(r.weight))&&Number(r.weight)>0)}
function completed(record){return Number.isFinite(Number(record?.portfolioReturn))}
function normalizedRows(rows){return rows.map(x=>({ticker:String(x.ticker).trim().toUpperCase(),ret:Number.isFinite(Number(x.monthlyReturn))?Number(x.monthlyReturn)*100:0,weight:Number(x.weight)}))}
function completedRecord(month,record,holdings,weights){return{
  month,
  port:Number(record.portfolioReturn),
  lastDate:record.lastDate||null,
  holdings,
  weights,
  selectedSleeve:record.selectedSleeve||null,
  decision:record.decision||null,
  rows:normalizedRows(record.rows||[]),
  serverBackfill:true,
  coverageRatio:Number(record.coverageRatio||1),
  universeCount:Number(record.universeCount||holdings.length),
  missingReturnWeight:Number(record.missingReturnWeight||0)
}}

function overlayServerMonths(months,{includeTargets=true}={}){
  const hh=read("holdings"),wh=read("weights"),mh=read("history");
  let targetsChanged=false,returnsChanged=false;
  for(const [month,record] of Object.entries(months||{})){
    const rows=record?.rows;
    if(!validRows(rows))continue;
    const sum=rows.reduce((a,x)=>a+Number(x.weight),0);
    if(Math.abs(sum-1)>1e-6)continue;
    const holdings=rows.map(x=>String(x.ticker).trim().toUpperCase());
    const weights=Object.fromEntries(rows.map(x=>[String(x.ticker).trim().toUpperCase(),Number(x.weight)]));
    if(includeTargets){hh[month]=holdings;wh[month]=weights;targetsChanged=true}
    if(completed(record)){
      mh[month]=completedRecord(month,record,holdings,weights);
      returnsChanged=true;
    }
  }
  if(targetsChanged){write("holdings",hh);write("weights",wh)}
  if(returnsChanged)write("history",mh);
  return{targetsChanged,returnsChanged};
}

async function repairCompletedServerReturns(){
  if(syncing||repairing)return;
  if(globalThis.SOMXStrategy?.getMode?.()!=="quu")return;
  if(!Object.keys(serverMonths).length)return;
  const live=globalThis.SOMXLive?.getMonthlyHistory?.()||{};
  let needsRepair=false;
  for(const [month,record] of Object.entries(serverMonths)){
    if(!completed(record))continue;
    const expected=Number(record.portfolioReturn),actual=Number(live?.[month]?.port);
    if(!Number.isFinite(actual)||Math.abs(actual-expected)>1e-8){needsRepair=true;break}
  }
  if(!needsRepair)return;
  repairing=true;
  try{
    overlayServerMonths(serverMonths,{includeTargets:false});
    if(globalThis.SOMXLive?.applyStrategy&&globalThis.SOMXStrategy?.getMode?.()==="quu"){
      await globalThis.SOMXLive.applyStrategy(globalThis.SOMXStrategy.getConfig());
    }
  }catch(e){console.warn("QUU completed-return repair",e)}finally{repairing=false}
}

async function syncQuuHistory({reload=true}={}){
  if(syncing)return;
  if(globalThis.SOMXStrategy?.getMode?.()!=="quu")return;
  syncing=true;
  try{
    const r=await fetch(`${HISTORY_URL}?v=${Date.now()}`,{cache:"no-store"});
    if(!r.ok)throw new Error(`QUU history HTTP ${r.status}`);
    const j=await r.json(),months=j?.months||{};
    serverMonths=months;
    const{returnsChanged}=overlayServerMonths(months,{includeTargets:true});
    window.dispatchEvent(new CustomEvent("somx:quu-history-synced",{detail:{months:Object.keys(months).sort()}}));
    if(returnsChanged)window.dispatchEvent(new Event("somx:historychange"));
    if(reload&&globalThis.SOMXLive?.applyStrategy&&globalThis.SOMXStrategy?.getMode?.()==="quu")await globalThis.SOMXLive.applyStrategy(globalThis.SOMXStrategy.getConfig());
  }catch(e){console.warn("QUU history sync",e)}finally{
    syncing=false;
    queueMicrotask(()=>repairCompletedServerReturns());
  }
}

window.addEventListener("load",()=>syncQuuHistory({reload:true}),{once:true});
window.addEventListener("somx:strategy-active",()=>syncQuuHistory({reload:true}));
// A client-side Alpaca history calculation can finish after the server sync and
// overwrite a completed server month with NaN when even one symbol is missing.
// Completed QUU server months are authoritative, so repair that race immediately.
window.addEventListener("somx:historychange",()=>queueMicrotask(()=>repairCompletedServerReturns()));
globalThis.QUUHistory={sync:syncQuuHistory,repair:repairCompletedServerReturns};
})();
