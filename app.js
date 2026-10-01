"use strict";

const ALPACA_REST="https://data.alpaca.markets/v2";
const ALPACA_WS="wss://stream.data.alpaca.markets/v2/iex";
const PIT_URL="https://raw.githubusercontent.com/fja05680/sp500/master/S%26P%20500%20Historical%20Components%20%26%20Changes%20%28Updated%29.csv";
const MARKET_CAP_URL="https://raw.githubusercontent.com/Ate329/top-us-stock-tickers/main/tickers/sp500.csv";
const CRED_KEY="somx.alpaca.credentials.v1";
const CENTER_PHOTO_KEY="somx.center.photo.v1";
const LEGACY_STATE_KEY="somx.live.state.v1";
const LEGACY_HISTORY_KEY="somx.monthly.history.v1";
const LEGACY_HOLDINGS_KEY="somx.holdings.history.v1";

const seededCoreHistory={
  "2026-01":["SNDK","WDC","WBD","ALB","TER","STX"],
  "2026-02":["SNDK","WDC","WBD","ALB","TER","STX"],
  "2026-03":["SNDK","WDC","WBD","ALB","TER","STX"],
  "2026-04":["SNDK","WDC","ALB","TER","STX","LITE"],
  "2026-05":["SNDK","WDC","TER","STX","LITE","CIEN"],
  "2026-06":["SNDK","WDC","STX","LITE","CIEN","MU"],
  "2026-07":["SNDK","WDC","STX","LITE","MU","DELL"],
  "2026-08":["SNDK","WDC","STX","MU","DELL","DDOG"],
  "2026-09":["MU","DELL","SNDK","STX","DDOG","MRNA"]
};
const seededCoreWeights=Object.fromEntries(Object.entries(seededCoreHistory).map(([m,h])=>[m,Object.fromEntries(h.map(s=>[s,1/h.length]))]));
const coreSeed={
  rebalanceMonth:"2026-09",strategyAnchor:"2026-09",initialized:true,
  holdings:["MU","DELL","SNDK","STX","DDOG","MRNA"],
  statuses:{MU:"HOLD",DELL:"HOLD",SNDK:"HOLD",STX:"HOLD",DDOG:"HOLD",MRNA:"IN"},
  targetWeights:{MU:1/6,DELL:1/6,SNDK:1/6,STX:1/6,DDOG:1/6,MRNA:1/6},
  basePrices:{},updatedAt:null
};
const basePalette=["#1767c9","#e33b36","#f6b11a","#702ec9","#1e8b4b","#dd3b73","#008f9c","#a45a12","#3857d6","#a63cae","#2d8c66","#d55b23"];

let activeStrategy=globalThis.SOMXStrategy?.getConfig?.()||{mode:"core",label:"SOMX Core",holdings:6,entryRank:6,exitRank:16,rebalanceMonths:1,factor:"momentum",weighting:"equal",factors:{momentum:{enabled:true,weight:100,lookback:6,skip:1}},filters:{}};
let strategySig=globalThis.SOMXStrategy?.signature?.(activeStrategy)||"core";
let state,monthlyHistory,holdingsHistory,weightsHistory,somvComponents={};
let creds=loadCreds(),latestPrices={},socket=null,pitRows=null,marketCapCache=null,starting=false,historyLoading=false,switching=false;

function currentNYMonth(){const p=new Intl.DateTimeFormat("en-CA",{timeZone:"America/New_York",year:"numeric",month:"2-digit"}).formatToParts(new Date());return `${p.find(x=>x.type==="year").value}-${p.find(x=>x.type==="month").value}`}
function addMonths(ym,n){const[y,m]=ym.split("-").map(Number),d=new Date(Date.UTC(y,m-1+n,1));return `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,"0")}`}
function monthEndDate(ym){const[y,m]=ym.split("-").map(Number);return new Date(Date.UTC(y,m,0,23,59,59,999))}
function isoDate(d){return d.toISOString().slice(0,10)}
function colorFor(i){return basePalette[i%basePalette.length]||`hsl(${(i*137.508)%360} 68% 46%)`}
function fmt(v){return Number.isFinite(v)?`${v>=0?"+":""}${v.toFixed(1)}%`:"--"}
function storageKey(kind,sig=strategySig){return `somx.${kind}.v2.${sig}`}
function clone(x){return JSON.parse(JSON.stringify(x))}
function loadCreds(){try{return JSON.parse(localStorage.getItem(CRED_KEY))||null}catch{return null}}
function saveCreds(c){localStorage.setItem(CRED_KEY,JSON.stringify(c));creds=c}
function equalWeights(holdings){const n=Math.max(1,holdings.length),w=1/n;return Object.fromEntries(holdings.map(s=>[s,w]))}
function normalizeWeights(weights,holdings){
  if(!holdings.length)return{};
  if(!weights||holdings.some(s=>!Number.isFinite(Number(weights[s]))||Number(weights[s])<0))return equalWeights(holdings);
  const sum=holdings.reduce((a,s)=>a+Number(weights[s]),0);if(!(sum>0))return equalWeights(holdings);
  return Object.fromEntries(holdings.map(s=>[s,Number(weights[s])/sum]));
}
function defaultState(){return activeStrategy.mode==="core"?clone(coreSeed):{rebalanceMonth:currentNYMonth(),strategyAnchor:currentNYMonth(),initialized:false,holdings:[],statuses:{},targetWeights:{},basePrices:{},updatedAt:null}}
function loadContext(){
  let st=null,mh={},hh={},wh={};
  try{st=JSON.parse(localStorage.getItem(storageKey("state"))||"null")}catch{}
  try{mh=JSON.parse(localStorage.getItem(storageKey("history"))||"{}")||{}}catch{}
  try{hh=JSON.parse(localStorage.getItem(storageKey("holdings"))||"{}")||{}}catch{}
  try{wh=JSON.parse(localStorage.getItem(storageKey("weights"))||"{}")||{}}catch{}
  if(activeStrategy.mode==="core"){
    if(!st){try{st=JSON.parse(localStorage.getItem(LEGACY_STATE_KEY)||"null")}catch{}}
    if(!Object.keys(mh).length){try{mh=JSON.parse(localStorage.getItem(LEGACY_HISTORY_KEY)||"{}")||{}}catch{}}
    let legacy={};try{legacy=JSON.parse(localStorage.getItem(LEGACY_HOLDINGS_KEY)||"{}")||{}}catch{}
    hh={...seededCoreHistory,...legacy,...hh};wh={...seededCoreWeights,...wh};
  }
  state={...defaultState(),...(st||{})};state.targetWeights=normalizeWeights(state.targetWeights,state.holdings||[]);
  monthlyHistory=mh;holdingsHistory=hh;weightsHistory=wh;
  try{somvComponents=JSON.parse(localStorage.getItem(storageKey("components"))||"{}")||{}}catch{somvComponents={}}
  if(state.holdings?.length&&!weightsHistory[state.rebalanceMonth])weightsHistory[state.rebalanceMonth]=clone(state.targetWeights);
}
function saveSomvComponents(){try{localStorage.setItem(storageKey("components"),JSON.stringify(somvComponents))}catch{}}
function saveState(){try{localStorage.setItem(storageKey("state"),JSON.stringify(state))}catch{}}
function saveMonthlyHistory(){try{localStorage.setItem(storageKey("history"),JSON.stringify(monthlyHistory))}catch{};window.dispatchEvent(new Event("somx:historychange"))}
function saveHoldingsHistory(){try{localStorage.setItem(storageKey("holdings"),JSON.stringify(holdingsHistory))}catch{}}
function saveWeightsHistory(){try{localStorage.setItem(storageKey("weights"),JSON.stringify(weightsHistory))}catch{}}
loadContext();

function parseCSVLine(line){const out=[];let cur="",q=false;for(let i=0;i<line.length;i++){const c=line[i];if(c==='"'){if(q&&line[i+1]==='"'){cur+='"';i++}else q=!q}else if(c===","&&!q){out.push(cur);cur=""}else cur+=c}out.push(cur);return out}
function normalizeTicker(s){return String(s||"").trim().toUpperCase().replace(/\//g,".").replace(/-/g,".")}
async function loadPitRows(){
  if(pitRows)return pitRows;
  const r=await fetch(PIT_URL,{cache:"no-store"});if(!r.ok)throw new Error("S&P 500 구성원 이력 다운로드 실패");
  const lines=(await r.text()).trim().split(/\r?\n/),head=parseCSVLine(lines[0]).map(x=>x.trim().toLowerCase()),di=head.findIndex(x=>x.includes("date")),ci=head.findIndex(x=>x.includes("component")||x.includes("ticker")||x.includes("symbol"));
  pitRows=lines.slice(1).map(line=>{const a=parseCSVLine(line);return{date:new Date(a[di]+"T00:00:00Z"),tickers:(a[ci]||"").split(",").map(normalizeTicker).filter(Boolean)}}).filter(x=>!Number.isNaN(+x.date)).sort((a,b)=>a.date-b.date);return pitRows;
}
async function universeAt(date){const rows=await loadPitRows();let lo=0,hi=rows.length-1,best=null;while(lo<=hi){const mid=(lo+hi)>>1;if(rows[mid].date<=date){best=rows[mid];lo=mid+1}else hi=mid-1}if(!best)throw new Error("해당 날짜 S&P 500 구성원을 찾지 못함");return best.tickers}
async function loadMarketCaps(force=false){
  if(!force&&marketCapCache&&Date.now()-marketCapCache.at<10*60*1000)return marketCapCache.map;
  const r=await fetch(MARKET_CAP_URL,{cache:"no-store"});if(!r.ok)throw new Error("시총 데이터 다운로드 실패");
  const lines=(await r.text()).trim().split(/\r?\n/),head=parseCSVLine(lines[0]).map(x=>x.trim().toLowerCase()),si=head.findIndex(x=>x==="symbol"||x.includes("ticker")),mi=head.findIndex(x=>x.replace(/[^a-z]/g,"").includes("marketcap"));
  if(si<0||mi<0)throw new Error("시총 데이터 형식을 읽지 못했습니다.");
  const map=new Map();for(const line of lines.slice(1)){const a=parseCSVLine(line),s=normalizeTicker(a[si]),v=Number(String(a[mi]||"").replace(/[$,]/g,"").trim());if(s&&Number.isFinite(v)&&v>0)map.set(s,v)}
  if(map.size<100)throw new Error("시총 데이터가 충분하지 않습니다.");marketCapCache={at:Date.now(),map};return map;
}
function headers(){return{"APCA-API-KEY-ID":creds.keyId,"APCA-API-SECRET-KEY":creds.secretKey}}
async function alpaca(path){if(!creds)throw new Error("Alpaca 키가 필요합니다.");const r=await fetch(`${ALPACA_REST}${path}`,{headers:headers()});if(!r.ok){const t=await r.text().catch(()=>"");throw new Error(`Alpaca ${r.status}: ${t.slice(0,180)}`)}return r.json()}
globalThis.alpaca=alpaca;

async function barsMonthEnd(symbols,targetDate){
  const y=targetDate.getUTCFullYear(),m=targetDate.getUTCMonth(),start=new Date(Date.UTC(y,m,18)),end=new Date(Date.UTC(y,m+1,2)),result={};
  for(let i=0;i<symbols.length;i+=80){const part=symbols.slice(i,i+80),q=new URLSearchParams({symbols:part.join(","),timeframe:"1Day",start:isoDate(start),end:isoDate(end),adjustment:"all",feed:"iex",limit:"10000",sort:"asc"}),data=await alpaca(`/stocks/bars?${q}`);for(const[s,bars]of Object.entries(data.bars||{})){const valid=bars.filter(b=>new Date(b.t)<=targetDate);if(valid.length)result[normalizeTicker(s)]=Number(valid.at(-1).c)}}return result;
}
async function rankMomentum(rebalanceYM,c){
  const look=Math.max(2,Number(c.factors?.momentum?.lookback)||6),skip=Math.max(1,Number(c.factors?.momentum?.skip)||1),universeDate=monthEndDate(addMonths(rebalanceYM,-1)),recentEnd=monthEndDate(addMonths(rebalanceYM,-skip)),earlyEnd=monthEndDate(addMonths(rebalanceYM,-look)),uni=await universeAt(universeDate),[pr,pe]=await Promise.all([barsMonthEnd(uni,recentEnd),barsMonthEnd(uni,earlyEnd)]),ranked=uni.map(s=>{const a=pr[s],b=pe[s];return Number.isFinite(a)&&Number.isFinite(b)&&b>0?{s,score:a/b-1,raw:{momentum:a/b-1}}:null}).filter(Boolean).sort((a,b)=>b.score-a.score);return{ranked,universe:new Set(uni),marketCapBySymbol:null};
}
async function rankMarketCap(rebalanceYM,c){
  const signalEnd=monthEndDate(addMonths(rebalanceYM,-1)),uni=await universeAt(signalEnd),caps=await loadMarketCaps(),ranked=globalThis.SOMXStrategy?.score?.({universe:uni,barsBySymbol:new Map(),signalDate:signalEnd,marketCapBySymbol:caps,config:c})||[];
  if(ranked.length<Math.max(1,Number(c.holdings)||6))throw new Error("시총 데이터와 S&P 500 종목 매칭이 부족합니다.");
  return{ranked,universe:new Set(uni),marketCapBySymbol:caps};
}
async function rankForRebalance(rebalanceYM,c=activeStrategy){return c.factor==="marketCap"?rankMarketCap(rebalanceYM,c):rankMomentum(rebalanceYM,c)}
function transition(prev,ranked,universe,c=activeStrategy){
  const n=Math.max(1,Number(c.holdings)||6),entry=Math.max(n,Number(c.entryRank)||n),exit=Math.max(entry+1,Number(c.exitRank)||16),pos=new Map(ranked.map((x,i)=>[x.s,i+1]));
  let keep=prev.filter(s=>universe.has(s)&&(pos.get(s)??Infinity)<=exit);
  for(const x of ranked.slice(0,entry)){if(keep.length>=n)break;if(!keep.includes(x.s))keep.push(x.s)}
  if(keep.length<n){for(const x of ranked){if(keep.length>=n)break;if(!keep.includes(x.s))keep.push(x.s)}}return keep.slice(0,n);
}
function somvSelection(coreHoldings,ranked){
  const core=[...new Set(coreHoldings)],top6=ranked.slice(0,6).map(x=>x.s);
  if(core.length!==6||new Set(top6).size!==6)throw new Error("SOMV 계산에 Core 6개와 Top6가 모두 필요합니다.");
  return{coreHoldings:core,top6,holdings:[...new Set([...core,...top6])]};
}
function canonicalCoreHistory(){return{...seededCoreHistory,...(globalThis.SOMXCoreBackfill?.history?.()||{})}}
async function coreForMonth(ym,result){
  const history=canonicalCoreHistory();if(history[ym])return [...history[ym]];
  const prior=Object.keys(history).filter(m=>m<ym).sort().at(-1);
  if(!prior)throw new Error("SOMX Core 기준 이력이 없습니다.");
  let core=[...history[prior]];
  for(let m=addMonths(prior,1);m<=ym;m=addMonths(m,1)){
    const r=m===ym?result:await rankForRebalance(m,globalThis.SOMXStrategy.presets.core);
    core=transition(core,r.ranked,r.universe,globalThis.SOMXStrategy.presets.core);
  }
  return core;
}
function shouldTrade(anchor,target,c=activeStrategy){return globalThis.SOMXStrategy?.isTradeMonth?.(anchor,target,c)??true}
async function targetWeightsFor(holdings,ranked,c=activeStrategy,marketCapBySymbol=null){
  let caps=marketCapBySymbol;if(c.weighting==="marketCap"&&!caps)caps=await loadMarketCaps();
  const w=globalThis.SOMXStrategy?.weightsFor?.({holdings,ranked,marketCapBySymbol:caps,config:c})||equalWeights(holdings);return normalizeWeights(w,holdings);
}

async function monthBars(month,holdings){
  if(!holdings.length)return{};const q=new URLSearchParams({symbols:holdings.join(","),timeframe:"1Day",start:`${month}-01`,end:`${addMonths(month,1)}-03`,adjustment:"all",feed:"iex",limit:"5000",sort:"asc"}),data=await alpaca(`/stocks/bars?${q}`);return data.bars||{};
}
async function carryWeights(month,holdings,startWeights){
  const w=normalizeWeights(startWeights,holdings),barsBy=await monthBars(month,holdings),raw={};
  for(const s of holdings){const bars=(barsBy[s]||[]).filter(b=>String(b.t).slice(0,7)===month),o=Number(bars[0]?.o),c=Number(bars.at(-1)?.c),rel=bars.length&&o>0&&Number.isFinite(c)?c/o:1;raw[s]=w[s]*rel}
  return normalizeWeights(raw,holdings);
}

async function bootstrapStrategy(){
  const ym=currentNYMonth();toast(`${activeStrategy.label} 현재 신호 계산 중…`);
  const result=await rankForRebalance(ym,activeStrategy),n=Math.max(1,Number(activeStrategy.holdings)||6);
  const selection=activeStrategy.mode==="somv"?somvSelection(await coreForMonth(ym,result),result.ranked):null;
  const holdings=selection?.holdings||result.ranked.slice(0,n).map(x=>x.s);
  if(holdings.length<n)throw new Error(`조건을 통과한 종목이 ${holdings.length}개뿐입니다.`);
  const targetWeights=await targetWeightsFor(holdings,result.ranked,activeStrategy,result.marketCapBySymbol);
  state={...(selection||{}),rebalanceMonth:ym,strategyAnchor:ym,initialized:true,holdings,statuses:Object.fromEntries(holdings.map(s=>[s,"IN"])),targetWeights,basePrices:{},updatedAt:new Date().toISOString()};
  if(selection){somvComponents[ym]={coreHoldings:selection.coreHoldings,top6:selection.top6};saveSomvComponents()}
  holdingsHistory={[ym]:[...holdings]};weightsHistory={[ym]:clone(targetWeights)};monthlyHistory={};saveState();saveHoldingsHistory();saveWeightsHistory();saveMonthlyHistory();
}
async function catchUpRebalances(){
  if(!state.initialized)await bootstrapStrategy();const now=currentNYMonth();let cursor=state.rebalanceMonth||now;
  while(cursor<now){
    if(!holdingsHistory[cursor])holdingsHistory[cursor]=[...state.holdings];if(!weightsHistory[cursor])weightsHistory[cursor]=clone(normalizeWeights(state.targetWeights,state.holdings));
    const next=addMonths(cursor,1),old=[...state.holdings],oldWeights=normalizeWeights(state.targetWeights,old);let holdings=old,targetWeights;
    if(shouldTrade(state.strategyAnchor||cursor,next,activeStrategy)){
      toast(`${activeStrategy.label} ${next} 리밸런스 계산 중…`);const result=await rankForRebalance(next,activeStrategy);if(activeStrategy.mode==="somv"){
        const core=transition(state.coreHoldings,result.ranked,result.universe,globalThis.SOMXStrategy.presets.core),selection=somvSelection(core,result.ranked);
        holdings=selection.holdings;state.coreHoldings=selection.coreHoldings;state.top6=selection.top6;
      }else holdings=transition(old,result.ranked,result.universe,activeStrategy);targetWeights=await targetWeightsFor(holdings,result.ranked,activeStrategy,result.marketCapBySymbol);
    }else targetWeights=await carryWeights(cursor,holdings,oldWeights);
    if(activeStrategy.mode==="somv"){somvComponents[next]={coreHoldings:[...state.coreHoldings],top6:[...state.top6]};saveSomvComponents()}
    const oldSet=new Set(old);holdingsHistory[next]=[...holdings];weightsHistory[next]=clone(targetWeights);state.holdings=holdings;state.targetWeights=targetWeights;state.statuses=Object.fromEntries(holdings.map(s=>[s,oldSet.has(s)?"HOLD":"IN"]));state.rebalanceMonth=next;state.basePrices={};state.updatedAt=new Date().toISOString();saveHoldingsHistory();saveWeightsHistory();saveState();cursor=next;
  }
}
async function getMonthBasePrices(){
  const m=state.rebalanceMonth;if(state.basePrices&&Object.keys(state.basePrices).length===state.holdings.length)return state.basePrices;
  const q=new URLSearchParams({symbols:state.holdings.join(","),timeframe:"1Day",start:`${m}-01`,end:`${addMonths(m,1)}-01`,adjustment:"all",feed:"iex",limit:"1000",sort:"asc"}),data=await alpaca(`/stocks/bars?${q}`),base={};
  for(const s of state.holdings){const bars=data.bars?.[s]||[];if(bars.length)base[s]=Number(bars[0].o)}state.basePrices=base;saveState();return base;
}
async function getLatestFallback(){if(!state.holdings.length)return;const q=new URLSearchParams({symbols:state.holdings.join(","),feed:"iex"}),data=await alpaca(`/stocks/bars/latest?${q}`);for(const[s,obj]of Object.entries(data.bars||{}))if(obj?.c)latestPrices[normalizeTicker(s)]=Number(obj.c)}
function connectStream(){
  if(!creds||!state.holdings.length)return;if(socket)try{socket.close()}catch{}socket=new WebSocket(ALPACA_WS);
  socket.onopen=()=>socket.send(JSON.stringify({action:"auth",key:creds.keyId,secret:creds.secretKey}));
  socket.onmessage=ev=>{let msgs;try{msgs=JSON.parse(ev.data)}catch{return}if(!Array.isArray(msgs))msgs=[msgs];for(const m of msgs){if(m.T==="success"&&m.msg==="authenticated")socket.send(JSON.stringify({action:"subscribe",trades:state.holdings}));else if(m.T==="t"&&m.S&&Number.isFinite(m.p)){latestPrices[normalizeTicker(m.S)]=m.p;render()}else if(m.T==="error")toast(`실시간 연결 오류: ${m.msg||m.code}`)}};
  socket.onclose=()=>{if(creds&&!switching)setTimeout(connectStream,5000)};
}

async function completedMonthReturn(month,holdings,startWeights){
  if(!holdings?.length)throw new Error(`${month} 보유종목 이력이 없습니다.`);const w=normalizeWeights(startWeights,holdings),dataBars=await monthBars(month,holdings),rows=[];
  for(const s of holdings){const bars=(dataBars[s]||[]).filter(b=>String(b.t).slice(0,7)===month);if(!bars.length){rows.push({ticker:s,ret:NaN,weight:w[s]});continue}const first=bars[0],last=bars.at(-1),rel=Number(first.o)>0?Number(last.c)/Number(first.o):NaN;rows.push({ticker:s,ret:Number.isFinite(rel)?(rel-1)*100:NaN,weight:w[s],first:Number(first.o),last:Number(last.c),lastDate:String(last.t).slice(0,10)})}
  const valid=rows.filter(x=>Number.isFinite(x.ret)),port=valid.length===holdings.length?(rows.reduce((a,x)=>a+w[x.ticker]*(1+x.ret/100),0)-1)*100:NaN;return{month,port,rows,lastDate:valid[0]?.lastDate||null,holdings:[...holdings],weights:clone(w),...(activeStrategy.mode==="somv"?clone(somvComponents[month]||{}):{})};
}
async function ensureMonthlyHistory(){
  if(historyLoading||!creds)return;historyLoading=true;const sig=strategySig;try{const current=currentNYMonth(),months=Object.keys(holdingsHistory).filter(m=>m<current).sort();for(const month of months){if(monthlyHistory[month]&&Number.isFinite(monthlyHistory[month].port))continue;const status=document.getElementById("history-status");if(status)status.textContent=`${month} 계산 중…`;try{const record=await completedMonthReturn(month,holdingsHistory[month],weightsHistory[month]);if(sig!==strategySig)return;monthlyHistory[month]=record;saveMonthlyHistory();renderHistory()}catch(e){console.warn("history",month,e)}}const status=document.getElementById("history-status");if(status)status.textContent="각 달 첫 거래일 시가 → 마지막 거래일 종가"}finally{historyLoading=false}
}
function renderHistory(){const list=document.getElementById("history-list");if(!list)return;const heading=document.querySelector("#history-modal h2");if(heading)heading.textContent=`${activeStrategy.mode==="somv"?"SOMV":activeStrategy.label} 월말 수익률 기록`;const months=Object.keys(monthlyHistory).sort().reverse();if(!months.length){list.innerHTML='<div class="history-empty">이 전략의 월말 기록이 아직 없습니다.</div>';return}list.innerHTML=months.map(month=>{const h=monthlyHistory[month],details=(h.rows||[]).map(x=>`<div class="history-stock"><span>${x.ticker}${activeStrategy.mode==="somv"?`<small class="history-membership">${h.coreHoldings?.includes(x.ticker)?(h.top6?.includes(x.ticker)?"Core · Top6":"Core"):"Top6"} · ${((x.weight||0)*100).toFixed(2)}%</small>`:""}</span><strong class="${x.ret<0?"neg":""}">${fmt(x.ret)}</strong></div>`).join("");return `<details class="history-month"><summary><span class="history-month-label">${month}</span><span class="history-date">${h.lastDate||""}${activeStrategy.mode==="somv"?` · ${h.holdings.length}종목`:""}</span><strong class="history-port ${h.port<0?"neg":""}">${fmt(h.port)}</strong></summary><div class="history-details">${details}</div></details>`}).join("")}
let somvBackfillPromise=null;
async function backfillSomvHistory(){
  if(activeStrategy.mode!=="somv"||!creds)return;
  if(somvBackfillPromise)return somvBackfillPromise;
  const sig=strategySig;
  somvBackfillPromise=(async()=>{
    const history=canonicalCoreHistory(),months=Object.keys(history).filter(m=>m<currentNYMonth()).sort();
    for(const month of months){
      if(sig!==strategySig)return;
      if(somvComponents[month]&&holdingsHistory[month])continue;
      document.getElementById("history-status").textContent=`SOMV ${month} · Core ∪ Top6 계산 중…`;
      const result=await rankForRebalance(month,globalThis.SOMXStrategy.presets.somv);
      if(sig!==strategySig)return;
      const selection=somvSelection(history[month],result.ranked);
      holdingsHistory[month]=selection.holdings;weightsHistory[month]=equalWeights(selection.holdings);
      somvComponents[month]={coreHoldings:selection.coreHoldings,top6:selection.top6};
      delete monthlyHistory[month];saveHoldingsHistory();saveWeightsHistory();saveSomvComponents();
    }
    while(historyLoading){await new Promise(r=>setTimeout(r,100));if(sig!==strategySig)return}
    await ensureMonthlyHistory();if(sig===strategySig)renderHistory();
  })().catch(e=>{if(sig===strategySig)document.getElementById("history-status").textContent=e.message||"SOMV 기록 계산 실패";console.error("somv history",e)}).finally(()=>{somvBackfillPromise=null});
  return somvBackfillPromise;
}
function refreshStrategyHistory(){if(activeStrategy.mode==="somv")return backfillSomvHistory();return ensureMonthlyHistory()}
function openHistory(){document.getElementById("history-modal").classList.add("show");renderHistory();if(creds)refreshStrategyHistory()}
function closeHistory(){document.getElementById("history-modal").classList.remove("show")}

function metrics(){
  const n=Math.max(1,state.holdings.length),startW=normalizeWeights(state.targetWeights,state.holdings),rows=state.holdings.map(s=>{const p=latestPrices[s],b=state.basePrices?.[s],rel=Number.isFinite(p)&&Number.isFinite(b)&&b>0?p/b:NaN;return{ticker:s,status:state.statuses?.[s]||"HOLD",price:p,base:b,startWeight:startW[s],rel,ret:Number.isFinite(rel)?(rel-1)*100:NaN}}),valid=rows.filter(x=>Number.isFinite(x.rel));
  const total=valid.reduce((a,x)=>a+x.startWeight*x.rel,0);rows.forEach(x=>x.weight=Number.isFinite(x.rel)&&total>0?x.startWeight*x.rel/total:x.startWeight);const port=valid.length===n?(total-1)*100:NaN;return{rows,port};
}
function setMetric(id,v){const el=document.getElementById(id);if(!el)return;el.textContent=fmt(v);el.classList.toggle("neg",Number.isFinite(v)&&v<0)}
function render(){
  const{rows,port}=metrics(),tbody=document.getElementById("holdings-body");if(!tbody)return;
  tbody.innerHTML=rows.map((p,i)=>`<tr><td><div class="stock"><div class="logo" style="color:${colorFor(i)}">${p.ticker}</div><div>${p.ticker}</div></div></td><td class="status-wrap"><span class="badge ${p.status==="IN"?"in":"hold"}">${p.status}</span></td><td class="ret ${p.ret<0?"neg":""}">${fmt(p.ret)}</td></tr>`).join("");
  const valid=rows.filter(x=>Number.isFinite(x.ret)),up=valid.filter(x=>x.ret>0).length,best=valid.length?[...valid].sort((a,b)=>b.ret-a.ret)[0]:null,worst=valid.length?[...valid].sort((a,b)=>a.ret-b.ret)[0]:null;setMetric("portfolio-return",port);document.getElementById("up-count").textContent=valid.length?`${up} / ${rows.length}`:"--";document.getElementById("best-ticker").textContent=best?.ticker||"--";setMetric("best-return",best?.ret);document.getElementById("worst-ticker").textContent=worst?.ticker||"--";setMetric("worst-return",worst?.ret);drawDonut(rows);document.body.dataset.strategy=activeStrategy.mode;document.getElementById("donut")?.setAttribute("aria-label",`${activeStrategy.label} portfolio donut`);
}
function polar(cx,cy,r,a){const rad=(a-90)*Math.PI/180;return[cx+r*Math.cos(rad),cy+r*Math.sin(rad)]}
function arcPath(cx,cy,ro,ri,a0,a1){const[x0,y0]=polar(cx,cy,ro,a0),[x1,y1]=polar(cx,cy,ro,a1),[xi1,yi1]=polar(cx,cy,ri,a1),[xi0,yi0]=polar(cx,cy,ri,a0),large=a1-a0>180?1:0;return`M ${x0} ${y0} A ${ro} ${ro} 0 ${large} 1 ${x1} ${y1} L ${xi1} ${yi1} A ${ri} ${ri} 0 ${large} 0 ${xi0} ${yi0} Z`}
function drawDonut(rows){
  const svg=document.getElementById("donut"),NS="http://www.w3.org/2000/svg";if(!svg)return;svg.innerHTML="";const defs=document.createElementNS(NS,"defs"),clip=document.createElementNS(NS,"clipPath"),clipCircle=document.createElementNS(NS,"circle");clip.setAttribute("id","somxCenterClip");clipCircle.setAttribute("cx","450");clipCircle.setAttribute("cy","450");clipCircle.setAttribute("r","116");clip.appendChild(clipCircle);defs.appendChild(clip);svg.appendChild(defs);const wheel=document.createElementNS(NS,"g");wheel.id="wheel";svg.appendChild(wheel);let angle=-30;
  for(const[i,p]of rows.entries()){const w=Number.isFinite(p.weight)?p.weight:1/Math.max(1,rows.length),sweep=w*360,a0=angle,a1=angle+sweep;angle=a1;const path=document.createElementNS(NS,"path");path.setAttribute("d",arcPath(450,450,405,130,a0,a1));path.setAttribute("fill",colorFor(i));path.setAttribute("stroke","#fff");path.setAttribute("stroke-width","1.5");wheel.appendChild(path);if(sweep>18){const mid=(a0+a1)/2,[tx,ty]=polar(450,450,280,mid),g=document.createElementNS(NS,"g"),t=document.createElementNS(NS,"text");g.setAttribute("transform",`translate(${tx} ${ty})`);t.setAttribute("x","0");t.setAttribute("y","12");t.setAttribute("text-anchor","middle");t.setAttribute("fill","#fff");t.setAttribute("font-size",rows.length>10?"28":"40");t.setAttribute("font-weight","900");t.setAttribute("class","upright-label");t.textContent=p.ticker;g.appendChild(t);wheel.appendChild(g)}}
  const center=document.createElementNS(NS,"g");center.setAttribute("class","center-photo-hit");center.setAttribute("role","button");center.setAttribute("tabindex","0");const ring=document.createElementNS(NS,"circle");ring.setAttribute("cx","450");ring.setAttribute("cy","450");ring.setAttribute("r","122");ring.setAttribute("class","center-photo-ring");center.appendChild(ring);const saved=localStorage.getItem(CENTER_PHOTO_KEY);if(saved){const img=document.createElementNS(NS,"image");img.setAttribute("x","334");img.setAttribute("y","334");img.setAttribute("width","232");img.setAttribute("height","232");img.setAttribute("preserveAspectRatio","xMidYMid slice");img.setAttribute("clip-path","url(#somxCenterClip)");img.setAttribute("href",saved);center.appendChild(img)}else{for(const[y,text,size]of[[442,"사진",24],[476,"클릭해서 선택",18]]){const t=document.createElementNS(NS,"text");t.setAttribute("x","450");t.setAttribute("y",String(y));t.setAttribute("text-anchor","middle");t.setAttribute("class","center-photo-placeholder");t.setAttribute("font-size",String(size));t.textContent=text;center.appendChild(t)}}const pick=()=>document.getElementById("centerPhotoInput").click();center.addEventListener("click",pick);center.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();pick()}});svg.appendChild(center);
}
function toast(msg){const t=document.getElementById("toast");if(!t)return;t.textContent=msg;t.style.display="block";clearTimeout(toast._t);toast._t=setTimeout(()=>t.style.display="none",3600)}

async function ensureInitialized(){const invalid=activeStrategy.mode==="somv"?state.holdings.length<6||state.holdings.length>12||state.coreHoldings?.length!==6:state.holdings.length!==Number(activeStrategy.holdings);if(!state.initialized||invalid)await bootstrapStrategy()}
async function start(){if(starting||!creds)return;starting=true;try{render();await ensureInitialized();await catchUpRebalances();await getMonthBasePrices();await getLatestFallback();render();connectStream();refreshStrategyHistory().catch(console.error);toast(`${activeStrategy.label} · 무료 IEX 연결됨`)}catch(e){console.error(e);toast(e.message||"연결 실패");if(String(e.message).includes("401")||String(e.message).includes("403"))openModal()}finally{starting=false}}
async function previewStrategy(c){if(!creds&&c.factor!=="marketCap")throw new Error("Alpaca 연결이 필요합니다.");const{ranked}=await rankForRebalance(currentNYMonth(),c);return ranked}
async function applyStrategy(c){
  if(starting){throw new Error("현재 계산이 끝난 뒤 다시 적용해주세요")}
  if(switching)return;switching=true;try{if(socket)try{socket.close()}catch{}activeStrategy=clone(c);strategySig=globalThis.SOMXStrategy?.signature?.(activeStrategy)||activeStrategy.mode;latestPrices={};loadContext();render();if(creds){await ensureInitialized();await catchUpRebalances();await getMonthBasePrices();await getLatestFallback();render();connectStream();refreshStrategyHistory().catch(console.error)}window.dispatchEvent(new Event("somx:strategy-active"));window.dispatchEvent(new Event("somx:historychange"));toast(`${activeStrategy.label} 적용됨`)}finally{switching=false}
}
globalThis.SOMXLive={previewStrategy,applyStrategy,getMonthlyHistory:()=>clone(monthlyHistory),getHoldingsHistory:()=>clone(holdingsHistory),getWeightsHistory:()=>clone(weightsHistory),getState:()=>clone(state),getStrategy:()=>clone(activeStrategy),refreshHistory:refreshStrategyHistory,somvSelection};

setInterval(async()=>{if(!creds||starting||switching)return;if(state.rebalanceMonth<currentNYMonth()){try{await catchUpRebalances();await getMonthBasePrices();await getLatestFallback();render();connectStream();refreshStrategyHistory().catch(console.error)}catch(e){toast(e.message||"리밸런스 실패")}}},60000);
const modal=document.getElementById("modal");function openModal(){document.getElementById("keyId").value=creds?.keyId||"";document.getElementById("secretKey").value=creds?.secretKey||"";modal.classList.add("show")}function closeModal(){modal.classList.remove("show")}
document.getElementById("settingsBtn").onclick=openModal;document.getElementById("saveBtn").onclick=()=>{const keyId=document.getElementById("keyId").value.trim(),secretKey=document.getElementById("secretKey").value.trim();if(!keyId||!secretKey){toast("Key ID와 Secret을 입력하세요");return}saveCreds({keyId,secretKey});closeModal();start()};document.getElementById("clearBtn").onclick=()=>{localStorage.removeItem(CRED_KEY);creds=null;if(socket)socket.close();toast("API 키 삭제됨")};modal.addEventListener("click",e=>{if(e.target===modal&&creds)closeModal()});
document.getElementById("historyBtn").addEventListener("click",openHistory);document.getElementById("historyCloseBtn").addEventListener("click",closeHistory);document.getElementById("history-modal").addEventListener("click",e=>{if(e.target.id==="history-modal")closeHistory()});
const centerPhotoInput=document.getElementById("centerPhotoInput");centerPhotoInput.addEventListener("change",()=>{const file=centerPhotoInput.files?.[0];if(!file)return;if(!file.type.startsWith("image/")){toast("이미지 파일을 선택하세요");return}const reader=new FileReader();reader.onload=()=>{const img=new Image();img.onload=()=>{const size=512,canvas=document.createElement("canvas");canvas.width=size;canvas.height=size;const ctx=canvas.getContext("2d"),scale=Math.max(size/img.width,size/img.height),w=img.width*scale,h=img.height*scale;ctx.drawImage(img,(size-w)/2,(size-h)/2,w,h);try{localStorage.setItem(CENTER_PHOTO_KEY,canvas.toDataURL("image/jpeg",.86));render();toast("중앙 사진이 저장됐습니다")}catch{toast("사진 저장 실패: 더 작은 이미지를 사용하세요")}};img.src=reader.result};reader.readAsDataURL(file);centerPhotoInput.value=""});
render();if(creds)start();else openModal();
