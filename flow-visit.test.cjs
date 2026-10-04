const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const C=require('./flow-paper-core.js'),fixture=JSON.parse(fs.readFileSync(__dirname+'/flow-test-fixture.json'));
const next=new Date(fixture.signalDate+'T12:00:00Z');do{next.setUTCDate(next.getUTCDate()+1)}while([0,6].includes(next.getUTCDay()));const date=next.toISOString().slice(0,10),q=C.halfYear(date);
const stateKey='flow.paper.state.v1.mock-paper';
function broker({closed=false,lost=false,unknown=false,stale=false,foreign=false,cash=100000,clockFail=0,accountFail=0,failures={}}={}){
 const store=new Map(),orders=new Map(),posts=[],positions=new Map(),calls=[];let locked=false,count=0,closedNow=closed;
 function apply(o){if(o.status==='filled')return;const r=o.request,p=positions.get(r.symbol)||{symbol:r.symbol,qty:0,current_price:'100',side:'long',market_value:0};if(r.side==='buy'){const value=Number(r.notional);assert(cash+1e-8>=value,'Cash overrun');cash-=value;p.qty+=value/100;}else{const qty=Number(r.qty);assert(p.qty+1e-8>=qty,'Oversell');p.qty-=qty;cash+=qty*100;}p.market_value=p.qty*100;if(p.qty<1e-9)positions.delete(p.symbol);else positions.set(p.symbol,p);o.status='filled';}
 const b={store,orders,posts,positions,calls,closed:()=>closedNow,setClosed:v=>closedNow=v,fillAll:()=>{for(const o of orders.values())apply(o)},
   locks:{request:async(n,opts,fn)=>{if(locked)return fn(null);locked=true;try{return await fn({})}finally{locked=false;}}},
   fetch:async(url,opts={})=>{
    if(url.includes('flow-latest.json'))return {ok:true,status:200,json:async()=>fixture};
    assert(url.startsWith(C.PAPER+'/'));const path=url.slice(C.PAPER.length);let data,status=200;
    calls.push({path,method:opts.method||'GET'});
    const failure=failures[path];
    if(failure&&failure.remaining-->0){
      if(failure.kind==='timeout')return new Promise((resolve,reject)=>opts.signal.addEventListener('abort',()=>{const e=Error('Aborted');e.name='AbortError';reject(e);},{once:true}));
      if(failure.status)return {ok:false,status:failure.status,headers:{get:()=>failure.retryAfter||null},json:async()=>({message:'mock server failure'})};
      throw TypeError('Sensitive diagnostic text must not be logged: MOCK_SECRET');
    }
    if((opts.method||'GET')==='GET'){assert.equal(opts.headers['Content-Type'],undefined);assert.equal(opts.cache,undefined);}
    if(path==='/account'&&accountFail-->0)throw Error('Temporary account connection failure');
    if(path==='/account')data={id:'mock-paper',status:'ACTIVE',cash:String(cash),equity:String(cash+[...positions.values()].reduce((s,p)=>s+p.market_value,0)),long_market_value:String([...positions.values()].reduce((s,p)=>s+p.market_value,0)),short_market_value:'0',buying_power:'400000'};
    else if(path==='/positions')data=[...positions.values()].map(p=>({...p,qty:String(p.qty),market_value:String(p.market_value)}));
    else if(path.startsWith('/orders?'))data=[...orders.values()].filter(o=>o.status!=='filled').map(o=>({id:o.id,status:o.status,client_order_id:o.request.client_order_id})).concat(foreign?[{client_order_id:'foreign',id:'foreign'}]:[]);
    else if(path==='/clock'&&clockFail-->0)throw Error('Temporary clock connection failure');
    else if(path==='/clock')data={is_open:!closedNow,timestamp:closedNow?fixture.signalDate+'T23:00:00Z':date+'T14:00:00Z',next_open:date+'T13:30:00Z',next_close:date+'T20:00:00Z'};
    else if(path.startsWith('/calendar'))data=stale?[{date:'2099-01-02',close:'16:00'}]:[{date:fixture.signalDate,close:'16:00'},{date,close:'16:00'}];
    else if(path.startsWith('/assets/'))data={tradable:true,fractionable:true,class:'us_equity'};
    else if(path==='/orders'&&opts.method==='POST'){
     assert.equal(opts.headers['Content-Type'],'application/json');const request=JSON.parse(opts.body);assert.equal(request.extended_hours,false);assert.equal(request.time_in_force,'day');assert.equal(request.type,'market');assert(![...orders.values()].some(o=>o.request.client_order_id===request.client_order_id),'Duplicate broker POST');posts.push(request);
     if(unknown&&posts.length===1)throw Error('Lost before acceptance');
     const o={id:'order-'+(++count),request,status:'accepted'};orders.set(o.id,o);if(!closedNow)apply(o);data={id:o.id,status:o.status};if(lost&&posts.length===1)throw Error('Lost after acceptance');
    }else if(path.startsWith('/orders:by_client_order_id?')){const id=new URL(C.PAPER+path).searchParams.get('client_order_id'),o=[...orders.values()].find(o=>o.request.client_order_id===id);if(o)data={id:o.id,status:o.status};else{status=404;data={message:'not found'}}}
    else if(path.startsWith('/orders/')){const o=orders.get(path.split('/').at(-1));assert(o);data={id:o.id,status:o.status};}
    else throw Error('Unexpected path '+path);
    return {ok:status===200,status,json:async()=>data};
   }};return b;
}
async function page(b,{offline=false,fastTimeout=false}={}){
 const elements={},events={},element=()=>({value:'',checked:false,disabled:false,textContent:'',style:{},setAttribute(){},append(){},replaceChildren(){}}),$=id=>elements[id]||(elements[id]=element());let interval;
 $('paper-key').value='MOCK';$('paper-secret').value='MOCK';$('budget').value='100000';$('remember').checked=true;
 const delays=[];
 const context={FLOWPaperCore:C,fetch:b.fetch,AbortController,setTimeout:(fn,ms)=>{if(ms!==15000)delays.push(ms);return setTimeout(fn,ms===15000?(fastTimeout?0:60000):0)},clearTimeout,document:{getElementById:$,createElement:element,createElementNS:element,visibilityState:'visible',addEventListener:(event,fn)=>events[event]=fn},localStorage:{getItem:k=>b.store.get(k)||null,setItem:(k,v)=>b.store.set(k,v),removeItem:k=>b.store.delete(k)},navigator:{locks:b.locks,onLine:!offline},Intl,Date,console,setInterval:fn=>(interval=fn,1),clearInterval(){}};
 vm.runInNewContext(fs.readFileSync(__dirname+'/flow-paper-visit.js','utf8'),context);
 for(let i=0;i<80;i++)await new Promise(r=>setImmediate(r));
 return {$,delays,stop:()=>$('stop').onclick(),connect:()=>$('connect').onclick(),start:()=>$('start').onclick(),tick:async()=>{if(interval)await interval()},state:()=>JSON.parse(b.store.get(stateKey))};
}
async function run(){
 // Immediate execution at 10am, not the old closing window; no margin budget.
 let b=broker(),p=await page(b);await p.connect();await p.start();assert.equal(b.posts.length,5);assert(p.state().started);assert(b.posts.reduce((s,o)=>s+Number(o.notional||0),0)<=99500);await p.tick();await page(b);assert.equal(b.posts.length,5);
 // Broker-accepted closed-session orders survive closing/reopening the page.
 b=broker({closed:true});p=await page(b);await p.connect();await p.start();assert.equal(b.posts.length,5);assert.equal(p.state().pending.phase,'buying');assert(p.$('status').textContent.includes('예약됨'));await page(b);assert.equal(b.posts.length,5);b.fillAll();p=await page(b);assert.equal(b.posts.length,5);assert(p.state().started);assert.equal(p.state().lastHalfYear,q);
 // A lost response reconciles by client ID, never reposts the same order.
 b=broker({closed:true,lost:true});p=await page(b);await p.connect();assert.equal(b.posts.length,1);assert.equal(p.state().armed,true);assert(p.state().pausedReason);await p.tick();assert.equal(b.posts.length,5);assert.equal(new Set(b.posts.map(o=>o.client_order_id)).size,5);
 // A genuinely unknown order must not be retried even on explicit restart.
 b=broker({closed:true,unknown:true});p=await page(b);await p.connect();await p.start();await p.start();assert.equal(b.posts.length,1);assert(p.$('status').textContent.includes('自')===false);assert(p.$('status').textContent.includes('자동 재전송하지 않습니다'));
 // Missed quarters catch up; do not buy against unfilled sale proceeds.
 b=broker({closed:true,cash:1000});const old=fixture.ranking[25].ticker;b.positions.set(old,{symbol:old,qty:990,current_price:'100',side:'long',market_value:99000});
 b.store.set(stateKey,JSON.stringify({armed:false,started:true,owned:[old],selected:[old],lastQuarter:'2025Q1',budget:100000,log:[],nav:[],intents:{}}));p=await page(b);await p.connect();await p.start();assert.equal(b.posts.length,1);assert.equal(b.posts[0].side,'sell');await page(b);assert.equal(b.posts.length,1);b.fillAll();p=await page(b);assert.equal(b.posts.length,6);assert(b.posts.slice(1).every(o=>o.side==='buy'));assert(b.posts.slice(1).reduce((s,o)=>s+Number(o.notional),0)<=99500);b.fillAll();p=await page(b);assert.equal(p.state().lastHalfYear,q);assert.equal(b.posts.length,6);
 // Strategy migration reconciles accepted legacy orders but never submits old unsubmitted targets.
 b=broker({closed:true});const legacyTicker=fixture.ranking[25].ticker,unusedTicker=fixture.ranking[26].ticker;
 const legacyRequest={symbol:legacyTicker,side:'buy',notional:'1000.00',type:'market',time_in_force:'day',client_order_id:'flow2-legacy-buy'};
 b.orders.set('legacy',{id:'legacy',request:legacyRequest,status:'accepted'});
 b.store.set(stateKey,JSON.stringify({armed:true,started:false,owned:[legacyTicker,unusedTicker],selected:[],budget:100000,log:['old record'],nav:[],intents:{},pending:{date,quarter:'2026Q4',signalDate:fixture.signalDate,rows:[],target:{},sells:[],buys:[{request:legacyRequest,local:'submitted',id:'legacy',status:'accepted'},{request:{...legacyRequest,symbol:unusedTicker,client_order_id:'unsubmitted-legacy'},local:'new'}],phase:'buying'}}));
 p=await page(b);await p.connect();assert.equal(b.posts.length,0);assert(p.$('status').textContent.includes('이전 FLOW 주문'));
 b.fillAll();p=await page(b);assert.equal(b.posts.length,1);assert.equal(b.posts[0].side,'sell');assert.equal(p.state().legacyPlans.length,1);assert(p.state().log.includes('old record'));
 b.fillAll();p=await page(b);assert.equal(b.posts.length,6);assert(!b.posts.some(o=>o.client_order_id==='unsubmitted-legacy'));assert.equal(p.state().ruleId,C.RULE_ID);
 // Guard foreign orders, stale signals, and repeated concurrent visits.
 b=broker({foreign:true});p=await page(b);await p.connect();await p.start();assert.equal(b.posts.length,0);
 b=broker({stale:true});p=await page(b);await p.connect();await p.start();assert.equal(b.posts.length,0);
 b=broker({closed:true});p=await page(b);await p.connect();await p.start();await Promise.all([page(b),page(b),p.tick()]);assert.equal(b.posts.length,5);
 // Passive preference survives transient API errors, reloads, and unknown-order checks.
 b=broker({clockFail:3});p=await page(b);await p.connect();assert(p.state().armed);assert(p.state().pausedReason);assert.equal(b.posts.length,0);p=await page(b);assert(p.state().armed);assert(!p.state().pausedReason);assert.equal(b.posts.length,5);await p.tick();assert.equal(b.posts.length,5);
 b=broker({accountFail:3});p=await page(b);await p.connect();assert.equal(b.posts.length,0);await p.tick();assert(p.state().armed);assert.equal(b.posts.length,5);
 // Only an explicit stop persists a manual pause; restart clears that pause.
 b=broker();p=await page(b);await p.connect();p.stop();assert.equal(p.state().armed,false);assert.equal(p.state().manualPaused,true);p=await page(b);assert.equal(p.state().armed,false);await p.start();assert(p.state().armed);assert.equal(p.state().manualPaused,false);assert.equal(b.posts.length,5);
 b=broker({unknown:true});p=await page(b);await p.connect();await p.tick();await page(b);assert.equal(b.posts.length,1);assert(p.state().armed);assert(p.state().pausedReason);
 // GET recovery uses bounded backoff, sanitized diagnostics, and preserves trading guards.
 b=broker({failures:{'/clock':{remaining:2}}});p=await page(b);await p.connect();assert.equal(b.posts.length,5);assert.deepEqual(p.delays,[1000,3000]);assert(p.state().log.some(s=>s.includes('조회 복구')));assert(!JSON.stringify(p.state()).includes('MOCK_SECRET'));
 b=broker({failures:{'/clock':{remaining:2,status:503}}});p=await page(b);await p.connect();assert.equal(b.posts.length,5);assert.equal(b.calls.filter(c=>c.path==='/clock').length,3);
 b=broker({failures:{'/clock':{remaining:1,status:429,retryAfter:'2'}}});p=await page(b);await p.connect();assert.equal(b.posts.length,5);assert.deepEqual(p.delays,[2000]);
 b=broker({failures:{'/clock':{remaining:5,status:429,retryAfter:'60'}}});p=await page(b);await p.connect();assert.equal(b.posts.length,0);assert.equal(b.calls.filter(c=>c.path==='/clock').length,1);assert(p.state().armed);
 b=broker({failures:{'/account':{remaining:5,status:401}}});p=await page(b);await p.connect();assert.equal(b.calls.length,1);assert.equal(b.posts.length,0);assert.equal(p.delays.length,0);
 b=broker({failures:{'/clock':{remaining:5}}});p=await page(b,{offline:true});await p.connect();assert.equal(b.calls.filter(c=>c.path==='/clock').length,1);assert(p.state().pausedReason.includes('オ')===false);assert(p.state().pausedReason.includes('오프라인'));assert(p.state().armed);
 b=broker({failures:{'/clock':{remaining:1,kind:'timeout'}}});p=await page(b,{fastTimeout:true});await p.connect();assert.equal(b.posts.length,5);assert(p.state().log.some(s=>s.includes('15초 응답 시간 초과')));
 b=broker({failures:{'/orders':{remaining:1,kind:'timeout'}}});p=await page(b,{fastTimeout:true});await p.connect();assert.equal(b.calls.filter(c=>c.method==='POST').length,1);await p.tick();assert.equal(b.calls.filter(c=>c.method==='POST').length,1);assert(p.state().pending.buys[0].local==='uncertain');
 console.log('PASS: bounded GET retries, backoff and Retry-After, auth/offline handling, sanitized errors, GET timeout recovery, POST timeout never resubmitted');
 console.log('PASS: passive auto-connect, reload persistence, transient clock/account recovery, explicit manual stop, and no duplicate unknown-order submissions');
 console.log('PASS: intraday execution, closed-session queue, reload resume, half-year catch-up, sell-before-buy, cash-only cap, foreign-order/stale-signal guards, lost-response reconciliation, unknown-order stop, concurrent duplicate prevention');
}
run().catch(e=>{console.error(e);process.exit(1)});

