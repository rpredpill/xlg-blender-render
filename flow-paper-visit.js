(function(){
 'use strict';
 const C=FLOWPaperCore,$=id=>document.getElementById(id),KEY='flow.paper.credentials.v1';
 let credentials=null,account=null,signal=null,state=null,busy=false,connecting=false,timer=null,lastPulseFetch=0;
 let displayBusy=false,pulseBusy=false,marketClock=null,clockChecked=0,calendar=[],calendarMonth='';
 const renderOperations=()=>globalThis.PulseView?.renderOperations(marketClock,state,calendar,clockChecked);
 const stateKey=()=>`flow.paper.state.v1.${account.id}`;
 const save=()=>localStorage.setItem(stateKey(),JSON.stringify(state));
 const status=t=>{$('status').textContent=t;globalThis.PulseView?.renderAccount(account,state);renderOperations();};
 const halfYear=C.halfYear;
 const nyDate=t=>new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(t));
 const log=t=>{if(!state)return;state.log.unshift(`${new Date().toISOString()} ${t}`);state.log=state.log.slice(0,150);save();$('log').textContent=state.log.join('\n');};
 async function api(path,method='GET',body){
   C.assert(credentials,'Paper API 연결이 필요합니다.');
   const connection=credentials,operation=`${method} ${path.split('?')[0]}`;
   const headers={'APCA-API-KEY-ID':connection.keyId,'APCA-API-SECRET-KEY':connection.secretKey};
   if(body!==undefined)headers['Content-Type']='application/json';
   const attempts=method==='GET'?3:1;
   for(let attempt=1;attempt<=attempts;attempt++){
     C.assert(credentials===connection,'계좌 연결이 변경되었습니다. 다시 확인하세요.');
     const controller=new AbortController(),started=Date.now();let timedOut=false;
     const timeout=setTimeout(()=>{timedOut=true;controller.abort();},15000);
     try{
       const r=await fetch(C.PAPER+path,{method,headers,cache:'no-store',body:body!==undefined?JSON.stringify(body):undefined,signal:controller.signal});
       if(!r.ok){
         let message='';try{message=(await r.json()).message||'';}catch{}
         const e=Error(`Paper API ${r.status} (${operation}): ${message}`);e.httpStatus=r.status;
         e.retryable=[429,500,502,503,504].includes(r.status);
         const retryAfter=r.headers?.get('Retry-After');
         if(retryAfter){const seconds=Number(retryAfter);e.retryDelay=Number.isFinite(seconds)?Math.max(0,seconds*1000):Math.max(0,Date.parse(retryAfter)-Date.now());if(!Number.isFinite(e.retryDelay)||e.retryDelay>5000)e.retryable=false;}
         throw e;
       }
       const value=r.status===204?null:await r.json();
       if(attempt>1&&credentials===connection)log(`Paper 조회 복구 (${operation}) · ${attempt}회 시도`);
       return value;
     }catch(cause){
       let e=cause;
       if(!cause.httpStatus){
         const offline=navigator.onLine===false;
         const kind=timedOut?'15초 응답 시간 초과':offline?'브라우저 오프라인':cause.name==='SyntaxError'?'응답 JSON 해석 실패':'네트워크 또는 브라우저 접근 차단 · 원인 미확정';
         const errorType=['TypeError','AbortError','SyntaxError'].includes(cause.name)?cause.name:'Error';
         e=Error(`Paper API 통신 실패 (${operation}) · ${kind} · ${errorType} · ${attempt}/${attempts}회 · ${Date.now()-started}ms · 화면 ${document.visibilityState==='hidden'?'숨김':'표시'}`);
         e.retryable=!offline;
       }
       e.orderUncertain=method==='POST'&&path==='/orders';
       if(attempt===attempts||!e.retryable||credentials!==connection){if(method==='GET'&&credentials===connection)log('조회 실패: '+e.message);throw e;}
       clearTimeout(timeout);
       const delay=Math.max(attempt===1?1000:3000,e.retryDelay||0);
       log(`조회 재시도 대기: ${e.message} · ${delay/1000}초 후 (${attempt+1}/${attempts})`);
       await new Promise(resolve=>setTimeout(resolve,delay));
     }finally{clearTimeout(timeout);}
   }
 }
 function drawTargets(){
   const rows=state?.pending?.rows||C.targets(signal,state?.selected||[]);$('holdings').replaceChildren();
   rows.forEach(r=>{const tr=document.createElement('tr');[r.ticker,r.rank,(r.weight*100).toFixed(2)+'%'].forEach(t=>{const td=document.createElement('td');td.textContent=t;tr.append(td);});$('holdings').append(tr);});
   const date=state?.pending?.signalDate||signal.signalDate;
   $('signal-meta').textContent=`신호 ${date} · ${signal.eligibleCount}/${signal.universeCount}개 데이터 유효 · ${state?.pending?'진행 중 주문의 고정 목표':signal.ready?'최신 목표 준비됨':'데이터 검증 필요'}`;
 }
 async function loadSignal(){let r;try{r=await fetch('https://raw.githubusercontent.com/rpredpill/xlg-blender-render/somx-pages/flow-latest.json?t='+Date.now(),{cache:'no-store'});C.assert(r.ok,'신호 조회 실패');}catch{r=await fetch('./flow-latest.json?t='+Date.now(),{cache:'no-store'});}C.assert(r.ok,'최신 신호 조회 실패');signal=await r.json();drawTargets();}
 const history=globalThis.FlowHistory?.create({api,document,storage:localStorage});
 function updatePulse(){
   if(!globalThis.PulseView)return;PulseView.renderAccount(account,state);
   if(!credentials||pulseBusy||Date.now()-lastPulseFetch<15000)return;pulseBusy=true;lastPulseFetch=Date.now();const pulseAccountId=account.id,pulseCredentials=credentials;
   Promise.allSettled([api('/positions'),api('/orders?status=all&limit=20&direction=desc')]).then(results=>{if(credentials!==pulseCredentials||account?.id!==pulseAccountId)return;for(const [i,r] of results.entries()){if(r.status==='fulfilled'){if(i===0)PulseView.renderPositions(r.value);else PulseView.renderOrders(r.value);}else PulseView.unavailable(i===0?'pulse-positions':'pulse-orders',i===0?3:5);}}).finally(()=>{pulseBusy=false;});
 }
 async function refresh(){
   const connection=credentials,next=await api('/account');if(connection!==credentials)return;account=next;updatePulse();void history?.update(account);
   $('account').textContent=`Paper · ${account.status} · 계좌 가치 $${Number(account.equity).toLocaleString('en-US',{maximumFractionDigits:2})} · 현금 $${Number(account.cash).toLocaleString('en-US',{maximumFractionDigits:2})}`;
   if(state?.started){const now=new Date().toISOString();if(!state.nav.length||Date.now()-Date.parse(state.nav.at(-1).time)>60000){state.nav.push({time:now,equity:Number(account.equity),cash:Number(account.cash)});state.nav=state.nav.slice(-500);save();}
     $('nav').replaceChildren();state.nav.slice(-15).reverse().forEach(r=>{const tr=document.createElement('tr');[r.time.replace('T',' ').slice(0,19)+' UTC',r.equity.toFixed(2),r.cash.toFixed(2)].forEach(t=>{const td=document.createElement('td');td.textContent=t;tr.append(td);});$('nav').append(tr);});globalThis.PulseView?.renderAccount(account,state);}
 }
 async function refreshDisplay(){
   if(displayBusy||connecting||busy||!credentials||document.visibilityState==='hidden')return;
   displayBusy=true;const connection=credentials;
   try{
     const results=await Promise.allSettled([refresh(),api('/clock')]);
     if(connection!==credentials)return;
     if(results[1].status==='fulfilled'){
       marketClock=results[1].value;clockChecked=Date.now();
       const d=nyDate(marketClock.is_open?marketClock.timestamp:marketClock.next_open),half=halfYear(d),done=state?.started&&state.lastHalfYear===half;
       const y=Number(d.slice(0,4)),h=Number(half.at(-1)),month=done?(h===1?`${y}-07`:`${y+1}-01`):`${y}-${h===1?'01':'07'}`;
       if(calendarMonth!==month){try{const days=await api('/calendar?start='+month+'-01&end='+month+'-10');if(connection!==credentials)return;calendar=days;calendarMonth=month;}catch{calendar=[];}}
     }
     renderOperations();
   }finally{displayBusy=false;}
 }
 function stop(message='접속 시 자동 실행 중지됨'){if(state){state.armed=false;state.manualPaused=true;delete state.pausedReason;save();}clearInterval(timer);timer=null;status(message);}
 function pause(message){if(state){state.pausedReason=message;save();}status(message);}
 function clearPause(){if(state?.pausedReason){delete state.pausedReason;save();globalThis.PulseView?.renderAccount(account,state);}}
 function installTimer(){clearInterval(timer);timer=setInterval(tick,30000);}
 function guard(positions,open){const own=new Set((state.pending?.sells||[]).concat(state.pending?.buys||[],state.exitCleanup?.orders||[],state.cashSweep?.orders||[]).map(o=>o.request.client_order_id));C.accountGuard(account,positions,open.filter(o=>!own.has(o.client_order_id)),state.owned);}
 async function connect(){
   if(busy||connecting)return;connecting=true;clearInterval(timer);timer=null;
   try{
     credentials={keyId:$('paper-key').value.trim(),secretKey:$('paper-secret').value.trim()};
     C.assert(credentials.keyId&&credentials.secretKey,'Paper API 키를 입력하세요.');
     account=null;state=null;marketClock=null;calendar=[];calendarMonth='';lastPulseFetch=0;history?.reset();globalThis.PulseView?.clear();renderOperations();await refresh();C.assert(account.id,'Paper 계좌 확인 실패');
     if($('remember').checked)localStorage.setItem(KEY,JSON.stringify(credentials));
     state=JSON.parse(localStorage.getItem(stateKey())||'null')||{armed:true,started:false,owned:[],selected:[],log:[],nav:[],intents:{}};
     state.owned=state.owned||[];state.selected=state.selected||[];state.nav=state.nav||[];state.log=state.log||[];state.intents=state.intents||{};
     if(C.migrateState(state))log('FLOW 규칙 전환 · 21일 평균 거래대금 Top5 · 동일비중 · 반기 · 이전 기록 유지');
     state.armed=state.manualPaused!==true;state.budget=Number.isFinite(Number(state.budget))&&Number(state.budget)>=10?Number(state.budget):Number($('budget').value)||100000;save();
     $('log').textContent=state.log.join('\n')||'아직 주문이 없습니다.';$('budget').value=state.budget||100000;
     await loadSignal();await refresh();$('start').disabled=false;
     status(state.armed?'Paper 연결 완료 · 접속 시 자동 매매를 확인합니다.':'Paper 연결 완료 · 수동 중지 상태를 유지합니다.');
     if(state.armed){installTimer();await tick();}
   }catch(e){$('start').disabled=!state;pause('연결 확인 대기 · '+e.message);if(e.httpStatus===401||e.httpStatus===403){clearInterval(timer);timer=null;}else if(state?.armed&&account){installTimer();}else if(!state&&credentials){timer=setInterval(connect,30000);}}finally{connecting=false;void refreshDisplay();}
 }
 function request(symbol,side,amount,date){return {symbol,side,type:'market',time_in_force:'day',extended_hours:false,...(side==='buy'?{notional:amount.toFixed(2)}:{qty:C.sellQuantity(amount)}),client_order_id:`flow3-${date}-${symbol.replace('.','')}-${side}`};}
 const record=request=>({request,local:'new',id:null,status:null});
 async function validateSignal(clock,executionDate){
   const today=nyDate(clock.timestamp),start=new Date(clock.timestamp);start.setUTCDate(start.getUTCDate()-20);
   const calendar=await api('/calendar?start='+start.toISOString().slice(0,10)+'&end='+executionDate);
   const time=new Intl.DateTimeFormat('en-GB',{timeZone:'America/New_York',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(clock.timestamp));
   const completed=calendar.filter(d=>d.date<today||(d.date===today&&!clock.is_open&&time>=(d.close||'16:00')));
   C.assert(completed.slice(-2).some(d=>d.date===signal.signalDate),'최신 완료 거래일 신호가 없습니다. 신호 갱신 후 다시 접속하세요.');C.assert(signal.ready,'편입 신호 검증 필요');
 }
 async function createPlan(clock,date){
   C.assert(!Object.values(state.intents).some(i=>['submitting','submitted'].includes(i.status)),'이전 버전의 미완료 주문이 있습니다. Alpaca Paper 주문 내역을 먼저 확인하세요.');
   await loadSignal();await validateSignal(clock,date);await refresh();
   const [positions,open]=await Promise.all([api('/positions'),api('/orders?status=open&limit=500')]);guard(positions,open);
   const rows=C.targets(signal,state.selected),capital=Math.min(state.budget,Number(account.equity))*.995;C.assert(capital>=10,'투자 가능한 금액 부족');
   for(const r of rows){const a=await api('/assets/'+encodeURIComponent(r.ticker));C.assert(a.tradable&&a.fractionable&&a.class==='us_equity','소수점 개별주식 거래 불가: '+r.ticker);}
   const target=Object.fromEntries(rows.map(r=>[r.ticker,capital*r.weight])),sells=[];
   for(const p of positions){
     if(!Object.hasOwn(target,p.symbol)){sells.push(record(request(p.symbol,'sell',C.sellQuantity(p.qty),date)));continue;}
     const excess=Number(p.market_value)-target[p.symbol];
     if(excess>1){C.assert(Number(p.current_price)>0,'매도 시세 확인 필요: '+p.symbol);const qty=Math.floor(Math.min(excess/Number(p.current_price),Number(p.qty))*1e9)/1e9;if(qty>0)sells.push(record(request(p.symbol,'sell',qty,date)));}
   }
   state.pending={date,halfYear:halfYear(date),ruleId:C.RULE_ID,signalDate:signal.signalDate,rows,target,sells,buys:[],phase:'selling'};
   state.owned=Array.from(new Set([...state.owned,...rows.map(r=>r.ticker)]));save();log(`접속 시 실행 계획 생성 · ${date} · 신호 ${signal.signalDate}`);drawTargets();
 }
 async function settle(orders){
   let filled=true;
   for(const o of orders){
     let result;
     if(o.local==='new'){
       o.local='uncertain';save(); // Durable before POST; never repost an uncertain request.
       log(`${o.request.side.toUpperCase()} ${o.request.symbol} 주문 제출`);
       result=await api('/orders','POST',o.request);C.assert(result.id,'주문 ID 응답 확인 필요');o.id=result.id;o.local='submitted';o.status=result.status;save();
     }else if(o.local==='uncertain'){
       try{result=await api('/orders:by_client_order_id?client_order_id='+encodeURIComponent(o.request.client_order_id));}
       catch(e){if(e.httpStatus===404)throw Error('주문 접수 여부를 확인할 수 없습니다. 자동 재전송하지 않습니다: '+o.request.symbol);throw e;}
       C.assert(result.id,'주문 접수 확인 필요');o.id=result.id;o.local='submitted';o.status=result.status;save();log(`${o.request.symbol} 기존 주문 접수 확인 · 재전송 없음`);
     }
     if(o.status!=='filled'){result=await api('/orders/'+o.id);o.status=result.status;save();}
     C.assert(!['rejected','canceled','expired','replaced','suspended'].includes(o.status),'주문 상태 확인 필요: '+o.request.symbol+' '+o.status);
     if(o.status!=='filled')filled=false;
   }
   return filled;
 }
 async function advance(clock){
   const plan=state.pending;await refresh();
   const [positions,open]=await Promise.all([api('/positions'),api('/orders?status=open&limit=500')]);guard(positions,open);
   if(plan.phase==='selling'){
     if(!await settle(plan.sells)){status(clock.is_open?'Paper 매도 체결 대기 · 다음 접속 때 이어 처리':'Paper 매도 주문 예약됨 · 다음 정규장 체결 후 다음 접속 때 매수를 이어 처리');return;}
     await refresh();const after=await api('/positions');const values=new Map(after.map(p=>[p.symbol,Number(p.market_value)]));
     // Only broker-confirmed sale proceeds count as cash. Buying power is never used.
     let available=Math.max(0,Number(account.cash)*.995);plan.buys=[];
     for(const r of plan.rows){const need=Math.max(0,plan.target[r.ticker]-(values.get(r.ticker)||0));const amount=Math.floor(Math.min(need,available)*100)/100;if(amount<1)continue;available-=amount;plan.buys.push(record(request(r.ticker,'buy',amount,plan.date)));}
     plan.phase='buying';save();
   }
   if(!await settle(plan.buys)){status(clock.is_open?'Paper 매수 체결 대기 · 화면을 닫아도 접수한 주문은 유지됩니다.':'Paper 매수 주문 예약됨 · 다음 정규장에 체결 시도 · 화면을 닫아도 됩니다.');return;}
   state.started=true;state.selected=plan.rows.map(r=>r.ticker);state.lastExecutionDate=plan.date;state.lastHalfYear=plan.halfYear;
   state.intents[plan.date]={status:'filled',signalDate:plan.signalDate,mode:'visit',ruleId:plan.ruleId,orders:plan.sells.concat(plan.buys).map(o=>({id:o.id,client_order_id:o.request.client_order_id,symbol:o.request.symbol}))};
   state.pending=null;save();log('Paper 기본 매수 체결 확인 완료 · 잔여 현금 추가 매수 확인');status('기본 매수 체결 완료 · 잔여 현금 투자 확인');await refresh();drawTargets();
 }
 async function cleanupExited(clock){
   if(!state.started||!state.selected?.length)return false;
   await refresh();
   const [positions,open]=await Promise.all([api('/positions'),api('/orders?status=open&limit=500')]);guard(positions,open);
   if(!state.exitCleanup){
     const residuals=positions.filter(p=>state.owned.includes(p.symbol)&&!state.selected.includes(p.symbol)&&Number(p.qty)>0&&Number(p.qty)<=0.000001000001&&Math.abs(Number(p.market_value))<0.01);
     if(!residuals.length)return false;
     const batch=Date.now().toString(36);
     state.exitCleanup={orders:residuals.map(p=>{const r=request(p.symbol,'sell',C.sellQuantity(p.qty),nyDate(clock.timestamp));r.client_order_id='flow-dust-'+batch+'-'+p.symbol.replace('.','');return record(r);})};save();log('편출 종목 소수점 잔여 전량 매도 계획 저장');
   }
   if(!await settle(state.exitCleanup.orders)){status('편출 종목 잔여 매도 체결 대기');return true;}
   const after=await api('/positions'),symbols=new Set(state.exitCleanup.orders.map(o=>o.request.symbol));
   C.assert(!after.some(p=>symbols.has(p.symbol)&&Number(p.qty)>0),'잔여 매도 체결 후 보유 수량 확인 대기');
   state.exitCleanupHistory=state.exitCleanupHistory||[];state.exitCleanupHistory.push({...state.exitCleanup,completedAt:new Date().toISOString()});state.exitCleanup=null;save();log('편출 종목 소수점 잔여 매도 완료');lastPulseFetch=0;await refresh();return false;
 }
 async function sweepCash(clock){
   if(!state.started||state.selected?.length!==5)return false;
   // A second pass begins only after prior orders have filled and no unrelated orders remain.
   await refresh();const [positions,open]=await Promise.all([api('/positions'),api('/orders?status=open&limit=500')]);guard(positions,open);
   if(!state.cashSweep){
     if(!clock.is_open)return false;
     const allocations=C.cashAllocation(account,positions,state.selected,state.budget);if(!allocations.length)return false;
     const batch=Date.now().toString(36);
     state.cashSweep={createdAt:new Date().toISOString(),orders:allocations.map(a=>{const r=request(a.symbol,'buy',Number(a.notional),nyDate(clock.timestamp));r.client_order_id='flow-cash-'+batch+'-'+a.symbol.replace('.','');return record(r);})};
     save();log('잔여 현금 추가 매수 계획 저장 · 현금 및 투자 한도 내 · 부족한 비중 우선');
   }
   if(!await settle(state.cashSweep.orders)){status('잔여 현금 추가 매수 체결 대기');return true;}
   state.cashSweepHistory=state.cashSweepHistory||[];state.cashSweepHistory.push({...state.cashSweep,completedAt:new Date().toISOString()});state.cashSweep=null;save();log('잔여 현금 추가 매수 체결 확인 완료');lastPulseFetch=0;await refresh();return false;
 }
 async function tick(){
   if(busy||!state?.armed||!credentials)return;busy=true;
   try{
     C.assert(navigator.locks,'중복 실행 방지를 지원하는 최신 브라우저가 필요합니다.');
     await navigator.locks.request('flow-paper-'+account.id,{ifAvailable:true},async lock=>{
       if(!lock)return;state=JSON.parse(localStorage.getItem(stateKey()));if(!state?.armed)return;
       const clock=await api('/clock');marketClock=clock;clockChecked=Date.now();renderOperations();
       if(state.pending && state.pending.ruleId!==C.RULE_ID){
         // Reconcile already submitted legacy orders, never submit the remaining old requests.
         const legacy=state.pending,submitted=legacy.sells.concat(legacy.buys).filter(o=>o.local!=='new');
         if(!await settle(submitted)){status('이전 FLOW 주문 체결 확인 대기 · 새 Top5 주문은 아직 제출하지 않습니다.');return;}
         state.legacyPlans=state.legacyPlans||[];state.legacyPlans.push({...legacy,archivedAt:new Date().toISOString(),reason:'strategy_changed'});
         state.pending=null;save();log('이전 주문 확인 완료 · 미제출 이전 목표를 종료하고 새 FLOW로 전환');
       }
       if(state.pending){await advance(clock);if(!state.pending)await sweepCash(clock);clearPause();return;}
       if(state.cashSweep){if(await sweepCash(clock))return;}
       if(await cleanupExited(clock))return;
       const date=clock.is_open?nyDate(clock.timestamp):nyDate(clock.next_open);C.assert(/^\d{4}-\d{2}-\d{2}$/.test(date),'거래일 확인 필요');
       if(state.started&&state.lastHalfYear===halfYear(date)){if(await sweepCash(clock))return;await refresh();clearPause();status(clock.is_open?'운용 확인 완료 · 주문 가능한 잔여 현금 투자 확인':'장 마감 · 잔여 현금 추가 매수는 다음 정규장 접속 시 확인');return;}
       await createPlan(clock,date);await advance(clock);if(!state.pending)await sweepCash(clock);clearPause();
     });
   }catch(e){const p=state?.pending,hasIntent=!!(p&&p.sells.concat(p.buys).some(o=>o.local!=='new'))||!!state?.exitCleanup?.orders.some(o=>o.local!=='new')||!!state?.cashSweep?.orders.some(o=>o.local!=='new');const suffix=e.orderUncertain||hasIntent?' · 기존 주문은 유지됩니다. 접수/체결 확인 후 이어 처리하며, 중복 재전송하지 않습니다.':' · 이번 실행에서 주문을 제출하지 않았습니다.';log('실행 확인 대기: '+e.message+suffix);pause(e.message+suffix);}
   finally{busy=false;}
 }
 $('connect').onclick=connect;
 $('start').onclick=async()=>{try{C.assert(state&&credentials,'Paper 계좌 연결 필요');C.assert(!busy,'기존 실행이 진행 중입니다.');await refresh();guard(await api('/positions'),await api('/orders?status=open&limit=500'));const budget=Number($('budget').value);C.assert(Number.isFinite(budget)&&budget>=10,'$10 이상 투자 한도를 입력하세요.');state.budget=budget;state.armed=true;state.manualPaused=false;delete state.pausedReason;save();installTimer();log(`접속 시 자동 실행 활성화 · 투자 한도 $${budget} · 현금 한도 적용`);await tick();}catch(e){pause(e.message);}};
 $('stop').onclick=()=>stop('접속 시 자동 실행 중지됨. 이미 접수한 주문은 Alpaca Paper에서 확인하세요.');
 $('forget').onclick=()=>{stop();localStorage.removeItem(KEY);credentials=null;account=null;state=null;marketClock=null;calendar=[];calendarMonth='';history?.reset();globalThis.PulseView?.clear();$('paper-key').value='';$('paper-secret').value='';$('start').disabled=true;status('Paper 키 삭제 완료');};
 setInterval(()=>{renderOperations();void refreshDisplay();},15000);
 document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible'){lastPulseFetch=0;void refreshDisplay().then(()=>tick());}});
 window.addEventListener('online',()=>{lastPulseFetch=0;void refreshDisplay();});
 loadSignal().catch(e=>status(e.message));
 const saved=JSON.parse(localStorage.getItem(KEY)||localStorage.getItem('somx.alpaca.credentials.v1')||'null');
 if(saved){$('remember').checked=!!localStorage.getItem(KEY);$('paper-key').value=saved.keyId||'';$('paper-secret').value=saved.secretKey||'';connect();}
})();
