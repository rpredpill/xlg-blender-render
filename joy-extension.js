(()=>{
 const base=globalThis.SOMXStrategy, key='somx.strategy.active.v1';
 const config={mode:'joy',label:'환희',holdings:10,entryRank:10,exitRank:20,rebalanceMonths:3,factor:'tradedValue',weighting:'score',filters:{},cadence:'liveSimulation'};
 const old={getMode:base.getMode.bind(base),getConfig:base.getConfig.bind(base),setMode:base.setMode.bind(base),signature:base.signature.bind(base)};
 base.getMode=()=>localStorage.getItem(key)==='joy'?'joy':old.getMode();
 base.getConfig=()=>base.getMode()==='joy'?{...config}:old.getConfig();
 base.setMode=m=>m==='joy'?localStorage.setItem(key,m):old.setMode(m);
 base.signature=c=>c?.mode==='joy'?'flow-backtest-v1':old.signature(c);
 globalThis.JoyStrategy={config};
})();
