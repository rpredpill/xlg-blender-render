(()=>{
 'use strict';
 const M=PortfolioMath,$=id=>document.getElementById(id),mode=document.body.dataset.page;
 const PAPER='https://paper-api.alpaca.markets/v2',DATA='https://data.alpaca.markets/v2',COLORS={FLOW:'#2563eb',QLD:'#dc2626',QQQ:'#ec4899',VOO:'#84cc16'};
 const money=v=>(Number(v)<0?'−$':'$')+Math.abs(Number(v)).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
 const pct=v=>(v>=0?'+':'')+v.toFixed(2)+'%';
 let credentials=null,busy=false,accountId=null,rows=[],range='ALL',lastSuccess=0,checkedAt='',saved=false;
 const set=(id,text)=>{if($(id))$(id).textContent=text;};
 const status=text=>set('page-status',text);
 function json(key){try{return JSON.parse(localStorage.getItem(key)||'null');}catch{return null;}}
 async function get(base,path){
  if(!credentials)throw Error('Paper 계좌 연결 필요');
  const connection=credentials,controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);
  try{
   const r=await fetch(base+path,{cache:'no-store',headers:{'APCA-API-KEY-ID':connection.keyId,'APCA-API-SECRET-KEY':connection.secretKey},signal:controller.signal});
   if(!r.ok){let detail='';try{detail=(await r.json()).message||'';}catch{}throw Error('조회 실패 '+r.status+(detail?' · '+detail:''));}
   const value=await r.json();if(connection!==credentials)throw Error('연결 계좌가 변경되었습니다. 다시 조회합니다.');return value;
  }finally{clearTimeout(timeout);}
 }
 function svgEl(name,attrs={},text){const e=document.createElementNS('http://www.w3.org/2000/svg',name);for(const [k,v]of Object.entries(attrs))e.setAttribute(k,String(v));if(text!==undefined)e.textContent=text;return e;}
 function td(tr,text,className){const e=document.createElement('td');e.textContent=text;if(className)e.className=className;tr.append(e);}
 function renderWeights(account,positions){
  const result=M.weights(account,positions),state=json('flow.paper.state.v1.'+account.id),selected=state?.selected||[];
  set('weights-total',money(result.total));set('weights-stock',(result.total>0?(1-result.cash/result.total)*100:0).toFixed(2)+'%');set('weights-cash',(result.total>0?result.cash/result.total*100:0).toFixed(2)+'%');
  const svg=$('weight-ring');svg.replaceChildren();svg.append(svgEl('circle',{cx:140,cy:140,r:96,fill:'none',stroke:'#eef1f5','stroke-width':29}));
  const palette=['#2563eb','#ec4899','#22a7a0','#a78bfa','#f59e0b','#5d789b'];let offset=0;
  const body=$('weight-rows');body.replaceChildren();
  result.rows.forEach((r,i)=>{
   const color=r.symbol==='현금'?'#b5beca':palette[i%palette.length],length=2*Math.PI*96,arc=r.chartWeight;
   if(arc>0)svg.append(svgEl('circle',{cx:140,cy:140,r:96,fill:'none',stroke:color,'stroke-width':29,'stroke-dasharray':arc*length+' '+length,'stroke-dashoffset':-offset*length,transform:'rotate(-90 140 140)'}));offset+=arc;
   const tr=document.createElement('tr'),cell=document.createElement('td'),dot=document.createElement('i'),name=document.createElement('span');dot.className='legend-dot';dot.style.background=color;name.textContent=r.symbol;cell.append(dot,name);tr.append(cell);
   td(tr,money(r.value));td(tr,(r.weight*100).toFixed(2)+'%');
   const target=selected.includes(r.symbol)?20:r.symbol==='현금'&&state?.investAll?0:null;
   td(tr,target===null?'—':target.toFixed(2)+'%');td(tr,target===null?'—':pct(r.weight*100-target)+'p');body.append(tr);
  });
  set('weight-note',(result.cash<0?'현금 부족 '+money(result.cash)+' · 표는 부족액을 포함한 순자산 기준, 원형 그래프는 양수 자산 구성 기준입니다. ':'')+'실제 평가금액과 현금 합계 기준 · 목표는 최근 완료한 리밸런싱 기준 · 가격 변화에 따라 실제 비중이 달라집니다.');
 }
 let targetSignal=null,targetFetched=0,targetGeneration=0;
 async function loadTargets(account){
  if(mode!=='weights'||!$('holdings'))return;const token=++targetGeneration;
  try{
   if(!targetSignal||Date.now()-targetFetched>=300000){
    let response;try{response=await fetch('https://raw.githubusercontent.com/rpredpill/xlg-blender-render/somx-pages/flow-latest.json?t='+Date.now(),{cache:'no-store'});if(!response.ok)throw Error('신호 조회 실패');}catch{response=await fetch('./flow-latest.json?t='+Date.now(),{cache:'no-store'});}
    if(!response.ok)throw Error('신호 조회 실패');const next=await response.json();if(token!==targetGeneration)return;targetSignal=next;targetFetched=Date.now();
   }
   if(token!==targetGeneration)return;const state=account?json('flow.paper.state.v1.'+account.id):null,signal=targetSignal;
   const targets=state?.pending?.rows||FLOWPaperCore.targets(signal,state?.selected||[]),body=$('holdings');body.replaceChildren();
   for(const row of targets){const tr=document.createElement('tr');td(tr,row.ticker);td(tr,row.rank);td(tr,(row.weight*100).toFixed(2)+'%');body.append(tr);}
   set('signal-meta','신호 '+(state?.pending?.signalDate||signal.signalDate)+' · '+(state?.pending?'진행 중 주문의 고정 목표':signal.eligibleCount+'/'+signal.universeCount+'개 데이터 유효 · 최신 목표 준비됨'));
  }catch{if(token!==targetGeneration)return;$('holdings').replaceChildren();set('signal-meta','최신 편입 신호 확인 대기 · 기존 보유 및 접수 주문은 유지합니다.');}
 }
 async function bars(start,end){
  const out={QQQ:[],QLD:[],VOO:[]};let token=null;const seen=new Set();
  do{
   const q=new URLSearchParams({symbols:'QQQ,QLD,VOO',timeframe:'1Day',start,end,adjustment:'all',feed:'sip',limit:'10000',sort:'asc'});if(token)q.set('page_token',token);
   const data=await get(DATA,'/stocks/bars?'+q);
   for(const s of Object.keys(out))out[s].push(...(data.bars?.[s]||[]));
   token=data.next_page_token||null;if(token){if(seen.has(token)||seen.size>=100)throw Error('가격 이력의 나머지 페이지 확인 필요');seen.add(token);}
  }while(token);
  return out;
 }
 function renderCompare(){
  for(const k of ['1M','1Y','5Y','ALL'])$('compare-'+k)?.setAttribute('aria-pressed',String(range===k));
  const selected=M.period(rows,range),indexed=M.indexed(selected),box=$('compare-chart'),body=$('compare-results');box.replaceChildren();body.replaceChildren();
  if(indexed.length<2){const p=document.createElement('p');p.className='muted';p.textContent='같은 거래일의 기록이 2개 이상 쌓이면 비교 그래프를 표시합니다.';box.append(p);set('compare-period','비교 가능한 공통 기록이 아직 부족합니다.');return;}
  const dates=indexed[0].date+' ~ '+indexed.at(-1).date;
  set('compare-period',dates+' · 공통 '+indexed.length+'거래일 · 첫날 100');
  const all=indexed.flatMap(r=>Object.keys(COLORS).map(s=>r[s])),lo=Math.min(...all),hi=Math.max(...all),pad=Math.max((hi-lo)*.08,1),min=lo-pad,max=hi+pad;
  const svg=svgEl('svg',{viewBox:'0 0 900 340',role:'img','aria-label':'Paper 계좌와 QQQ QLD VOO 동일 기간 가치 변화'});svg.style.width='100%';
  for(let i=0;i<5;i++){const y=22+i*65,v=max-(max-min)*i/4;svg.append(svgEl('line',{x1:64,x2:880,y1:y,y2:y,stroke:'#eef1f5'}),svgEl('text',{x:55,y:y+5,'text-anchor':'end',fill:'#68768a','font-size':13},v.toFixed(1)));}
  const baseline=22+260*(max-100)/(max-min);svg.append(svgEl('line',{x1:64,x2:880,y1:baseline,y2:baseline,stroke:'#bdc7d4','stroke-dasharray':'5 5'}));
  for(const symbol of Object.keys(COLORS)){
   const color= indexed.at(-1)[symbol]>100?'#dc2626':'#2563eb',dash={FLOW:'',QQQ:'6 4',QLD:'10 4',VOO:'2 4'}[symbol];
   $('compare-color-'+symbol)?.setAttribute('stroke',color);
   const path=indexed.map((r,i)=>(i?'L':'M')+(64+i*816/(indexed.length-1)).toFixed(2)+','+(22+260*(max-r[symbol])/(max-min)).toFixed(2)).join(' ');
   svg.append(svgEl('path',{d:path,fill:'none',stroke:color,'stroke-width':2.7,'stroke-dasharray':dash}));
   const tr=document.createElement('tr');td(tr,symbol==='FLOW'?'FLOW · Paper 계좌':symbol);const change=indexed.at(-1)[symbol]-100;td(tr,pct(change),change>0?'positive':'negative');td(tr,indexed.at(-1)[symbol].toFixed(2));body.append(tr);
  }
  svg.append(svgEl('text',{x:64,y:324,fill:'#68768a','font-size':13},indexed[0].date),svgEl('text',{x:880,y:324,'text-anchor':'end',fill:'#68768a','font-size':13},indexed.at(-1).date));box.append(svg);
  set('compare-note','계좌는 입출금·이전 규칙 운용을 포함한 가치 변화입니다. ETF는 배당·분할 조정 종가 기준이며, 동일 납입 조건의 투자 수익률 비교는 아닙니다. 정규장 마감이 완료된 공통 거래일만 표시합니다.');
 }
 async function loadComparison(account){
  const cacheKey='flow.paper.compare.v1.'+account.id;
  if(accountId!==account.id){accountId=account.id;rows=[];checkedAt='';renderCompare();const cache=json(cacheKey);if(Array.isArray(cache?.rows)&&cache.rows.every(r=>/^\d{4}-\d{2}-\d{2}$/.test(r?.date)&&Object.keys(COLORS).every(s=>Number.isFinite(r[s])&&r[s]>0))){rows=cache.rows;checkedAt=cache.checkedAt||'';saved=true;renderCompare();}}
  const clock=await get(PAPER,'/clock'),today=M.date(clock.timestamp),startCalendar=new Date(clock.timestamp);startCalendar.setUTCDate(startCalendar.getUTCDate()-20);
  const calendar=await get(PAPER,'/calendar?start='+startCalendar.toISOString().slice(0,10)+'&end='+today);
  const time=new Intl.DateTimeFormat('en-GB',{timeZone:'America/New_York',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(clock.timestamp));
  const complete=calendar.filter(d=>d.date<today||d.date===today&&!clock.is_open&&time>=(d.close||'16:00')).at(-1)?.date;
  if(!complete)throw Error('완료 거래일 확인 필요');
  const created=Date.parse(account.created_at);if(!Number.isFinite(created))throw Error('계좌 개설일 확인 필요');
  const data=await get(PAPER,'/account/portfolio/history?timeframe=1D&start='+encodeURIComponent(new Date(created).toISOString())),history=M.history(data);
  if(!history.length){rows=[];renderCompare();return;}
  const end=new Date(Date.now()-16*60000).toISOString(),prices=await bars(history[0].date,end);
  for(const symbol of Object.keys(prices))if(!prices[symbol].length)throw Error(symbol+' 가격 이력을 불러오지 못했습니다.');
  rows=M.align(history,prices,complete);checkedAt=new Date().toISOString();saved=false;
  try{localStorage.setItem(cacheKey,JSON.stringify({rows,checkedAt}));}catch{}
  renderCompare();
 }
 async function loadSchedule(account){
  const clock=await get(PAPER,'/clock'),state=json('flow.paper.state.v1.'+account.id),plan=M.schedule(state,clock);
  const days=await get(PAPER,'/calendar?start='+plan.month+'-01&end='+plan.month+'-10'),first=days.filter(d=>d.date.startsWith(plan.month)).sort((a,b)=>a.date.localeCompare(b.date))[0];
  if(!first)throw Error('휴장일을 반영한 첫 거래일 확인 필요');
  set('rebalance-date',first.date.replaceAll('-','.'));
  set('rebalance-detail',plan.pending?'진행 중인 리밸런싱 주문이 있습니다.':plan.done?'이번 반기 완료 · 다음 반기 첫 거래일':plan.started?'이번 반기 실행 대상 · 미완료':'첫 투자 대기 · 이번 반기 기준일');
  set('schedule-checked',new Date().toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})+' KST 기준');
 }
 async function refresh(force=false){
  if(busy||!credentials||document.visibilityState==='hidden')return;
  if(!force&&Date.now()-lastSuccess<(mode==='compare'?300000:15000))return;
  busy=true;
  try{
   const account=await get(PAPER,'/account');
   if(mode==='weights'){const positions=await get(PAPER,'/positions');renderWeights(account,positions);void loadTargets(account);}else if(mode==='schedule')await loadSchedule(account);else await loadComparison(account);
   lastSuccess=Date.now();status(mode==='weights'?'현재 보유 비중 · Paper 계좌':mode==='schedule'?'리밸런싱 일정 · Paper 계좌':'동일 기간 비교 · 완료 거래일 기준');
   set('page-checked',(mode==='weights'?new Date(lastSuccess):new Date(checkedAt||lastSuccess)).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})+' KST 기준');
  }catch(e){if(mode==='weights'){$('weight-ring').replaceChildren();$('weight-rows').replaceChildren();set('weight-note','현재 보유 내역 조회 실패 · 계좌 연결에서 조회 상태를 확인하세요.');}if(mode==='schedule'){set('rebalance-date','—');set('rebalance-detail','일정을 확인하지 못했습니다. 계좌 연결에서 조회 상태를 확인하세요.');set('schedule-checked','');}status((mode==='compare'&&rows.length?'저장된 비교 기록 표시 · ':'')+'조회 확인 필요 · '+(e.name==='AbortError'?'응답 시간 초과':e.message));}
  finally{busy=false;}
 }
 for(const k of ['1M','1Y','5Y','ALL'])if($('compare-'+k))$('compare-'+k).onclick=()=>{range=k;renderCompare();};
 document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')void refresh(true);});
 window.addEventListener('online',()=>void refresh(true));setInterval(()=>void refresh(),15000);
 const existing=json('flow.paper.credentials.v1')||json('somx.alpaca.credentials.v1');
 if(existing?.keyId&&existing?.secretKey){credentials=existing;void refresh(true);}else{status('Paper 계좌 연결 필요 · 설정에 저장한 연결 정보를 이어 사용합니다.');}
 if(mode==='compare')renderCompare();if(mode==='weights'&&!credentials)void loadTargets(null);
})();
