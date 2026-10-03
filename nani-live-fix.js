(()=>{
"use strict";

const ACTIVE_STORE="somx.strategy.active.v1";
const isStoredNani=()=>{try{return localStorage.getItem(ACTIVE_STORE)==="nani"}catch{return false}};
const isNani=()=>activeStrategy?.mode==="nani"||isStoredNani();
const wait=ms=>new Promise(r=>setTimeout(r,ms));
let syncing=false,lastSnapshot=null;

const previous={
  bootstrapStrategy,
  ensureInitialized,
  catchUpRebalances,
  getMonthBasePrices,
  getLatestFallback,
  connectStream,
  refreshStrategyHistory,
  render
};

function historyKey(){return `somx.history.v2.${globalThis.NaniStrategy?.signature||"nani-ndx100-nk225-v1"}`}
function readHistory(){try{return JSON.parse(localStorage.getItem(historyKey())||"{}")||{}}catch{return{}}}
function fmt2(v){const n=Number(v);return Number.isFinite(n)?`${n>=0?"+":""}${n.toFixed(2)}%`:"--"}
function setMetric2(id,v){const el=document.getElementById(id);if(!el)return;const n=Number(v);el.textContent=fmt2(n);el.classList.toggle("neg",Number.isFinite(n)&&n<0)}
function activateClosureContext(){
  if(!globalThis.NaniStrategy)return false;
  if(activeStrategy?.mode!=="nani"){
    activeStrategy=JSON.parse(JSON.stringify(globalThis.NaniStrategy.config));
    strategySig=globalThis.NaniStrategy.signature;
    latestPrices={};
    loadContext();
  }
  return true;
}
function latestCompletedHistory(){
  const hist=readHistory();
  const months=Object.keys(hist).filter(m=>Number.isFinite(Number(hist[m]?.port))).sort();
  return months.length?hist[months.at(-1)]:null;
}
function renderNani(){
  if(!isNani())return false;
  activateClosureContext();
  const holdings=state?.holdings||[];
  const weights=state?.targetWeights||{};
  if(!holdings.length){
    if(lastSnapshot?.rows?.length){
      state.holdings=lastSnapshot.rows.map(x=>String(x.ticker).toUpperCase());
      state.targetWeights=Object.fromEntries(lastSnapshot.rows.map(x=>[String(x.ticker).toUpperCase(),Number(x.weight)]));
    }else return true;
  }
  const h=latestCompletedHistory();
  const retBy=new Map((h?.rows||[]).map(x=>[String(x.ticker).toUpperCase(),Number(x.ret)]));
  const rows=state.holdings.map((ticker,i)=>({
    ticker,
    status:state.statuses?.[ticker]||"HOLD",
    weight:Number(weights[ticker])||0,
    ret:retBy.get(ticker),
    country:ticker.endsWith(".T")?"JP":"US"
  })).sort((a,b)=>b.weight-a.weight);
  const tbody=document.getElementById("holdings-body");
  if(tbody)tbody.innerHTML=rows.map((r,i)=>`<tr><td><div class="stock"><div class="logo" style="color:${colorFor(i)}">${r.ticker}</div><div>${r.ticker}<small style="display:block;opacity:.55">${r.country}</small></div></div></td><td class="status-wrap"><span class="badge hold">HOLD</span></td><td class="ret ${Number(r.ret)<0?"neg":""}">${fmt2(r.ret)}</td></tr>`).join("");
  const donutRows=rows.map(r=>({ticker:r.ticker,weight:r.weight}));
  drawDonut(donutRows);
  const valid=rows.filter(x=>Number.isFinite(Number(x.ret))),up=valid.filter(x=>Number(x.ret)>0).length;
  const best=valid.length?[...valid].sort((a,b)=>Number(b.ret)-Number(a.ret))[0]:null;
  const worst=valid.length?[...valid].sort((a,b)=>Number(a.ret)-Number(b.ret))[0]:null;
  setMetric2("portfolio-return",h?.port);
  const upEl=document.getElementById("up-count");if(upEl)upEl.textContent=valid.length?`${up} / ${valid.length}`:"--";
  const bt=document.getElementById("best-ticker");if(bt)bt.textContent=best?.ticker||"--";setMetric2("best-return",best?.ret);
  const wt=document.getElementById("worst-ticker");if(wt)wt.textContent=worst?.ticker||"--";setMetric2("worst-return",worst?.ret);
  const months=Object.keys(readHistory()).filter(m=>Number.isFinite(Number(readHistory()[m]?.port))).sort();
  const title=document.querySelector(".summary-title > span");if(title)title.textContent=months.length?`월간 수익률 · ${months.at(-1)}`:"월간 수익률";
  const panel=document.getElementById("benchmark-panel");if(panel)panel.style.display="none";
  document.body.dataset.strategy="nani";
  document.getElementById("donut")?.setAttribute("aria-label","Nani portfolio donut");
  return true;
}

async function syncNaniSnapshot(force=false){
  if(!isNani()||syncing||!globalThis.NaniStrategy?.fetchSnapshot)return lastSnapshot;
  syncing=true;
  try{
    activateClosureContext();
    const j=await globalThis.NaniStrategy.fetchSnapshot();
    lastSnapshot=j;
    const ym=currentNYMonth();
    const allocationYm=/^\d{4}-\d{2}$/.test(String(j.allocationMonth||""))?String(j.allocationMonth):ym;
    const holdings=j.rows.map(x=>normalizeTicker(x.ticker));
    const targetWeights=Object.fromEntries(j.rows.map(x=>[normalizeTicker(x.ticker),Number(x.weight)]));
    const same=state?.initialized&&state.snapshotAt===(j.generatedAt||null)&&state.holdings?.length===holdings.length&&holdings.every((s,i)=>state.holdings[i]===s);
    if(!force&&same){renderNani();return j}
    const old=new Set(state?.holdings||[]);
    activeStrategy.holdings=holdings.length;
    state={
      rebalanceMonth:allocationYm,
      strategyAnchor:j.rebalanceMonth||allocationYm,
      initialized:true,
      holdings,
      statuses:Object.fromEntries(holdings.map(s=>[s,old.has(s)?"HOLD":"IN"])),
      targetWeights,
      basePrices:{},
      updatedAt:new Date().toISOString(),
      snapshotAt:j.generatedAt||null,
      snapshotAllocationMonth:allocationYm,
      serverSnapshot:true
    };
    holdingsHistory[allocationYm]=[...holdings];
    weightsHistory[allocationYm]=JSON.parse(JSON.stringify(targetWeights));
    holdingsHistory[ym]=[...holdings];
    weightsHistory[ym]=JSON.parse(JSON.stringify(targetWeights));
    saveState();saveHoldingsHistory();saveWeightsHistory();
    if(globalThis.NaniHistory?.sync)await globalThis.NaniHistory.sync();
    loadContext();
    renderNani();
    window.dispatchEvent(new Event("somx:historychange"));
    return j;
  }catch(e){
    console.warn("Nani snapshot sync",e);
    return lastSnapshot;
  }finally{syncing=false}
}

bootstrapStrategy=async function(){
  if(isNani()){
    const j=await syncNaniSnapshot(true);
    if(!j&&!state?.initialized)throw new Error("Nani 스냅샷을 불러오지 못했습니다.");
    return;
  }
  return previous.bootstrapStrategy();
};
ensureInitialized=async function(){
  if(isNani()){
    activateClosureContext();
    if(!state?.initialized||!state?.holdings?.length)await syncNaniSnapshot(true);else await syncNaniSnapshot(false);
    if(!state?.initialized||!state?.holdings?.length)throw new Error("Nani 스냅샷을 불러오지 못했습니다.");
    return;
  }
  return previous.ensureInitialized();
};
catchUpRebalances=async function(){if(isNani()){await syncNaniSnapshot(false);return}return previous.catchUpRebalances()};
getMonthBasePrices=async function(){if(isNani())return{};return previous.getMonthBasePrices()};
getLatestFallback=async function(){if(isNani())return;return previous.getLatestFallback()};
connectStream=function(){
  if(isNani()){
    if(socket)try{socket.close()}catch{}socket=null;
    return;
  }
  return previous.connectStream();
};
refreshStrategyHistory=function(){
  if(isNani()){
    const p=globalThis.NaniHistory?.sync?.();
    if(p&&typeof p.then==="function")p.then(()=>{loadContext();renderNani()}).catch(e=>console.warn("Nani history",e));
    return p;
  }
  return previous.refreshStrategyHistory();
};
render=function(){if(isNani()&&renderNani())return;return previous.render()};

window.addEventListener("somx:strategy-active",()=>{
  if(isStoredNani()){
    activateClosureContext();
    setTimeout(()=>syncNaniSnapshot(true),0);
  }else{
    const panel=document.getElementById("benchmark-panel");if(panel)panel.style.display="";
  }
});
window.addEventListener("somx:nani-history-synced",()=>{
  if(!isNani())return;
  loadContext();
  renderNani();
});
window.addEventListener("load",()=>{
  if(!isNani())return;
  activateClosureContext();
  setTimeout(()=>syncNaniSnapshot(false),0);
});
document.addEventListener("visibilitychange",()=>{if(!document.hidden&&isNani())syncNaniSnapshot(false)});
setInterval(()=>{if(isNani())syncNaniSnapshot(false)},300000);

globalThis.NaniLive={sync:syncNaniSnapshot,render:renderNani};
})();