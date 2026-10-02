(()=>{
"use strict";

const SIG="quu-ndx100-rollover-v1";
const HISTORY_URL="./quu-history.json";
const key=kind=>`somx.${kind}.v2.${SIG}`;
let syncing=false,serverMonths={};

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

function overlayServerMonths(months){
  const hh=read("holdings"),wh=read("weights"),mh=read("history");
  let changed=false;
  for(const [month,record] of Object.entries(months||{})){
    const rows=record?.rows;
    if(!validRows(rows))continue;
    const sum=rows.reduce((a,x)=>a+Number(x.weight),0);
    if(Math.abs(sum-1)>1e-6)continue;
    const holdings=rows.map(x=>String(x.ticker).trim().toUpperCase());
    const weights=Object.fromEntries(rows.map(x=>[String(x.ticker).trim().toUpperCase(),Number(x.weight)]));
    hh[month]=holdings;wh[month]=weights;
    if(completed(record))mh[month]=completedRecord(month,record,holdings,weights);
    changed=true;
  }
  if(changed){write("holdings",hh);write("weights",wh);write("history",mh)}
  return changed;
}

function renderServerHistory(){
  if(globalThis.SOMXStrategy?.getMode?.()!=="quu")return false;
  const modal=document.getElementById("history-modal"),list=document.getElementById("history-list");
  if(!modal||!list)return false;
  const heading=document.querySelector("#history-modal h2");
  if(heading)heading.textContent="QUU 월말 수익률 기록";
  const status=document.getElementById("history-status");
  if(status)status.textContent="서버 확정 기록 · 각 달 첫 거래일 시가 → 마지막 거래일 종가";
  const entries=Object.entries(serverMonths).filter(([,record])=>completed(record)).sort((a,b)=>b[0].localeCompare(a[0]));
  if(!entries.length){list.innerHTML='<div class="history-empty">QUU 월말 기록을 불러오는 중…</div>';modal.classList.add("show");return false}
  list.innerHTML=entries.map(([month,record])=>{
    const port=Number(record.portfolioReturn);
    const rows=(record.rows||[]).map(x=>{const r=Number.isFinite(Number(x.monthlyReturn))?Number(x.monthlyReturn)*100:NaN;return `<div class="history-stock"><span>${esc(String(x.ticker||"").trim().toUpperCase())}<small class="history-membership">${(Number(x.weight||0)*100).toFixed(2)}%</small></span><strong class="${r<0?"neg":""}">${pct(r)}</strong></div>`}).join("");
    const sleeve=record.selectedSleeve?` · ${esc(record.selectedSleeve)}`:"";
    return `<details class="history-month"><summary><span class="history-month-label">${esc(month)}</span><span class="history-date">${esc(record.lastDate||"")}${sleeve}</span><strong class="history-port ${port<0?"neg":""}">${pct(port)}</strong></summary><div class="history-details">${rows}</div></details>`;
  }).join("");
  modal.classList.add("show");
  return true;
}

async function syncQuuHistory(){
  if(syncing||globalThis.SOMXStrategy?.getMode?.()!=="quu")return;
  syncing=true;
  try{
    const r=await fetch(`${HISTORY_URL}?v=${Date.now()}`,{cache:"no-store"});
    if(!r.ok)throw new Error(`QUU history HTTP ${r.status}`);
    const j=await r.json();serverMonths=j?.months||{};
    if(overlayServerMonths(serverMonths))window.dispatchEvent(new Event("somx:historychange"));
    if(document.getElementById("history-modal")?.classList.contains("show"))renderServerHistory();
  }catch(e){console.warn("QUU history sync",e)}finally{syncing=false}
}

// Capture before app.js history handler. QUU completed months come from the
// server archive; do not start Alpaca month-by-month recomputation here.
document.addEventListener("click",e=>{
  const btn=e.target?.closest?.("#historyBtn");
  if(!btn||globalThis.SOMXStrategy?.getMode?.()!=="quu")return;
  e.preventDefault();e.stopImmediatePropagation();
  if(!Object.keys(serverMonths).length){syncQuuHistory().then(renderServerHistory);renderServerHistory()}else renderServerHistory();
},true);

window.addEventListener("load",()=>syncQuuHistory(),{once:true});
window.addEventListener("somx:strategy-active",()=>syncQuuHistory());
globalThis.QUUHistory={sync:syncQuuHistory,render:renderServerHistory};
})();
