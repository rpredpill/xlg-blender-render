(()=>{
"use strict";
const MARKER_KEY="somx.core.canonical.backfill.v2";
let running=false;

/*
  Reconstructed from the previously verified monthly Top16 rank sequence.
  The rank month M determines holdings for M+1 (signal at month-end, trade on
  the first US trading day of the next month). Starting from all 8,008
  possible 6-stock states in 2023-07, the state set becomes unique by the
  2023-12 signal, so the first unambiguous holding month is 2024-01.

  Important correction discovered during v23 validation:
  the older 2026-01 seed contained TER, while the deterministic long-run
  chain contains MU. The chains rejoin by 2026-06, so the current 2026-09
  live Core state is not changed here; only historical holdings/returns are
  corrected and extended.
*/
const CORE_HISTORY={
  "2024-01":["WRK","KEY","PSX","ZION","CEG","MPC"],
  "2024-02":["ANET","INTC","AXON","FICO","NRG","WRK"],
  "2024-03":["AMD","ALL","NRG","PANW","FICO","ANET"],
  "2024-04":["SMCI","AMD","NVDA","DECK","UBER","TPR"],
  "2024-05":["SMCI","NVDA","BLDR","AMD","UBER","TPR"],
  "2024-06":["SMCI","VST","NVDA","GE","CEG","NRG"],
  "2024-07":["SMCI","VST","NVDA","CEG","GE","NRG"],
  "2024-08":["VST","NVDA","CEG","SMCI","GE","NRG"],
  "2024-09":["MMM","NEM","NVDA","VST","NRG","GE"],
  "2024-10":["MMM","NEM","GEV","VTR","IRM","K"],
  "2024-11":["FICO","PLTR","GEV","IRM","VTR","MMM"],
  "2024-12":["TPL","PLTR","GEV","IRM","FICO","NCLH"],
  "2025-01":["PLTR","TPL","AXON","UAL","GEV","FICO"],
  "2025-02":["PLTR","UAL","AXON","GEV","VST","TSLA"],
  "2025-03":["PLTR","UAL","VST","TSLA","GEV","AXON"],
  "2025-04":["PLTR","TPR","UAL","TPL","RL","DFS"],
  "2025-05":["PLTR","TPR","EQT","VRSN","FOX","FOXA"],
  "2025-06":["PLTR","VRSN","PM","NFLX","NEM","CRWD"],
  "2025-07":["PLTR","PM","NEM","CRWD","NFLX","VRSN"],
  "2025-08":["SMCI","PLTR","DG","PM","NEM","NFLX"],
  "2025-09":["GEV","PLTR","AMD","COIN","WDC","NEM"],
  "2025-10":["HOOD","GEV","WDC","STX","PLTR","COIN"],
  "2025-11":["HOOD","WDC","APP","STX","WBD","MU"],
  "2025-12":["SNDK","WDC","MU","WBD","HOOD","STX"],
  "2026-01":["SNDK","WDC","WBD","ALB","STX","MU"],
  "2026-02":["SNDK","WDC","WBD","ALB","STX","MU"],
  "2026-03":["SNDK","WDC","WBD","ALB","STX","MU"],
  "2026-04":["SNDK","WDC","ALB","STX","MU","LITE"],
  "2026-05":["SNDK","WDC","STX","MU","LITE","CIEN"]
};

function status(text){const el=document.getElementById("history-status");if(el)el.textContent=text}
function sameSet(a,b){if(!a||!b||a.length!==b.length)return false;const A=[...a].sort(),B=[...b].sort();return A.every((x,i)=>x===B[i])}
function marker(){try{return JSON.parse(localStorage.getItem(MARKER_KEY)||"null")}catch{return null}}
function markDone(){try{localStorage.setItem(MARKER_KEY,JSON.stringify({version:2,start:"2024-01",end:"2026-05",completedAt:new Date().toISOString()}))}catch{}}
async function waitHistoryIdle(){for(let i=0;i<160&&historyLoading;i++)await new Promise(r=>setTimeout(r,250))}

function installHistory(){
  let changed=0;
  for(const[month,names]of Object.entries(CORE_HISTORY)){
    const old=holdingsHistory?.[month];
    if(!sameSet(old,names)){
      holdingsHistory[month]=[...names];
      weightsHistory[month]=equalWeights(names);
      if(monthlyHistory?.[month])delete monthlyHistory[month];
      changed++;
    }else if(!weightsHistory?.[month])weightsHistory[month]=equalWeights(names);
  }
  saveHoldingsHistory();saveWeightsHistory();saveMonthlyHistory();
  return changed;
}

async function backfillCoreHistory(){
  if(running||activeStrategy?.mode!=="core"||!creds)return;
  running=true;
  try{
    const done=marker();
    status(done?.version===2?"SOMX Core 과거 월별 수익률 확인 중…":"SOMX Core canonical 기록 확장 중…");
    const changed=installHistory();markDone();
    if(changed)status(`Core 과거 보유이력 ${changed}개월 갱신 · 월별 수익률 계산 중…`);
    await waitHistoryIdle();if(activeStrategy?.mode!=="core")return;await ensureMonthlyHistory();if(activeStrategy?.mode!=="core")return;renderHistory();
    const months=Object.keys(monthlyHistory||{}).sort(),first=months[0]||"--",last=months.at(-1)||"--";
    status(`각 달 첫 거래일 시가 → 마지막 거래일 종가 · ${first} ~ ${last} · ${months.length}개월`);
  }catch(e){console.error("core backfill",e);status(e?.message||"Core 과거 기록 확장 실패");}
  finally{running=false}
}

const historyBtn=document.getElementById("historyBtn");
historyBtn?.addEventListener("click",e=>{e.stopImmediatePropagation();document.getElementById("history-modal")?.classList.add("show");renderHistory();if(!creds)return;if(activeStrategy?.mode==="core")backfillCoreHistory();else refreshStrategyHistory();},true);

globalThis.SOMXCoreBackfill={run:backfillCoreHistory,history:()=>JSON.parse(JSON.stringify(CORE_HISTORY))};
})();
