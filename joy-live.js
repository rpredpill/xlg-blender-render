(()=>{
 const isJoy=()=>localStorage.getItem('somx.strategy.active.v1')==='joy';
 let data=null,selected=null,promise=null;
 const originalRender=render,originalStart=start;
 const fmt=v=>Number.isFinite(v)?`${v>=0?'+':''}${v.toFixed(2)}%`:'--';
 function renderJoy(){
   if(!isJoy())return;
   document.body.dataset.strategy='joy';
   const selector=document.getElementById('joy-month');if(selector)selector.hidden=false;
   document.querySelector('.summary-title > span').textContent=`환희 · 백테스트 월간 수익률${selected?' · '+selected:''}`;
   const panel=document.getElementById('benchmark-panel');panel.style.display='';
   document.querySelectorAll('[data-benchmark]').forEach(b=>b.hidden=!['SPY','QQQ'].includes(b.dataset.benchmark));
   const th=document.querySelectorAll('#holdings-body')[0]?.closest('table').querySelectorAll('th');if(th){th[1].textContent='월말 비중';th[2].textContent='종목 수익률';}
   const h=data?.months?.[selected];if(!h){document.getElementById('holdings-body').textContent='환희 백테스트를 불러오는 중…';return;}
   const rows=h.rows;
   drawDonut(rows);document.getElementById('donut').setAttribute('aria-label','환희 백테스트 포트폴리오');
   document.getElementById('holdings-body').innerHTML=rows.map((r,i)=>`<tr><td><div class="stock"><div class="logo" style="color:${colorFor(i)}">${r.ticker}</div><div>${r.ticker}</div></div></td><td>${(r.weight*100).toFixed(2)}%</td><td class="ret ${r.ret<0?'neg':''}">${fmt(r.ret)}</td></tr>`).join('');
   setMetric('portfolio-return',h.port);
   const valid=rows.filter(r=>Number.isFinite(r.ret)),best=[...valid].sort((a,b)=>b.ret-a.ret)[0],worst=[...valid].sort((a,b)=>a.ret-b.ret)[0];
   document.getElementById('up-count').textContent=`${valid.filter(r=>r.ret>0).length} / ${valid.length}`;
   document.getElementById('best-ticker').textContent=best?.ticker||'--';setMetric('best-return',best?.ret);
   document.getElementById('worst-ticker').textContent=worst?.ticker||'--';setMetric('worst-return',worst?.ret);
   document.getElementById('joy-note').hidden=false;
   document.getElementById('joy-note').textContent=`백테스트 ${data.start} ~ ${data.end} · ${h.lastDate} 보유 비중 · 종목 수익률은 ${h.firstDate} 첫 종가 → 월말 종가. 포트폴리오는 매매비용 포함 NAV 기준. 실제 Paper 기록은 공포에서 확인하세요.`;
 }
 async function load(){
   if(!promise)promise=fetch('./joy-history.json?v=1').then(r=>{if(!r.ok)throw Error('환희 기록을 불러오지 못했습니다.');return r.json()}).then(j=>{data=j;const s=document.getElementById('joy-month');s.replaceChildren(...Object.keys(j.months).sort().reverse().map(m=>{const o=document.createElement('option');o.value=m;o.textContent=m;return o}));selected=localStorage.getItem('joy.month.v1');if(!j.months[selected])selected=Object.keys(j.months).sort().at(-1);s.value=selected;return j}).catch(e=>{promise=null;throw e});
   return promise;
 }
 async function activate(){
   localStorage.setItem('somx.strategy.active.v1','joy');
   activeStrategy={...globalThis.JoyStrategy.config};strategySig='flow-backtest-v1';
   if(socket){socket.close();socket=null;}
   state={initialized:true,rebalanceMonth:currentNYMonth(),holdings:[],targetWeights:{},basePrices:{},statuses:{}};
   document.body.dataset.strategy='joy';renderJoy();
   try{await load();if(!isJoy())return;monthlyHistory=data.months;renderJoy();window.dispatchEvent(new Event('somx:historychange'));}catch(e){document.getElementById('holdings-body').textContent=e.message;}
 }
 render=function(){if(isJoy())return renderJoy();document.querySelectorAll('[data-benchmark]').forEach(b=>b.hidden=false);document.getElementById('joy-month')?.setAttribute('hidden','');document.getElementById('joy-note')?.setAttribute('hidden','');const th=document.getElementById('holdings-body')?.closest('table').querySelectorAll('th');if(th){th[1].textContent='상태';th[2].textContent='1개월 수익률';}return originalRender();};
 start=async function(){if(isJoy())return activate();return originalStart();};
 document.getElementById('historyBtn').addEventListener('click',e=>{
   if(!isJoy())return;e.preventDefault();e.stopImmediatePropagation();
   document.querySelector('#history-modal h2').textContent='환희 · 백테스트 월말 기록';
   document.getElementById('history-status').textContent='2015-11 ~ 2024-12 · 매매비용 포함 · 실제 Paper 성과 아님';
   document.getElementById('history-list').innerHTML=Object.values(data?.months||{}).reverse().map(h=>`<details class="history-month"><summary><span>${h.month}</span><strong class="${h.port<0?'neg':''}">${fmt(h.port)}</strong></summary><div class="history-details">${h.rows.map(r=>`<div class="history-stock"><span>${r.ticker} · ${(r.weight*100).toFixed(2)}%</span><strong>${fmt(r.ret)}</strong></div>`).join('')}</div></details>`).join('');
   document.getElementById('history-modal').classList.add('show');
 },true);
 function boot(){
   const s=document.createElement('select');s.id='joy-month';s.setAttribute('aria-label','환희 백테스트 월 선택');s.hidden=true;s.style.cssText='font:inherit;padding:8px;max-width:140px';document.querySelector('.summary-title').append(s);
   const note=document.createElement('p');note.id='joy-note';note.hidden=true;note.style.cssText='font-size:14px;line-height:1.6;padding:0 20px';document.querySelector('.summary-grid').after(note);
   s.onchange=()=>{selected=s.value;localStorage.setItem('joy.month.v1',selected);renderJoy();};
   if(isJoy())activate();
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);else boot();
 globalThis.JoyLive={activate,render:renderJoy};
})();
