"use strict";

/* ==================== SOMX FIXED METHODOLOGY ====================
   Universe: PIT S&P 500
   Momentum: 6-1 raw price momentum = prior month-end / five-month-earlier month-end - 1
   Holdings: 6
   Entry: current Top 6
   Exit: rank > 16 or index removal
   Rebalance: first US trading day of each month
   During month: no trading; weights drift with live prices
   =============================================================== */

const ALPACA_REST = "https://data.alpaca.markets/v2";
const ALPACA_WS   = "wss://stream.data.alpaca.markets/v2/iex";
const PIT_URL = "https://raw.githubusercontent.com/fja05680/sp500/master/S%26P%20500%20Historical%20Components%20%26%20Changes%20%28Updated%29.csv";
const STORAGE_KEY = "somx.live.state.v1";
const CRED_KEY = "somx.alpaca.credentials.v1";
const CENTER_PHOTO_KEY = "somx.center.photo.v1";
const HISTORY_KEY = "somx.monthly.history.v1";
const HOLDINGS_HISTORY_KEY = "somx.holdings.history.v1";

/* Canonical monthly holdings known from the current SOMX chain.
   Equal-weight monthly return is independent of ticker ordering. */
const seededHoldingsHistory = {
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

const palette = ["#1767c9","#e33b36","#f6b11a","#702ec9","#1e8b4b","#dd3b73"];
const seed = {
  rebalanceMonth:"2026-09",
  holdings:["MU","DELL","SNDK","STX","DDOG","MRNA"],
  statuses:{MU:"HOLD",DELL:"HOLD",SNDK:"HOLD",STX:"HOLD",DDOG:"HOLD",MRNA:"IN"},
  basePrices:{},
  updatedAt:null
};

let state = loadState();
let creds = loadCreds();
let latestPrices = {};
let socket = null;
let pitRows = null;
let starting = false;
let monthlyHistory = loadMonthlyHistory();
let holdingsHistory = loadHoldingsHistory();
let historyLoading = false;

function loadState(){
  try { return {...seed, ...(JSON.parse(localStorage.getItem(STORAGE_KEY))||{})}; }
  catch { return structuredClone(seed); }
}
function saveState(){ localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); }
function loadCreds(){ try{return JSON.parse(localStorage.getItem(CRED_KEY))||null}catch{return null} }
function loadMonthlyHistory(){ try{return JSON.parse(localStorage.getItem(HISTORY_KEY))||{}}catch{return {}} }
function saveMonthlyHistory(){ try{localStorage.setItem(HISTORY_KEY,JSON.stringify(monthlyHistory))}catch{} }
function loadHoldingsHistory(){
  let saved={}; try{saved=JSON.parse(localStorage.getItem(HOLDINGS_HISTORY_KEY))||{}}catch{}
  return {...seededHoldingsHistory,...saved};
}
function saveHoldingsHistory(){ try{localStorage.setItem(HOLDINGS_HISTORY_KEY,JSON.stringify(holdingsHistory))}catch{} }
function saveCreds(c){ localStorage.setItem(CRED_KEY, JSON.stringify(c)); creds=c; }
function headers(){ return {"APCA-API-KEY-ID":creds.keyId,"APCA-API-SECRET-KEY":creds.secretKey}; }

function fmt(v){
  if(!Number.isFinite(v)) return "--";
  return `${v>=0?"+":""}${v.toFixed(1)}%`;
}
function ym(d=new Date()){ return `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,"0")}`; }
function currentNYMonth(){
  const parts = new Intl.DateTimeFormat("en-CA",{timeZone:"America/New_York",year:"numeric",month:"2-digit"}).formatToParts(new Date());
  const y=parts.find(x=>x.type==="year").value, m=parts.find(x=>x.type==="month").value;
  return `${y}-${m}`;
}
function addMonths(ymStr,n){
  let [y,m]=ymStr.split("-").map(Number);
  const d=new Date(Date.UTC(y,m-1+n,1));
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,"0")}`;
}
function monthEndDate(ymStr){
  const [y,m]=ymStr.split("-").map(Number);
  return new Date(Date.UTC(y,m,0));
}
function isoDate(d){ return d.toISOString().slice(0,10); }

function parseCSVLine(line){
  const out=[]; let cur="", q=false;
  for(let i=0;i<line.length;i++){
    const c=line[i];
    if(c === '"'){
      if(q && line[i+1] === '"'){cur+='"'; i++;}
      else q=!q;
    } else if(c==="," && !q){out.push(cur);cur="";}
    else cur+=c;
  }
  out.push(cur); return out;
}
async function loadPitRows(){
  if(pitRows) return pitRows;
  const r=await fetch(PIT_URL,{cache:"no-store"});
  if(!r.ok) throw new Error("S&P 500 구성원 이력 다운로드 실패");
  const text=await r.text();
  const lines=text.trim().split(/\r?\n/);
  const head=parseCSVLine(lines[0]).map(x=>x.trim().toLowerCase());
  const di=head.findIndex(x=>x.includes("date"));
  const ci=head.findIndex(x=>x.includes("component")||x.includes("ticker")||x.includes("symbol"));
  pitRows=lines.slice(1).map(line=>{
    const a=parseCSVLine(line);
    return {date:new Date(a[di]+"T00:00:00Z"), tickers:(a[ci]||"").split(",").map(x=>x.trim()).filter(Boolean)};
  }).filter(x=>!Number.isNaN(+x.date)).sort((a,b)=>a.date-b.date);
  return pitRows;
}
async function universeAt(date){
  const rows=await loadPitRows();
  let lo=0,hi=rows.length-1,best=null;
  while(lo<=hi){
    const mid=(lo+hi)>>1;
    if(rows[mid].date<=date){best=rows[mid];lo=mid+1}else hi=mid-1;
  }
  if(!best) throw new Error("해당 날짜 S&P 500 구성원을 찾지 못함");
  return best.tickers;
}

async function alpaca(path){
  if(!creds) throw new Error("Alpaca 키가 필요합니다.");
  const r=await fetch(`${ALPACA_REST}${path}`,{headers:headers()});
  if(!r.ok){
    const t=await r.text().catch(()=> "");
    throw new Error(`Alpaca ${r.status}: ${t.slice(0,180)}`);
  }
  return r.json();
}
async function barsMonthEnd(symbols,targetDate){
  const y=targetDate.getUTCFullYear(), m=targetDate.getUTCMonth();
  const start=new Date(Date.UTC(y,m,18));
  const end=new Date(Date.UTC(y,m+1,2));
  const q=new URLSearchParams({
    symbols:symbols.join(","), timeframe:"1Day", start:isoDate(start), end:isoDate(end),
    adjustment:"all", feed:"iex", limit:"10000", sort:"asc"
  });
  const data=await alpaca(`/stocks/bars?${q}`);
  const result={};
  for(const [s,bars] of Object.entries(data.bars||{})){
    const valid=bars.filter(b=>new Date(b.t)<=targetDate);
    if(valid.length) result[s]=valid[valid.length-1].c;
  }
  return result;
}
async function rankForRebalance(rebalanceYM){
  const signalYM=addMonths(rebalanceYM,-1);
  const earlyYM=addMonths(signalYM,-5);
  const signalEnd=monthEndDate(signalYM);
  signalEnd.setUTCHours(23,59,59,999);
  const earlyEnd=monthEndDate(earlyYM);
  earlyEnd.setUTCHours(23,59,59,999);

  const uni=await universeAt(signalEnd);
  const [p1,p6]=await Promise.all([barsMonthEnd(uni,signalEnd),barsMonthEnd(uni,earlyEnd)]);
  const ranked=uni.map(s=>{
    const a=p1[s], b=p6[s];
    return (Number.isFinite(a)&&Number.isFinite(b)&&b>0)?{s,m:a/b-1}:null;
  }).filter(Boolean).sort((a,b)=>b.m-a.m);
  return {ranked, universe:new Set(uni)};
}
function transition(prev, ranked, universe){
  const pos=new Map(ranked.map((x,i)=>[x.s,i+1]));
  let keep=prev.filter(s=>universe.has(s) && (pos.get(s)??Infinity)<=16);
  for(const x of ranked.slice(0,6)){
    if(keep.length>=6) break;
    if(!keep.includes(x.s)) keep.push(x.s);
  }
  return keep.slice(0,6);
}
async function catchUpRebalances(){
  const nowYM=currentNYMonth();
  let cursor=state.rebalanceMonth||seed.rebalanceMonth;
  while(cursor<nowYM){
    if(!holdingsHistory[cursor]) holdingsHistory[cursor]=[...state.holdings];
    const next=addMonths(cursor,1);
    toast(`SOMX ${next} 리밸런스 계산 중…`);
    const {ranked,universe}=await rankForRebalance(next);
    const old=[...state.holdings];
    const holdings=transition(old,ranked,universe);
    const oldSet=new Set(old);
    holdingsHistory[next]=[...holdings];
    saveHoldingsHistory();
    state.holdings=holdings;
    state.statuses=Object.fromEntries(holdings.map(s=>[s,oldSet.has(s)?"HOLD":"IN"]));
    state.rebalanceMonth=next;
    state.basePrices={};
    state.updatedAt=new Date().toISOString();
    saveState();
    cursor=next;
  }
}
async function getMonthBasePrices(){
  const m=state.rebalanceMonth;
  if(state.basePrices && Object.keys(state.basePrices).length===state.holdings.length) return state.basePrices;
  const start=`${m}-01`;
  const next=addMonths(m,1)+"-01";
  const q=new URLSearchParams({
    symbols:state.holdings.join(","),timeframe:"1Day",start,end:next,
    adjustment:"all",feed:"iex",limit:"1000",sort:"asc"
  });
  const data=await alpaca(`/stocks/bars?${q}`);
  const base={};
  for(const s of state.holdings){
    const bars=(data.bars||{})[s]||[];
    if(bars.length) base[s]=bars[0].o;
  }
  state.basePrices=base;
  saveState();
  return base;
}
async function getLatestFallback(){
  const q=new URLSearchParams({symbols:state.holdings.join(","),feed:"iex"});
  const data=await alpaca(`/stocks/bars/latest?${q}`);
  for(const [s,obj] of Object.entries(data.bars||{})) if(obj?.c) latestPrices[s]=obj.c;
}
function connectStream(){
  if(socket) try{socket.close()}catch{}
  socket=new WebSocket(ALPACA_WS);
  socket.onopen=()=>socket.send(JSON.stringify({action:"auth",key:creds.keyId,secret:creds.secretKey}));
  socket.onmessage=(ev)=>{
    let msgs; try{msgs=JSON.parse(ev.data)}catch{return}
    if(!Array.isArray(msgs)) msgs=[msgs];
    for(const m of msgs){
      if(m.T==="success" && m.msg==="authenticated"){
        socket.send(JSON.stringify({action:"subscribe",trades:state.holdings}));
      } else if(m.T==="t" && m.S && Number.isFinite(m.p)){
        latestPrices[m.S]=m.p; render();
      } else if(m.T==="error"){
        toast(`실시간 연결 오류: ${m.msg||m.code}`);
      }
    }
  };
  socket.onclose=()=>{ if(creds) setTimeout(()=>connectStream(),5000); };
}

async function completedMonthReturn(month, holdings){
  if(!holdings || holdings.length!==6) throw new Error(`${month} 보유종목 이력이 없습니다.`);
  const next=addMonths(month,1);
  const q=new URLSearchParams({
    symbols:holdings.join(","), timeframe:"1Day", start:`${month}-01`, end:`${next}-03`,
    adjustment:"all", feed:"iex", limit:"1000", sort:"asc"
  });
  const data=await alpaca(`/stocks/bars?${q}`);
  const rows=[];
  for(const s of holdings){
    const bars=((data.bars||{})[s]||[]).filter(b=>String(b.t).slice(0,7)===month);
    if(!bars.length){ rows.push({ticker:s,ret:NaN}); continue; }
    const first=bars[0], last=bars[bars.length-1];
    const rel=(Number.isFinite(first.o)&&first.o>0&&Number.isFinite(last.c))?last.c/first.o:NaN;
    rows.push({ticker:s,ret:Number.isFinite(rel)?(rel-1)*100:NaN,first:first.o,last:last.c,lastDate:String(last.t).slice(0,10)});
  }
  const valid=rows.filter(x=>Number.isFinite(x.ret));
  const port=valid.length===6?valid.reduce((a,x)=>a+(1+x.ret/100),0)/6*100-100:NaN;
  return {month,port,rows,lastDate:valid[0]?.lastDate||null,holdings:[...holdings]};
}
async function ensureMonthlyHistory(){
  if(historyLoading||!creds)return;
  historyLoading=true;
  try{
    const current=currentNYMonth();
    const months=Object.keys(holdingsHistory).filter(m=>m<current).sort();
    for(const month of months){
      if(monthlyHistory[month] && Number.isFinite(monthlyHistory[month].port)) continue;
      const status=document.getElementById("history-status");
      if(status) status.textContent=`${month} 계산 중…`;
      try{
        monthlyHistory[month]=await completedMonthReturn(month,holdingsHistory[month]);
        saveMonthlyHistory();
        renderHistory();
      }catch(e){
        console.warn("history",month,e);
      }
    }
    const status=document.getElementById("history-status");
    if(status) status.textContent="각 달 첫 거래일 시가 → 마지막 거래일 종가";
  }finally{historyLoading=false}
}
function renderHistory(){
  const list=document.getElementById("history-list");
  if(!list)return;
  const months=Object.keys(monthlyHistory).sort().reverse();
  if(!months.length){
    list.innerHTML='<div class="history-empty">아직 계산된 월말 기록이 없습니다.</div>';
    return;
  }
  list.innerHTML=months.map(month=>{
    const h=monthlyHistory[month], rows=h.rows||[];
    const details=rows.map(x=>`<div class="history-stock"><span>${x.ticker}</span><strong class="${x.ret<0?"neg":""}">${fmt(x.ret)}</strong></div>`).join("");
    return `<details class="history-month">
      <summary><span class="history-month-label">${month}</span><span class="history-date">${h.lastDate||""}</span><strong class="history-port ${h.port<0?"neg":""}">${fmt(h.port)}</strong></summary>
      <div class="history-details">${details}</div>
    </details>`;
  }).join("");
}
function openHistory(){
  const modal=document.getElementById("history-modal");
  modal.classList.add("show");
  renderHistory();
  if(creds) ensureMonthlyHistory();
}
function closeHistory(){ document.getElementById("history-modal").classList.remove("show"); }

function metrics(){
  const rows=state.holdings.map(s=>{
    const p=latestPrices[s], b=state.basePrices?.[s];
    const rel=(Number.isFinite(p)&&Number.isFinite(b)&&b>0)?p/b:NaN;
    return {ticker:s,status:state.statuses?.[s]||"HOLD",price:p,base:b,rel,ret:Number.isFinite(rel)?(rel-1)*100:NaN};
  });
  const valid=rows.filter(x=>Number.isFinite(x.rel));
  const sum=valid.reduce((a,x)=>a+x.rel,0);
  rows.forEach(x=>x.weight=Number.isFinite(x.rel)&&sum>0?x.rel/sum:1/6);
  const port=valid.length===6?(valid.reduce((a,x)=>a+x.rel,0)/6-1)*100:NaN;
  return {rows,port};
}
function render(){
  const {rows,port}=metrics();
  const tbody=document.getElementById("holdings-body");
  tbody.innerHTML=rows.map((p,i)=>`
    <tr><td><div class="stock"><div class="logo" style="color:${palette[i]}">${p.ticker}</div><div>${p.ticker}</div></div></td>
    <td class="status-wrap"><span class="badge ${p.status==="IN"?"in":"hold"}">${p.status}</span></td>
    <td class="ret ${p.ret<0?"neg":""}">${fmt(p.ret)}</td></tr>`).join("");

  const valid=rows.filter(x=>Number.isFinite(x.ret));
  const up=valid.filter(x=>x.ret>0).length;
  const best=valid.length?[...valid].sort((a,b)=>b.ret-a.ret)[0]:null;
  const worst=valid.length?[...valid].sort((a,b)=>a.ret-b.ret)[0]:null;
  setMetric("portfolio-return",port);
  document.getElementById("up-count").textContent=valid.length?`${up} / 6`:"--";
  document.getElementById("best-ticker").textContent=best?.ticker||"--";
  setMetric("best-return",best?.ret);
  document.getElementById("worst-ticker").textContent=worst?.ticker||"--";
  setMetric("worst-return",worst?.ret);
  drawDonut(rows);
}
function setMetric(id,v){
  const el=document.getElementById(id); el.textContent=fmt(v); el.classList.toggle("neg",Number.isFinite(v)&&v<0);
}
function polar(cx,cy,r,a){const rad=(a-90)*Math.PI/180;return [cx+r*Math.cos(rad),cy+r*Math.sin(rad)]}
function arcPath(cx,cy,ro,ri,a0,a1){
  const [x0,y0]=polar(cx,cy,ro,a0),[x1,y1]=polar(cx,cy,ro,a1),[xi1,yi1]=polar(cx,cy,ri,a1),[xi0,yi0]=polar(cx,cy,ri,a0);
  const large=(a1-a0)>180?1:0;
  return `M ${x0} ${y0} A ${ro} ${ro} 0 ${large} 1 ${x1} ${y1} L ${xi1} ${yi1} A ${ri} ${ri} 0 ${large} 0 ${xi0} ${yi0} Z`;
}
function drawDonut(rows){
  const svg=document.getElementById("donut"),NS="http://www.w3.org/2000/svg";
  svg.innerHTML="";

  const defs=document.createElementNS(NS,"defs");
  const clip=document.createElementNS(NS,"clipPath");
  clip.setAttribute("id","somxCenterClip");
  const clipCircle=document.createElementNS(NS,"circle");
  clipCircle.setAttribute("cx","450");
  clipCircle.setAttribute("cy","450");
  clipCircle.setAttribute("r","116");
  clip.appendChild(clipCircle);
  defs.appendChild(clip);
  svg.appendChild(defs);

  const wheel=document.createElementNS(NS,"g");
  wheel.setAttribute("id","wheel");
  svg.appendChild(wheel);

  const cx=450,cy=450,ro=405,ri=130;
  let angle=-30;

  rows.forEach((p,i)=>{
    const w=Number.isFinite(p.weight)?p.weight:1/6;
    const sweep=w*360, a0=angle,a1=angle+sweep;
    angle=a1;

    const path=document.createElementNS(NS,"path");
    path.setAttribute("d",arcPath(cx,cy,ro,ri,a0,a1));
    path.setAttribute("fill",palette[i]);
    path.setAttribute("stroke","#fff");
    path.setAttribute("stroke-width","1.5");
    wheel.appendChild(path);

    if(sweep>18){
      const mid=(a0+a1)/2;
      const [tx,ty]=polar(cx,cy,280,mid);

      const labelGroup=document.createElementNS(NS,"g");
      labelGroup.setAttribute("transform",`translate(${tx} ${ty})`);
      wheel.appendChild(labelGroup);

      const t=document.createElementNS(NS,"text");
      t.setAttribute("x","0");
      t.setAttribute("y","12");
      t.setAttribute("text-anchor","middle");
      t.setAttribute("fill","#fff");
      t.setAttribute("font-size","40");
      t.setAttribute("font-weight","900");
      t.setAttribute("class","upright-label");
      t.textContent=p.ticker;
      labelGroup.appendChild(t);
    }
  });

  const center=document.createElementNS(NS,"g");
  center.setAttribute("class","center-photo-hit");
  center.setAttribute("role","button");
  center.setAttribute("tabindex","0");
  center.setAttribute("aria-label","중앙 사진 선택 또는 교체");
  svg.appendChild(center);

  const ring=document.createElementNS(NS,"circle");
  ring.setAttribute("cx","450");
  ring.setAttribute("cy","450");
  ring.setAttribute("r","122");
  ring.setAttribute("class","center-photo-ring");
  center.appendChild(ring);

  const savedPhoto=localStorage.getItem(CENTER_PHOTO_KEY);
  if(savedPhoto){
    const img=document.createElementNS(NS,"image");
    img.setAttribute("x","334");
    img.setAttribute("y","334");
    img.setAttribute("width","232");
    img.setAttribute("height","232");
    img.setAttribute("preserveAspectRatio","xMidYMid slice");
    img.setAttribute("clip-path","url(#somxCenterClip)");
    img.setAttribute("href",savedPhoto);
    center.appendChild(img);
  } else {
    const hint=document.createElementNS(NS,"text");
    hint.setAttribute("x","450");
    hint.setAttribute("y","442");
    hint.setAttribute("text-anchor","middle");
    hint.setAttribute("class","center-photo-placeholder");
    hint.textContent="사진";
    center.appendChild(hint);

    const hint2=document.createElementNS(NS,"text");
    hint2.setAttribute("x","450");
    hint2.setAttribute("y","476");
    hint2.setAttribute("text-anchor","middle");
    hint2.setAttribute("class","center-photo-placeholder");
    hint2.setAttribute("font-size","18");
    hint2.textContent="클릭해서 선택";
    center.appendChild(hint2);
  }

  const openPicker=()=>document.getElementById("centerPhotoInput").click();
  center.addEventListener("click",openPicker);
  center.addEventListener("keydown",e=>{
    if(e.key==="Enter"||e.key===" "){e.preventDefault();openPicker();}
  });
}
function toast(msg){
  const t=document.getElementById("toast");t.textContent=msg;t.style.display="block";clearTimeout(toast._t);toast._t=setTimeout(()=>t.style.display="none",3500);
}
async function start(){
  if(starting||!creds)return; starting=true;
  try{
    render();
    await catchUpRebalances();
    await getMonthBasePrices();
    await getLatestFallback();
    render();
    connectStream();
    ensureMonthlyHistory().catch(console.error);
    toast("무료 IEX 실시간 연결됨");
  }catch(e){
    console.error(e); toast(e.message||"연결 실패");
    if(String(e.message).includes("401")||String(e.message).includes("403")) openModal();
  }finally{starting=false}
}

setInterval(async()=>{
  if(!creds)return;
  if(state.rebalanceMonth<currentNYMonth()){
    try{await catchUpRebalances();await getMonthBasePrices();await getLatestFallback();render();connectStream();ensureMonthlyHistory().catch(console.error)}catch(e){toast(e.message)}
  }
},60_000);

const modal=document.getElementById("modal");
function openModal(){document.getElementById("keyId").value=creds?.keyId||"";document.getElementById("secretKey").value=creds?.secretKey||"";modal.classList.add("show")}
function closeModal(){modal.classList.remove("show")}
document.getElementById("settingsBtn").onclick=openModal;
document.getElementById("saveBtn").onclick=()=>{
  const keyId=document.getElementById("keyId").value.trim(),secretKey=document.getElementById("secretKey").value.trim();
  if(!keyId||!secretKey){toast("Key ID와 Secret을 입력하세요");return}
  saveCreds({keyId,secretKey});closeModal();start();
};
document.getElementById("clearBtn").onclick=()=>{localStorage.removeItem(CRED_KEY);creds=null;if(socket)socket.close();toast("API 키 삭제됨")};
modal.addEventListener("click",e=>{if(e.target===modal&&creds)closeModal()});

document.getElementById("historyBtn").addEventListener("click",openHistory);
document.getElementById("historyCloseBtn").addEventListener("click",closeHistory);
document.getElementById("history-modal").addEventListener("click",e=>{if(e.target.id==="history-modal")closeHistory()});

const centerPhotoInput=document.getElementById("centerPhotoInput");
centerPhotoInput.addEventListener("change",()=>{
  const file=centerPhotoInput.files?.[0];
  if(!file)return;
  if(!file.type.startsWith("image/")){toast("이미지 파일을 선택하세요");return;}
  const reader=new FileReader();
  reader.onload=()=>{
    const img=new Image();
    img.onload=()=>{
      const size=512;
      const canvas=document.createElement("canvas");
      canvas.width=size;canvas.height=size;
      const ctx=canvas.getContext("2d");
      const scale=Math.max(size/img.width,size/img.height);
      const w=img.width*scale,h=img.height*scale;
      ctx.drawImage(img,(size-w)/2,(size-h)/2,w,h);
      try{
        const data=canvas.toDataURL("image/jpeg",0.86);
        localStorage.setItem(CENTER_PHOTO_KEY,data);
        render();
        toast("중앙 사진이 저장됐습니다");
      }catch(e){
        toast("사진 저장 실패: 더 작은 이미지를 사용하세요");
      }
    };
    img.src=reader.result;
  };
  reader.readAsDataURL(file);
  centerPhotoInput.value="";
});

render();
if(creds) start(); else openModal();
