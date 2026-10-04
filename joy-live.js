(()=>{
 'use strict';
 const spinOrigin=performance.now();
 const isJoy=()=>localStorage.getItem('somx.strategy.active.v1')==='joy';
 let data=null,forward=null,selected='live',promise=null,ws=null,quotes={},paintTimer=null,lastChartPaint=0,loading=false,epoch=0,status='최신 종가를 불러오는 중…',lastSnapshotFetch=0;
 const originalRender=render,originalStart=start;
 const fmt=v=>Number.isFinite(v)?`${v>=0?'+':''}${v.toFixed(2)}%`:'--';
 const $=id=>document.getElementById(id);
 function liveBase(){
  let h=forward?.months?.[forward.currentMonth];if(!h)return null;
  if(currentNYMonth()>h.month)h={...h,month:currentNYMonth(),port:0,firstDate:h.lastDate,rows:h.rows.map(r=>({...r,ret:0})),benchmarks:{SPY:0,QQQ:0},complete:false};
  return h;
 }
 function record(){return selected==='live'?(liveBase()?JoyLiveCore.mark(liveBase(),quotes):null):data?.months?.[selected]||forward?.months?.[selected];}
 function chartHistory(){
  const months={...(data?.months||{}),...(forward?.months||{})},h=record();
  if(selected==='live'&&h)months[h.month]=h;
  return months;
 }
 function setMetrics(id,value){const e=$(id);if(!e)return;e.textContent=fmt(value);e.classList.toggle('neg',Number.isFinite(value)&&value<0);}
 function renderJoy(){
  if(!isJoy())return;
  document.body.dataset.strategy='joy';$('joy-month')?.removeAttribute('hidden');
  const h=record(),isLive=selected==='live';
  document.querySelector('.summary-title > span').textContent=isLive?'환희 · 이번 달 수익률':`환희 · 백테스트 ${selected}`;
  $('benchmark-panel').style.display='';document.querySelectorAll('[data-benchmark]').forEach(b=>b.hidden=!['SPY','QQQ'].includes(b.dataset.benchmark));
  const th=$('holdings-body').closest('table').querySelectorAll('th');th[1].textContent=isLive?'현재 비중':'월말 비중';th[2].textContent='월간 수익률';
  if(!h){$('holdings-body').innerHTML='<tr><td colspan="3">최신 환희 데이터를 준비하는 중…</td></tr>';drawDonut([]);setMetrics('portfolio-return',null);$('up-count').textContent='--';$('best-ticker').textContent='--';$('worst-ticker').textContent='--';setMetrics('best-return',null);setMetrics('worst-return',null);return;}
  const rows=[...h.rows].sort((a,b)=>b.weight-a.weight);drawDonut(rows);const spinPhase=((performance.now()-spinOrigin)/1000)%80;document.querySelectorAll('#wheel,.upright-label').forEach(e=>e.style.animationDelay=`-${spinPhase}s`);$('donut').setAttribute('aria-label',isLive?'환희 최신 포트폴리오':'환희 백테스트 포트폴리오');
  $('holdings-body').innerHTML=rows.map((r,i)=>`<tr><td><div class="stock"><div class="logo" style="color:${colorFor(i)}">${r.ticker}</div><div>${r.ticker}${isLive?`<small class="holding-price">$${Number(r.price).toFixed(2)}</small>`:''}</div></div></td><td class="weight-cell">${(r.weight*100).toFixed(2)}%</td><td class="ret ${r.ret<0?'neg':''}">${fmt(r.ret)}</td></tr>`).join('');
  setMetrics('portfolio-return',h.port);const valid=rows.filter(r=>Number.isFinite(r.ret)),best=[...valid].sort((a,b)=>b.ret-a.ret)[0],worst=[...valid].sort((a,b)=>a.ret-b.ret)[0];
  $('up-count').textContent=`${valid.filter(r=>r.ret>0).length} / ${valid.length}`;$('best-ticker').textContent=best?.ticker||'--';setMetrics('best-return',best?.ret);$('worst-ticker').textContent=worst?.ticker||'--';setMetrics('worst-return',worst?.ret);
  $('joy-note').hidden=false;$('joy-note').textContent=isLive?`${status} · 신호 ${forward.signalDate} · 백테스트 ${data.start}부터 이어 계산. 가격에 따라 비중이 변하며 분기말 신호로 다음 거래일 종가에 리밸런싱합니다. 실제 주문·체결은 공포에서 확인하세요. 2025년 이후 순위 자료 확보율 96.8–100%.`:`백테스트 ${data.start} ~ ${forward?.asOf||data.end} · ${h.lastDate} 비중 · 종목: ${h.firstDate} 첫 종가 → 월말 종가 · 포트폴리오: 비용 포함 NAV`;
  monthlyHistory=chartHistory();
 }
 function fillSelector(){const s=$('joy-month');if(!s)return;const live=document.createElement('option');live.value='live';live.textContent='최신 · 실시간';const months=[...new Set([...Object.keys(data?.months||{}),...Object.keys(forward?.months||{})])].filter(m=>m<currentNYMonth()).sort().reverse();s.replaceChildren(live,...months.map(m=>{const o=document.createElement('option');o.value=m;o.textContent=m;return o}));if(selected!=='live'&&!months.includes(selected))selected='live';s.value=selected;}
 async function refreshSnapshot(){
  if(loading)return;loading=true;
  try{
   let r;try{r=await fetch('https://raw.githubusercontent.com/rpredpill/xlg-blender-render/somx-pages/joy-latest.json?t='+Date.now(),{cache:'no-store'});if(!r.ok)throw Error();}catch{r=await fetch('./joy-latest.json?t='+Date.now(),{cache:'no-store'});}
   if(!r.ok)throw Error('최신 종가 갱신을 기다리는 중');const j=await r.json();if(!j.ready||!j.months?.[j.currentMonth]?.rows?.length)throw Error('최신 데이터 검증 실패');
   if(forward?.asOf!==j.asOf||forward?.allocationDate!==j.allocationDate)quotes={};forward=j;lastSnapshotFetch=Date.now();
   status=`최신 종가 ${j.asOf} · IEX 실시간은 저장된 Alpaca 키로 연결`;
   fillSelector();if(isJoy()){renderJoy();window.dispatchEvent(new Event('somx:historychange'));}
  }catch(e){status=e.message;if(isJoy())renderJoy();}finally{loading=false;}
 }
 async function load(){
  if(!promise)promise=fetch('./joy-history.json?v=2').then(r=>{if(!r.ok)throw Error('과거 기록 조회 실패');return r.json()}).then(j=>{data=j;fillSelector();return j}).catch(e=>{promise=null;throw e});
  await Promise.all([promise,refreshSnapshot()]);
 }
 function savedCredentials(){try{const c=JSON.parse(localStorage.getItem('somx.alpaca.credentials.v1')||localStorage.getItem('flow.paper.credentials.v1')||'null');return c?.keyId&&c?.secretKey?c:null;}catch{return null;}}
 function regularTime(timestamp){const p=new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23',weekday:'short'}).formatToParts(new Date(timestamp));const f=Object.fromEntries(p.map(x=>[x.type,x.value]));return {date:`${f.year}-${f.month}-${f.day}`,open:!['Sat','Sun'].includes(f.weekday)&&Number(f.hour)*60+Number(f.minute)>=570&&Number(f.hour)*60+Number(f.minute)<960};}
 function stop(){epoch++;clearTimeout(paintTimer);paintTimer=null;if(ws){ws.onclose=null;ws.close();ws=null;}quotes={};}
 function accept(message){
  if(!message.t||!Number.isFinite(new Date(message.t).getTime()))return;
  const session=regularTime(message.t);if(!session.open||!(['SPY','QQQ'].includes(message.S)||liveBase()?.rows.some(r=>r.ticker===message.S))||session.date<(forward?.asOf||''))return;
  const price=Number(message.p??message.c);if(!(price>0))return;
  const prior=quotes[message.S];if(prior&&new Date(prior.time)>new Date(message.t))return;
  quotes[message.S]={price,time:message.t,date:session.date};status=`IEX 실시간 · ${new Date(message.t).toLocaleTimeString('ko-KR',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',second:'2-digit'})} KST`;
  if(isJoy()&&selected==='live'&&!paintTimer)paintTimer=setTimeout(()=>{paintTimer=null;if(!isJoy())return;renderJoy();if(Date.now()-lastChartPaint>5000){lastChartPaint=Date.now();window.dispatchEvent(new Event('somx:historychange'));}},500);
 }
 async function connect(){
  if(!isJoy()||document.hidden||!forward||selected!=='live'||ws)return;
  if(!regularTime(new Date()).open){status=`휴장 / 정규장 외 · 최신 종가 ${forward.asOf}`;renderJoy();return;}
  const c=savedCredentials();if(!c){status=`최신 종가 ${forward.asOf} · 장중 실시간은 설정에 저장된 Alpaca 키가 필요합니다`;renderJoy();return;}
  const token=epoch,symbols=[...liveBase().rows.map(r=>r.ticker),'SPY','QQQ'],headers={'APCA-API-KEY-ID':c.keyId,'APCA-API-SECRET-KEY':c.secretKey};
  try{const r=await fetch('https://data.alpaca.markets/v2/stocks/trades/latest?'+new URLSearchParams({symbols:symbols.join(','),feed:'iex'}),{headers});if(r.ok){const j=await r.json();if(token!==epoch||!isJoy())return;for(const [S,m] of Object.entries(j.trades||{}))accept({...m,S});}}
  catch{status=`시세 연결 실패 · ${forward.asOf} 종가 유지`;}
  if(token!==epoch||!isJoy())return;
  const stream=new WebSocket('wss://stream.data.alpaca.markets/v2/iex');ws=stream;
  stream.onopen=()=>{if(token!==epoch){stream.close();return;}stream.send(JSON.stringify({action:'auth',key:c.keyId,secret:c.secretKey}));};
  stream.onmessage=e=>{if(token!==epoch||!isJoy())return;let messages;try{messages=JSON.parse(e.data);}catch{return;}for(const m of messages){if(m.T==='success'&&m.msg==='authenticated'){status='IEX 연결됨 · 체결 시세 대기';stream.send(JSON.stringify({action:'subscribe',trades:symbols}));renderJoy();}else if(m.T==='t')accept(m);else if(m.T==='error'){status=`IEX 시세 연결 오류 ${m.code||''} · 최신 종가 유지`;stream.close();renderJoy();}}};
  stream.onerror=()=>{if(token===epoch){status=`실시간 연결 실패 · ${forward.asOf} 종가 유지`;renderJoy();}};
  stream.onclose=()=>{if(ws===stream)ws=null;};
 }
 async function activate(){
  stop();localStorage.setItem('somx.strategy.active.v1','joy');selected='live';
  activeStrategy={...globalThis.JoyStrategy.config};strategySig='flow-backtest-v1';if(socket){socket.close();socket=null;}
  state={initialized:true,rebalanceMonth:currentNYMonth(),holdings:[],targetWeights:{},basePrices:{},statuses:{}};
  document.body.dataset.strategy='joy';renderJoy();const token=epoch;
  try{await load();if(token!==epoch||!isJoy())return;fillSelector();renderJoy();window.dispatchEvent(new Event('somx:historychange'));connect();}catch(e){if(isJoy())$('holdings-body').innerHTML='<tr><td colspan="3">환희 데이터를 불러오지 못했습니다. 새로고침해 주세요.</td></tr>';}
 }
 render=function(){if(isJoy())return renderJoy();stop();document.querySelectorAll('[data-benchmark]').forEach(b=>b.hidden=false);$('joy-month')?.setAttribute('hidden','');$('joy-note')?.setAttribute('hidden','');const th=$('holdings-body')?.closest('table').querySelectorAll('th');if(th){th[1].textContent='상태';th[2].textContent='1개월 수익률';}return originalRender();};
 start=async function(){if(isJoy()){await refreshSnapshot();return connect();}return originalStart();};
 $('historyBtn').addEventListener('click',e=>{if(!isJoy())return;e.preventDefault();e.stopImmediatePropagation();document.querySelector('#history-modal h2').textContent='환희 · 월말 기록';$('history-status').textContent='과거 백테스트와 현재 모의 계산 · 실제 Paper 성과 아님';const records={...(data?.months||{}),...(forward?.months||{})};$('history-list').innerHTML=Object.keys(records).sort().reverse().map(m=>{const h=records[m];return `<details class="history-month"><summary><span class="history-month-label">${m}</span><span class="history-date">${h.provenance?'모의 계산':'백테스트'}${h.complete===false?' · 진행 중':''}</span><strong class="history-port ${h.port<0?'neg':''}">${fmt(h.port)}</strong></summary><div class="history-details">${h.rows.map(r=>`<div class="history-stock"><span>${r.ticker} · ${(r.weight*100).toFixed(2)}%</span><strong class="${r.ret<0?'neg':''}">${fmt(r.ret)}</strong></div>`).join('')}</div></details>`;}).join('');$('history-modal').classList.add('show');},true);
 function boot(){const s=document.createElement('select');s.id='joy-month';s.setAttribute('aria-label','환희 기간 선택');s.hidden=true;document.querySelector('.summary-title').append(s);const note=document.createElement('p');note.id='joy-note';note.hidden=true;document.querySelector('.summary-grid').after(note);s.onchange=()=>{stop();selected=s.value;renderJoy();window.dispatchEvent(new Event('somx:historychange'));connect();};if(isJoy())activate();}
 document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();else if(isJoy()){refreshSnapshot().then(connect);}});
 setInterval(()=>{if(isJoy()&&!document.hidden){if(Date.now()-lastSnapshotFetch>300000)refreshSnapshot().then(connect);else connect();}},30000);
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);else boot();
 globalThis.JoyLive={activate,render:renderJoy,stop,getMonthlyHistory:chartHistory,getBenchmarks:(months,symbol)=>{const records=chartHistory();return new Map(months.map(m=>[m,records[m]?.benchmarks?.[symbol]]));},isHistorical:()=>selected!=='live'&&!!data?.months?.[selected]};
})();
