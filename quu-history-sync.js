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
function normalizedRows(rows){return rows.map(x=>({ticker:String(x.ticker).trim().toUpperCase(),ret:Number.isFinite(Number(x.monthlyReturn))?Number(x.monthlyReturn)*100:null,weight:Number(x.weight)}))}
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
function esc(v){return String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
function pct(v){const n=Number(v);return Number.isFinite(n)?`${n>=0?"+":""}${n.toFixed(1)}%`:"--"}

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

function renderServerHistory(){
  if(globalThis.SOMXStrategy?.getMode?.()!=="quu")return false;
  const list=document.getElementById("history-list");
  if(!list)return false;
  const heading=document.querySelector("#history-modal h2");
  if(heading)heading.textContent="QUU 월말 수익률 기록";
  const status=document.getElementById("history-status");
  if(status)status.textContent="서버 확정 기록 · 각 달 첫 거래일 시가 → 마지막 거래일 종가";
  const entries=Object.entries(serverMonths)
    .filter(([,record])=>completed(record))
    .sort((a,b)=>b[0].localeCompare(a[0]));
  if(!entries.length){
    list.innerHTML='<div class="history-empty">QUU 월말 기록을 불러오는 중…</div>';
    return false;
  }
  list.innerHTML=entries.map(([month,record])=>{
    const port=Number(record.portfolioReturn);
    const rows=(record.rows||[]).map(x=>{
      const r=Number.isFinite(Number(x.monthlyReturn))?Number(x.monthlyReturn)*100:NaN;
      return `<div class="history-stock"><span>${esc(String(x.ticker||"").trim().toUpperCase())}<small class="history-membership">${(Number(x.weight||0)*100).toFixed(2)}%</small></span><strong class="${r<0?"neg":""}">${pct(r)}</strong></div>`;
    }).join("");
    const sleeve=record.selectedSleeve?` · ${esc(record.selectedSleeve)}`:"";
    return `<details class="history-month"><summary><span class="history-month-label">${esc(month)}</span><span class="history-date">${esc(record.lastDate||"")}${sleeve}</span><strong class="history-port ${port<0?"neg":""}">${pct(port)}</strong></summary><div class="history-details">${rows}</div></details>`;
  }).join("");
  return true;
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
  if(!needsRepair){renderServerHistory();return}
  repairing=true;
  try{
    overlayServerMonths(serverMonths,{includeTargets:false});
    renderServerHistory();
    if(globalThis.SOMXLive?.applyStrategy&&globalThis.SOMXStrategy?.getMode?.()==="quu"){
      await globalThis.SOMXLive.applyStrategy(globalThis.SOMXStrategy.getConfig());
    }
    renderServerHistory();
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
    renderServerHistory();
    window.dispatchEvent(new CustomEvent("somx:quu-history-synced",{detail:{months:Object.keys(months).sort()}}));
    if(returnsChanged)window.dispatchEvent(new Event("somx:historychange"));
    if(reload&&globalThis.SOMXLive?.applyStrategy&&globalThis.SOMXStrategy?.getMode?.()==="quu")await globalThis.SOMXLive.applyStrategy(globalThis.SOMXStrategy.getConfig());
    renderServerHistory();
  }catch(e){console.warn("QUU history sync",e)}finally{
    syncing=false;
    queueMicrotask(()=>repairCompletedServerReturns());
  }
}

window.addEventListener("load",()=>syncQuuHistory({reload:true}),{once:true});
window.addEventListener("somx:strategy-active",()=>syncQuuHistory({reload:true}));
window.addEventListener("somx:historychange",()=>setTimeout(()=>{renderServerHistory();repairCompletedServerReturns()},0));
window.addEventListener("somx:quu-history-synced",()=>setTimeout(renderServerHistory,0));

// QUU has authoritative server-completed monthly returns. Intercept the history
// button before app.js starts an Alpaca month-by-month recalculation; that client
// calculation can be incomplete when a single symbol is unavailable and caused
// completed months such as 2026-09 to flicker/reappear as "--".
document.addEventListener("click",e=>{
  const btn=e.target?.closest?.("#historyBtn");
  if(!btn||globalThis.SOMXStrategy?.getMode?.()!=="quu")return;
  e.preventDefault();
  e.stopPropagation();
  e.stopImmediatePropagation();
  document.getElementById("history-modal")?.classList.add("show");
  if(!renderServerHistory())syncQuuHistory({reload:false});
},true);

globalThis.QUUHistory={sync:syncQuuHistory,repair:repairCompletedServerReturns,render:renderServerHistory};
})();
