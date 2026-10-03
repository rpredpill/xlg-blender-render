(()=>{
"use strict";

const ACTIVE_STORE="somx.strategy.active.v1";
const clone=x=>JSON.parse(JSON.stringify(x));

const snpi={
  mode:"snpi",label:"SNPI",universe:"sp500",
  holdings:500,entryRank:500,exitRank:500,rebalanceMonths:1,
  factor:"snpiScore",weighting:"sqrtCapMomentumCubeCap20",
  factors:{momentum:{enabled:true,weight:100,lookback:6,skip:1},marketCap:{enabled:true,weight:100}},
  cap:0.20,filters:{}
};
const snpy={
  mode:"snpy",label:"SNPY",universe:"sp500",
  holdings:100,entryRank:100,exitRank:100,rebalanceMonths:1,
  factor:"snpiScoreTop100",weighting:"sqrtCapMomentumCubeCap20",
  factors:{momentum:{enabled:true,weight:100,lookback:6,skip:1},marketCap:{enabled:true,weight:100}},
  cap:0.20,filters:{}
};
const ququ={
  mode:"ququ",label:"QUQU v2",universe:"nasdaqCompositePIT",
  holdings:328,entryRank:328,exitRank:328,rebalanceMonths:1,
  factor:"ququV2",weighting:"sqrtFloatCapWinsorMomentumCubeCap20",
  factors:{momentum:{enabled:true,weight:100,lookback:6,skip:1},marketCap:{enabled:true,weight:100}},
  cap:0.20,filters:{floatCapTopPct:10,liquidityBottomPct:10,volLookback:63,volExponent:0.25,winsorZ:3}
};
const quu={
  mode:"quu",label:"QUU",universe:"nasdaq100",
  holdings:100,entryRank:100,exitRank:100,rebalanceMonths:1,
  factor:"quuLeadershipRollover",weighting:"leadershipRollover",
  factors:{momentum:{enabled:true,weight:100,lookback:6,skip:1},marketCap:{enabled:true,weight:100}},
  cap:0.20,filters:{}
};

const modes=["snpi","snpy","ququ","quu"];
function loadMode(){
  try{
    const old=localStorage.getItem(ACTIVE_STORE);
    if(old==="core"||old==="custom")return"snpi";
    return modes.includes(old)?old:"snpi";
  }catch{return"snpi"}
}
function saveMode(m){try{localStorage.setItem(ACTIVE_STORE,modes.includes(m)?m:"snpi")}catch{}}
let activeMode=loadMode();
function config(mode=activeMode){return clone(mode==="quu"?quu:mode==="ququ"?ququ:mode==="snpy"?snpy:snpi)}
function signature(c=config()){
  if(c.mode==="snpi")return"snpi-sp500-v1";
  if(c.mode==="snpy")return"snpy-sp500-top100-v1";
  if(c.mode==="ququ")return"ququ-nasdaq-composite-v2";
  if(c.mode==="quu")return"quu-ndx100-rollover-v1";
  return"snpi-sp500-v1";
}
function monthsBetween(a,b){const[ay,am]=a.split("-").map(Number),[by,bm]=b.split("-").map(Number);return(by-ay)*12+(bm-am)}
function isTradeMonth(anchor,target,c=config()){return monthsBetween(anchor,target)%Math.max(1,Number(c.rebalanceMonths)||1)===0}
function requiredCalendarDays(c=config()){if(c.factor==="momentum")return Math.min(800,Math.max(180,(Number(c.factors?.momentum?.lookback)||6)*32+40));return 180}
const ret=(a,b)=>Number.isFinite(a)&&Number.isFinite(b)&&b>0?a/b-1:NaN;
function eom(signalDate,monthsBack){const d=new Date(signalDate);d.setUTCDate(1);d.setUTCMonth(d.getUTCMonth()-monthsBack+1);d.setUTCDate(0);d.setUTCHours(23,59,59,999);return+d}
function lastClose(bars,ts){let x=NaN;for(const b of bars){if(+new Date(b.t)<=ts)x=+b.c;else break}return x}
function capValue(source,s){return Number(source?.get?.(s)??source?.[s])}
function score({universe,barsBySymbol,signalDate,marketCapBySymbol,config:c=config()}){
  if(c.factor==="marketCap"){
    if(!marketCapBySymbol)throw new Error("시총 데이터 소스를 불러오지 못했습니다.");
    return universe.map(s=>{const v=capValue(marketCapBySymbol,s);return{s,score:v,raw:{marketCap:v}}})
      .filter(x=>Number.isFinite(x.score)&&x.score>0).sort((a,b)=>b.score-a.score);
  }
  const mom=c.factors?.momentum||{},skip=Math.max(1,Number(mom.skip)||1),look=Math.max(skip+1,Number(mom.lookback)||6),recentTs=eom(signalDate,skip-1),earlyTs=eom(signalDate,look-1),rows=[];
  for(const s of universe){const bars=(barsBySymbol.get(s)||[]).filter(b=>+new Date(b.t)<=+new Date(signalDate)).sort((a,b)=>+new Date(a.t)-+new Date(b.t));const a=lastClose(bars,recentTs),b=lastClose(bars,earlyTs),m=ret(a,b);if(Number.isFinite(m))rows.push({s,score:m,raw:{momentum:m}})}
  return rows.sort((a,b)=>b.score-a.score);
}
function weightsFor({holdings,marketCapBySymbol,config:c=config()}){
  const names=[...holdings];if(!names.length)return{};
  if(c.weighting==="marketCap"){
    if(!marketCapBySymbol)throw new Error("시총 비중 계산용 데이터가 없습니다.");
    const raw=Object.fromEntries(names.map(s=>[s,capValue(marketCapBySymbol,s)]));
    if(names.some(s=>!Number.isFinite(raw[s])||raw[s]<=0))throw new Error("선택 종목 일부의 시총 데이터가 없습니다.");
    const sum=names.reduce((a,s)=>a+raw[s],0);return Object.fromEntries(names.map(s=>[s,raw[s]/sum]));
  }
  const w=1/names.length;return Object.fromEntries(names.map(s=>[s,w]));
}
function capAndRedistribute(raw,cap=0.20){const free=new Set(Object.keys(raw)),out={};let remaining=1;while(free.size){let total=0;for(const s of free)total+=raw[s];if(!(total>0))throw new Error("raw weight sum <= 0");const over=[...free].filter(s=>remaining*raw[s]/total>cap+1e-12);if(!over.length){for(const s of free)out[s]=remaining*raw[s]/total;break}for(const s of over){out[s]=cap;remaining-=cap;free.delete(s)}}return out}
function deriveSnpyRows(sourceRows){const ranked=(sourceRows||[]).map(x=>{const ticker=String(x.ticker||"").trim().toUpperCase(),marketCap=Number(x.marketCap),momentum=Number(x.momentum),gross=1+momentum,stored=Number(x.rawWeightScore);const raw=Number.isFinite(stored)&&stored>0?stored:(marketCap>0&&gross>0?Math.sqrt(marketCap)*(gross**3):NaN);return{ticker,marketCap,momentum,raw}}).filter(x=>x.ticker&&Number.isFinite(x.raw)&&x.raw>0).sort((a,b)=>b.raw-a.raw).slice(0,100);if(ranked.length!==100)throw new Error(`SNPY snapshot ${ranked.length}/100`);const raw=Object.fromEntries(ranked.map(x=>[x.ticker,x.raw])),weights=capAndRedistribute(raw,0.20);return ranked.map(x=>({ticker:x.ticker,weight:weights[x.ticker],momentum:x.momentum,marketCap:x.marketCap})).sort((a,b)=>b.weight-a.weight)}
function currentNYMonth(){const p=new Intl.DateTimeFormat("en-CA",{timeZone:"America/New_York",year:"numeric",month:"2-digit"}).formatToParts(new Date());return`${p.find(x=>x.type==="year").value}-${p.find(x=>x.type==="month").value}`}
function storageKey(kind,c){return `somx.${kind}.v2.${signature(c)}`}
async function fetchSnapshot(c){
  const mode=c.mode,name=mode==="snpi"?"SNPI":mode==="snpy"?"SNPY":mode==="quu"?"QUU":"QUQU";
  const file=mode==="quu"?"quu-latest.json":mode==="ququ"?"ququ-latest.json":"snpi-latest.json";
  const r=await fetch(`./${file}?v=${Date.now()}`,{cache:"no-store"});if(!r.ok)throw new Error(`${name} snapshot HTTP ${r.status}`);const j=await r.json();
  if(mode==="ququ"&&j.strategy!=="QUQU v2")throw new Error(`QUQU v2 snapshot expected, got ${j.strategy||"unknown"}`);
  let rows;if(mode==="snpy")rows=deriveSnpyRows(j.rows);else{const min=mode==="snpi"?495:mode==="quu"?1:100;if(!Array.isArray(j.rows)||j.rows.length<min)throw new Error(`${name} snapshot ${j.rows?.length||0}/${min}+`);rows=j.rows.map(x=>({ticker:String(x.ticker||"").trim().toUpperCase(),weight:Number(x.weight),momentum:x.momentum==null?null:Number(x.momentum),marketCap:x.marketCap==null?null:Number(x.marketCap)}))}
  if(rows.some(x=>!x.ticker||!Number.isFinite(x.weight)||x.weight<=0))throw new Error(`${name} 목표비중 데이터가 올바르지 않습니다.`);const sum=rows.reduce((a,x)=>a+x.weight,0);if(!(sum>0)||Math.abs(sum-1)>1e-6)throw new Error(`${name} 비중합 오류 ${sum}`);return{...j,strategy:name,holdings:rows.length,rows};
}
async function seedSnapshotContext(c){const j=await fetchSnapshot(c),ym=currentNYMonth(),holdings=j.rows.map(x=>x.ticker),targetWeights=Object.fromEntries(j.rows.map(x=>[x.ticker,x.weight]));c.holdings=holdings.length;c.entryRank=holdings.length;c.exitRank=holdings.length;const st={rebalanceMonth:ym,strategyAnchor:ym,initialized:true,holdings,statuses:Object.fromEntries(holdings.map(s=>[s,"IN"])),targetWeights,basePrices:{},updatedAt:new Date().toISOString(),snapshotAt:j.generatedAt||null};let hh={},wh={};try{hh=JSON.parse(localStorage.getItem(storageKey("holdings",c))||"{}")||{}}catch{}try{wh=JSON.parse(localStorage.getItem(storageKey("weights",c))||"{}")||{}}catch{}hh[ym]=[...holdings];wh[ym]={...targetWeights};localStorage.setItem(storageKey("state",c),JSON.stringify(st));localStorage.setItem(storageKey("holdings",c),JSON.stringify(hh));localStorage.setItem(storageKey("weights",c),JSON.stringify(wh));return j}

globalThis.SOMXStrategy={getConfig:()=>config(),getMode:()=>activeMode,setMode:m=>{activeMode=modes.includes(m)?m:"snpi";saveMode(activeMode)},signature,isTradeMonth,requiredCalendarDays,score,weightsFor,fetchSnapshot,seedSnapshotContext,presets:{snpi:clone(snpi),core:clone(snpi),snpy:clone(snpy),ququ:clone(ququ),quu:clone(quu)}};

function initUI(){
  const root=document.getElementById("strategy-settings-root");if(!root)return;
  root.innerHTML=`<div class="strategy-settings-head"><div><strong>Strategy</strong><span id="strategy-current"></span></div><small>고정 전략 선택</small></div><div class="strategy-tabs"><button data-mode="snpi" type="button">SNPI</button><button data-mode="snpy" type="button">SNPY</button><button data-mode="ququ" type="button">QUQU</button><button data-mode="quu" type="button">QUU</button></div><div class="strategy-body"><div id="core-lock-note" class="strategy-section"><div class="strategy-section-title"></div><div class="core-rule"></div></div><section class="strategy-section preview-section"><div class="strategy-section-title">Current signal preview</div><div id="strategy-preview" class="strategy-preview">Preview를 누르면 현재 목표비중을 불러옵니다.</div></section></div><div class="strategy-actions"><button id="strategy-preview-btn" class="strategy-btn secondary" type="button">Preview</button><button id="strategy-apply-btn" class="strategy-btn primary" type="button">Apply Strategy</button></div>`;
  let editMode=activeMode;
  const q=s=>root.querySelector(s),qa=s=>[...root.querySelectorAll(s)];
  function fill(c){qa(".strategy-tabs button").forEach(b=>b.classList.toggle("active",b.dataset.mode===editMode));const title=q("#core-lock-note .strategy-section-title"),rule=q("#core-lock-note .core-rule");title.textContent=c.label;rule.textContent=c.mode==="ququ"?"Nasdaq Composite PIT · FloatCap 상위 10% · 6-1M momentum · volatility/liquidity overlay · 월간 리밸런싱":c.mode==="quu"?"Nasdaq-100 · Leadership Rollover · 월간 리밸런싱":c.mode==="snpy"?"S&P 500 · Top 100 · sqrt(MarketCap) × Momentum³ · 20% cap":"S&P 500 · 전체 · sqrt(MarketCap) × Momentum³ · 20% cap";updateCurrent()}
  function updateCurrent(){const el=q("#strategy-current");if(el)el.textContent=`현재 · ${config().label}`}
  qa(".strategy-tabs button").forEach(b=>b.onclick=()=>{editMode=b.dataset.mode;fill(config(editMode))});
  q("#strategy-preview-btn").onclick=async()=>{const c=config(editMode),box=q("#strategy-preview");box.textContent=`최신 ${c.label} 스냅샷을 불러오는 중…`;try{const j=await fetchSnapshot(c),rows=[...j.rows].sort((a,b)=>b.weight-a.weight);box.innerHTML=rows.slice(0,20).map((r,i)=>`<div class="preview-row"><span>${i+1}</span><strong>${r.ticker}</strong><em>${(r.weight*100).toFixed(2)}%</em></div>`).join("")||"표시할 종목이 없습니다."}catch(e){box.textContent=e.message||"Preview 실패"}};
  q("#strategy-apply-btn").onclick=async()=>{const c=config(editMode),box=q("#strategy-preview");q("#strategy-apply-btn").textContent="Applying…";try{const j=await seedSnapshotContext(c);c.holdings=j.rows.length;c.entryRank=j.rows.length;c.exitRank=j.rows.length;activeMode=editMode;saveMode(activeMode);if(globalThis.SOMXLive?.applyStrategy)await globalThis.SOMXLive.applyStrategy(c);box.textContent=`${c.label} 최신 스냅샷 적용 · ${(j.generatedAt||"").slice(0,10)} · ${j.rows.length}종목`;updateCurrent()}catch(e){box.textContent=e.message||"전략 적용 실패"}finally{q("#strategy-apply-btn").textContent="Apply Strategy"}};
  updateCurrent();fill(config());window.addEventListener("somx:strategy-active",updateCurrent);
}
if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",initUI);else initUI();
})();
