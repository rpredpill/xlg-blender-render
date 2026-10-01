(()=>{
"use strict";
let busy=false;

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
const originalMetrics=metrics;
metrics=function(){
  const out=originalMetrics();
  if(!creds||state?.rebalanceMonth!==currentNYMonth()||!state?.holdings?.length)return out;
  const noBase=state.holdings.every(s=>!Number.isFinite(Number(state.basePrices?.[s])));
  if(!noBase)return out;
  for(const row of out.rows){
    row.rel=1;
    row.ret=0;
    row.weight=row.startWeight;
  }
  out.port=0;
  return out;
};

window.addEventListener("load",()=>setTimeout(refreshMonthBase,1200));
document.addEventListener("visibilitychange",()=>{if(!document.hidden)refreshMonthBase()});
setInterval(refreshMonthBase,20000);
})();