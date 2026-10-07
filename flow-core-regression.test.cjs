const assert=require('assert'),C=require(__dirname+'/flow-paper-core.js'),M=require(__dirname+'/portfolio-math.js');
const selected=['A','B','C','D','E'],positions=selected.map(symbol=>({symbol,qty:'200',side:'long',current_price:'100',market_value:'20000'}));
const account={status:'ACTIVE',cash:'-0.73',equity:'99999.27',long_market_value:'100000',short_market_value:'0'};
assert.throws(()=>C.accountGuard(account,positions,[],selected));const repair=C.cashRepair(account,positions,selected);assert.equal(repair.symbol,'A');assert(Number(repair.qty)*100>=2.73);assert(Number(repair.qty)<200);
for(const a of [{...account,cash:'-20'},{...account,short_market_value:'-1'},{...account,status:'DISABLED'},{...account,cash:'NaN'},{...account,equity:'NaN'}])assert.throws(()=>C.cashRepair(a,positions,selected));
assert.throws(()=>C.cashRepair(account,positions,['A']));assert.throws(()=>C.accountGuard(account,positions,[{id:'external'}],selected,true));
for(const cash of [0,.99,1,1.99,2,5,549.64,10000]){const allocations=C.cashAllocation({...account,cash:String(cash),equity:String(100000+cash)},positions,selected,100000,true),total=allocations.reduce((s,r)=>s+Number(r.notional),0);assert(total<=Math.max(0,cash-1)+1e-8);assert(allocations.every(r=>Number(r.notional)>=1));}
assert.equal(C.cashAllocation({...account,cash:'100',equity:'100100'},positions,selected,100000,false).length,0);
const w=M.weights(account,positions);assert.equal(w.cash,-.73);assert(Math.abs(w.rows.reduce((s,r)=>s+r.chartWeight,0)-1)<1e-10);assert(w.rows.at(-1).weight<0);assert(w.rows.at(-1).chartWeight===0);
const calendar=['2025-12-22','2026-03-23','2026-06-22','2026-09-21','2026-12-21','2027-03-22'].map(date=>({date}));
for(const [state,clock,expected]of [[{started:true,lastCycle:'2026Q3'},{is_open:true,timestamp:'2026-10-06T14:00:00Z'},'2026-12-21'],[{started:true,lastCycle:'2026Q2'},{is_open:true,timestamp:'2026-10-06T14:00:00Z'},'2026-09-21'],[{started:true,lastCycle:'2026Q4'},{is_open:false,timestamp:'2026-12-31T23:00:00Z',next_open:'2027-01-04T14:30:00Z'},'2027-03-22']])assert.equal(M.schedule(state,clock,calendar).date,expected);
console.log('PASS cash limits, bounded corrective sell, no shorts/external holdings, negative cash weights, rebalance boundaries');

