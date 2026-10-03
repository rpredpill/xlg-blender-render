(()=>{
"use strict";
const SIG="ququ-nasdaq-composite-v2";
const SOURCE="./ququ-v2-ablation.json";
const HISTORY_KEY=`somx.history.v2.${SIG}`;
const LIVE_START="2026-10";
let syncing=false,backfill={};

function active(){
  const api=globalThis.SOMXStrategy,c=api?.getConfig?.();
  return api?.getMode?.()==="ququ"&&api?.signature?.(c)===SIG;
}
function readStored(){try{return JSON.parse(localStorage.getItem(HISTORY_KEY)||"{}")||{}}catch{return{}}}
function finitePort(h){return h&&Number.isFinite(Number(h.port))}
function fmt(v){v=Number(v);return Number.isFinite(v)?`${v>=0?"+":""}${v.toFixed(1)}%`:"--"}

function pruneOldBackfillCache(){
  const stored=readStored(),live={};
  for(const [month,h] of Object.entries(stored)){
    if(month>=LIVE_START&&finitePort(h)&&h?.backtest!==true)live[month]=h;
  }
  try{
    if(Object.keys(live).length)localStorage.setItem(HISTORY_KEY,JSON.stringify(live));
    else localStorage.removeItem(HISTORY_KEY);
  }catch(e){console.debug("QUQU history cache prune",e)}
}

function combined(){
  const out={...backfill},stored=readStored();
  for(const [month,h] of Object.entries(stored)){
    if(month>=LIVE_START&&finitePort(h)&&h?.backtest!==true)out[month]={...h,liveObserved:true,backtest:false};
  }
  return out;
}

function renderCombined(){
  if(!active())return;
  const list=document.getElementById("history-list"),modal=document.getElementById("history-modal");
  if(!list||!modal?.classList.contains("show"))return;
  const heading=modal.querySelector("h2"),status=document.getElementById("history-status");
  if(heading)heading.textContent="QUQU v2 월말 수익률 기록";
  if(status)status.textContent="2022-10~2026-09 백테스트 · 2026-10부터 실제 기록";
  const history=combined(),months=Object.keys(history).sort().reverse();
  if(!months.length){list.innerHTML='<div class="history-empty">QUQU v2 기록을 불러오는 중…</div>';return}
  list.innerHTML=months.map(month=>{
    const h=history[month],isBackfill=h.backtest===true||month<LIVE_START;
    const rows=Array.isArray(h.rows)?h.rows:[];
    const details=rows.map(x=>`<div class="history-stock"><span>${x.ticker}</span><strong class="${Number(x.ret)<0?"neg":""}">${fmt(x.ret)}</strong></div>`).join("");
    const label=isBackfill?"백테스트":(h.lastDate||"실제 기록");
    return `<details class="history-month"><summary><span class="history-month-label">${month}</span><span class="history-date">${label}</span><strong class="history-port ${Number(h.port)<0?"neg":""}">${fmt(h.port)}</strong></summary><div class="history-details">${details}</div></details>`;
  }).join("");
}

async function sync(){
  if(syncing||!active())return;
  syncing=true;
  try{
    const r=await fetch(`${SOURCE}?v=${Date.now()}`,{cache:"no-store"});
    if(!r.ok)throw new Error(`QUQU v2 history HTTP ${r.status}`);
    const j=await r.json(),months=Array.isArray(j?.months)?j.months:[],next={};
    for(const m of months){
      const month=String(m?.month||""),v=m?.variants?.["+ Winsorization"],port=Number(v?.returnPct);
      // 2026-10 onward is reserved for actual site-observed month-end records.
      if(!/^\d{4}-\d{2}$/.test(month)||month>=LIVE_START||!Number.isFinite(port))continue;
      next[month]={month,port,rows:[],lastDate:"백테스트",backtest:true,liveObserved:false,source:"ququ-v2-ablation",signalMonth:m.signalMonth||null,holdingsCount:Number(v?.holdings)||null,turnoverPct:Number.isFinite(Number(v?.turnoverPct))?Number(v.turnoverPct):null};
    }
    backfill=next;
    // Backfill is read directly from the repository, not duplicated in localStorage.
    // This avoids the quota problem caused by hundreds of QUQU holdings.
    pruneOldBackfillCache();
    renderCombined();
    window.dispatchEvent(new CustomEvent("somx:ququ-v2-history-synced",{detail:{months:Object.keys(backfill).length}}));
  }catch(e){console.warn("QUQU v2 history sync",e)}finally{syncing=false}
}

const historyBtn=document.getElementById("historyBtn");
historyBtn?.addEventListener("click",()=>setTimeout(()=>{if(Object.keys(backfill).length)renderCombined();else sync()},0));
window.addEventListener("somx:historychange",()=>setTimeout(renderCombined,0));
window.addEventListener("somx:strategy-active",()=>setTimeout(()=>{sync();renderCombined()},0));
window.addEventListener("load",()=>setTimeout(sync,300),{once:true});
globalThis.QUQUV2History={sync,render:renderCombined,getBackfill:()=>({...backfill})};
})();