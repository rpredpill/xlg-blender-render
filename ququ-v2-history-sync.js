(()=>{
"use strict";
const SIG="ququ-nasdaq-composite-v2";
const SOURCE="./ququ-v2-ablation.json";
const HISTORY_KEY=`somx.history.v2.${SIG}`;
let syncing=false;

function active(){
  const api=globalThis.SOMXStrategy,c=api?.getConfig?.();
  return api?.getMode?.()==="ququ"&&api?.signature?.(c)===SIG;
}
function read(){try{return JSON.parse(localStorage.getItem(HISTORY_KEY)||"{}")||{}}catch{return{}}}
function write(v){localStorage.setItem(HISTORY_KEY,JSON.stringify(v))}
function quota(e){return e?.name==="QuotaExceededError"||String(e?.message||e).toLowerCase().includes("quota")}
function isObservedRecord(h){
  return !!h&&Number.isFinite(Number(h.port))&&(h.liveObserved===true||h.backtest!==true);
}

async function sync(){
  if(syncing||!active())return;
  syncing=true;
  try{
    const r=await fetch(`${SOURCE}?v=${Date.now()}`,{cache:"no-store"});
    if(!r.ok)throw new Error(`QUQU v2 history HTTP ${r.status}`);
    const j=await r.json();
    const months=Array.isArray(j?.months)?j.months:[];
    const history=read();
    let added=0;
    for(const m of months){
      const month=String(m?.month||"");
      const v=m?.variants?.["+ Winsorization"];
      const port=Number(v?.returnPct);
      if(!/^\d{4}-\d{2}$/.test(month)||!Number.isFinite(port))continue;
      // Any month-end record produced by the live Alpaca path has no
      // backtest:true marker. Never replace such an observed record with
      // historical backfill, even if the backfill dataset is later extended.
      if(isObservedRecord(history[month]))continue;
      history[month]={
        month,port,rows:[],lastDate:"백테스트",holdings:[],weights:{},
        backtest:true,liveObserved:false,source:"ququ-v2-ablation",
        signalMonth:m.signalMonth||null,holdingsCount:Number(v?.holdings)||null,
        turnoverPct:Number.isFinite(Number(v?.turnoverPct))?Number(v.turnoverPct):null
      };
      added++;
    }
    try{write(history)}catch(e){
      if(!quota(e))throw e;
      // Compact to just the fields rendered by the month-end modal.
      const compact={};
      for(const [month,h] of Object.entries(history))compact[month]={month,port:Number(h.port),rows:[],lastDate:h.lastDate||"백테스트",backtest:!!h.backtest,liveObserved:!!h.liveObserved};
      write(compact);
    }
    window.dispatchEvent(new Event("somx:historychange"));
    window.dispatchEvent(new CustomEvent("somx:ququ-v2-history-synced",{detail:{months:added}}));
  }catch(e){console.warn("QUQU v2 history sync",e)}finally{syncing=false}
}

window.addEventListener("load",()=>setTimeout(sync,300),{once:true});
window.addEventListener("somx:strategy-active",()=>setTimeout(sync,0));
globalThis.QUQUV2History={sync};
})();