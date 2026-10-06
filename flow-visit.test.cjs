const fs=require('fs'),vm=require('vm'),assert=require('assert'),C=require(__dirname+'/flow-paper-core.js');
const source=fs.readFileSync(__dirname+'/flow-paper-visit.js','utf8'),fixture=JSON.parse(fs.readFileSync(__dirname+'/flow-test-fixture.json','utf8'));
function create(opts={}){
 const nodes=new Map();const node=()=>({textContent:'',value:'100000',checked:true,disabled:false,children:[],replaceChildren(...a){this.children=a},append(...a){this.children.push(...a)}}),writes=[],posts=[],orders=new Map(),requests=[],storage=new Map();let count=0;
 const selected=['A','B','C','D','E'];let account={id:'test',status:'ACTIVE',cash:opts.cash??'-0.73',equity:'99999.27',long_market_value:'100000',short_market_value:'0'},positions=selected.map(symbol=>({symbol,qty:'200',side:'long',current_price:'100',market_value:'20000'})),state={armed:true,started:true,owned:selected,selected,log:[],nav:[],intents:{},ruleId:C.RULE_ID,budget:100000,investAll:true,lastHalfYear:'2026H2'},clock={is_open:opts.isOpen??true,timestamp:'2026-10-06T14:00:00Z',next_open:'2026-10-07T13:30:00Z'};
 if(opts.empty){positions=[];account.cash='100000';account.equity='100000';account.long_market_value='0';state.started=false;state.owned=[];state.selected=[];delete state.lastHalfYear;}
 const doc={visibilityState:'visible',getElementById(id){if(!nodes.has(id))nodes.set(id,node());return nodes.get(id)},createElement:node,addEventListener(){}};
 const ctx={document:doc,console,Date,Intl,AbortController,URLSearchParams,navigator:{onLine:true,locks:{request:async(name,arg,callback)=>typeof arg==='function'?arg():callback({name})}},localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>{writes.push({key:k,value:JSON.parse(v)});storage.set(k,v)},removeItem:k=>storage.delete(k)},setTimeout,clearTimeout,setInterval:()=>1,clearInterval(){},window:{addEventListener(){}},FLOWPaperCore:C,PulseView:{renderAccount(){},clear(){},renderPositions(){},renderOrders(){},unavailable(){}},StatusNotice:{show:t=>doc.getElementById('status').textContent=t},fetch:async(url,o={})=>{
 requests.push({url,method:o.method||'GET'});if(url.includes('flow-latest.json'))return {ok:true,status:200,json:async()=>fixture};const path=url.replace(C.PAPER,'');
 const response=(value,status=200)=>({ok:status<400,status,headers:{get(){return null}},json:async()=>structuredClone(value)});
 if(o.method==='POST'){
  const request=JSON.parse(o.body);posts.push(request);if(opts.postReject)return response({message:'not permitted'},403);const id='order-'+(++count);const filled=opts.accepted?'accepted':'filled';const order={id,status:filled,client_order_id:request.client_order_id,...request};orders.set(id,order);
  if(filled==='filled'){let position=positions.find(p=>p.symbol===request.symbol);if(!position){position={symbol:request.symbol,side:'long',qty:'0',current_price:'100',market_value:'0'};positions.push(position);}const qty=request.side==='sell'?-Number(request.qty):Number(request.notional)/100;position.qty=String(Number(position.qty)+qty);position.market_value=String(Number(position.qty)*100);account.cash=String(Number(account.cash)-qty*100);account.long_market_value=String(positions.reduce((sum,p)=>sum+Number(p.market_value),0));account.equity=String(Number(account.long_market_value)+Number(account.cash));}
  if(opts.postReject)return response({message:'not permitted'},403);
  if(opts.uncertain&&!opts.didTimeout){opts.didTimeout=true;throw new TypeError('lost response');}
  return response(order);
 }
 if(path==='/account')return response(account);
 if(path==='/clock')return response(clock);
 if(path==='/positions')return response(positions);
 if(path.startsWith('/calendar'))return response(opts.stale?[{date:'2099-01-02',close:'16:00'}]:[{date:fixture.signalDate,close:'16:00'},{date:'2026-10-06',close:'16:00'}]);
 if(path.startsWith('/assets/'))return response({tradable:true,fractionable:true,class:'us_equity'});
 if(path.startsWith('/orders:by_client_order_id'))return response([...orders.values()].find(o=>o.client_order_id===decodeURIComponent(path.split('=')[1]))||{},[...orders.values()].some(o=>o.client_order_id===decodeURIComponent(path.split('=')[1]))?200:404);
 if(path.startsWith('/orders/'))return response(orders.get(path.split('/')[2]));
 if(path.startsWith('/orders?'))return response((path.includes('status=open')?[...orders.values()].filter(o=>o.status!=='filled'):[]).concat(opts.foreign?[{id:'foreign',client_order_id:'foreign'}]:[]));
 if(opts.delayed){return new Promise(resolve=>{opts.resolve=()=>response(account)&&resolve(response(account));});}
 throw Error('Unexpected '+path);
 }};ctx.globalThis=ctx;vm.createContext(ctx);
 const cut=source.indexOf(' const saved=readJSON(KEY)');
 const injected=source.slice(0,cut)+` globalThis.T={tick,settle,refresh,repairCash,api,log,guard,set(a,s){account=a;state=s;credentials={keyId:'MOCK',secretKey:'MOCK'};},setState(s){state=s;},get(){return state;},lock(v){tradingLocked=v;},setCredentials(c){credentials=c;}};})();`;
 vm.runInContext(opts.startup?source:injected,ctx);ctx.T?.set(account,state);storage.set('flow.paper.state.v1.test',JSON.stringify(state));
 return {ctx,T:ctx.T,nodes,writes,posts,storage,state,account,clock,orders,opts,requests};
}
(async()=>{
 let startup=create({startup:true});await startup.nodes.get('connect').onclick();assert.equal(startup.posts.length,1);assert.equal(startup.posts[0].side,'sell');assert(Number(startup.account.cash)>0);
 startup=create({startup:true,empty:true});await startup.nodes.get('connect').onclick();assert(startup.posts.length>=5);assert(startup.posts.every(o=>o.side==='buy'));assert(Number(startup.account.cash)>=C.CASH_RESERVE-1e-6);assert(JSON.parse(startup.storage.get('flow.paper.state.v1.test')).started);
 startup=create({startup:true,empty:true,foreign:true});await startup.nodes.get('connect').onclick();assert.equal(startup.posts.length,0,'foreign orders block new investment');
 startup=create({startup:true,empty:true,stale:true});await startup.nodes.get('connect').onclick();assert.equal(startup.posts.length,0,'stale signals block new investment');
 let a=create({isOpen:false});await a.T.tick();assert.equal(a.posts.length,0);assert(a.T.get().pausedReason.includes('다음 정규장'));const logs=a.T.get().log.length;await a.T.tick();assert.equal(a.T.get().log.length,logs,'same error should not spam');
 a=create();await a.T.tick();assert.equal(a.posts.length,1);assert.equal(a.posts[0].side,'sell');assert(Number(a.account.cash)>0);assert.equal(a.T.get().cashRepair,null);assert(a.T.get().cashRepairHistory.length===1);await a.T.tick();assert(a.posts.filter(o=>o.side==='buy').every(o=>Number(o.notional)<=Number(a.account.cash)+1));assert(a.posts.filter(o=>o.side==='sell').length===1);
 a=create({cash:'-20'});await a.T.tick();assert.equal(a.posts.length,0);assert(a.T.get().pausedReason);
 a=create({uncertain:true});await a.T.tick();assert.equal(a.posts.length,1);assert.equal(a.T.get().cashRepair.orders[0].local,'uncertain');await a.T.tick();assert.equal(a.posts.length,1,'uncertain POST must never be repeated');assert(a.T.get().cashRepairHistory.length===1);
 a=create({accepted:true});await a.T.tick();await a.T.tick();assert.equal(a.posts.length,1,'accepted sell must not duplicate');
 a=create({cash:'3.00'});a.T.lock(true);const buys=['A','B'].map((symbol,i)=>({request:{symbol,notional:'2.00',side:'buy',type:'market',time_in_force:'day',client_order_id:'test'+i},local:'new',id:null,status:null}));a.T.get().cashSweep={orders:buys};await a.T.settle(buys);assert.equal(a.posts.length,1);assert.equal(buys[1].local,'skipped');assert.equal(Number(a.account.cash),1);
 a=create({cash:'3.00',accepted:true});a.T.lock(true);const pending=['A','B'].map((symbol,i)=>({request:{symbol,notional:'1.00',side:'buy',client_order_id:'pending'+i},local:'new',id:null,status:null}));a.T.get().cashSweep={orders:pending};assert.equal(await a.T.settle(pending),false);assert.equal(a.posts.length,1,'wait for first fill before submitting second');
 a=create({cash:'3.00'});a.writes.length=0;await a.T.refresh();a.T.log('조회 실패 test');assert.equal(a.writes.length,0,'read-only refresh/log must not overwrite trading ledger');
 a=create({cash:'3.00',postReject:true});a.T.lock(true);const rejected={request:{symbol:'A',notional:'1.00',side:'buy',client_order_id:'rejected'},local:'new',id:null,status:null};a.T.get().cashSweep={orders:[rejected]};await assert.rejects(()=>a.T.settle([rejected]));assert.equal(rejected.local,'rejected');await assert.rejects(()=>a.T.settle([rejected]));assert.equal(a.posts.length,1);
 a=create({cash:'3.00'});a.storage.set('flow.paper.state.v1.test','corrupted');a.writes.length=0;await a.T.tick();assert.equal(a.posts.length,0);assert.equal(a.writes.length,0);assert.equal(a.storage.get('flow.paper.state.v1.test'),'corrupted');
 a=create({cash:'3.00',delayed:true});const late=a.T.api('/delayed');a.T.setCredentials({keyId:'NEW',secretKey:'NEW'});a.opts.resolve();await assert.rejects(()=>late,/계좌 연결이 변경/);
 console.log('PASS full connection/initial investment, corrupt-state blocking, regular-hours debt repair, repeated status, bounded debt, exact-once POST recovery, fill sequencing, fresh cash reserve, definite rejection, ledger isolation and old-account response rejection');
})().catch(e=>{console.error(e);process.exitCode=1});
