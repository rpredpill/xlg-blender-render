const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const C=require('./flow-paper-core.js'),signal=JSON.parse(fs.readFileSync(__dirname+'/flow-latest.json'));
const execution=new Date(signal.signalDate+'T12:00:00Z');do{execution.setUTCDate(execution.getUTCDate()+1)}while([0,6].includes(execution.getUTCDay()));const testDate=execution.toISOString().slice(0,10);
const account={id:'mock-paper',status:'ACTIVE',cash:'100000',equity:'100000',long_market_value:'0',short_market_value:'0'};
assert.equal(C.PAPER,'https://paper-api.alpaca.markets/v2');
assert.throws(()=>C.accountGuard(account,[{symbol:'QQQ',side:'long',qty:1}],[],[]));
assert.throws(()=>C.accountGuard({...account,cash:'-1'},[],[],[]));
assert.throws(()=>C.accountGuard({...account,long_market_value:'200000'},[],[],[]));
assert.throws(()=>C.accountGuard(account,[],[{id:'other-order'}],[]));
const rows=C.targets(signal),orders=C.initialOrders(rows,C.buyBudget({...account,buying_power:'400000'},400000),'2026-10-05');
assert.equal(rows.length,10);assert(Math.abs(rows.reduce((s,r)=>s+r.weight,0)-1)<1e-10);
assert(orders.reduce((s,o)=>s+Number(o.notional),0)<=99500);
const buffered=C.targets(signal,[signal.ranking[15].ticker,signal.ranking[25].ticker]);
assert(buffered.some(r=>r.rank===16));assert(!buffered.some(r=>r.rank===26));
assert.equal(new Set(buffered.map(r=>r.ticker)).size,10);
async function integration({closed=false,timeout=false,clockFailure=false}={}){
 const elements={},store=new Map(),posts=[];let interval;
 const element=()=>({value:'',disabled:false,textContent:'',children:[],append(x){this.children.push(x)},replaceChildren(){this.children=[]}});
 const $=id=>elements[id]||(elements[id]=element());$('paper-key').value='MOCK';$('paper-secret').value='MOCK';$('budget').value='100000';
 const context={FLOWPaperCore:C,document:{getElementById:$,createElement:element},localStorage:{getItem:k=>store.get(k)||null,setItem:(k,v)=>store.set(k,v),removeItem:k=>store.delete(k)},navigator:{locks:{request:async(n,o,fn)=>fn({})}},console,Intl,Date,setInterval:fn=>(interval=fn,1),clearInterval(){},setTimeout};
 context.fetch=async(url,options={})=>{
  let data;
  if(url.includes('flow-latest.json'))data=signal;
  else {assert(url.startsWith(C.PAPER+'/'));const path=url.slice(C.PAPER.length);
   if((options.method||'GET')==='GET'){assert.equal(options.headers['Content-Type'],undefined);assert.equal(options.cache,undefined);assert.equal(options.headers['Cache-Control'],undefined);assert.equal(options.headers.Pragma,undefined);}
   if(path==='/account')data=account;
   else if(path==='/positions'||path.startsWith('/orders?'))data=[];
   else if(path==='/clock'){if(clockFailure)throw Error('Mock clock network failure');data={is_open:!closed,timestamp:testDate+'T19:58:45Z',next_close:testDate+'T20:00:00Z'};}
   else if(path.startsWith('/calendar'))data=[{date:signal.signalDate},{date:testDate}];
   else if(path.startsWith('/assets/'))data={tradable:true,fractionable:true,class:'us_equity'};
   else if(path==='/orders'&&options.method==='POST'){assert.equal(options.headers['Content-Type'],'application/json');posts.push(JSON.parse(options.body));if(timeout)throw Error('Mock response lost after submission');data={id:'mock-'+posts.length,status:'filled'};}
   else if(path.startsWith('/orders/'))data={id:path.split('/').at(-1),status:'filled'};
   else throw Error('Unexpected endpoint '+path);
  }
  return {ok:true,status:200,json:async()=>data};
 };
 vm.runInNewContext(fs.readFileSync(__dirname+'/flow-paper.js','utf8'),context);
 await $('connect').onclick();await $('start').onclick();
 for(let i=0;i<120;i++)await new Promise(r=>setImmediate(r));
 if(clockFailure){assert.equal(posts.length,0);assert($('status').textContent.includes('주문을 제출하지 않았습니다'));assert(!$('status').textContent.includes('접수/체결 확인 필요'));}
 else if(closed)assert.equal(posts.length,0);
 else if(timeout){assert.equal(posts.length,1);assert.equal(JSON.parse(store.get('flow.paper.state.v1.mock-paper')).armed,false);await interval();assert.equal(posts.length,1);}
 else{assert.equal(posts.length,10);assert(posts.every(p=>p.side==='buy'&&p.type==='market'&&p.time_in_force==='day'));assert(posts.reduce((s,p)=>s+Number(p.notional),0)<=99500);assert.equal(new Set(posts.map(p=>p.client_order_id)).size,10);await interval();assert.equal(posts.length,10);}
}
(async()=>{await integration();await integration({closed:true});await integration({timeout:true});await integration({clockFailure:true});console.log('PASS: Alpaca CORS-compatible GET, JSON POST, read-failure reporting, cash-only sizing, ownership, Top20 buffer, closed-market guard, paper-only routing, filled execution, lost-response stop, duplicate prevention');})().catch(e=>{console.error(e);process.exit(1)});
require('./flow-visit.test.cjs');
