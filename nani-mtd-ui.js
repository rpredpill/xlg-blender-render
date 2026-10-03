(()=>{
"use strict";

const ACTIVE_STORE="somx.strategy.active.v1";
let timer=null,loading=false,lastData=null,renderObserver=null;

function isNani(){
  try{
    return globalThis.SOMXStrategy?.getMode?.()==="nani"||localStorage.getItem(ACTIVE_STORE)==="nani"||document.body.dataset.strategy==="nani";
  }catch{return document.body.dataset.strategy==="nani"}
}
function fmt(v){const n=Number(v);return Number.isFinite(n)?`${n>=0?"+":""}${n.toFixed(2)}%`:"--"}
function setMetric(id,v){
  const el=document.getElementById(id);if(!el)return;
  const n=Number(v);el.textContent=fmt(n);el.classList.toggle("neg",Number.isFinite(n)&&n<0);
}
function normalizeTicker(s){return String(s||"").trim().toUpperCase().replace(/-/g,".")}

function apply(j){
  if(!isNani())return;
  const mtd=j?.currentMonthMTD;
  if(!mtd?.ready||!Number.isFinite(Number(mtd.portfolioReturn))||!Array.isArray(mtd.rows))return;
  lastData=j;

  const values=mtd.rows.map(r=>({
    ticker:normalizeTicker(r.ticker),
    ret:Number(r.monthlyReturn)*100,
    weight:Number(r.weight)||0,
    country:r.country||null
  })).filter(r=>r.ticker&&Number.isFinite(r.ret));
  const byTicker=new Map(values.map(r=>[r.ticker,r.ret]));

  document.querySelectorAll("#holdings-body tr").forEach(tr=>{
    const ticker=normalizeTicker(tr.querySelector(".logo")?.textContent);
    const ret=byTicker.get(ticker);
    const cell=tr.querySelector(".ret");
    if(cell&&Number.isFinite(ret)){
      cell.textContent=fmt(ret);
      cell.classList.toggle("neg",ret<0);
    }
  });

  setMetric("portfolio-return",Number(mtd.portfolioReturn)*100);
  const up=values.filter(x=>x.ret>0).length;
  const upEl=document.getElementById("up-count");if(upEl)upEl.textContent=values.length?`${up} / ${values.length}`:"--";

  const best=values.length?[...values].sort((a,b)=>b.ret-a.ret)[0]:null;
  const worst=values.length?[...values].sort((a,b)=>a.ret-b.ret)[0]:null;
  const bt=document.getElementById("best-ticker");if(bt)bt.textContent=best?.ticker||"--";
  const wt=document.getElementById("worst-ticker");if(wt)wt.textContent=worst?.ticker||"--";
  setMetric("best-return",best?.ret);
  setMetric("worst-return",worst?.ret);

  const title=document.querySelector(".summary-title > span");
  if(title)title.textContent=`월간 수익률 · ${mtd.month||j.allocationMonth||""}`.trim();
}

async function refresh(){
  if(!isNani()||loading)return;
  loading=true;
  try{
    const r=await fetch(`./nani-latest.json?v=${Date.now()}`,{cache:"no-store"});
    if(!r.ok)throw new Error(`Nani latest HTTP ${r.status}`);
    const j=await r.json();
    if(j?.version!=="4.0")return;
    apply(j);
  }catch(e){console.warn("Nani MTD UI",e)}finally{loading=false}
}
function schedule(delay=120){clearTimeout(timer);timer=setTimeout(refresh,delay)}
function observeDashboardRenders(){
  if(renderObserver)return;
  const tbody=document.getElementById("holdings-body");
  if(!tbody)return;
  renderObserver=new MutationObserver(()=>{
    if(!isNani())return;
    if(lastData)requestAnimationFrame(()=>apply(lastData));
    else schedule(50);
  });
  renderObserver.observe(tbody,{childList:true});
}
function boot(){observeDashboardRenders();schedule(250)}

window.addEventListener("load",boot,{once:true});
window.addEventListener("somx:strategy-active",()=>{observeDashboardRenders();schedule(100)});
window.addEventListener("somx:nani-history-synced",()=>schedule(100));
window.addEventListener("somx:historychange",()=>schedule(100));
document.addEventListener("visibilitychange",()=>{if(!document.hidden){observeDashboardRenders();if(lastData)apply(lastData);else schedule(50)}});
new MutationObserver(()=>{if(isNani()){observeDashboardRenders();if(lastData)apply(lastData);else schedule(50)}}).observe(document.body,{attributes:true,attributeFilter:["data-strategy"]});
if(document.readyState!=="loading")boot();
setInterval(()=>{if(isNani())refresh()},300000);

globalThis.NaniMTDUI={refresh,apply,getLast:()=>lastData};
})();
