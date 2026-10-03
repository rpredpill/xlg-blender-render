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
function config(mode=activeMode){
  return clone(mode==="quu"?quu:mode==="ququ"?ququ:mode==="snpy"?snpy:snpi);
}
function signature(c=config()){
  if(c.mode==="snpi")return"snpi-sp500-v1";
  if(c.mode==="snpy")return"snpy-sp500-top100-v1";
  if(c.mode==="ququ")return"ququ-nasdaq-composite-v2";
  if(c.mode==="quu")return"quu-ndx100-rollover-v1";
  return"snpi-sp500-v1";
}
function monthsBetween(a,b){const[ay,am]=a.split("-").map(Number),[by,bm]=b.split("-").map(Number);return(by-ay)*12+(bm-am)}
function isTradeMonth(anchor,target,c=config()){return monthsBetween(anchor,target)%Math.max(1,Number(c.rebalanceMonths)||1)===0}
function requiredCalendarDays(){return 240}
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
  const mom=c.factors?.momentum||{},skip=Math.max(1,Number(mom.skip)||1),look=Math.max(skip+1,Number(mom.lookback)||6),rows=[];
  const recentTs=eom(signalDate,skip-1),earlyTs=eom(signalDate,look-1);
  for(const s of universe){
    const bars=(barsBySymbol.get(s)||[]).filter(b=>+new Date(b.t)<=+new Date(signalDate)).sort((a,b)=>+new Date(a.t)-+new Date(b.t));
    const m=ret(lastClose(bars,recentTs),lastClose(bars,earlyTs));
    if(Number.isFinite(m))rows.push({s,score:m,raw:{momentum:m}});
  }
  return rows.sort((a,b)=>b.score-a.score);
}
function weightsFor({holdings,marketCapBySymbol,config:c=config()}){
  const names=[...holdings];if(!names.length)return{};
  if(c.weighting==="marketCap"){
    if(!marketCapBySymbol)throw new Error("시총 비중 계산용 데이터가 없습니다.");
    const raw=Object.fromEntries(names.map(s=>[s,capValue(marketCapBySymbol,s)]));
    const sum=names.reduce((a,s)=>a+(Number.isFinite(raw[s])&&raw[s]>0?raw[s]:0),0);
    if(!(sum>0))throw new Error("시총 비중 계산용 데이터가 없습니다.");
    return Object.fromEntries(names.map(s=>[s,raw[s]/sum]));
  }
  const w=1/names.length;return Object.fromEntries(names.map(s=>[s,w]));
}

function capAndRedistribute(raw,cap=0.20){
  const free=new Set(Object.keys(raw)),out={};let remaining=1;
  while(free.size){
    let total=0;for(const s of free)total+=raw[s];
    if(!(total>0))throw new Error("SNPY raw weight sum <= 0");
    const over=[...free].filter(s=>remaining*raw[s]/total>cap+1e-12);
    if(!over.length){for(const s of free)out[s]=remaining*raw[s]/total;break}
    for(const s of over){out[s]=cap;remaining-=cap;free.delete(s)}
  }
  return out;
}
function deriveSnpyRows(sourceRows){
  const ranked=(sourceRows||[]).map(x=>{
    const ticker=String(x.ticker||"").trim().toUpperCase(),marketCap=Number(x.marketCap),momentum=Number(x.momentum),gross=1+momentum,stored=Number(x.rawWeightScore);
    const raw=Number.isFinite(stored)&&stored>0?stored:(marketCap>0&&gross>0?Math.sqrt(marketCap)*(gross**3):NaN);
    return{ticker,marketCap,momentum,raw};
  }).filter(x=>x.ticker&&Number.isFinite(x.raw)&&x.raw>0).sort((a,b)=>b.raw-a.raw).slice(0,100);
  if(ranked.length!==100)throw new Error(`SNPY snapshot ${ranked.length}/100`);
  const raw=Object.fromEntries(ranked.map(x=>[x.ticker,x.raw])),weights=capAndRedistribute(raw,0.20);
  return ranked.map(x=>({ticker:x.ticker,weight:weights[x.ticker],momentum:x.momentum,marketCap:x.marketCap})).sort((a,b)=>b.weight-a.weight);
}

function currentNYMonth(){
  const p=new Intl.DateTimeFormat("en-CA",{timeZone:"America/New_York",year:"numeric",month:"2-digit"}).formatToParts(new Date());
  return`${p.find(x=>x.type==="year").value}-${p.find(x=>x.type==="month").value}`;
}
function storageKey(kind,c){return`somx.${kind}.v2.${signature(c)}`}
function isQuotaError(e){return e?.name==="QuotaExceededError"||e?.name==="NS_ERROR_DOM_QUOTA_REACHED"||String(e?.message||e).toLowerCase().includes("quota")}
function clearLegacyQuquCache(){
  for(const kind of ["state","holdings","weights","history","components"]){
    try{localStorage.removeItem(`somx.${kind}.v2.ququ-ndx100-v1`)}catch{}
  }
}
function compactQuquContext(c,st,holdings,targetWeights,allocationYm,ym){
  const hk=storageKey("holdings",c),wk=storageKey("weights",c),sk=storageKey("state",c),histk=storageKey("history",c);
  const hh=allocationYm===ym?{[ym]:[...holdings]}:{[allocationYm]:[...holdings],[ym]:[...holdings]};
  const wh=allocationYm===ym?{[ym]:{...targetWeights}}:{[allocationYm]:{...targetWeights},[ym]:{...targetWeights}};
  clearLegacyQuquCache();
  // Replace the two large derived maps first. This shrinks an old oversized
  // QUQU cache before any new bytes are added to localStorage.
  try{localStorage.removeItem(hk);localStorage.removeItem(wk)}catch{}
  const write=()=>{
    localStorage.setItem(hk,JSON.stringify(hh));
    localStorage.setItem(wk,JSON.stringify(wh));
    localStorage.setItem(sk,JSON.stringify(st));
  };
  try{write()}
  catch(e){
    if(!isQuotaError(e))throw e;
    // Monthly history is a derived browser cache. Drop it only as a last-resort
    // quota recovery; the production snapshot remains the source of truth.
    try{localStorage.removeItem(histk)}catch{}
    try{localStorage.removeItem(hk);localStorage.removeItem(wk);localStorage.removeItem(sk)}catch{}
    write();
  }
}
async function fetchSnapshot(c){
  const mode=c.mode,name=mode==="snpi"?"SNPI":mode==="snpy"?"SNPY":mode==="quu"?"QUU":"QUQU";
  const file=mode==="quu"?"quu-latest.json":mode==="ququ"?"ququ-latest.json":"snpi-latest.json";
  const r=await fetch(`./${file}?v=${Date.now()}`,{cache:"no-store"});
  if(!r.ok)throw new Error(`${name} snapshot HTTP ${r.status}`);
  const j=await r.json();
  if(mode==="ququ"&&j.strategy!=="QUQU v2")throw new Error(`QUQU v2 snapshot expected, got ${j.strategy||"unknown"}`);
  let rows;
  if(mode==="snpy")rows=deriveSnpyRows(j.rows);
  else{
    const min=mode==="snpi"?495:mode==="quu"?1:100;
    if(!Array.isArray(j.rows)||j.rows.length<min)throw new Error(`${name} snapshot ${j.rows?.length||0}/${min}+`);
    rows=j.rows.map(x=>({ticker:String(x.ticker||"").trim().toUpperCase(),weight:Number(x.weight),momentum:x.momentum==null?null:Number(x.momentum),marketCap:x.marketCap==null?null:Number(x.marketCap)}));
  }
  if(rows.some(x=>!x.ticker||!Number.isFinite(x.weight)||x.weight<=0))throw new Error(`${name} 목표비중 데이터가 올바르지 않습니다.`);
  const sum=rows.reduce((a,x)=>a+x.weight,0);
  if(!(sum>0)||Math.abs(sum-1)>1e-6)throw new Error(`${name} 비중합 오류 ${sum}`);
  return{...j,displayStrategy:name,holdings:rows.length,rows};
}
async function seedSnapshotContext(c){
  const j=await fetchSnapshot(c),ym=currentNYMonth(),holdings=j.rows.map(x=>x.ticker),targetWeights=Object.fromEntries(j.rows.map(x=>[x.ticker,x.weight]));
  c.holdings=holdings.length;c.entryRank=holdings.length;c.exitRank=holdings.length;
  const allocationYm=c.mode==="ququ"&&/^\d{4}-\d{2}$/.test(String(j.allocationMonth||""))?String(j.allocationMonth):ym;
  const st={rebalanceMonth:ym,strategyAnchor:ym,initialized:true,holdings,statuses:Object.fromEntries(holdings.map(s=>[s,"IN"])),targetWeights,basePrices:{},updatedAt:new Date().toISOString(),snapshotAt:j.generatedAt||null,snapshotAllocationMonth:allocationYm};
  if(c.mode==="ququ"){
    compactQuquContext(c,st,holdings,targetWeights,allocationYm,ym);
    return j;
  }
  let hh={},wh={};
  try{hh=JSON.parse(localStorage.getItem(storageKey("holdings",c))||"{}")||{}}catch{}
  try{wh=JSON.parse(localStorage.getItem(storageKey("weights",c))||"{}")||{}}catch{}
  hh[allocationYm]=[...holdings];wh[allocationYm]={...targetWeights};
  hh[ym]=[...holdings];wh[ym]={...targetWeights};
  localStorage.setItem(storageKey("state",c),JSON.stringify(st));
  localStorage.setItem(storageKey("holdings",c),JSON.stringify(hh));
  localStorage.setItem(storageKey("weights",c),JSON.stringify(wh));
  return j;
}

const ruleText=c=>c.mode==="snpi"
  ?"S&P 500 전체 구성종목 · 월간 6-1 · √시총 × 상대모멘텀³ · 단일종목 최대 20% · 초과분 비례 재분배"
  :c.mode==="snpy"
  ?"S&P 500 · SNPI raw score 상위 100 · 월간 6-1 · Top100 안에서 √시총 × 상대모멘텀³ 재계산 · 단일종목 최대 20%"
  :c.mode==="quu"
  ?"Nasdaq-100 · 리더십 분산 강함=QUQU p=3 · 리더십 약함=QQQ · 극단적 분산이 고점 후 꺾이면 QQQ · 월간 전환"
  :"Nasdaq Composite PIT · FloatCap 상위 10% · 6-1 · 63D 변동성 조정 · 유동성 하위 10% 제외 · ±3σ winsor · √FloatCap × 상대신호³ · 20% cap";

globalThis.SOMXStrategy={
  getConfig:()=>config(),getMode:()=>activeMode,
  setMode:m=>{activeMode=modes.includes(m)?m:"snpi";saveMode(activeMode)},
  signature,isTradeMonth,requiredCalendarDays,score,weightsFor,fetchSnapshot,seedSnapshotContext,
  presets:{snpi:clone(snpi),core:clone(snpi),snpy:clone(snpy),ququ:clone(ququ),quu:clone(quu)}
};

function initUI(){
  const root=document.getElementById("strategy-settings-root");if(!root)return;
  root.innerHTML=`
    <div class="strategy-settings-head"><div><strong>Strategy</strong><span id="strategy-current"></span></div><small>고정 전략 선택</small></div>
    <div class="strategy-tabs"><button data-mode="snpi" type="button">SNPI</button><button data-mode="snpy" type="button">SNPY</button><button data-mode="ququ" type="button">QUQU</button><button data-mode="quu" type="button">QUU</button></div>
    <div class="strategy-body">
      <section class="strategy-section"><div id="strategy-rule-title" class="strategy-section-title"></div><div id="strategy-rule" class="core-rule"></div></section>
      <section class="strategy-section preview-section"><div class="strategy-section-title">Current signal preview</div><div id="strategy-preview" class="strategy-preview">Preview를 누르면 최신 목표비중을 불러옵니다.</div></section>
    </div>
    <div class="strategy-actions"><button id="strategy-preview-btn" class="strategy-btn secondary" type="button">Preview</button><button id="strategy-apply-btn" class="strategy-btn primary" type="button">Apply Strategy</button></div>`;

  let editMode=activeMode;
  const q=s=>root.querySelector(s),qa=s=>[...root.querySelectorAll(s)];
  function fill(){
    const c=config(editMode);
    q("#strategy-rule-title").textContent=`${c.label} · Locked`;
    q("#strategy-rule").textContent=ruleText(c);
    q("#strategy-preview").textContent=`Preview를 누르면 최신 ${c.label} 목표비중을 불러옵니다.`;
    qa(".strategy-tabs button").forEach(b=>b.classList.toggle("active",b.dataset.mode===editMode));
  }
  function updateCurrent(){const el=q("#strategy-current");if(el)el.textContent=`현재 · ${config().label}`}
  qa(".strategy-tabs button").forEach(b=>b.onclick=()=>{editMode=b.dataset.mode;fill()});

  q("#strategy-preview-btn").onclick=async()=>{
    const c=config(editMode),box=q("#strategy-preview");
    box.textContent=`최신 ${c.label} 스냅샷을 불러오는 중…`;
    try{
      const j=await fetchSnapshot(c),rows=[...j.rows].sort((a,b)=>b.weight-a.weight);
      const regime=c.mode==="quu"?`<div class="preview-row"><span>Regime</span><strong>${j.selectedSleeve||"--"}</strong><em>${j.decision?.extremeDispersionRollover?"Rollover":j.decision?.leadershipStrong?"Leadership":"Weak"}</em></div>`:"";
      const meta=c.mode==="ququ"&&j.allocationMonth?`<div class="preview-row"><span>Allocation</span><strong>${j.allocationMonth}</strong><em>${rows.length}종목</em></div>`:"";
      box.innerHTML=regime+meta+rows.slice(0,20).map((r,i)=>`<div class="preview-row"><span>${i+1}</span><strong>${r.ticker}</strong><em>${(r.weight*100).toFixed(2)}%</em></div>`).join("");
    }catch(e){box.textContent=e.message||`${c.label} Preview 실패`}
  };

  q("#strategy-apply-btn").onclick=async()=>{
    const c=config(editMode),box=q("#strategy-preview"),btn=q("#strategy-apply-btn");
    btn.textContent="Applying…";btn.disabled=true;
    try{
      const j=await seedSnapshotContext(c);
      c.holdings=j.rows.length;c.entryRank=j.rows.length;c.exitRank=j.rows.length;
      activeMode=editMode;saveMode(activeMode);

      if(c.mode==="ququ"){
        box.textContent=`QUQU v2 적용 완료 · ${j.rows.length}종목 · 다시 불러오는 중…`;
        updateCurrent();
        setTimeout(()=>location.reload(),80);
        return;
      }

      if(globalThis.SOMXLive?.applyStrategy)await globalThis.SOMXLive.applyStrategy(c);
      const sleeve=c.mode==="quu"&&j.selectedSleeve?` · ${j.selectedSleeve}`:"";
      box.textContent=`${c.label} 적용${sleeve} · ${j.rows.length}종목`;
      updateCurrent();
    }catch(e){box.textContent=e.message||"전략 적용 실패"}
    finally{btn.textContent="Apply Strategy";btn.disabled=false}
  };

  updateCurrent();fill();window.addEventListener("somx:strategy-active",updateCurrent);
}
if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",initUI);else initUI();
})();