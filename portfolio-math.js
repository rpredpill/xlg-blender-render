(function(root){
 'use strict';
 const date=t=>new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(t));
 function weights(account,positions){
  const cash=Number(account.cash);
  if(!Number.isFinite(cash)||cash<0)throw Error('현금 잔액 확인 필요');
  const held=positions.filter(p=>Number.isFinite(Number(p.qty))&&Number(p.qty)!==0);
  if(held.some(p=>p.side!=='long'||!Number.isFinite(Number(p.market_value))||Number(p.market_value)<0))throw Error('현재 비중 페이지는 현금 및 매수 보유 계좌를 지원합니다.');
  const dust=held.filter(p=>Math.abs(Number(p.qty))<=0.000001000001&&Number(p.market_value)<0.01);
  const rows=held.filter(p=>!dust.includes(p)).map(p=>({symbol:p.symbol,value:Number(p.market_value),qty:p.qty}));
  rows.sort((a,b)=>b.value-a.value||a.symbol.localeCompare(b.symbol));
  if(dust.length)rows.push({symbol:'미세 잔여',value:dust.reduce((s,p)=>s+Number(p.market_value),0),qty:null});
  const total=rows.reduce((s,r)=>s+r.value,0)+cash;
  return {total,cash,rows:rows.concat({symbol:'현금',value:cash,qty:null}).map(r=>({...r,weight:total>0?r.value/total:0}))};
 }
 function history(data){
  if(!Array.isArray(data.timestamp)||!Array.isArray(data.equity)||data.timestamp.length!==data.equity.length)throw Error('계좌 이력 형식 확인 필요');
  const days=new Map();data.timestamp.forEach((t,i)=>{const v=data.equity[i];if(Number.isFinite(t)&&Number.isFinite(v)&&v>0)days.set(date(t*1000),{date:date(t*1000),value:v});});
  return [...days.values()].sort((a,b)=>a.date.localeCompare(b.date));
 }
 function align(account,bars,cutoff){
  const maps={FLOW:new Map(account.filter(r=>r.date<=cutoff).map(r=>[r.date,r.value]))};
  for(const symbol of ['QQQ','QLD','VOO']){maps[symbol]=new Map();for(const b of bars[symbol]||[]){if(Number.isFinite(b?.c)&&b.c>0){const d=date(b.t);if(d<=cutoff)maps[symbol].set(d,b.c);}}}
  const dates=[...maps.FLOW.keys()].filter(d=>['QQQ','QLD','VOO'].every(s=>maps[s].has(d))).sort();
  return dates.map(d=>({date:d,...Object.fromEntries(Object.entries(maps).map(([s,m])=>[s,m.get(d)]))}));
 }
 function period(rows,range,now=Date.now()){
  if(range==='ALL')return rows;
  const d=new Date(now),day=d.getUTCDate();d.setUTCDate(1);
  if(range==='1M')d.setUTCMonth(d.getUTCMonth()-1);else d.setUTCFullYear(d.getUTCFullYear()-(range==='5Y'?5:1));
  const month=d.getUTCMonth();d.setUTCDate(day);if(d.getUTCMonth()!==month)d.setUTCDate(0);
  const start=d.toISOString().slice(0,10);return rows.filter(r=>r.date>=start);
 }
 function indexed(rows){
  if(!rows.length)return [];
  const first=rows[0];return rows.map(r=>({date:r.date,...Object.fromEntries(['FLOW','QQQ','QLD','VOO'].map(s=>[s,r[s]/first[s]*100]))}));
 }
 const api={date,weights,history,align,period,indexed};root.PortfolioMath=api;if(typeof module!=='undefined')module.exports=api;
})(globalThis);
