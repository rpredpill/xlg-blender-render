const assert=require('node:assert/strict'),H=require('./flow-history.js');
function dom(){const elements={},node=()=>({style:{},attrs:{},children:[],textContent:'',setAttribute(k,v){this.attrs[k]=v},append(...x){this.children.push(...x)},replaceChildren(){this.children=[]}});return {elements,getElementById:id=>['history-change','performance'].includes(id)?null:elements[id]||(elements[id]=node()),createElement:node,createElementNS:node};}
async function main(){
 const sec=s=>Date.parse(s)/1000;
 assert.deepEqual(H.normalize({timestamp:[3,1,2,4,5],equity:[110,0,null,0,105]}),[{time:3000,equity:110},{time:4000,equity:0},{time:5000,equity:105}]);
 assert.throws(()=>H.normalize({timestamp:[1],equity:[]}));
 assert.deepEqual(H.change([{equity:100},{equity:110}]),{amount:10,percent:(110/100-1)*100});
 assert.deepEqual(H.change([{equity:100},{equity:90}]),{amount:-10,percent:(90/100-1)*100});
 assert.deepEqual(H.change([{equity:100},{equity:100}]),{amount:0,percent:0});
 assert.equal(H.change([]),null);assert.equal(H.change([{equity:100}]),null);assert.equal(H.change([{equity:0},{equity:100}]),null);
 const rows=[{time:Date.parse('2026-10-01T20:00:00Z'),equity:100},{time:Date.parse('2026-10-02T20:00:00Z'),equity:105}];
 assert.equal(H.merge(rows,[{time:rows[1].time,equity:108}]).length,2);assert.equal(H.merge(rows,[{time:rows[1].time,equity:108}])[1].equity,108);
 assert.equal(H.select(rows,'1D',Date.parse('2026-10-04')).length,1);assert.equal(H.select(rows,'1M',Date.parse('2026-10-04')).length,2);
 assert.equal(H.select([{time:Date.parse('2026-02-28T23:00:00Z'),equity:1}],'1M',Date.parse('2026-03-31T00:00:00Z')).length,1);
 const document=dom(),store=new Map(),storage={getItem:k=>store.get(k),setItem:(k,v)=>store.set(k,v)},calls=[];let fail=false;
 const api=async path=>{calls.push(path);if(fail)throw Error('offline');return {timestamp:[sec('2026-10-01T20:00:00Z'),sec('2026-10-02T14:00:00Z'),sec('2026-10-02T20:00:00Z')],equity:[100,102,105]};};
 let time=Date.parse('2026-10-04T12:00:00Z');const h=H.create({api,document,storage,now:()=>time});
 await h.update({id:'A',created_at:'2026-01-01T00:00:00Z'});assert.equal(calls.length,1);assert(calls[0].includes('timeframe=1D&start='));assert.equal(JSON.parse(store.get('flow.paper.history.v1.A')).daily.length,2);assert(document.elements['paper-chart'].children[0].children.some(n=>n.attrs.d));assert.equal(document.elements['history-value-change'].textContent,'+$5.00 (+5.00%)');
 document.elements['history-1D'].onclick();await new Promise(r=>setImmediate(r));assert(calls[1].includes('period=7D&timeframe=5Min'));assert(document.elements['paper-chart'].children[0].children.at(-1).textContent.includes('ET'));assert.equal(document.elements['history-value-change'].textContent,'+$3.00 (+2.94%)');
 assert(document.elements['paper-chart'].children[0].children.some(n=>n.attrs.d));assert.equal(document.elements['history-1D'].attrs['aria-pressed'],'true');
 time+=400000;fail=true;await h.update({id:'A'});assert(document.elements['paper-chart'].children[0].children.some(n=>n.attrs.d));assert.equal(JSON.parse(store.get('flow.paper.history.v1.A')).daily.length,2);
 h.reset();assert.equal(document.elements['history-value-change'].textContent,'—');assert.equal(document.elements['paper-chart'].children.length,1);assert(document.elements['paper-chart'].children[0].textContent.includes('연결'));
 // A stale response cannot fill a different account's chart or storage.
 let resolve;const d=dom(),pending=H.create({api:()=>new Promise(r=>resolve=r),document:d,storage,now:()=>time});const req=pending.update({id:'OLD'});pending.reset();resolve({timestamp:[1,2],equity:[10,20]});await req;assert(!store.has('flow.paper.history.v1.OLD'));assert(d.elements['paper-chart'].children[0].textContent.includes('연결'));
 // Selecting intraday while a daily request is in flight fetches it after completion.
 let finish;const d2=dom(),paths=[],switcher=H.create({api:path=>{paths.push(path);return paths.length===1?new Promise(r=>finish=r):Promise.resolve({timestamp:[1,2],equity:[1,2]});},document:d2,storage,now:()=>time});
 const flight=switcher.update({id:'SWITCH'});d2.elements['history-1D'].onclick();finish({timestamp:[1,2],equity:[1,2]});await flight;await new Promise(r=>setImmediate(r));assert.equal(paths.length,2);assert(paths[1].includes('5Min'));
 // A long daily history is preserved beyond the old 500 snapshot limit.
 const long=Array.from({length:1500},(_,i)=>({time:Date.parse('2020-01-01T21:00:00Z')+i*86400000,equity:100+i}));assert.equal(H.merge([],long).length,1500);
 console.log('PASS: history validation, daily merge, calendar ranges, last trading day, account isolation, persistent cache, API failure fallback, period buttons and removed-caption compatibility');
}
main().catch(e=>{console.error(e);process.exitCode=1});
