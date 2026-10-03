(()=>{
"use strict";
let busy=false,ququSnapshotBusy=false,ququPollTimer=null;
const QUQU_BATCH=60;

const originalEnsureInitialized=ensureInitialized;
const originalBootstrapStrategy=bootstrapStrategy;
const originalCatchUpRebalances=catchUpRebalances;
const originalMonthBars=monthBars;
const originalGetMonthBasePrices=getMonthBasePrices;
const originalGetLatestFallback=getLatestFallback;
const originalConnectStream=connectStream;
const originalMetrics=metrics;

const isQuqu=()=>activeStrategy?.mode==="ququ";
const chunks=(arr,n)=>{const out=[];for(let i=0;i<arr.length;i+=n)out.push(arr.slice(i,i+n));return out};
const wait=ms=>new Promise(r=>setTimeout(r,ms));

async function syncQuquSnapshot(force=false){
  if(!isQuqu()||ququSnapshotBusy||!globalThis.SOMXStrategy?.fetchSnapshot)return false;
  ququSnapshotBusy=true;
  try{
    const j=await globalThis.SOMXStrategy.fetchSnapshot(activeStrategy);
    const ym=currentNYMonth(),rows=j.rows||[],holdings=rows.map(r=>normalizeTicker(r.ticker));
    const targetWeights=Object.fromEntries(rows.map(r=>[normalizeTicker(r.ticker),Number(r.weight)]));
    const allocationYm=/^\d{4}-\d{2}$/.test(String(j.allocationMonth||""))?String(j.allocationMonth):ym;
    const sameHoldings=state?.holdings?.length===holdings.length&&holdings.every((s,i)=>state.holdings[i]===s);
    const sameSnapshot=state?.snapshotAt===(j.generatedAt||null);
    if(!force&&state?.initialized&&state.rebalanceMonth===ym&&sameHoldings&&sameSnapshot)return false;

    const oldSet=new Set(state?.holdings||[]);
    activeStrategy.holdings=holdings.length;
    activeStrategy.entryRank=holdings.length;
    activeStrategy.exitRank=holdings.length;
    state={
      rebalanceMonth:ym,strategyAnchor:ym,initialized:true,holdings,
      statuses:Object.fromEntries(holdings.map(s=>[s,oldSet.has(s)?"HOLD":"IN"])),
      targetWeights,basePrices:{},updatedAt:new Date().toISOString(),
      snapshotAt:j.generatedAt||null,snapshotAllocationMonth:allocationYm
    };
    holdingsHistory[allocationYm]=[...holdings];
    weightsHistory[allocationYm]=clone(targetWeights);
    holdingsHistory[ym]=[...holdings];
    weightsHistory[ym]=clone(targetWeights);
    saveState();saveHoldingsHistory();saveWeightsHistory();
    window.dispatchEvent(new Event("somx:historychange"));
    return true;
  }catch(e){
    console.warn("QUQU snapshot sync",e);
    return false;
  }finally{ququSnapshotBusy=false}
}

bootstrapStrategy=async function(){
  if(isQuqu()){
    const changed=await syncQuquSnapshot(true);
    if(!changed&&!state?.initialized)throw new Error("QUQU v2 스냅샷을 불러오지 못했습니다.");
    return;
  }
  return originalBootstrapStrategy();
};

ensureInitialized=async function(){
  if(isQuqu()){
    await syncQuquSnapshot(false);
    if(!state?.initialized||!state?.holdings?.length)await syncQuquSnapshot(true);
    if(!state?.initialized||!state?.holdings?.length)throw new Error("QUQU v2 스냅샷을 불러오지 못했습니다.");
    return;
  }
  return originalEnsureInitialized();
};

catchUpRebalances=async function(){
  if(isQuqu()){
    // QUQU is production-snapshot driven. Never send it through the generic
    // S&P 500 browser ranking/catch-up path.
    await syncQuquSnapshot(false);
    return;
  }
  return originalCatchUpRebalances();
};

monthBars=async function(month,holdings){
  if(!isQuqu())return originalMonthBars(month,holdings);
  if(!holdings?.length)return{};
  const merged={};
  for(const part of chunks(holdings,QUQU_BATCH)){
    try{
      const q=new URLSearchParams({symbols:part.join(","),timeframe:"1Day",start:`${month}-01`,end:`${addMonths(month,1)}-03`,adjustment:"all",feed:"iex",limit:"5000",sort:"asc"});
      const data=await alpaca(`/stocks/bars?${q}`);
      for(const[s,bars]of Object.entries(data.bars||{}))merged[normalizeTicker(s)]=bars;
    }catch(e){console.debug("QUQU month batch skipped",part[0],part.at(-1),e)}
  }
  return merged;
};

getMonthBasePrices=async function(){
  if(!isQuqu())return originalGetMonthBasePrices();
  const m=state.rebalanceMonth;
  if(state.basePrices&&state.holdings.every(s=>Number(state.basePrices[s])>0))return state.basePrices;
  const base={...(state.basePrices||{})};
  const all=await monthBars(m,state.holdings);
  for(const s of state.holdings){
    const bars=(all[s]||[]).filter(b=>String(b.t).slice(0,7)===m);
    if(bars.length&&Number(bars[0]?.o)>0)base[s]=Number(bars[0].o);
  }
  state.basePrices=base;saveState();return base;
};

getLatestFallback=async function(){
  if(!isQuqu())return originalGetLatestFallback();
  if(!state.holdings.length)return;
  for(const part of chunks(state.holdings,QUQU_BATCH)){
    try{
      const q=new URLSearchParams({symbols:part.join(","),feed:"iex"});
      const data=await alpaca(`/stocks/bars/latest?${q}`);
      for(const[s,obj]of Object.entries(data.bars||{}))if(obj?.c)latestPrices[normalizeTicker(s)]=Number(obj.c);
    }catch(e){console.debug("QUQU latest batch skipped",part[0],part.at(-1),e)}
  }
};

connectStream=function(){
  if(!isQuqu()){
    if(ququPollTimer){clearInterval(ququPollTimer);ququPollTimer=null}
    return originalConnectStream();
  }
  // Hundreds of QUQU names are handled by batched IEX polling instead of one
  // oversized websocket subscription.
  if(socket)try{socket.close()}catch{}socket=null;
  if(ququPollTimer)clearInterval(ququPollTimer);
  const poll=async()=>{
    if(!isQuqu()||!creds)return;
    try{await getLatestFallback();render()}catch(e){console.debug("QUQU latest poll",e)}
  };
  ququPollTimer=setInterval(poll,60000);
};

async function hydrateQuquLive(){
  if(!isQuqu()||!creds)return;
  try{
    await ensureInitialized();
    await getMonthBasePrices();
    await getLatestFallback();
    render();
    connectStream();
    refreshStrategyHistory().catch(console.error);
  }catch(e){
    console.warn("QUQU live hydration",e);
    toast("QUQU 적용됨 · 실시간 가격 일부는 다시 불러오는 중");
  }
}

async function applySnapshotStrategy(c){
  if(c?.mode!=="ququ"){
    if(globalThis.SOMXLive?._originalApplyStrategy)return globalThis.SOMXLive._originalApplyStrategy(c);
    if(globalThis.SOMXLive?.applyStrategy&&globalThis.SOMXLive.applyStrategy!==applySnapshotStrategy)return globalThis.SOMXLive.applyStrategy(c);
    return;
  }

  // Do not make Apply depend on hundreds of Alpaca requests. The production
  // snapshot is already persisted by seedSnapshotContext before this call.
  const deadline=Date.now()+2500;
  while(starting&&Date.now()<deadline)await wait(50);
  if(starting)throw new Error("QUQU_RELOAD_REQUIRED");
  if(switching)return;

  switching=true;
  try{
    if(socket)try{socket.close()}catch{}socket=null;
    if(ququPollTimer){clearInterval(ququPollTimer);ququPollTimer=null}
    activeStrategy=clone(c);
    strategySig=globalThis.SOMXStrategy?.signature?.(activeStrategy)||activeStrategy.mode;
    latestPrices={};
    loadContext();
    render();
    window.dispatchEvent(new Event("somx:strategy-active"));
    window.dispatchEvent(new Event("somx:historychange"));
    toast(`${activeStrategy.label} 적용됨`);
  }finally{switching=false}

  // Live price/base-price work is deliberately background-only so the Apply
  // button succeeds immediately even if Alpaca is slow or one symbol is bad.
  setTimeout(()=>hydrateQuquLive(),0);
}

if(globalThis.SOMXLive?.applyStrategy){
  globalThis.SOMXLive._originalApplyStrategy=globalThis.SOMXLive.applyStrategy.bind(globalThis.SOMXLive);
  globalThis.SOMXLive.applySnapshotStrategy=applySnapshotStrategy;
}

function hasCurrentMonthBase(){
  if(!state?.holdings?.length)return false;
  return state.holdings.every(s=>Number.isFinite(Number(state.basePrices?.[s]))&&Number(state.basePrices[s])>0);
}

async function refreshMonthBase(){
  if(busy||!creds||!state?.holdings?.length)return;
  if(state.rebalanceMonth!==currentNYMonth()||hasCurrentMonthBase())return;
  busy=true;
  try{
    await getMonthBasePrices();
    if(hasCurrentMonthBase()){
      await getLatestFallback();
      render();
    }
  }catch(e){
    console.debug("month base pending",e);
  }finally{busy=false}
}

/*
  On the first US trading day the official monthly base open may not exist yet
  when the app is opened before 09:30 ET. In that short pending window, a new
  month has not moved yet, so show 0.0% rather than '--'. The real first-day
  open replaces this automatically as soon as Alpaca publishes the daily bar.
*/
metrics=function(){
  const out=originalMetrics();
  if(!creds||state?.rebalanceMonth!==currentNYMonth()||!state?.holdings?.length)return out;
  const noBase=state.holdings.every(s=>!Number.isFinite(Number(state.basePrices?.[s])));
  if(!noBase)return out;
  for(const row of out.rows){row.rel=1;row.ret=0;row.weight=row.startWeight}
  out.port=0;
  return out;
};

window.addEventListener("load",()=>setTimeout(async()=>{
  if(isQuqu())await syncQuquSnapshot(false);
  await refreshMonthBase();
},1200));
document.addEventListener("visibilitychange",async()=>{
  if(document.hidden)return;
  if(isQuqu())await syncQuquSnapshot(false);
  refreshMonthBase();
});
setInterval(refreshMonthBase,20000);
setInterval(()=>{if(isQuqu())syncQuquSnapshot(false)},300000);
})();