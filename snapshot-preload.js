(()=>{
"use strict";
try{
  const ACTIVE="somx.strategy.active.v1";
  let mode=localStorage.getItem(ACTIVE);
  if(mode==="core"){mode="snpi";localStorage.setItem(ACTIVE,"snpi")}
  if(mode!=="snpi"&&mode!=="ququ")return;
  const file=mode==="snpi"?"snpi-latest.json":"ququ-latest.json";
  const sig=mode==="snpi"?"snpi-sp500-v1":"ququ-ndx100-v1";
  const min=mode==="snpi"?495:100;
  const xhr=new XMLHttpRequest();
  xhr.open("GET",`./${file}?v=${Date.now()}`,false);
  xhr.setRequestHeader("Cache-Control","no-cache");
  xhr.send(null);
  if(xhr.status<200||xhr.status>=300)return;
  const j=JSON.parse(xhr.responseText||"{}");
  if(!Array.isArray(j.rows)||j.rows.length<min)return;
  const rows=j.rows.map(r=>({ticker:String(r.ticker||"").trim().toUpperCase(),weight:Number(r.weight)}));
  if(rows.some(r=>!r.ticker||!Number.isFinite(r.weight)||r.weight<=0))return;
  const sum=rows.reduce((a,r)=>a+r.weight,0);if(Math.abs(sum-1)>1e-6)return;
  const p=new Intl.DateTimeFormat("en-CA",{timeZone:"America/New_York",year:"numeric",month:"2-digit"}).formatToParts(new Date());
  const ym=`${p.find(x=>x.type==="year").value}-${p.find(x=>x.type==="month").value}`;
  const holdings=rows.map(r=>r.ticker),targetWeights=Object.fromEntries(rows.map(r=>[r.ticker,r.weight]));
  const key=kind=>`somx.${kind}.v2.${sig}`;
  let hh={},wh={};try{hh=JSON.parse(localStorage.getItem(key("holdings"))||"{}")||{}}catch{}try{wh=JSON.parse(localStorage.getItem(key("weights"))||"{}")||{}}catch{}
  hh[ym]=holdings;wh[ym]=targetWeights;
  const st={rebalanceMonth:ym,strategyAnchor:ym,initialized:true,holdings,statuses:Object.fromEntries(holdings.map(s=>[s,"IN"])),targetWeights,basePrices:{},updatedAt:new Date().toISOString(),snapshotAt:j.generatedAt||null};
  localStorage.setItem(key("state"),JSON.stringify(st));
  localStorage.setItem(key("holdings"),JSON.stringify(hh));
  localStorage.setItem(key("weights"),JSON.stringify(wh));
}catch(e){console.warn("snapshot preload",e)}
})();
