/* All trading requests are fixed to the PAPER endpoint. No live endpoint option. */
(function(root){
 'use strict';
 const PAPER='https://paper-api.alpaca.markets/v2';
 function assert(ok,message){if(!ok)throw Error(message);}
 const RULE_ID='FLOW_MEAN21_TOP5_EQUAL_SEMIANNUAL_V2';
 function targets(signal,previous=[]){
   assert(signal?.ready && signal.version===2 && signal.ruleId===RULE_ID,'새 FLOW 신호가 준비되지 않았습니다.');
   assert(Array.isArray(signal.ranking)&&signal.ranking.length>=5,'Top5 데이터가 부족합니다.');
   const rows=signal.ranking.slice(0,5);
   assert(new Set(rows.map(r=>r.ticker)).size===5&&rows.every((r,i)=>r.rank===i+1&&Number.isFinite(r.score)&&r.score>0),'Top5 순위 데이터 오류');
   return rows.map(r=>({...r,weight:.2}));
 }
 function halfYear(d){return d.slice(0,4)+'H'+(Number(d.slice(5,7))<=6?1:2);}
 function migrateState(state){
   if(state.ruleId===RULE_ID)return false;
   state.ruleTransitions=state.ruleTransitions||[];
   state.ruleTransitions.push({time:new Date().toISOString(),from:state.ruleId||'FLOW_V1_63_MEDIAN_TOP10_EXIT20_QUARTER',to:RULE_ID});
   for(const i of Object.values(state.intents||{}))if(!i.ruleId)i.ruleId='FLOW_V1_63_MEDIAN_TOP10_EXIT20_QUARTER';
   state.ruleId=RULE_ID;delete state.lastHalfYear;
   return true;
 }
 function accountGuard(account,positions,orders,owned=[]){
   assert(account.status==='ACTIVE'&&!account.trading_blocked&&!account.account_blocked,'계좌가 거래 가능한 상태가 아닙니다.');
   assert(Number(account.cash)>=0&&Number(account.short_market_value||0)===0,'현금 또는 공매도 계좌 상태 확인 필요');
   assert(Number(account.long_market_value||0)<=Number(account.equity)+1,'차입 투자 상태에서는 실행하지 않습니다.');
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
 const api={PAPER,RULE_ID,halfYear,migrateState,assert,targets,accountGuard,buyBudget,initialOrders};
 root.FLOWPaperCore=api;if(typeof module!=='undefined')module.exports=api;
})(typeof globalThis!=='undefined'?globalThis:this);

