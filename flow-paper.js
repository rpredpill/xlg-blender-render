(function(){
 'use strict';
 const C=FLOWPaperCore,$=id=>document.getElementById(id),KEY='flow.paper.credentials.v1';
 let credentials=null,account=null,signal=null,state=null,busy=false,timer=null;
 const stateKey=()=>`flow.paper.state.v1.${account.id}`;
 const save=()=>localStorage.setItem(stateKey(),JSON.stringify(state));
 const status=t=>$('status').textContent=t;
 const log=t=>{if(!state)return;state.log.unshift(`${new Date().toISOString()} ${t}`);state.log=state.log.slice(0,100);save();$('log').textContent=state.log.join('\n');};
 async function api(path,method='GET',body){
   C.assert(credentials,'Paper API 연결이 필요합니다.');
   const headers={'APCA-API-KEY-ID':credentials.keyId,'APCA-API-SECRET-KEY':credentials.secretKey};
   // Clock/calendar CORS permit authentication headers only. JSON is for writes;
   // cache:no-store can also add Cache-Control/Pragma, which Alpaca does not allow.
   if(body!==undefined)headers['Content-Type']='application/json';
   let r;try{r=await fetch(C.PAPER+path,{method,headers,body:body!==undefined?JSON.stringify(body):undefined});}
   catch{const e=Error(`Paper API 통신 실패 (${method} ${path.split('?')[0]}) · 네트워크 또는 브라우저 접근 제한을 확인하세요.`);e.transport=true;e.orderUncertain=method==='POST'&&path==='/orders';throw e;}
   if(!r.ok){let message='';try{message=(await r.json()).message||'';}catch{}throw Error(`Paper API ${r.status}: ${message}`);}
   return r.status===204?null:r.json();
 }
 function drawTargets(){
   const rows=C.targets(signal,state?.selected||[]);$('holdings').replaceChildren();
   rows.forEach(r=>{const tr=document.createElement('tr');[r.ticker,r.rank,(r.weight*100).toFixed(2)+'%'].forEach(t=>{const td=document.createElement('td');td.textContent=t;tr.append(td);});$('holdings').append(tr);});
   $('signal-meta').textContent=`신호 ${signal.signalDate} · ${signal.eligibleCount}/${signal.universeCount}개 21일 데이터 유효 · ${signal.ready?'실행 가능':'데이터 검증 필요'}`;
 }
 async function loadSignal(){let r;try{r=await fetch('https://raw.githubusercontent.com/rpredpill/xlg-blender-render/somx-pages/flow-latest.json?t='+Date.now(),{cache:'no-store'});C.assert(r.ok,'신호 조회 실패');}catch{r=await fetch('./flow-latest.json?t='+Date.now(),{cache:'no-store'});}C.assert(r.ok,'최신 신호 조회 실패');signal=await r.json();drawTargets();}
 async function refresh(){
   account=await api('/account');
   $('account').textContent=`Paper · ${account.status} · 계좌 가치 $${Number(account.equity).toLocaleString('en-US',{maximumFractionDigits:2})} · 현금 $${Number(account.cash).toLocaleString('en-US',{maximumFractionDigits:2})}`;
   if(state?.started){const now=new Date().toISOString();state.nav.push({time:now,equity:Number(account.equity),cash:Number(account.cash)});state.nav=state.nav.slice(-500);save();$('nav').replaceChildren();state.nav.slice(-15).reverse().forEach(r=>{const tr=document.createElement('tr');[r.time.replace('T',' ').slice(0,19)+' UTC',r.equity.toFixed(2),r.cash.toFixed(2)].forEach(t=>{const td=document.createElement('td');td.textContent=t;tr.append(td);});$('nav').append(tr);});$('performance').textContent='연결된 FLOW 전용 Paper 계좌의 실제 equity 기록입니다. 입출금이 있으면 수익률과 다를 수 있습니다.';}
 }
 function stop(message='실행 중지됨'){if(state){state.armed=false;save();}clearInterval(timer);timer=null;status(message);}
 async function connect(){
   try{
     credentials={keyId:$('paper-key').value.trim(),secretKey:$('paper-secret').value.trim()};
     C.assert(credentials.keyId&&credentials.secretKey,'Paper API 키를 입력하세요.');
     await refresh();C.assert(account.id,'Paper 계좌 확인 실패');
     // Validation must succeed on the fixed PAPER host before keys are retained.
     if($('remember').checked)localStorage.setItem(KEY,JSON.stringify(credentials));
     state=JSON.parse(localStorage.getItem(stateKey())||'null')||{armed:false,started:false,owned:[],log:[],nav:[],intents:{}};
     $('log').textContent=state.log.join('\n')||'아직 주문이 없습니다.';$('budget').value=state.budget||100000;
     await loadSignal();await refresh();$('start').disabled=false;
     status('Paper 계좌 연결 완료 · 실행 버튼을 누르면 다음 정규장 마감 부근에 시작합니다.');
     if(state.armed){timer=setInterval(tick,15000);status('Paper 실행 예약됨 · 이 화면을 열어 두세요.');tick();}
   }catch(e){$('start').disabled=true;credentials=null;stop(e.message);}
 }
 async function submit(order){
   const clock=await api('/clock');C.assert(clock.is_open&&(new Date(clock.next_close)-new Date(clock.timestamp))>15000,'마감까지 주문 시간이 부족합니다. 자동 재주문하지 않습니다.');
   // Intent is persisted before POST. A timeout never causes an automatic duplicate retry.
   log(`${order.side.toUpperCase()} ${order.symbol} ${order.notional?'$'+order.notional:order.qty+'주'} 제출`);
   const result=await api('/orders','POST',order);
   C.assert(!['rejected','canceled','expired'].includes(result.status),'주문 거절 또는 취소: '+order.symbol);
   state.intents[state.runningDate].orders.push({id:result.id,client_order_id:order.client_order_id,symbol:order.symbol});save();
   return result;
 }
 async function execute(date){
   C.assert(!state.intents[date],'오늘 실행 의도가 이미 기록돼 있습니다. Paper 계좌의 주문 내역을 확인하세요.');
   await refresh();const [positions,open]=await Promise.all([api('/positions'),api('/orders?status=open&limit=500')]);
   C.accountGuard(account,positions,open,state.owned);
   const rows=C.targets(signal,state.selected||[]);
   for(const row of rows){const asset=await api('/assets/'+encodeURIComponent(row.ticker));C.assert(asset.tradable&&asset.fractionable&&asset.class==='us_equity','소수점 개별주식 거래 불가: '+row.ticker);}
   const capital=Math.min(state.budget,Number(account.equity))*.995;
   C.assert(capital>=10,'투자 가능한 금액 부족');
   const target=new Map(rows.map(r=>[r.ticker,capital*r.weight]));
   // Once any order is submitted, stop/restart cannot silently run the same allocation twice.
   state.runningDate=date;state.intents[date]={cycle:state.nextCycle,signalDate:signal.signalDate,orders:[],status:'submitting'};
   state.owned=Array.from(new Set([...state.owned,...rows.map(r=>r.ticker)]));save();
   const sells=[];
   for(const p of positions){const excess=Number(p.market_value)-(target.get(p.symbol)||0);if(excess>1){const raw=(target.has(p.symbol)?excess/Number(p.current_price):Number(p.qty));const qty=(Math.floor(Math.min(raw,Number(p.qty))*1e6)/1e6).toFixed(6);if(Number(qty)>0)sells.push(await submit({symbol:p.symbol,qty,side:'sell',type:'market',time_in_force:'day',client_order_id:`flow3-${date}-${p.symbol.replace('.','')}-sell`}));}}
   for(const order of sells){let result=order;for(let i=0;i<8&&!['filled','canceled','expired','rejected'].includes(result.status);i++){await new Promise(r=>setTimeout(r,1000));result=await api('/orders/'+order.id);}C.assert(result.status==='filled','매도 체결이 완료되지 않았습니다. Paper 주문 내역 확인 필요');}
   await refresh();const after=await api('/positions');const values=new Map(after.map(p=>[p.symbol,Number(p.market_value)]));
   let available=Math.max(0,Number(account.cash)*.995);
   const buys=[];
   for(const row of rows){const need=Math.max(0,target.get(row.ticker)-(values.get(row.ticker)||0));const amount=Math.floor(Math.min(need,available)*100)/100;if(amount<1)continue;available-=amount;buys.push(await submit({symbol:row.ticker,notional:amount.toFixed(2),side:'buy',type:'market',time_in_force:'day',client_order_id:`flow3-${date}-${row.ticker.replace('.','')}-buy`}));}
   state.started=true;state.selected=rows.map(r=>r.ticker);state.intents[date].status='submitted';save();
   for(const order of buys){let result=await api('/orders/'+order.id);C.assert(result.status==='filled','매수 체결 확인 필요: '+order.symbol+' · Paper 주문 내역을 확인하세요.');}
   state.intents[date].status='filled';state.lastExecutionDate=date;state.lastCycle=state.intents[date].cycle;save();
   log('Paper 체결 확인 완료.');status('Paper 체결 확인 완료 · 다음 분기 리밸런싱 대기');await refresh();
 }
 async function tick(){
   if(busy||!state?.armed||!credentials)return;busy=true;
   try{
     const clock=await api('/clock');const now=new Date(clock.timestamp);const nyDate=new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).format(now);
     if(!clock.is_open){status('Paper 예약 중 · 정규장 마감 부근까지 대기 · 화면을 열어 두세요.');return;}
     const remaining=(new Date(clock.next_close)-now)/1000;
     if(remaining>90||remaining<30)return;
     const start=new Date(now);start.setUTCDate(start.getUTCDate()-14);
     const calendar=await api('/calendar?start='+start.toISOString().slice(0,10)+'&end='+nyDate);
     const previous=calendar.filter(d=>d.date<nyDate).at(-1)?.date;
     const range=C.calendarRange(nyDate),sessions=await api('/calendar?start='+range.start+'&end='+range.end),schedule=C.quarterSchedule(nyDate,sessions);
     if(state.started&&C.lastCycle(state,sessions)===schedule.latest.cycle)return;
     state.nextCycle=schedule.latest.cycle;
     await loadSignal();C.assert(signal.signalDate===previous,'직전 거래일 신호가 아직 없습니다. 최신 신호 갱신 후 다시 실행하세요.');
     C.assert(navigator.locks,'중복 실행 방지를 지원하는 최신 브라우저가 필요합니다.');
     await navigator.locks.request('flow-paper-'+account.id,{ifAvailable:true},async lock=>{if(!lock)return;state=JSON.parse(localStorage.getItem(stateKey()));if(state?.armed)await execute(nyDate);});
   }catch(e){const intent=state?.runningDate&&state.intents[state.runningDate];const uncertainty=e.orderUncertain||intent&&['submitting','submitted'].includes(intent.status);const suffix=uncertainty?' · 주문 접수/체결 확인 필요. 자동 재주문하지 않습니다.':' · 이번 실행에서 주문을 제출하지 않았습니다.';log('실행 중단: '+e.message+suffix);stop(e.message+suffix);}
   finally{busy=false;}
 }
 $('connect').onclick=connect;
 $('start').onclick=async()=>{try{C.assert(state&&credentials,'Paper 계좌 연결 필요');await refresh();C.accountGuard(account,await api('/positions'),await api('/orders?status=open&limit=500'),state.owned);const budget=Number($('budget').value);C.assert(Number.isFinite(budget)&&budget>=10,'$10 이상 투자 한도를 입력하세요.');state.budget=budget;state.armed=true;save();clearInterval(timer);timer=setInterval(tick,15000);log(`Paper 자동 실행 예약 · 투자 한도 $${budget} · 현금 한도 적용`);status('Paper 실행 예약됨 · 다음 정규장 마감 부근에 시작 · 화면을 열어 두세요.');tick();}catch(e){stop(e.message);}};
 $('stop').onclick=()=>stop('Paper 실행 중지됨. 이미 제출한 주문은 Alpaca Paper에서 확인하세요.');
 $('forget').onclick=()=>{stop();localStorage.removeItem(KEY);credentials=null;$('paper-key').value='';$('paper-secret').value='';$('start').disabled=true;status('Paper 키 삭제 완료');};
 loadSignal().catch(e=>status(e.message));
 const saved=JSON.parse(localStorage.getItem(KEY)||localStorage.getItem('somx.alpaca.credentials.v1')||'null');
 if(saved){$('remember').checked=!!localStorage.getItem(KEY);$('paper-key').value=saved.keyId||'';$('paper-secret').value=saved.secretKey||'';connect();}
})();


