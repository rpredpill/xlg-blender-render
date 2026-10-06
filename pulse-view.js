(()=>{
 'use strict';
 const $=id=>document.getElementById(id),money=v=>Number.isFinite(Number(v))?(Number(v)<0?'−$':'$')+Math.abs(Number(v)).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}):'—';
 const cell=(tr,text,className)=>{const td=document.createElement('td');td.textContent=text;if(className)td.className=className;tr.append(td);return td;};
 const empty=(id,text,cols)=>{const tr=document.createElement('tr');const td=cell(tr,text,'empty-cell');td.colSpan=cols;$(id)?.replaceChildren(tr);};
 const missingLogos=new Set();
 function stockAvatar(symbol){
  const avatar=document.createElement('span'),fallback=document.createElement('span');avatar.className='stock-avatar';fallback.className='stock-logo-fallback';fallback.textContent=symbol.slice(0,2);avatar.append(fallback);
  if(!/^[A-Z0-9][A-Z0-9.-]{0,14}$/.test(symbol)||missingLogos.has(symbol))return avatar;
  // Company logo images from Financial Modeling Prep's symbol image service.
  const img=document.createElement('img');img.className='stock-logo';img.alt='';img.loading='eager';img.decoding='async';img.referrerPolicy='no-referrer';img.hidden=true;
  img.onload=()=>{img.hidden=false;fallback.hidden=true;avatar.classList.add('has-logo');};
  img.onerror=()=>{missingLogos.add(symbol);img.remove();fallback.hidden=false;avatar.classList.remove('has-logo');};
  img.src='https://images.financialmodelingprep.com/symbol/'+encodeURIComponent(symbol)+'.png';avatar.append(img);return avatar;
 }
 function renderAccount(){}
 function renderPositions(rows){
  rows=rows.filter(r=>Number.isFinite(Number(r.qty))&&Math.abs(Number(r.qty))>0);
  const isRemainder=r=>Math.abs(Number(r.qty))<=0.000001000001&&Number.isFinite(Number(r.market_value))&&Math.abs(Number(r.market_value))<0.01;
  rows=rows.filter(r=>!isRemainder(r));
  if(!rows.length){empty('pulse-positions','아직 보유 종목이 없습니다.',3);return;}
  const body=$('pulse-positions');body.replaceChildren();
  for(const r of [...rows].sort((a,b)=>Number(b.market_value)-Number(a.market_value))){const tr=document.createElement('tr'),td=document.createElement('td'),wrap=document.createElement('span'),avatar=stockAvatar(r.symbol),name=document.createElement('span');wrap.className='stock-symbol';name.textContent=r.symbol;wrap.append(avatar,name);td.append(wrap);tr.append(td);cell(tr,money(r.market_value));const change=r.unrealized_plpc==null?NaN:Number(r.unrealized_plpc)*100;cell(tr,Number.isFinite(change)?`${change>=0?'+':''}${change.toFixed(2)}%`:'—',change>0?'positive':'negative');body.append(tr);}
 }
 function renderOrders(rows){
  if(!rows.length){empty('pulse-orders','아직 주문 내역이 없습니다.',5);return;}
  const statuses={new:'접수',accepted:'접수',pending_new:'접수 중',filled:'체결 완료',partially_filled:'일부 체결',canceled:'취소',rejected:'거절',expired:'만료',done_for_day:'당일 종료',pending_cancel:'취소 중',held:'대기',calculated:'정산 중'};
  const body=$('pulse-orders');body.replaceChildren();for(const r of rows){const tr=document.createElement('tr');cell(tr,r.symbol);cell(tr,r.side==='buy'?'매수':'매도',r.side==='buy'?'positive':'negative');const td=document.createElement('td'),badge=document.createElement('span');badge.className='order-badge';badge.textContent=statuses[r.status]||r.status;td.append(badge);tr.append(td);cell(tr,Number(r.filled_qty||0).toLocaleString('en-US',{maximumFractionDigits:6}));cell(tr,r.filled_avg_price!=null?money(r.filled_avg_price):'—');body.append(tr);}
 }
 function unavailable(id,cols){empty(id,'내역을 불러오지 못했습니다. 다음 갱신 때 다시 확인합니다.',cols);}
 function clear(){renderAccount(null,null);empty('pulse-positions','계좌 연결 후 보유 종목을 확인하세요.',3);empty('pulse-orders','계좌를 연결하면 최근 주문을 불러옵니다.',5);}
 globalThis.PulseView={renderAccount,renderPositions,renderOrders,unavailable,clear};
})();
