/* All trading requests are fixed to the PAPER endpoint. No live endpoint option. */
(function(root){
 'use strict';
 const PAPER='https://paper-api.alpaca.markets/v2';
 function assert(ok,message){if(!ok)throw Error(message);}
 const CASH_RESERVE=1;
 const RULE_ID='FLOW_MEAN21_TOP5_EQUAL_QQQ_CALENDAR_QUARTERLY_V3';
 function targets(signal,previous=[]){
   assert(signal?.ready && signal.version===3 && signal.ruleId===RULE_ID,'새 FLOW 신호가 준비되지 않았습니다.');
   assert(Array.isArray(signal.ranking)&&signal.ranking.length>=5,'Top5 데이터가 부족합니다.');
   const rows=signal.ranking.slice(0,5);
   assert(new Set(rows.map(r=>r.ticker)).size===5&&rows.every((r,i)=>r.rank===i+1&&Number.isFinite(r.score)&&r.score>0),'Top5 순위 데이터 오류');
   return rows.map(r=>({...r,weight:.2}));
 }
 function halfYear(d){return d.slice(0,4)+'H'+(Number(d.slice(5,7))<=6?1:2);}
 function calendarRange(d){const y=Number(d.slice(0,4));return {start:(y-1)+'-12-01',end:(y+1)+'-12-31'};}
 function quarterSchedule(d,calendar){
   assert(/^\d{4}-\d{2}-\d{2}$/.test(d),'실행 날짜 확인 필요');
   const y=Number(d.slice(0,4)),sessions=calendar.map(x=>x.date).sort(),events=[];
   for(let year=y-1;year<=y+1;year++)for(const month of [3,6,9,12]){
     const first=new Date(Date.UTC(year,month-1,1)),day=1+(5-first.getUTCDay()+7)%7+14;
     const friday=new Date(Date.UTC(year,month-1,day)).toISOString().slice(0,10);
     const next=sessions.find(x=>x>friday&&x<=friday.slice(0,7)+'-31');
     if(next)events.push({date:next,cycle:year+'Q'+month/3});
   }
   const latest=events.filter(x=>x.date<=d).at(-1),next=events.find(x=>x.date>d);
   assert(latest&&next,'분기 리밸런싱 거래일 확인 필요');return {latest,next};
 }
 function lastCycle(state,calendar){
   if(state.lastCycle)return state.lastCycle;
   if(state.started&&state.lastExecutionDate){return quarterSchedule(state.lastExecutionDate,calendar).latest.cycle;}
   return null;
 }
 function migrateState(state){
   if(state.ruleId===RULE_ID)return false;
   const previous=state.ruleId||'FLOW_V1_63_MEDIAN_TOP10_EXIT20_QUARTER';
   state.ruleTransitions=state.ruleTransitions||[];
   state.ruleTransitions.push({time:new Date().toISOString(),from:state.ruleId||'FLOW_V1_63_MEDIAN_TOP10_EXIT20_QUARTER',to:RULE_ID});
   for(const i of Object.values(state.intents||{}))if(!i.ruleId)i.ruleId=previous;
   if(state.pending?.ruleId==='FLOW_MEAN21_TOP5_EQUAL_SEMIANNUAL_V2'){
     state.pending.originRuleId=state.pending.ruleId;state.pending.ruleId=RULE_ID;
   }
   state.ruleId=RULE_ID;delete state.lastCycle;
   return true;
 }
 function accountGuard(account,positions,orders,owned=[],allowSmallDebt=false){
   assert(account.status==='ACTIVE'&&!account.trading_blocked&&!account.account_blocked,'계좌가 거래 가능한 상태가 아닙니다.');
   const cash=Number(account.cash),equity=Number(account.equity),long=Number(account.long_market_value||0),short=Number(account.short_market_value||0);
   assert([cash,equity,long,short].every(Number.isFinite)&&equity>0&&long>=0&&short===0,'계좌 현금·평가금액·공매도 상태 확인 필요');
   const smallDebt=allowSmallDebt&&cash<0&&-cash<=Math.min(5,equity*.0001);
   assert(cash>=0||smallDebt,'현금 부족 · 신규 매수 중단 · 잔액 확인 필요');
   assert(smallDebt||long<=equity+1,'차입 투자 상태에서는 실행하지 않습니다.');
   assert(positions.every(p=>p.side==='long'&&owned.includes(p.symbol)&&Number(p.qty)>0),'FLOW 전용 빈 Paper 계좌가 필요합니다. 기존 다른 보유 종목을 매매하지 않습니다.');
   assert(orders.length===0,'미체결 주문이 있습니다. 먼저 계좌에서 확인하세요.');
 }
 function buyBudget(account,requested){
   assert(Number.isFinite(requested)&&requested>=10,'투자 금액은 $10 이상이어야 합니다.');
   // Reserve for execution price drift. Buying power / margin never enters this calculation.
   return Math.max(0,Math.min(requested,Number(account.cash))*.995);
 }
 function initialOrders(rows,budget,date){
   assert(budget>=10,'사용 가능한 현금이 부족합니다.');
   return rows.map(r=>({symbol:r.ticker,notional:(Math.floor(budget*r.weight*100)/100).toFixed(2),side:'buy',type:'market',time_in_force:'day',client_order_id:`flow3-${date}-${r.ticker.replace('.','')}-buy`}));
 }
 function sellQuantity(value){
   const qty=typeof value==='string'?value:Number(value).toFixed(9);
   assert(/^\d+(\.\d{1,9})?$/.test(qty)&&Number(qty)>0,'매도 수량 확인 필요');
   return qty;
 }
 function cashAllocation(account,positions,selected,budget,investAll=false){
   assert(selected.length===5&&new Set(selected).size===5,'현행 FLOW 보유 목표 확인 필요');
   const equity=Number(account.equity),cash=Number(account.cash),values=new Map(positions.map(p=>[p.symbol,Number(p.market_value)]));
   assert(Number.isFinite(equity)&&Number.isFinite(cash)&&cash>=0&&Number.isFinite(budget)&&budget>=10&&[...values.values()].every(v=>Number.isFinite(v)&&v>=0),'추가 매수 금액 확인 필요');
   const invested=[...values.values()].reduce((a,b)=>a+b,0),limit=investAll?invested+cash:Math.min(budget,equity);
   const cents=Math.floor(Math.max(0,Math.min(cash-CASH_RESERVE,limit-invested))*100+1e-7);
   if(cents<100)return [];
   const rows=selected.map(symbol=>({symbol,need:Math.max(0,limit/5-(values.get(symbol)||0)),cents:0})).sort((a,b)=>b.need-a.need||a.symbol.localeCompare(b.symbol));
   const total=rows.reduce((s,r)=>s+r.need,0);if(total<=0)return [];
   for(const r of rows){const amount=Math.floor(cents*r.need/total);r.cents=amount>=100?amount:0;}
   rows[0].cents+=cents-rows.reduce((s,r)=>s+r.cents,0);
   return rows.filter(r=>r.cents>=100).map(r=>({symbol:r.symbol,notional:(r.cents/100).toFixed(2)}));
 }
 function cashRepair(account,positions,owned){
   accountGuard(account,positions,[],owned,true);
   const cash=Number(account.cash);if(cash>=0)return null;
   const amount=-cash+CASH_RESERVE+1;
   const held=positions.filter(p=>Number(p.current_price)>0&&Number.isFinite(Number(p.current_price))&&Number(p.market_value)>=amount*2).sort((a,b)=>Number(b.market_value)-Number(a.market_value)||a.symbol.localeCompare(b.symbol));
   assert(held.length>0,'소액 현금 부족을 정리할 보유 종목 확인 필요');
   const position=held[0],qty=Math.ceil(amount/Number(position.current_price)*1e9)/1e9;
   assert(qty>0&&qty<Number(position.qty),'소액 잔액 정리 매도 수량 확인 필요');
   return {symbol:position.symbol,qty:qty.toFixed(9)};
 }
 const api={PAPER,CASH_RESERVE,cashRepair,RULE_ID,halfYear,calendarRange,quarterSchedule,lastCycle,migrateState,assert,targets,accountGuard,buyBudget,initialOrders,sellQuantity,cashAllocation};
 root.FLOWPaperCore=api;if(typeof module!=='undefined')module.exports=api;
})(typeof globalThis!=='undefined'?globalThis:this);
