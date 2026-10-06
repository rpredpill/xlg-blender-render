(()=>{
 'use strict';
 const M=PortfolioMath,$=id=>document.getElementById(id),mode=document.body.dataset.page;
 const PAPER='https://paper-api.alpaca.markets/v2',DATA='https://data.alpaca.markets/v2',COLORS={FLOW:'#2563eb',QLD:'#dc2626',QQQ:'#ec4899',VOO:'#84cc16'};
 const money=v=>(Number(v)<0?'−$':'$')+Math.abs(Number(v)).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
 const pct=v=>(v>=0?'+':'')+v.toFixed(2)+'%';
 let credentials=null,busy=false,accountId=null,rows=[],range='1M',lastSuccess=0,checkedAt='',saved=false;
 const set=(id,text)=>{if($(id))$(id).textContent=text;};
 const status=text=>set('page-status',text);
 function json(key){try{return JSON.parse(localStorage.getItem(key)||'null');}catch{return null;}}
 async function get(base,path){
  if(!credentials)throw Error('Paper 계좌 연결 필요');
  const connection=credentials,controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);
  try{
   const r=await fetch(base+path,{cache:'no-store',headers:{'APCA-API-KEY-ID':connection.keyId,'APCA-API-SECRET-KEY':connection.secretKey},signal:controller.signal});
   if(!r.ok){let detail='';try{detail=(await r.json()).message||'';}catch{}throw Error('조회 실패 '+r.status+(detail?' · '+detail:''));}
   const value=await r.json();if(connection!==credentials)throw Error('연결 계좌가 변경되었습니다. 다시 조회합니다.');return value;
  }finally{clearTimeout(timeout);}
 }
 function svgEl(name,attrs={},text){const e=document.createElementNS('http://www.w3.org/2000/svg',name);for(const [k,v]of Object.entries(attrs))e.setAttribute(k,String(v));if(text!==undefined)e.textContent=text;return e;}
 function td(tr,text,className){const e=document.createElement('td');e.textContent=text;if(className)e.className=className;tr.append(e);}
 function renderWeights(account,positions){
  const result=M.weights(account,positions),state=json('flow.paper.state.v1.'+account.id),selected=state?.selected||[];
  set('weights-total',money(result.total));set('weights-stock',(result.total>0?(1-result.cash/result.total)*100:0).toFixed(2)+'%');set('weights-cash',(result.total>0?result.cash/result.total*100:0).toFixed(2)+'%');
  const svg=$('weight-ring');svg.replaceChildren();svg.append(svgEl('circle',{cx:140,cy:140,r:96,fill:'none',stroke:'#eef1f5','stroke-width':29}));
  const palette=['#2563eb','#ec4899','#22a7a0','#a78bfa','#f59e0b','#5d789b'];let offset=0;
  const body=$('weight-rows');body.replaceChildren();
  result.rows.forEach((r,i)=>{
   const color=r.symbol==='현금'?'#b5beca':palette[i%palette.length],length=2*Math.PI*96,arc=r.chartWeight;
   if(arc>0)svg.append(svgEl('circle',{cx:140,cy:140,r:96,fill:'none',stroke:color,'stroke-width':29,'stroke-dasharray':arc*length+' '+length,'stroke-dashoffset':-offset*length,transform:'rotate(-90 140 140)'}));offset+=arc;
   const tr=document.createElement('tr'),cell=document.createElement('td'),dot=document.createElement('i'),name=document.createElement('span');dot.className='legend-dot';dot.style.background=color;name.textContent=r.symbol;cell.append(dot,name);tr.append(cell);
   td(tr,money(r.value));td(tr,(r.weight*100).toFixed(2)+'%');
   const target=selected.includes(r.symbol)?20:r.symbol==='현금'&&state?.investAll?0:null;
   td(tr,target===null?'—':target.toFixed(2)+'%');td(tr,target===null?'—':pct(r.weight*100-target)+'p');body.append(tr);
  });
  set('weight-note',(result.cash<0?'현금 부족 '+money(result.cash)+' · 표는 부족액을 포함한 순자산 기준, 원형 그래프는 양수 자산 구성 기준입니다. ':'')+'실제 평가금액과 현금 합계 기준 · 목표는 최근 완료한 리밸런싱 기준 · 가격 변화에 따라 실제 비중이 달라집니다.');
 }
 let targetSignal=null,targetFetched=0,targetGeneration=0;
 async function loadTargets(account){
  if(mode!=='weights'||!$('holdings'))return;const token=++targetGeneration;
  try{
   if(!targetSignal||Date.now()-targetFetched>=300000){
    let response;try{response=await fetch('https://raw.githubusercontent.com/rpredpill/xlg-blender-render/somx-pages/flow-latest.json?t='+Date.now(),{cache:'no-store'});if(!response.ok)throw Error('신호 조회 실패');}catch{response=await fetch('./flow-latest.json?t='+Date.now(),{cache:'no-store'});}
    if(!response.ok)throw Error('신호 조회 실패');const next=await response.json();if(token!==targetGeneration)return;targetSignal=next;targetFetched=Date.now();
   }
   if(token!==targetGeneration)return;const state=account?json('flow.paper.state.v1.'+account.id):null,signal=targetSignal;
   const targets=state?.pending?.rows||FLOWPaperCore.targets(signal,state?.selected||[]),body=$('holdings');body.replaceChildren();
   for(const row of targets){const tr=document.createElement('tr');td(tr,row.ticker);td(tr,row.rank);td(tr,(row.weight*100).toFixed(2)+'%');body.append(tr);}
   set('signal-meta','신호 '+(state?.pending?.signalDate||signal.signalDate)+' · '+(state?.pending?'진행 중 주문의 고정 목표':signal.eligibleCount+'/'+signal.universeCount+'개 데이터 유효 · 최신 목표 준비됨'));
  }catch{if(token!==targetGeneration)return;$('holdings').replaceChildren();set('signal-meta','최신 편입 신호 확인 대기 · 기존 보유 및 접수 주문은 유지합니다.');}
 }
 async function bars(start,end){
  const out={QQQ:[],QLD:[],VOO:[]};let token=null;const seen=new Set();
  do{
   const q=new URLSearchParams({symbols:'QQQ,QLD,VOO',timeframe:'1Day',start,end,adjustment:'all',feed:'sip',limit:'10000',sort:'asc'});if(token)q.set('page_token',token);
   const data=await get(DATA,'/stocks/bars?'+q);
   for(const s of Object.keys(out))out[s].push(...(data.bars?.[s]||[]));
   token=data.next_page_token||null;if(token){if(seen.has(token)||seen.size>=100)throw Error('가격 이력의 나머지 페이지 확인 필요');seen.add(token);}
  }while(token);
  return out;
 }
 const COMPARE_RANGES={ '1D':'1일','5D':'5일','1M':'1개월','6M':'6개월',YTD:'YTD',ALL:'전체' };
 let focusedSeries=null,compareLoaded=false,compareError='';
 function renderCompare(){
  if(mode!=='compare')return;
  for(const k of Object.keys(COMPARE_RANGES))$('compare-'+k)?.setAttribute('aria-pressed',String(range===k));
  const selected=M.period(rows,range),indexed=M.indexed(selected),box=$('compare-chart'),body=$('compare-results');box.replaceChildren();body.replaceChildren();
  const feedback=$('compare-feedback');if(feedback){feedback.hidden=!compareError;feedback.textContent=compareError;}
  set('compare-coverage','');
  for(const symbol of ['FLOW','QQQ','QLD','VOO']){
   const value=indexed.length>=2?indexed.at(-1)[symbol]-100:null,color=COLORS[symbol];
   const card=document.createElement('button');card.type='button';card.id='compare-focus-'+symbol;card.className='compare-series'+(focusedSeries===symbol?' is-focused':'');card.setAttribute('aria-pressed',String(focusedSeries===symbol));card.setAttribute('aria-label',symbol+' '+(value===null?'기록 대기':pct(value))+' · 그래프 강조');
   const title=document.createElement('span');title.className='compare-series-name';title.textContent=symbol;
   const swatch=svgEl('svg',{viewBox:'0 0 40 10',width:40,height:10,'aria-hidden':'true'});swatch.append(svgEl('path',{d:'M1 5h38',stroke:color,'stroke-width':symbol==='FLOW'?4:3,'stroke-dasharray':{FLOW:'',QQQ:'8 5',QLD:'14 6',VOO:'2 5'}[symbol]}));
   const number=document.createElement('strong');number.textContent=value===null?'—':pct(value);number.style.color=value===null?'#84909f':color;card.append(title,swatch,number);
   card.onclick=()=>{focusedSeries=focusedSeries===symbol?null:symbol;renderCompare();$('compare-focus-'+symbol)?.focus?.({preventScroll:true});};body.append(card);
  }
  if(indexed.length<2){const p=document.createElement('strong');p.className='compare-empty';p.textContent=compareError?'비교 기록을 불러오지 못했어요':!credentials?'설정에서 계좌를 연결하면 비교할 수 있어요.':compareLoaded?'비교 가능한 거래일 기록이 아직 부족해요.':'비교 데이터를 불러오는 중…';box.append(p);return;}
  const short=range!=='ALL'&&selected[0]===rows[0]&&(range==='5D'?rows.length<6:range!=='1D');
  set('compare-coverage',short?COMPARE_RANGES[range]+' 선택 · 현재 쌓인 '+rows.length+'거래일 기록으로 표시':range==='1D'?'최근 완료 거래일의 변화':range==='5D'?'최근 완료된 5거래일의 변화':'');
  const values=indexed.flatMap(r=>['FLOW','QQQ','QLD','VOO'].filter(s=>!focusedSeries||s===focusedSeries).map(s=>r[s]-100));
  const lo=Math.min(0,...values),hi=Math.max(0,...values),pad=Math.max((hi-lo)*.13,.15),min=lo-pad,max=hi+pad;
  const width=Math.max(280,Math.round(box.clientWidth||760)),height=width<520?280:340,left=62,right=width-12,top=18,bottom=height-42,font=width<520?14:15;
  const svg=svgEl('svg',{viewBox:'0 0 '+width+' '+height,role:'img','aria-label':'선택 기간 '+COMPARE_RANGES[range]+' 계좌와 ETF 가치 변화 비교'});svg.style.width='100%';
  const y=v=>top+(bottom-top)*(max-v)/(max-min),x=i=>left+i*(right-left)/(indexed.length-1);
  for(let i=0;i<4;i++){const v=max-(max-min)*i/3,yy=y(v);svg.append(svgEl('line',{x1:left,x2:right,y1:yy,y2:yy,stroke:'#dce2e9'}),svgEl('text',{x:left-8,y:yy+5,'text-anchor':'end',fill:'#526174','font-size':font,'font-weight':500},(v>0?'+':'')+v.toFixed(1)+'%'));}
  svg.append(svgEl('line',{x1:left,x2:right,y1:y(0),y2:y(0),stroke:'#99a6b6','stroke-dasharray':'4 4'}));
  const ordered=['QQQ','QLD','VOO','FLOW'].filter(s=>s!==focusedSeries).concat(focusedSeries?[focusedSeries]:[]);
  for(const symbol of ordered){
   const color=COLORS[symbol],dim=focusedSeries&&focusedSeries!==symbol;
   const path=indexed.map((r,i)=>(i?'L':'M')+x(i).toFixed(2)+','+y(r[symbol]-100).toFixed(2)).join(' ');
   svg.append(svgEl('path',{d:path,fill:'none',stroke:color,'stroke-width':symbol==='FLOW'?4:3,'stroke-dasharray':{FLOW:'',QQQ:'8 5',QLD:'14 6',VOO:'2 5'}[symbol],opacity:dim?.12:1,'stroke-linecap':'round','stroke-linejoin':'round'}));
  }
  const label=d=>indexed[0].date.slice(0,4)!==indexed.at(-1).date.slice(0,4)?d.slice(2).replaceAll('-','.'):d.slice(5).replace('-','/');svg.append(svgEl('text',{x:left,y:height-10,fill:'#526174','font-size':font},label(indexed[0].date)),svgEl('text',{x:right,y:height-10,'text-anchor':'end',fill:'#526174','font-size':font},label(indexed.at(-1).date)));box.append(svg);
 }
 async function loadComparison(account){
  const cacheKey='flow.paper.compare.v1.'+account.id;
  if(accountId!==account.id){accountId=account.id;rows=[];checkedAt='';compareLoaded=false;focusedSeries=null;renderCompare();const cache=json(cacheKey);if(Array.isArray(cache?.rows)&&cache.rows.every(r=>/^\d{4}-\d{2}-\d{2}$/.test(r?.date)&&Object.keys(COLORS).every(s=>Number.isFinite(r[s])&&r[s]>0))){rows=cache.rows;checkedAt=cache.checkedAt||'';saved=true;renderCompare();}}
  const clock=await get(PAPER,'/clock'),today=M.date(clock.timestamp),startCalendar=new Date(clock.timestamp);startCalendar.setUTCDate(startCalendar.getUTCDate()-20);
  const calendar=await get(PAPER,'/calendar?start='+startCalendar.toISOString().slice(0,10)+'&end='+today);
  const time=new Intl.DateTimeFormat('en-GB',{timeZone:'America/New_York',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(clock.timestamp));
  const complete=calendar.filter(d=>d.date<today||d.date===today&&!clock.is_open&&time>=(d.close||'16:00')).at(-1)?.date;
  if(!complete)throw Error('완료 거래일 확인 필요');
  const created=Date.parse(account.created_at);if(!Number.isFinite(created))throw Error('계좌 개설일 확인 필요');
  const data=await get(PAPER,'/account/portfolio/history?timeframe=1D&start='+encodeURIComponent(new Date(created).toISOString())),history=M.history(data);
  if(!history.length){rows=[];compareLoaded=true;renderCompare();return;}
  const end=new Date(Date.now()-16*60000).toISOString(),prices=await bars(history[0].date,end);
  for(const symbol of Object.keys(prices))if(!prices[symbol].length)throw Error(symbol+' 가격 이력을 불러오지 못했습니다.');
  rows=M.align(history,prices,complete);compareLoaded=true;compareError='';checkedAt=new Date().toISOString();saved=false;
  try{localStorage.setItem(cacheKey,JSON.stringify({rows,checkedAt}));}catch{}
  renderCompare();
 }
 async function loadSchedule(account){
  const clock=await get(PAPER,'/clock'),state=json('flow.paper.state.v1.'+account.id),plan=M.schedule(state,clock);
  const days=await get(PAPER,'/calendar?start='+plan.month+'-01&end='+plan.month+'-10'),first=days.filter(d=>d.date.startsWith(plan.month)).sort((a,b)=>a.date.localeCompare(b.date))[0];
  if(!first)throw Error('휴장일을 반영한 첫 거래일 확인 필요');
  set('rebalance-date',first.date.replaceAll('-','.'));
  set('rebalance-detail',plan.pending?'진행 중인 리밸런싱 주문이 있습니다.':plan.done?'이번 반기 완료 · 다음 반기 첫 거래일':plan.started?'이번 반기 실행 대상 · 미완료':'첫 투자 대기 · 이번 반기 기준일');
  set('schedule-checked',new Date().toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})+' KST 기준');
 }
 async function refresh(force=false){
  if(busy||!credentials||document.visibilityState==='hidden')return;
  if(!force&&Date.now()-lastSuccess<(mode==='compare'?300000:15000))return;
  busy=true;
  try{
   const account=await get(PAPER,'/account');
   if(mode==='weights'){const positions=await get(PAPER,'/positions');renderWeights(account,positions);void loadTargets(account);}else if(mode==='schedule')await loadSchedule(account);else await loadComparison(account);
   lastSuccess=Date.now();status(mode==='weights'?'현재 보유 비중 · Paper 계좌':mode==='schedule'?'리밸런싱 일정 · Paper 계좌':'동일 기간 비교 · 완료 거래일 기준');
   set('page-checked',(mode==='weights'?new Date(lastSuccess):new Date(checkedAt||lastSuccess)).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})+' KST 기준');
  }catch(e){if(mode==='compare'){compareError=(rows.length?'저장된 비교 기록 표시 · ':'')+'조회 실패 · 잠시 후 다시 확인합니다.';renderCompare();}if(mode==='weights'){$('weight-ring').replaceChildren();$('weight-rows').replaceChildren();set('weight-note','현재 보유 내역 조회 실패 · 계좌 연결에서 조회 상태를 확인하세요.');}if(mode==='schedule'){set('rebalance-date','—');set('rebalance-detail','일정을 확인하지 못했습니다. 계좌 연결에서 조회 상태를 확인하세요.');set('schedule-checked','');}status((mode==='compare'&&rows.length?'저장된 비교 기록 표시 · ':'')+'조회 확인 필요 · '+(e.name==='AbortError'?'응답 시간 초과':e.message));}
  finally{busy=false;}
 }
 for(const k of Object.keys(COMPARE_RANGES))if($('compare-'+k))$('compare-'+k).onclick=()=>{range=k;renderCompare();};
 document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')void refresh(true);});
 window.addEventListener('online',()=>void refresh(true));setInterval(()=>void refresh(),15000);
 const existing=json('flow.paper.credentials.v1')||json('somx.alpaca.credentials.v1');
 if(existing?.keyId&&existing?.secretKey){credentials=existing;void refresh(true);}else{status('Paper 계좌 연결 필요 · 설정에 저장한 연결 정보를 이어 사용합니다.');}
 window.addEventListener('resize',()=>{if(mode==='compare')renderCompare();});if(mode==='compare')renderCompare();if(mode==='weights'&&!credentials)void loadTargets(null);
})();
