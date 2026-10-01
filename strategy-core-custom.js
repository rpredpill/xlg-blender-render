(()=>{
"use strict";

const STORE="somx.strategy.builder.v5";
const ACTIVE_STORE="somx.strategy.active.v1";
const clone=x=>JSON.parse(JSON.stringify(x));

/* Core is immutable: canonical SOMX. */
const core={
  mode:"core",label:"SOMX Core",
  holdings:6,entryRank:6,exitRank:16,rebalanceMonths:1,
  factor:"momentum",
  weighting:"equal",
  factors:{
    momentum:{enabled:true,weight:100,lookback:6,skip:1},
    marketCap:{enabled:false,weight:0}
  },
  filters:{}
};

/* QUQU uses the server-built Nasdaq-100 monthly snapshot and can be applied to the live dashboard. */
const ququ={
  mode:"ququ",label:"QUQU",universe:"nasdaq100",
  holdings:100,entryRank:100,exitRank:100,rebalanceMonths:1,
  factor:"ququScore",weighting:"sqrtCapMomentumCubeCap20",
  factors:{momentum:{enabled:true,weight:100,lookback:6,skip:1},marketCap:{enabled:true,weight:100}},
  cap:0.20,filters:{}
};

const defaultCustom={
  mode:"custom",label:"Custom",
  holdings:6,entryRank:6,exitRank:16,rebalanceMonths:1,
  factor:"momentum",
  weighting:"equal",
  factors:{
    momentum:{enabled:true,weight:100,lookback:6,skip:1},
    marketCap:{enabled:false,weight:0}
  },
  filters:{}
};

function normalizeCustom(saved){
  const c=clone(defaultCustom);
  if(saved&&typeof saved==="object"){
    for(const k of ["holdings","entryRank","exitRank","rebalanceMonths","factor","weighting"]){if(saved[k]!=null)c[k]=saved[k]}
    if(saved.factors?.momentum)c.factors.momentum={...c.factors.momentum,...saved.factors.momentum};
  }
  if(!["momentum","marketCap"].includes(c.factor))c.factor="momentum";
  if(!["equal","marketCap"].includes(c.weighting))c.weighting="equal";
  c.factors.momentum.enabled=c.factor==="momentum";
  c.factors.momentum.weight=c.factor==="momentum"?100:0;
  c.factors.marketCap.enabled=c.factor==="marketCap";
  c.factors.marketCap.weight=c.factor==="marketCap"?100:0;
  c.mode="custom";c.label="Custom";c.filters={};
  return c;
}
function loadCustom(){
  try{
    const v5=JSON.parse(localStorage.getItem(STORE)||"null");if(v5)return normalizeCustom(v5);
    const old=JSON.parse(localStorage.getItem("somx.strategy.builder.v4")||"null")||JSON.parse(localStorage.getItem("somx.strategy.builder.v3")||"null")||JSON.parse(localStorage.getItem("somx.strategy.builder.v2")||"null");
    return normalizeCustom(old);
  }catch{return clone(defaultCustom)}
}
function saveCustom(c){try{localStorage.setItem(STORE,JSON.stringify(normalizeCustom(c)))}catch{}}
/* SOMV was retired. Core / QUQU / Custom are valid active strategies. */
function loadMode(){try{return ["core","ququ","custom"].includes(localStorage.getItem(ACTIVE_STORE))?localStorage.getItem(ACTIVE_STORE):"core"}catch{return"core"}}
function saveMode(m){try{localStorage.setItem(ACTIVE_STORE,["core","ququ","custom"].includes(m)?m:"core")}catch{}}

let custom=loadCustom(),activeMode=loadMode();
function config(mode=activeMode){return clone(mode==="custom"?custom:mode==="ququ"?ququ:core)}
function signature(c=config()){
  if(c.mode==="core")return"core";
  if(c.mode==="ququ")return"ququ-ndx100-v1";
  const raw=JSON.stringify(c);let h=2166136261;for(let i=0;i<raw.length;i++){h^=raw.charCodeAt(i);h=Math.imul(h,16777619)}
  return`custom-${(h>>>0).toString(36)}`;
}
function monthsBetween(a,b){const[ay,am]=a.split("-").map(Number),[by,bm]=b.split("-").map(Number);return(by-ay)*12+(bm-am)}
function isTradeMonth(anchor,target,c=config()){return monthsBetween(anchor,target)%Math.max(1,Number(c.rebalanceMonths)||1)===0}
function requiredCalendarDays(c=config()){if(c.factor==="momentum")return Math.min(800,Math.max(180,(Number(c.factors?.momentum?.lookback)||6)*32+40));return 180}
const ret=(a,b)=>Number.isFinite(a)&&Number.isFinite(b)&&b>0?a/b-1:NaN;
function eom(signalDate,monthsBack){const d=new Date(signalDate);d.setUTCDate(1);d.setUTCMonth(d.getUTCMonth()-monthsBack+1);d.setUTCDate(0);d.setUTCHours(23,59,59,999);return+d}
function lastClose(bars,ts){let x=NaN;for(const b of bars){if(+new Date(b.t)<=ts)x=+b.c;else break}return x}
function capValue(source,s){return Number(source?.get?.(s)??source?.[s])}

/* S&P 500 Core/Custom scorer. QUQU receives its weights from ququ-latest.json. */
function score({universe,barsBySymbol,signalDate,marketCapBySymbol,config:c=config()}){
  if(c.factor==="marketCap"){
    if(!marketCapBySymbol)throw new Error("시총 데이터 소스를 불러오지 못했습니다.");
    return universe.map(s=>{const v=capValue(marketCapBySymbol,s);return{s,score:v,raw:{marketCap:v}}}).filter(x=>Number.isFinite(x.score)&&x.score>0).sort((a,b)=>b.score-a.score);
  }
  const mom=c.factors?.momentum||{},skip=Math.max(1,Number(mom.skip)||1),look=Math.max(skip+1,Number(mom.lookback)||6);
  const recentTs=eom(signalDate,skip-1),earlyTs=eom(signalDate,look-1),rows=[];
  for(const s of universe){
    const bars=(barsBySymbol.get(s)||[]).filter(b=>+new Date(b.t)<=+new Date(signalDate)).sort((a,b)=>+new Date(a.t)-+new Date(b.t));
    const a=lastClose(bars,recentTs),b=lastClose(bars,earlyTs),m=ret(a,b);if(Number.isFinite(m))rows.push({s,score:m,raw:{momentum:m}})
  }
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

function currentNYMonth(){const p=new Intl.DateTimeFormat("en-CA",{timeZone:"America/New_York",year:"numeric",month:"2-digit"}).formatToParts(new Date());return `${p.find(x=>x.type==="year").value}-${p.find(x=>x.type==="month").value}`}
function ququStorageKey(kind){return `somx.${kind}.v2.${signature(ququ)}`}
async function fetchQuquSnapshot(){
  const r=await fetch(`./ququ-latest.json?v=${Date.now()}`,{cache:"no-store"});
  if(!r.ok)throw new Error(`QUQU snapshot HTTP ${r.status}`);
  const j=await r.json();
  if(!Array.isArray(j.rows)||j.rows.length!==100)throw new Error(`QUQU snapshot ${j.rows?.length||0}/100`);
  const rows=j.rows.map(x=>({ticker:String(x.ticker||"").trim().toUpperCase(),weight:Number(x.weight),momentum:Number(x.momentum),marketCap:Number(x.marketCap)}));
  if(rows.some(x=>!x.ticker||!Number.isFinite(x.weight)||x.weight<=0))throw new Error("QUQU 목표비중 데이터가 올바르지 않습니다.");
  const sum=rows.reduce((a,x)=>a+x.weight,0);if(!(sum>0)||Math.abs(sum-1)>1e-6)throw new Error(`QUQU 비중합 오류 ${sum}`);
  return{...j,rows};
}
async function seedQuquContext(){
  const j=await fetchQuquSnapshot(),ym=currentNYMonth(),holdings=j.rows.map(x=>x.ticker),targetWeights=Object.fromEntries(j.rows.map(x=>[x.ticker,x.weight]));
  const st={rebalanceMonth:ym,strategyAnchor:ym,initialized:true,holdings,statuses:Object.fromEntries(holdings.map(s=>[s,"IN"])),targetWeights,basePrices:{},updatedAt:new Date().toISOString(),ququSnapshotAt:j.generatedAt||null};
  let hh={},wh={};try{hh=JSON.parse(localStorage.getItem(ququStorageKey("holdings"))||"{}")||{}}catch{}try{wh=JSON.parse(localStorage.getItem(ququStorageKey("weights"))||"{}")||{}}catch{}
  hh[ym]=[...holdings];wh[ym]={...targetWeights};
  localStorage.setItem(ququStorageKey("state"),JSON.stringify(st));localStorage.setItem(ququStorageKey("holdings"),JSON.stringify(hh));localStorage.setItem(ququStorageKey("weights"),JSON.stringify(wh));
  return j;
}

globalThis.SOMXStrategy={
  getConfig:()=>config(),getMode:()=>activeMode,getCustom:()=>clone(custom),
  setCustom:c=>{custom=normalizeCustom(c);saveCustom(custom)},
  setMode:m=>{activeMode=["core","ququ","custom"].includes(m)?m:"core";saveMode(activeMode)},
  signature,isTradeMonth,requiredCalendarDays,score,weightsFor,
  presets:{core:clone(core),ququ:clone(ququ),custom:clone(defaultCustom)}
};

function initUI(){
  const root=document.getElementById("strategy-settings-root");if(!root)return;
  root.innerHTML=`
    <div class="strategy-settings-head"><div><strong>Strategy</strong><span id="strategy-current"></span></div><small>Core · QUQU는 고정 규칙, Custom은 직접 설정</small></div>
    <div class="strategy-tabs"><button data-mode="core" type="button">Core</button><button data-mode="ququ" type="button">QUQU</button><button data-mode="custom" type="button">Custom</button></div>
    <div class="strategy-body">
      <div id="core-lock-note" class="strategy-section" style="display:none"><div class="strategy-section-title">SOMX Core · Locked</div><div class="core-rule"></div></div>
      <section class="strategy-section"><div class="strategy-section-title">Portfolio</div><div class="strategy-grid"><label>Holdings<input id="st-holdings" type="number" min="3" max="100"></label><label>Entry Top<input id="st-entry" type="number" min="3" max="100"></label><label>Exit Rank<input id="st-exit" type="number" min="4" max="100"></label><label>Rebalance<select id="st-rebalance"><option value="1">Monthly</option><option value="2">Every 2 months</option><option value="3">Quarterly</option></select></label></div></section>
      <section class="strategy-section"><div class="strategy-section-title">Factor · 어떤 종목을 고를지</div><div class="factor-choice-grid">
        <label class="factor-choice" data-factor-choice="momentum"><input type="radio" name="st-factor" value="momentum"><span><strong>Momentum</strong><small>가격 모멘텀 순위</small></span></label>
        <label class="factor-choice" data-factor-choice="marketCap"><input type="radio" name="st-factor" value="marketCap"><span><strong>Market Cap</strong><small>시가총액 큰 순서</small></span></label>
      </div><div id="momentum-options" class="factor-options"><label>Lookback <input id="st-mom-look" type="number" min="2" max="24"></label><label>Skip <input id="st-mom-skip" type="number" min="1" max="3"></label></div></section>
      <section class="strategy-section"><div class="strategy-section-title">Weighting · 고른 종목을 얼마씩 살지</div><div class="weighting-row"><select id="st-weighting"><option value="equal">Equal Weight</option><option value="marketCap">Market Cap Weight</option></select><span id="weighting-note"></span></div><div id="marketcap-note" class="factor-data-note" hidden>Market Cap 관련 계산은 현재 S&P 500의 일일 시총 스냅샷을 사용합니다. 과거 PIT 시총 데이터가 아니라 현재/향후 운용용입니다.</div></section>
      <section class="strategy-section preview-section"><div class="strategy-section-title">Current signal preview</div><div id="strategy-preview" class="strategy-preview">Preview를 누르면 현재 신호 기준 랭킹을 계산합니다.</div></section>
    </div>
    <div class="strategy-actions"><button id="strategy-preview-btn" class="strategy-btn secondary" type="button">Preview</button><button id="strategy-apply-btn" class="strategy-btn primary" type="button">Apply Strategy</button></div>`;

  let editMode=activeMode,working=config();
  const q=s=>root.querySelector(s),qa=s=>[...root.querySelectorAll(s)];
  function pull(){
    if(editMode!=="custom")return config(editMode);
    const c=clone(working);c.mode="custom";c.label="Custom";
    c.holdings=Math.max(3,Math.min(20,+q("#st-holdings").value||6));
    c.entryRank=Math.max(c.holdings,Math.min(50,+q("#st-entry").value||c.holdings));
    c.exitRank=Math.max(c.entryRank+1,Math.min(100,+q("#st-exit").value||16));
    c.rebalanceMonths=+q("#st-rebalance").value||1;
    c.factor=q('input[name="st-factor"]:checked')?.value||"momentum";
    c.weighting=q("#st-weighting").value||"equal";
    c.factors.momentum.lookback=Math.max(2,Math.min(24,+q("#st-mom-look").value||6));
    c.factors.momentum.skip=Math.max(1,Math.min(3,+q("#st-mom-skip").value||1));
    c.factors.momentum.enabled=c.factor==="momentum";c.factors.momentum.weight=c.factor==="momentum"?100:0;
    c.factors.marketCap.enabled=c.factor==="marketCap";c.factors.marketCap.weight=c.factor==="marketCap"?100:0;c.filters={};return normalizeCustom(c);
  }
  function refreshChoiceUI(c){
    qa('[data-factor-choice]').forEach(el=>el.classList.toggle("active",el.dataset.factorChoice===c.factor));
    q("#momentum-options").hidden=c.factor!=="momentum";
    const notes={equal:"모든 종목을 같은 비중으로 시작",marketCap:"선택 종목의 시가총액에 비례"};q("#weighting-note").textContent=notes[c.weighting]||"QUQU 전용 동적 비중";
    q("#marketcap-note").hidden=!(c.factor==="marketCap"||c.weighting==="marketCap");
  }
  function fill(c){
    working=clone(c);const qu=editMode==="ququ";
    q("#st-holdings").value=c.holdings;q("#st-entry").value=c.entryRank;q("#st-exit").value=c.exitRank;q("#st-rebalance").value=String(c.rebalanceMonths);
    q("#st-weighting").value=["equal","marketCap"].includes(c.weighting)?c.weighting:"equal";
    q("#st-mom-look").value=c.factors?.momentum?.lookback??6;q("#st-mom-skip").value=c.factors?.momentum?.skip??1;
    const radio=q(`input[name="st-factor"][value="${["momentum","marketCap"].includes(c.factor)?c.factor:"momentum"}"]`);if(radio)radio.checked=true;
    const locked=editMode!=="custom";
    q("#core-lock-note .strategy-section-title").textContent=qu?"QUQU · Locked":"SOMX Core · Locked";
    q("#core-lock-note .core-rule").textContent=qu?"Nasdaq-100 · 100종목 전체 보유 · 월간 6-1 · √시총 × 상대모멘텀³ 동적 비중 · 단일종목 최대 20% · 초과분 비례 재분배":"PIT S&P 500 · 6종목 · Entry Top 6 · 16위까지 유지 / 17위부터 퇴출 · 6-1 Momentum · Equal Weight · Monthly";
    qa(".strategy-body input,.strategy-body select").forEach(x=>x.disabled=locked);
    q("#core-lock-note").style.display=locked?"block":"none";
    qa(".strategy-tabs button").forEach(b=>b.classList.toggle("active",b.dataset.mode===editMode));
    q("#strategy-preview-btn").textContent="Preview";q("#strategy-apply-btn").textContent="Apply Strategy";
    q("#strategy-preview").textContent=qu?"Preview를 누르면 최신 QUQU 100종목 목표비중을 불러옵니다.":"Preview를 누르면 현재 신호 기준 랭킹을 계산합니다.";
    refreshChoiceUI(c);
  }
  function updateCurrent(){const el=q("#strategy-current");if(el)el.textContent=`현재 · ${config().label}`}
  qa(".strategy-tabs button").forEach(b=>b.onclick=()=>{editMode=b.dataset.mode;working=config(editMode);fill(working)});
  root.addEventListener("input",()=>{if(editMode==="custom"){working=pull();refreshChoiceUI(working)}});
  q("#strategy-preview-btn").onclick=async()=>{
    const c=pull(),box=q("#strategy-preview");
    if(editMode==="ququ"){
      box.textContent="최신 QUQU 스냅샷을 불러오는 중…";
      try{const j=await fetchQuquSnapshot(),rows=[...j.rows].sort((a,b)=>b.weight-a.weight);box.innerHTML=rows.slice(0,20).map((r,i)=>`<div class="preview-row"><span>${i+1}</span><strong>${r.ticker}</strong><em>${(r.weight*100).toFixed(2)}%</em></div>`).join("")}catch(e){box.textContent=e.message||"QUQU Preview 실패"}return;
    }
    box.textContent="현재 S&P 500을 계산하는 중…";
    try{
      if(!globalThis.SOMXLive?.previewStrategy)throw new Error("실시간 엔진 연결을 기다리는 중입니다.");
      const rows=await globalThis.SOMXLive.previewStrategy(c),value=r=>c.factor==="marketCap"?`$${(r.score/1e9).toFixed(1)}B`:`${(r.score*100).toFixed(1)}%`;
      box.innerHTML=rows.slice(0,Math.min(20,c.entryRank)).map((r,i)=>`<div class="preview-row"><span>${i+1}</span><strong>${r.s}</strong><em>${value(r)}</em></div>`).join("")||"조건을 통과한 종목이 없습니다.";
    }catch(e){box.textContent=e.message||"Preview 실패"}
  };
  q("#strategy-apply-btn").onclick=async()=>{
    const c=pull(),box=q("#strategy-preview");if(editMode==="custom"){custom=normalizeCustom(c);saveCustom(custom)}
    q("#strategy-apply-btn").textContent="Applying…";
    try{
      if(editMode==="ququ"){const j=await seedQuquContext();box.textContent=`QUQU 최신 스냅샷 적용 · ${(j.generatedAt||"").slice(0,10)} · 100종목`;}
      activeMode=editMode;saveMode(activeMode);
      if(globalThis.SOMXLive?.applyStrategy)await globalThis.SOMXLive.applyStrategy(config());
      updateCurrent();
    }catch(e){box.textContent=e.message||"전략 적용 실패"}finally{q("#strategy-apply-btn").textContent="Apply Strategy"}
  };
  updateCurrent();fill(config());window.addEventListener("somx:strategy-active",updateCurrent);
}
if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",initUI);else initUI();
})();