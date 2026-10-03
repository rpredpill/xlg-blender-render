(()=>{
"use strict";

const ACTIVE_STORE="somx.strategy.active.v1";
const SIG="nani-ndx100-nk225-v1";
const clone=x=>JSON.parse(JSON.stringify(x));
const nani={
  mode:"nani",label:"Nani",universe:"nasdaq100+nikkei225",
  holdings:325,entryRank:325,exitRank:325,rebalanceMonths:6,
  factor:"relativeMomentum6_1",weighting:"floatCapMomentumCubeCap20",
  factors:{momentum:{enabled:true,weight:100,lookback:6,skip:1},marketCap:{enabled:true,weight:100}},
  cap:0.20,filters:{baseCurrency:"USD",floatAdjusted:true}
};
const base=globalThis.SOMXStrategy;
if(!base)return;

function activeStored(){try{return localStorage.getItem(ACTIVE_STORE)}catch{return null}}
function monthsBetween(a,b){const[ay,am]=String(a).split("-").map(Number),[by,bm]=String(b).split("-").map(Number);return(by-ay)*12+(bm-am)}
function key(kind){return`somx.${kind}.v2.${SIG}`}
function currentMonth(){const p=new Intl.DateTimeFormat("en-CA",{timeZone:"America/New_York",year:"numeric",month:"2-digit"}).formatToParts(new Date());return`${p.find(x=>x.type==="year").value}-${p.find(x=>x.type==="month").value}`}
async function fetchNaniSnapshot(){
  const r=await fetch(`./nani-latest.json?v=${Date.now()}`,{cache:"no-store"});
  if(!r.ok)throw new Error(`Nani snapshot HTTP ${r.status}`);
  const j=await r.json();
  if(j.ready===false)throw new Error(j.dataStatus?.message||"Nani 데이터 검증이 아직 완료되지 않았습니다.");
  if(!Array.isArray(j.rows)||j.rows.length<300)throw new Error(`Nani snapshot ${j.rows?.length||0}/300+`);
  const rows=j.rows.map(x=>({
    ...x,
    ticker:String(x.ticker||"").trim().toUpperCase(),
    weight:Number(x.weight),
    momentum:x.momentum==null?null:Number(x.momentum),
    relativeMomentum:x.relativeMomentum==null?null:Number(x.relativeMomentum),
    floatCap:x.floatCap==null?null:Number(x.floatCap),
    marketCap:x.marketCap==null?null:Number(x.marketCap)
  }));
  if(rows.some(x=>!x.ticker||!Number.isFinite(x.weight)||x.weight<=0))throw new Error("Nani 목표비중 데이터가 올바르지 않습니다.");
  const sum=rows.reduce((a,x)=>a+x.weight,0);
  if(Math.abs(sum-1)>1e-6)throw new Error(`Nani 비중합 오류 ${sum}`);
  return{...j,displayStrategy:"Nani",holdings:rows.length,rows};
}
async function seedNani(){
  const j=await fetchNaniSnapshot(),ym=currentMonth(),allocationYm=/^\d{4}-\d{2}$/.test(String(j.allocationMonth||""))?String(j.allocationMonth):ym;
  const holdings=j.rows.map(x=>x.ticker),targetWeights=Object.fromEntries(j.rows.map(x=>[x.ticker,x.weight]));
  const st={rebalanceMonth:allocationYm,strategyAnchor:allocationYm,initialized:true,holdings,statuses:Object.fromEntries(holdings.map(s=>[s,"IN"])),targetWeights,basePrices:{},updatedAt:new Date().toISOString(),snapshotAt:j.generatedAt||null,snapshotAllocationMonth:allocationYm,serverSnapshot:true};
  localStorage.setItem(key("state"),JSON.stringify(st));
  localStorage.setItem(key("holdings"),JSON.stringify({[allocationYm]:holdings,[ym]:holdings}));
  localStorage.setItem(key("weights"),JSON.stringify({[allocationYm]:targetWeights,[ym]:targetWeights}));
  localStorage.setItem(ACTIVE_STORE,"nani");
  return j;
}

const original={
  getConfig:base.getConfig.bind(base),getMode:base.getMode.bind(base),setMode:base.setMode.bind(base),
  signature:base.signature.bind(base),isTradeMonth:base.isTradeMonth.bind(base),fetchSnapshot:base.fetchSnapshot.bind(base),seedSnapshotContext:base.seedSnapshotContext.bind(base)
};
base.getMode=()=>activeStored()==="nani"?"nani":original.getMode();
base.getConfig=()=>base.getMode()==="nani"?clone(nani):original.getConfig();
base.setMode=m=>{if(m==="nani"){localStorage.setItem(ACTIVE_STORE,"nani");return}original.setMode(m)};
base.signature=c=>c?.mode==="nani"?SIG:original.signature(c);
base.isTradeMonth=(anchor,target,c)=>c?.mode==="nani"?monthsBetween(anchor,target)%6===0:original.isTradeMonth(anchor,target,c);
base.fetchSnapshot=c=>c?.mode==="nani"?fetchNaniSnapshot():original.fetchSnapshot(c);
base.seedSnapshotContext=c=>c?.mode==="nani"?seedNani():original.seedSnapshotContext(c);
base.presets={...(base.presets||{}),nani:clone(nani)};

function fmt(v){return Number.isFinite(Number(v))?`${Number(v)>=0?"+":""}${Number(v).toFixed(2)}%`:"--"}
function readHistory(){try{return JSON.parse(localStorage.getItem(key("history"))||"{}")||{}}catch{return{}}}
function colorFor(i){const h=(i*137.508+212)%360;return`hsl(${h} 58% 48%)`}
function polar(cx,cy,r,a){const rad=(a-90)*Math.PI/180;return[cx+r*Math.cos(rad),cy+r*Math.sin(rad)]}
function arcPath(cx,cy,ro,ri,a0,a1){const[x0,y0]=polar(cx,cy,ro,a0),[x1,y1]=polar(cx,cy,ro,a1),[xi1,yi1]=polar(cx,cy,ri,a1),[xi0,yi0]=polar(cx,cy,ri,a0),large=a1-a0>180?1:0;return`M ${x0} ${y0} A ${ro} ${ro} 0 ${large} 1 ${x1} ${y1} L ${xi1} ${yi1} A ${ri} ${ri} 0 ${large} 0 ${xi0} ${yi0} Z`}
function drawNaniDonut(rows){
  const svg=document.getElementById("donut"),NS="http://www.w3.org/2000/svg";if(!svg)return;svg.innerHTML="";let angle=-30;
  for(const[i,p]of rows.entries()){
    const sweep=Number(p.weight)*360,a0=angle,a1=angle+sweep;angle=a1;
    const path=document.createElementNS(NS,"path");path.setAttribute("d",arcPath(450,450,405,130,a0,a1));path.setAttribute("fill",colorFor(i));path.setAttribute("stroke","#fff");path.setAttribute("stroke-width","1");svg.appendChild(path);
    if(sweep>18){const mid=(a0+a1)/2,[tx,ty]=polar(450,450,280,mid),t=document.createElementNS(NS,"text");t.setAttribute("x",String(tx));t.setAttribute("y",String(ty));t.setAttribute("text-anchor","middle");t.setAttribute("fill","#fff");t.setAttribute("font-size","24");t.setAttribute("font-weight","900");t.textContent=p.ticker;svg.appendChild(t)}
  }
  const c=document.createElementNS(NS,"circle");c.setAttribute("cx","450");c.setAttribute("cy","450");c.setAttribute("r","116");c.setAttribute("fill","var(--card,#fff)");svg.appendChild(c);
  const t=document.createElementNS(NS,"text");t.setAttribute("x","450");t.setAttribute("y","460");t.setAttribute("text-anchor","middle");t.setAttribute("font-size","34");t.setAttribute("font-weight","900");t.setAttribute("fill","currentColor");t.textContent="Nani";svg.appendChild(t);
}
function setMetric(id,v){const el=document.getElementById(id);if(!el)return;el.textContent=fmt(v);el.classList.toggle("neg",Number.isFinite(Number(v))&&Number(v)<0)}
function renderNaniMain(j){
  const rows=[...j.rows].sort((a,b)=>b.weight-a.weight),hist=readHistory(),lastMonth=Object.keys(hist).sort().at(-1),h=lastMonth?hist[lastMonth]:null,retBy=new Map((h?.rows||[]).map(x=>[x.ticker,Number(x.ret)]));
  document.body.dataset.strategy="nani";
  const tbody=document.getElementById("holdings-body");if(tbody)tbody.innerHTML=rows.map((r,i)=>`<tr><td><div class="stock"><div class="logo" style="color:${colorFor(i)}">${r.ticker}</div><div>${r.ticker}${r.country?`<small style="display:block;opacity:.55">${r.country}</small>`:""}</div></div></td><td class="status-wrap"><span class="badge hold">HOLD</span></td><td class="ret ${retBy.get(r.ticker)<0?"neg":""}">${fmt(retBy.get(r.ticker))}</td></tr>`).join("");
  drawNaniDonut(rows);
  const valid=(h?.rows||[]).filter(x=>Number.isFinite(Number(x.ret))),up=valid.filter(x=>Number(x.ret)>0).length,best=valid.length?[...valid].sort((a,b)=>Number(b.ret)-Number(a.ret))[0]:null,worst=valid.length?[...valid].sort((a,b)=>Number(a.ret)-Number(b.ret))[0]:null;
  setMetric("portfolio-return",h?.port);const upEl=document.getElementById("up-count");if(upEl)upEl.textContent=valid.length?`${up} / ${valid.length}`:"--";
  const bt=document.getElementById("best-ticker");if(bt)bt.textContent=best?.ticker||"--";setMetric("best-return",best?.ret);const wt=document.getElementById("worst-ticker");if(wt)wt.textContent=worst?.ticker||"--";setMetric("worst-return",worst?.ret);
  const title=document.querySelector(".summary-title > span");if(title)title.textContent=lastMonth?`월간 수익률 · ${lastMonth}`:"월간 수익률";
  const panel=document.getElementById("benchmark-panel");if(panel)panel.style.display="none";
}
function restoreBaseVisuals(){const panel=document.getElementById("benchmark-panel");if(panel)panel.style.display="";const title=document.querySelector(".summary-title > span");if(title)title.textContent="월간 수익률"}
function renderNaniHistory(){
  const modal=document.getElementById("history-modal"),list=document.getElementById("history-list"),heading=document.querySelector("#history-modal h2"),status=document.getElementById("history-status");if(!modal||!list)return;
  if(heading)heading.textContent="Nani 월말 수익률 기록";if(status)status.textContent="서버 검증 월말 기록 · 현지 거래일 기준";
  const hist=readHistory(),months=Object.keys(hist).sort().reverse();
  if(!months.length)list.innerHTML='<div class="history-empty">Nani 월말 기록이 아직 없습니다.</div>';
  else list.innerHTML=months.map(month=>{const h=hist[month],details=(h.rows||[]).map(x=>`<div class="history-stock"><span>${x.ticker}<small class="history-membership">${((x.weight||0)*100).toFixed(2)}%</small></span><strong class="${x.ret<0?"neg":""}">${fmt(x.ret)}</strong></div>`).join("");return`<details class="history-month"><summary><span class="history-month-label">${month}</span><span class="history-date">${h.lastDate||""}</span><strong class="history-port ${h.port<0?"neg":""}">${fmt(h.port)}</strong></summary><div class="history-details">${details}</div></details>`}).join("");
  modal.classList.add("show");
}
async function activateNani(){const j=await seedNani();renderNaniMain(j);window.dispatchEvent(new Event("somx:strategy-active"));window.dispatchEvent(new Event("somx:historychange"));return j}

function initUI(){
  const root=document.getElementById("strategy-settings-root");if(!root)return;
  const tabs=root.querySelector(".strategy-tabs"),preview=root.querySelector("#strategy-preview"),rule=root.querySelector("#strategy-rule"),ruleTitle=root.querySelector("#strategy-rule-title"),apply=root.querySelector("#strategy-apply-btn"),previewBtn=root.querySelector("#strategy-preview-btn"),current=root.querySelector("#strategy-current");
  if(!tabs||tabs.querySelector('[data-mode="nani"]'))return;
  const b=document.createElement("button");b.type="button";b.dataset.mode="nani";b.textContent="Nani";tabs.appendChild(b);
  let selected=activeStored()==="nani";
  const selectNani=()=>{selected=true;tabs.querySelectorAll("button").forEach(x=>x.classList.toggle("active",x===b));if(ruleTitle)ruleTitle.textContent="Nani · Locked";if(rule)rule.textContent="Nasdaq-100 ∪ Nikkei 225 · 6-1 상대모멘텀 · Float-adjusted Market Cap × 상대모멘텀³ · USD 환산 · 단일종목 20% cap · 6개월 리밸런싱";if(preview)preview.textContent="Preview를 누르면 최신 Nani 목표비중을 불러옵니다."};
  b.addEventListener("click",selectNani);
  tabs.querySelectorAll('button:not([data-mode="nani"])').forEach(x=>x.addEventListener("click",()=>{selected=false}));
  previewBtn?.addEventListener("click",async e=>{if(!selected)return;e.preventDefault();e.stopImmediatePropagation();if(preview)preview.textContent="최신 Nani 스냅샷을 불러오는 중…";try{const j=await fetchNaniSnapshot(),rows=[...j.rows].sort((a,b)=>b.weight-a.weight);if(preview)preview.innerHTML=`<div class="preview-row"><span>Allocation</span><strong>${j.allocationMonth||"--"}</strong><em>${rows.length}종목</em></div>`+rows.slice(0,20).map((r,i)=>`<div class="preview-row"><span>${i+1}</span><strong>${r.ticker}</strong><em>${(r.weight*100).toFixed(2)}%</em></div>`).join("")}catch(err){if(preview)preview.textContent=err.message||"Nani Preview 실패"}},true);
  apply?.addEventListener("click",async e=>{if(!selected)return;e.preventDefault();e.stopImmediatePropagation();apply.disabled=true;apply.textContent="Applying…";try{const j=await activateNani();if(preview)preview.textContent=`Nani 적용 · ${j.rows.length}종목 · 반기 리밸런싱`;if(current)current.textContent="현재 · Nani"}catch(err){if(preview)preview.textContent=err.message||"Nani 적용 실패"}finally{apply.disabled=false;apply.textContent="Apply Strategy"}},true);
  if(selected){selectNani();if(current)current.textContent="현재 · Nani";fetchNaniSnapshot().then(renderNaniMain).catch(()=>{})}
}

document.getElementById("historyBtn")?.addEventListener("click",e=>{if(activeStored()!=="nani")return;e.preventDefault();e.stopImmediatePropagation();renderNaniHistory()},true);
window.addEventListener("somx:nani-history-synced",()=>{if(activeStored()!=="nani")return;fetchNaniSnapshot().then(renderNaniMain).catch(()=>{})});
window.addEventListener("somx:strategy-active",()=>{if(activeStored()!=="nani")restoreBaseVisuals()});
if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",initUI);else initUI();

globalThis.NaniStrategy={config:clone(nani),signature:SIG,fetchSnapshot:fetchNaniSnapshot,seed:seedNani,activate:activateNani,renderHistory:renderNaniHistory};
})();