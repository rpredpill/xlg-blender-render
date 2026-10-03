(()=>{
"use strict";

const ACTIVE_STORE="somx.strategy.active.v1";
const SIG="nani-nk100-ndx100-50-50-v3";
const clone=x=>JSON.parse(JSON.stringify(x));
const nani={
  mode:"nani",label:"Nani",universe:"nikkei100+nasdaq100",
  holdings:null,entryRank:null,exitRank:null,rebalanceMonths:null,
  factor:"relativeMomentum6_1",weighting:"floatCapMomentumCubeCountry5050",
  factors:{momentum:{enabled:true,weight:100,lookback:6,skip:1},marketCap:{enabled:true,weight:100}},
  cap:null,filters:{baseCurrency:"USD",floatAdjusted:true,japanBucket:.5,usBucket:.5},
  cadence:"serverSnapshotOnly"
};
const base=globalThis.SOMXStrategy;
if(!base)return;

function activeStored(){try{return localStorage.getItem(ACTIVE_STORE)}catch{return null}}
function key(kind){return`somx.${kind}.v3.${SIG}`}
function currentMonth(){const p=new Intl.DateTimeFormat("en-CA",{timeZone:"America/New_York",year:"numeric",month:"2-digit"}).formatToParts(new Date());return`${p.find(x=>x.type==="year").value}-${p.find(x=>x.type==="month").value}`}

async function fetchNaniSnapshot(){
  const r=await fetch(`./nani-latest.json?v=${Date.now()}`,{cache:"no-store"});
  if(!r.ok)throw new Error(`Nani snapshot HTTP ${r.status}`);
  const j=await r.json();
  if(j.ready!==true||j.version!=="3.0")throw new Error("Nani v3 데이터가 아직 준비되지 않았습니다.");
  if(!Array.isArray(j.rows)||j.rows.length<195)throw new Error(`Nani snapshot ${j.rows?.length||0}/195+`);
  const rows=j.rows.map(x=>({...x,ticker:String(x.ticker||"").trim().toUpperCase(),weight:Number(x.weight),momentum:x.momentum==null?null:Number(x.momentum),relativeMomentum:x.relativeMomentum==null?null:Number(x.relativeMomentum),floatCap:x.floatCap==null?null:Number(x.floatCap)}));
  if(rows.some(x=>!x.ticker||!Number.isFinite(x.weight)||x.weight<=0))throw new Error("Nani 목표비중 데이터가 올바르지 않습니다.");
  const sum=rows.reduce((a,x)=>a+x.weight,0),jp=rows.filter(x=>x.country==="JP").reduce((a,x)=>a+x.weight,0),us=rows.filter(x=>x.country==="US").reduce((a,x)=>a+x.weight,0);
  if(Math.abs(sum-1)>1e-6||Math.abs(jp-.5)>1e-6||Math.abs(us-.5)>1e-6)throw new Error(`Nani 비중합 오류 total=${sum} JP=${jp} US=${us}`);
  return{...j,displayStrategy:"Nani",holdings:rows.length,rows};
}

async function seedNani(){
  const j=await fetchNaniSnapshot(),ym=currentMonth(),allocationYm=/^\d{4}-\d{2}$/.test(String(j.allocationMonth||""))?String(j.allocationMonth):ym;
  const holdings=j.rows.map(x=>x.ticker),targetWeights=Object.fromEntries(j.rows.map(x=>[x.ticker,x.weight]));
  const st={rebalanceMonth:null,strategyAnchor:null,initialized:true,holdings,statuses:Object.fromEntries(holdings.map(s=>[s,"IN"])),targetWeights,basePrices:{},updatedAt:new Date().toISOString(),snapshotAt:j.generatedAt||null,snapshotAllocationMonth:allocationYm,serverSnapshot:true};
  localStorage.setItem(key("state"),JSON.stringify(st));
  localStorage.setItem(key("holdings"),JSON.stringify({[allocationYm]:holdings,[ym]:holdings}));
  localStorage.setItem(key("weights"),JSON.stringify({[allocationYm]:targetWeights,[ym]:targetWeights}));
  localStorage.setItem(ACTIVE_STORE,"nani");
  return j;
}

const original={getConfig:base.getConfig.bind(base),getMode:base.getMode.bind(base),setMode:base.setMode.bind(base),signature:base.signature.bind(base),isTradeMonth:base.isTradeMonth.bind(base),fetchSnapshot:base.fetchSnapshot.bind(base),seedSnapshotContext:base.seedSnapshotContext.bind(base)};
base.getMode=()=>activeStored()==="nani"?"nani":original.getMode();
base.getConfig=()=>base.getMode()==="nani"?clone(nani):original.getConfig();
base.setMode=m=>{if(m==="nani"){localStorage.setItem(ACTIVE_STORE,"nani");return}original.setMode(m)};
base.signature=c=>c?.mode==="nani"?SIG:original.signature(c);
base.isTradeMonth=(anchor,target,c)=>c?.mode==="nani"?false:original.isTradeMonth(anchor,target,c);
base.fetchSnapshot=c=>c?.mode==="nani"?fetchNaniSnapshot():original.fetchSnapshot(c);
base.seedSnapshotContext=c=>c?.mode==="nani"?seedNani():original.seedSnapshotContext(c);
base.presets={...(base.presets||{}),nani:clone(nani)};

function fmt(v){return Number.isFinite(Number(v))?`${Number(v)>=0?"+":""}${Number(v).toFixed(2)}%`:"--"}
function readHistory(){try{return JSON.parse(localStorage.getItem(key("history"))||"{}")||{}}catch{return{}}}
function renderHistory(){
  const modal=document.getElementById("history-modal"),list=document.getElementById("history-list"),heading=document.querySelector("#history-modal h2"),status=document.getElementById("history-status");
  if(!modal||!list)return;
  if(heading)heading.textContent="Nani 월말 수익률 기록";
  if(status)status.textContent="실시간 저장된 당시 목표비중만 기록 · 역산 백필 없음";
  const hist=readHistory(),months=Object.keys(hist).filter(m=>Number.isFinite(Number(hist[m]?.port))).sort().reverse();
  if(!months.length)list.innerHTML='<div class="history-empty">Nani v3는 역산 기록을 사용하지 않습니다. 검증된 월말 기록이 쌓이면 여기에 표시됩니다.</div>';
  else list.innerHTML=months.map(month=>{const h=hist[month],details=(h.rows||[]).map(x=>`<div class="history-stock"><span>${x.ticker}<small class="history-membership">${((x.weight||0)*100).toFixed(2)}%</small></span><strong class="${x.ret<0?"neg":""}">${fmt(x.ret)}</strong></div>`).join("");return`<details class="history-month"><summary><span class="history-month-label">${month}</span><span class="history-date">${h.lastDate||""}</span><strong class="history-port ${h.port<0?"neg":""}">${fmt(h.port)}</strong></summary><div class="history-details">${details}</div></details>`}).join("");
  modal.classList.add("show");
}

async function activateNani(){const j=await seedNani();window.dispatchEvent(new Event("somx:strategy-active"));window.dispatchEvent(new Event("somx:historychange"));return j}

function initUI(){
  const root=document.getElementById("strategy-settings-root");if(!root)return;
  const tabs=root.querySelector(".strategy-tabs"),preview=root.querySelector("#strategy-preview"),rule=root.querySelector("#strategy-rule"),ruleTitle=root.querySelector("#strategy-rule-title"),apply=root.querySelector("#strategy-apply-btn"),previewBtn=root.querySelector("#strategy-preview-btn"),current=root.querySelector("#strategy-current");
  if(!tabs||tabs.querySelector('[data-mode="nani"]'))return;
  const b=document.createElement("button");b.type="button";b.dataset.mode="nani";b.textContent="Nani";tabs.appendChild(b);
  let selected=activeStored()==="nani";
  const selectNani=()=>{selected=true;tabs.querySelectorAll("button").forEach(x=>x.classList.toggle("active",x===b));if(ruleTitle)ruleTitle.textContent="Nani · Core";if(rule)rule.textContent="Nikkei 100 ∪ Nasdaq-100 · Nikkei 100 = Nikkei 225 유동성시총 상위 100 · 일본 50% : 미국 50% · 6-1 상대모멘텀 · Float-adjusted Market Cap × 상대모멘텀³ · cap 없음";if(preview)preview.textContent="Preview를 누르면 최신 Nani v3 목표비중을 불러옵니다."};
  b.addEventListener("click",selectNani);
  tabs.querySelectorAll('button:not([data-mode="nani"])').forEach(x=>x.addEventListener("click",()=>{selected=false}));
  previewBtn?.addEventListener("click",async e=>{if(!selected)return;e.preventDefault();e.stopImmediatePropagation();if(preview)preview.textContent="최신 Nani v3 스냅샷을 불러오는 중…";try{const j=await fetchNaniSnapshot(),rows=[...j.rows].sort((a,b)=>b.weight-a.weight);if(preview)preview.innerHTML=`<div class="preview-row"><span>Signal</span><strong>${j.signalMonth||"--"}</strong><em>${rows.length}종목 · JP 50% / US 50%</em></div>`+rows.slice(0,20).map((r,i)=>`<div class="preview-row"><span>${i+1}</span><strong>${r.ticker}</strong><em>${(r.weight*100).toFixed(2)}%</em></div>`).join("")}catch(err){if(preview)preview.textContent=err.message||"Nani Preview 실패"}},true);
  apply?.addEventListener("click",async e=>{if(!selected)return;e.preventDefault();e.stopImmediatePropagation();apply.disabled=true;apply.textContent="Applying…";try{const j=await activateNani();if(preview)preview.textContent=`Nani v3 적용 · ${j.rows.length}종목 · JP 50% / US 50%`;if(current)current.textContent="현재 · Nani"}catch(err){if(preview)preview.textContent=err.message||"Nani 적용 실패"}finally{apply.disabled=false;apply.textContent="Apply Strategy"}},true);
  if(selected){selectNani();if(current)current.textContent="현재 · Nani"}
}

document.getElementById("historyBtn")?.addEventListener("click",e=>{if(activeStored()!=="nani")return;e.preventDefault();e.stopImmediatePropagation();renderHistory()},true);
if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",initUI);else initUI();

globalThis.NaniStrategy={config:clone(nani),signature:SIG,fetchSnapshot:fetchNaniSnapshot,seed:seedNani,activate:activateNani,renderHistory};
})();