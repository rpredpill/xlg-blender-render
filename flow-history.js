(function(root){
 'use strict';
 const LABELS={'1D':'1일','1M':'1개월','1Y':'1년','5Y':'5년',ALL:'전체'};
 const ny=t=>new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(t));
 function normalize(data){
   if(!Array.isArray(data?.timestamp)||!Array.isArray(data.equity)||data.timestamp.length!==data.equity.length)throw Error('계좌 이력 응답 형식 확인 필요');
   const out=new Map();data.timestamp.forEach((t,i)=>{const e=data.equity[i];if(typeof t==='number'&&Number.isFinite(t)&&typeof e==='number'&&Number.isFinite(e)&&e>=0)out.set(t*1000,{time:t*1000,equity:e});});
   const rows=[...out.values()].sort((a,b)=>a.time-b.time),first=rows.findIndex(r=>r.equity>0);return first<0?[]:rows.slice(first);
 }
 function merge(a,b){const days=new Map();for(const r of a.concat(b)){if(Number.isFinite(r.time)&&Number.isFinite(r.equity)&&r.equity>=0)days.set(ny(r.time),r);}return [...days.values()].sort((a,b)=>a.time-b.time);}
 function select(rows,range,now=Date.now()){
   if(range==='1D'){const last=rows.at(-1);return last?rows.filter(r=>ny(r.time)===ny(last.time)):[];}
   if(range==='ALL')return rows;
   const d=new Date(now),day=d.getUTCDate();d.setUTCDate(1);
   if(range==='1M')d.setUTCMonth(d.getUTCMonth()-1);else d.setUTCFullYear(d.getUTCFullYear()-(range==='5Y'?5:1));
   const month=d.getUTCMonth();d.setUTCDate(day);if(d.getUTCMonth()!==month)d.setUTCDate(0);
   return rows.filter(r=>r.time>=d.getTime());
 }
 function change(rows){
   if(rows.length<2)return null;
   const first=rows[0].equity,last=rows.at(-1).equity;
   if(!Number.isFinite(first)||first<=0||!Number.isFinite(last))return null;
   return {amount:last-first,percent:(last/first-1)*100};
 }
 function create({api,document,storage,now=()=>Date.now()}){
   const $=id=>document.getElementById(id);let accountId=null,range='1D',daily=[],intraday=[],lastDaily=0,lastIntraday=0,busy=false,generation=0,error='',storageError=false,queued=false,live=null;
   function save(){try{storage.setItem('flow.paper.history.v1.'+accountId,JSON.stringify({daily,updated:lastDaily}));storageError=false;}catch{storageError=true;}}
   function render(){
     for(const k of Object.keys(LABELS))$('history-'+k)?.setAttribute('aria-pressed',String(range===k));
     let source=range==='1D'?intraday:daily;
     if(live&&source.length&&live.time>source.at(-1).time&&(range!=='1D'||ny(live.time)===ny(source.at(-1).time)))source=source.concat(live);
     const rows=select(source,range,now()),box=$('paper-chart');box.replaceChildren();
     const summary=$('history-value-change'),value=change(rows);
     if(summary){
       const sign=value?.amount>0?'+':value?.amount<0?'−':'';
       summary.textContent=value?sign+'$'+Math.abs(value.amount).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})+' ('+sign+Math.abs(value.percent).toFixed(2)+'%)':'—';
       summary.className='history-value-change'+(value?.amount>0?' positive':' negative');
       summary.setAttribute('aria-label',LABELS[range]+' 계좌 가치 변화 '+summary.textContent);
     }
     if(rows.length<2){const strong=document.createElement('strong');strong.textContent=rows.length?'기록을 모으고 있어요':accountId?'이 기간의 기록이 아직 없어요':'계좌를 연결하면 이력을 불러옵니다.';box.append(strong);return;}
     const ns='http://www.w3.org/2000/svg',make=(name,attrs,text)=>{const e=document.createElementNS(ns,name);for(const [k,v]of Object.entries(attrs))e.setAttribute(k,String(v));if(text!==undefined)e.textContent=text;return e;};
     const values=rows.map(r=>r.equity),lo=Math.min(...values),hi=Math.max(...values),pad=Math.max((hi-lo)*.1,hi*.005,1),min=lo-pad,max=hi+pad;
     const width=Math.max(280,Math.round(box.clientWidth||760)),height=width<520?260:320;
     const left=88,right=width-16,top=18,bottom=height-42,font=width<340?14:width<520?15:16,color=value?.amount>0?'#dc2626':'#2563eb';
     const svg=make('svg',{viewBox:'0 0 '+width+' '+height,role:'img','aria-label':`FLOW Paper 계좌 가치 ${LABELS[range]}`});svg.style.width='100%';svg.style.display='block';
     const start=rows[0].time,span=rows.at(-1).time-start||1;
     const points=rows.map(r=>({x:left+(r.time-start)*(right-left)/span,y:top+(bottom-top)*(max-r.equity)/(max-min)}));
     const path=points.map((p,i)=>(i?'L':'M')+p.x.toFixed(2)+','+p.y.toFixed(2)).join(' ');
     const defs=make('defs',{}),gradient=make('linearGradient',{id:'flow-history-fill',x1:'0%',y1:'0%',x2:'0%',y2:'100%'});
     gradient.append(make('stop',{offset:'0%','stop-color':color,'stop-opacity':.25}),make('stop',{offset:'100%','stop-color':color,'stop-opacity':0}));defs.append(gradient);svg.append(defs);
     svg.append(make('path',{d:path+' L'+right+','+bottom+' L'+left+','+bottom+' Z',fill:'url(#flow-history-fill)',stroke:'none','aria-hidden':'true'}));
     for(let i=0;i<4;i++){const y=top+i*(bottom-top)/3;svg.append(make('line',{x1:left,x2:right,y1:y,y2:y,stroke:'#dce2e9','stroke-width':1}),make('text',{x:left-9,y:y+5,'text-anchor':'end',fill:'#526174','font-size':font,'font-weight':500},'$'+(max-(max-min)*i/3).toLocaleString('en-US',{maximumFractionDigits:0})));}
     svg.append(make('path',{d:path,fill:'none',stroke:color,'stroke-width':3,'stroke-linejoin':'round','stroke-linecap':'round'}));
     const last=points.at(-1);svg.append(make('circle',{cx:last.x,cy:last.y,r:4,fill:color,stroke:'#fff','stroke-width':2,'aria-hidden':'true'}));
     const label=r=>range==='1D'?new Intl.DateTimeFormat('ko-KR',{timeZone:'America/New_York',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(r.time))+' ET':ny(r.time);
     svg.append(make('text',{x:left,y:height-10,fill:'#526174','font-size':font,'font-weight':500},label(rows[0])),make('text',{x:right,y:height-10,'text-anchor':'end',fill:'#526174','font-size':font,'font-weight':500},label(rows.at(-1))));box.append(svg);
   }
   function reset(){generation++;accountId=null;daily=[];intraday=[];live=null;lastDaily=lastIntraday=0;busy=false;queued=false;error='';render();}
   async function update(account){
     if(!account?.id)return;
     if(accountId!==account.id){reset();accountId=account.id;try{const cached=JSON.parse(storage.getItem('flow.paper.history.v1.'+accountId)||'null');if(Array.isArray(cached?.daily))daily=merge([],cached.daily);}catch{}render();}
     if(Number.isFinite(Number(account.equity))){live={time:now(),equity:Number(account.equity)};render();}
     if(busy)return;
     const dailyDue=now()-lastDaily>=300000,dayDue=range==='1D'&&now()-lastIntraday>=60000;if(!dailyDue&&!dayDue)return;
     busy=true;error='';render();const id=accountId,token=generation;
     try{
       const jobs=[];
       if(dailyDue){const created=Date.parse(account.created_at),start=Number.isFinite(created)?new Date(created).toISOString():'2000-01-01T00:00:00Z';jobs.push(api('/account/portfolio/history?timeframe=1D&start='+encodeURIComponent(start)).then(data=>({kind:'daily',rows:normalize(data)})));}
       if(dayDue)jobs.push(api('/account/portfolio/history?period=7D&timeframe=5Min&intraday_reporting=market_hours').then(data=>({kind:'intraday',rows:normalize(data)})));
       const results=await Promise.allSettled(jobs);if(token!==generation||id!==accountId)return;
       for(const r of results){if(r.status==='rejected'){error='조회 실패';continue;}if(r.value.kind==='daily'){daily=merge(daily,r.value.rows);lastDaily=now();save();}else{intraday=r.value.rows;lastIntraday=now();}}
     }finally{if(token===generation&&id===accountId){busy=false;render();if(queued){queued=false;if(currentAccount)void update(currentAccount);}}}
   }
   for(const k of Object.keys(LABELS))if($('history-'+k))$('history-'+k).onclick=()=>{range=k;if(busy)queued=true;render();if(currentAccount)void update(currentAccount);};
   let currentAccount=null;document.defaultView?.addEventListener('resize',render);render();
   return {update(account){currentAccount=account;return update(account);},reset(){currentAccount=null;reset();},render};
 }
 const exported={normalize,merge,select,change,create};if(typeof module!=='undefined'&&module.exports)module.exports=exported;else root.FlowHistory=exported;
})(globalThis);
