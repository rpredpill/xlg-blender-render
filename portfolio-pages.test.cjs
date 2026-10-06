const fs=require('fs'),vm=require('vm'),assert=require('assert');
async function test(mode,cash='20'){
 const html=fs.readFileSync(__dirname+'/'+({weights:'weights',compare:'compare',schedule:'schedule'}[mode])+'.html','utf8'),ids=new Set([...html.matchAll(/id="([^"]+)"/g)].map(m=>m[1])),nodes=new Map(),requests=[],timers=[];
 function el(){return {textContent:'',children:[],style:{},value:'',attrs:{},append(...x){this.children.push(...x)},replaceChildren(...x){this.children=x},setAttribute(k,v){this.attrs[k]=v}}}
 const doc={body:{dataset:{page:mode}},visibilityState:'visible',getElementById(k){if(!ids.has(k))return null;if(!nodes.has(k))nodes.set(k,el());return nodes.get(k)},createElement:el,createElementNS:el,addEventListener(){}};
 const ctx={document:doc,Date,Intl,URLSearchParams,AbortController,setTimeout,clearTimeout,setInterval:f=>timers.push(f),window:{addEventListener(){}},localStorage:{getItem(k){return k==='flow.paper.credentials.v1'?JSON.stringify({keyId:'MOCK',secretKey:'MOCK'}):k==='flow.paper.state.v1.test'?JSON.stringify({started:true,lastHalfYear:'2026H2',selected:['A','B','C','D','E'],investAll:true}):null},setItem(){}},fetch:async(url,o)=>{
  requests.push({url,method:o.method||'GET'});let data;
  if(url.endsWith('/account'))data={id:'test',created_at:'2026-09-01T00:00:00Z',cash,equity:'100'};
  else if(url.endsWith('/positions'))data=[{symbol:'A',side:'long',qty:'1',market_value:'80'}];
  else if(url.endsWith('/clock'))data={timestamp:'2026-10-05T14:00:00Z',is_open:true};
  else if(url.includes('/calendar?'))data=mode==='schedule'?[{date:'2027-01-04',close:'16:00'}]:[{date:'2026-10-01',close:'16:00'},{date:'2026-10-02',close:'16:00'},{date:'2026-10-05',close:'16:00'}];
  else if(url.includes('/portfolio/history?'))data={timestamp:[Date.parse('2026-10-01T20:00:00Z')/1000,Date.parse('2026-10-02T20:00:00Z')/1000],equity:[100,110]};
  else if(url.includes('/stocks/bars?')){const make=(a,b)=>[{t:'2026-10-01T04:00:00Z',c:a},{t:'2026-10-02T04:00:00Z',c:b}];data=url.includes('page_token=next')?{bars:{QLD:make(100,120),VOO:make(100,105)},next_page_token:null}:{bars:{QQQ:make(100,110)},next_page_token:'next'};}
  else throw Error('Unexpected '+url);return {ok:true,json:async()=>data};
 }};ctx.globalThis=ctx;vm.createContext(ctx);for(const name of ['portfolio-math.js','portfolio-pages.js'])vm.runInContext(fs.readFileSync(__dirname+'/'+name,'utf8'),ctx);
 for(let i=0;i<250;i++)await Promise.resolve();assert(requests.every(r=>r.method==='GET'));
 if(mode==='weights'){assert.equal(nodes.get('weight-rows').children.length,2);assert(nodes.get('weight-ring').children.length>=3);if(Number(cash)<0)assert(nodes.get('weight-note').textContent.includes('현금 부족'));}
 else if(mode==='schedule'){assert.equal(nodes.get('rebalance-date').textContent,'2027.01.04');assert(nodes.get('rebalance-detail').textContent.includes('이번 반기 완료'));}
 else{assert.equal(nodes.get('compare-results').children.length,4);assert(nodes.get('compare-period').textContent.includes('2026-10-01 ~ 2026-10-02'));assert.equal(requests.filter(r=>r.url.includes('/stocks/bars?')).length,2);nodes.get('compare-1Y').onclick();assert.equal(nodes.get('compare-1Y').attrs['aria-pressed'],'true');}
 console.log('PASS '+mode+(Number(cash)<0?' negative cash':'')+': actual HTML rendering, read-only requests and controls');
}
(async()=>{await test('weights');await test('weights','-0.73');await test('compare');await test('schedule')})().catch(e=>{console.error(e);process.exitCode=1});
