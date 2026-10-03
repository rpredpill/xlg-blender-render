(function(root){
 'use strict';
 function mark(snapshot,quotes={}){
  const base=snapshot.rows||[];
  const rows=base.map(r=>{
   const q=quotes[r.ticker],p=Number(q?.price),ref=Number(r.price);
   const valid=q&&p>0&&ref>0&&String(q.date)>=String(r.priceDate||snapshot.lastDate);
   const ratio=valid?p/ref:1;
   return {...r,weight:r.weight*ratio,ret:Number.isFinite(r.ret)?((1+r.ret/100)*ratio-1)*100:null,price:valid?p:r.price,quoteTime:valid?q.time:null};
  });
  const growth=rows.reduce((s,r)=>s+r.weight,0);
  if(!(growth>0)||!Number.isFinite(growth))throw Error('환희 비중 데이터 오류');
  rows.forEach(r=>r.weight/=growth);
  const benchmarks={...snapshot.benchmarks};
  for(const s of ['SPY','QQQ']){const q=quotes[s],b=snapshot.benchmarkPrices?.[s];if(q&&b&&q.date>=b.priceDate&&q.price>0&&b.price>0&&Number.isFinite(benchmarks[s]))benchmarks[s]=((1+benchmarks[s]/100)*q.price/b.price-1)*100;}
  return {...snapshot,benchmarks,rows,port:Number.isFinite(snapshot.port)?((1+snapshot.port/100)*growth-1)*100:null};
 }
 const api={mark};root.JoyLiveCore=api;if(typeof module!=='undefined')module.exports=api;
})(typeof globalThis!=='undefined'?globalThis:this);
