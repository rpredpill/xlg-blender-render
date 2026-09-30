(()=>{
  const STORE="somx.strategy.builder.v1";
  const ACTIVE_STORE="somx.strategy.active.v1";
  const clone=x=>JSON.parse(JSON.stringify(x));
  const core={mode:"core",label:"SOMX Core",holdings:6,entryRank:6,exitRank:16,rebalanceMonths:1,factors:{momentum:{enabled:true,weight:100,lookback:6,skip:1},trend:{enabled:false,weight:0,sma:200},relativeStrength:{enabled:false,weight:0,lookback:6,skip:1},lowVol:{enabled:false,weight:0,window:60},drawdown:{enabled:false,weight:0,window:126},high52:{enabled:false,weight:0,window:252},liquidity:{enabled:false,weight:0,window:20}},filters:{positiveMomentum:false,aboveSma:false,maxDrawdownEnabled:false,maxDrawdown:40,minDollarVolumeEnabled:false,minDollarVolume:20}};
  const aggressive={...clone(core),mode:"aggressive",label:"Aggressive",holdings:10,entryRank:10,exitRank:15};
  const defaultCustom={...clone(core),mode:"custom",label:"Custom",factors:{momentum:{enabled:true,weight:45,lookback:6,skip:1},trend:{enabled:true,weight:15,sma:200},relativeStrength:{enabled:true,weight:10,lookback:6,skip:1},lowVol:{enabled:true,weight:10,window:60},drawdown:{enabled:true,weight:10,window:126},high52:{enabled:true,weight:10,window:252},liquidity:{enabled:false,weight:0,window:20}},filters:{positiveMomentum:false,aboveSma:false,maxDrawdownEnabled:false,maxDrawdown:40,minDollarVolumeEnabled:false,minDollarVolume:20}};
  function loadCustom(){try{return {...clone(defaultCustom),...(JSON.parse(localStorage.getItem(STORE))||{})}}catch{return clone(defaultCustom)}}
  function saveCustom(c){try{localStorage.setItem(STORE,JSON.stringify(c))}catch{}}
  function loadMode(){try{const m=localStorage.getItem(ACTIVE_STORE);return ["core","aggressive","custom"].includes(m)?m:"core"}catch{return "core"}}
  function saveMode(m){try{localStorage.setItem(ACTIVE_STORE,m)}catch{}}
  let custom=loadCustom(),activeMode=loadMode();
  function config(mode=activeMode){return clone(mode==="core"?core:mode==="aggressive"?aggressive:custom)}
  function signature(c=config()){
    if(c.mode==="core")return "core";
    if(c.mode==="aggressive")return "aggressive";
    const raw=JSON.stringify(c);let h=2166136261;for(let i=0;i<raw.length;i++){h^=raw.charCodeAt(i);h=Math.imul(h,16777619)}return `custom-${(h>>>0).toString(36)}`;
  }
  function monthsBetween(a,b){const [ay,am]=a.split("-").map(Number),[by,bm]=b.split("-").map(Number);return (by-ay)*12+(bm-am)}
  function isTradeMonth(anchor,target,c=config()){return monthsBetween(anchor,target)%Math.max(1,Number(c.rebalanceMonths)||1)===0}
  function requiredCalendarDays(c=config()){
    const fs=c.factors||{};let d=380;
    if(fs.momentum?.enabled)d=Math.max(d,(Number(fs.momentum.lookback)||6)*32+40);
    if(fs.relativeStrength?.enabled)d=Math.max(d,(Number(fs.relativeStrength.lookback)||6)*32+40);
    if(fs.trend?.enabled)d=Math.max(d,(Number(fs.trend.sma)||200)*2);
    if(fs.lowVol?.enabled)d=Math.max(d,(Number(fs.lowVol.window)||60)*2);
    if(fs.drawdown?.enabled)d=Math.max(d,(Number(fs.drawdown.window)||126)*2);
    if(fs.high52?.enabled)d=Math.max(d,(Number(fs.high52.window)||252)*2);
    return Math.min(800,Math.max(180,d));
  }
  const ret=(a,b)=>Number.isFinite(a)&&Number.isFinite(b)&&b>0?a/b-1:NaN;
  function stdev(xs){if(xs.length<2)return NaN;const m=xs.reduce((a,b)=>a+b,0)/xs.length;return Math.sqrt(xs.reduce((a,b)=>a+(b-m)*(b-m),0)/(xs.length-1))}
  function maxDrawdown(closes){let peak=-Infinity,worst=0;for(const x of closes){if(x>peak)peak=x;if(peak>0)worst=Math.min(worst,x/peak-1)}return worst}
  function endOfMonthBefore(signalDate,monthsBack){const d=new Date(signalDate);d.setUTCDate(1);d.setUTCMonth(d.getUTCMonth()-monthsBack+1);d.setUTCDate(0);d.setUTCHours(23,59,59,999);return +d}
  function lastCloseAt(bars,ts){let out=NaN;for(const b of bars){const t=+new Date(b.t);if(t<=ts)out=Number(b.c);else break}return out}
  function dailyStats(bars,c,signalDate,spyBars){
    const fs=c.factors,signalTs=+new Date(signalDate),valid=bars.filter(b=>+new Date(b.t)<=signalTs&&Number(b.c)>0).sort((a,b)=>+new Date(a.t)-+new Date(b.t));
    if(valid.length<15)return null;
    const closes=valid.map(b=>Number(b.c)),volumes=valid.map(b=>Number(b.v)||0),last=closes.at(-1);
    const mom=fs.momentum||{},rs=fs.relativeStrength||{};
    const momentum=(()=>{const recent=lastCloseAt(valid,endOfMonthBefore(signalDate,Math.max(1,Number(mom.skip)||1)-1));const early=lastCloseAt(valid,endOfMonthBefore(signalDate,Math.max(Number(mom.lookback)||6,Number(mom.skip)||1)-1));return ret(recent,early)})();
    const smaN=Math.max(10,Number(fs.trend?.sma)||200),smaSlice=closes.slice(-smaN),sma=smaSlice.length?smaSlice.reduce((a,b)=>a+b,0)/smaSlice.length:NaN,trend=ret(last,sma);
    let spyMomentum=NaN;if(spyBars?.length){const sr=lastCloseAt(spyBars,endOfMonthBefore(signalDate,Math.max(1,Number(rs.skip)||1)-1)),se=lastCloseAt(spyBars,endOfMonthBefore(signalDate,Math.max(Number(rs.lookback)||6,Number(rs.skip)||1)-1));spyMomentum=ret(sr,se)}
    const rsOwn=(()=>{const recent=lastCloseAt(valid,endOfMonthBefore(signalDate,Math.max(1,Number(rs.skip)||1)-1));const early=lastCloseAt(valid,endOfMonthBefore(signalDate,Math.max(Number(rs.lookback)||6,Number(rs.skip)||1)-1));return ret(recent,early)})();
    const relativeStrength=Number.isFinite(rsOwn)&&Number.isFinite(spyMomentum)?rsOwn-spyMomentum:NaN;
    const vw=Math.max(10,Number(fs.lowVol?.window)||60),recent=closes.slice(-(vw+1)),rets=[];for(let i=1;i<recent.length;i++)rets.push(recent[i]/recent[i-1]-1);const vol=stdev(rets),lowVol=Number.isFinite(vol)?-vol:NaN;
    const ddN=Math.max(20,Number(fs.drawdown?.window)||126),dd=maxDrawdown(closes.slice(-ddN));
    const hiN=Math.max(20,Number(fs.high52?.window)||252),hi=Math.max(...closes.slice(-hiN)),high52=ret(last,hi);
    const liqN=Math.max(5,Number(fs.liquidity?.window)||20),liqBars=valid.slice(-liqN),liquidity=liqBars.length?liqBars.reduce((a,b)=>a+(Number(b.c)||0)*(Number(b.v)||0),0)/liqBars.length:NaN;
    return {momentum,trend,relativeStrength,lowVol,drawdown:dd,high52,liquidity,last,sma};
  }
  function percentileMap(items,key){const arr=items.filter(x=>Number.isFinite(x.raw[key])).sort((a,b)=>a.raw[key]-b.raw[key]),m=new Map();if(!arr.length)return m;if(arr.length===1){m.set(arr[0].s,1);return m}arr.forEach((x,i)=>m.set(x.s,i/(arr.length-1)));return m}
  function score({universe,barsBySymbol,spyBars,signalDate,config:c=config()}){
    const rows=[];for(const s of universe){const raw=dailyStats(barsBySymbol.get(s)||[],c,signalDate,spyBars);if(!raw)continue;const f=c.filters||{};if(f.positiveMomentum&&!(raw.momentum>0))continue;if(f.aboveSma&&!(raw.last>raw.sma))continue;if(f.maxDrawdownEnabled&&!(raw.drawdown>=-Math.abs(Number(f.maxDrawdown)||40)/100))continue;if(f.minDollarVolumeEnabled&&!(raw.liquidity>=Math.max(0,Number(f.minDollarVolume)||0)*1e6))continue;rows.push({s,raw})}
    const enabled=Object.entries(c.factors||{}).filter(([,v])=>v?.enabled&&Number(v.weight)>0).map(([k,v])=>({k,w:Number(v.weight)}));if(!enabled.length)return [];
    const pct=new Map(enabled.map(({k})=>[k,percentileMap(rows,k)])),sumW=enabled.reduce((a,x)=>a+x.w,0)||1;
    for(const r of rows){let total=0,used=0;for(const {k,w} of enabled){const p=pct.get(k).get(r.s);if(Number.isFinite(p)){total+=p*w;used+=w}}r.score=used?total/used:NaN}
    return rows.filter(r=>Number.isFinite(r.score)).sort((a,b)=>b.score-a.score);
  }

  globalThis.SOMXStrategy={getConfig:()=>config(),getMode:()=>activeMode,getCustom:()=>clone(custom),setCustom:c=>{custom=clone(c);saveCustom(custom)},setMode:m=>{if(["core","aggressive","custom"].includes(m)){activeMode=m;saveMode(m)}},signature,isTradeMonth,requiredCalendarDays,score,presets:{core:clone(core),aggressive:clone(aggressive),custom:clone(defaultCustom)}};

  function initUI(){
    const launch=document.getElementById("strategyBtn");if(!launch)return;
    const modal=document.createElement("div");modal.className="strategy-backdrop";modal.id="strategy-modal";modal.innerHTML=`<div class="strategy-modal"><div class="strategy-top"><div><h2>Strategy Lab</h2><p>S&P 500 안에서 규칙을 조합해 SOMX 변형 전략을 만듭니다.</p></div><button class="strategy-x" type="button">×</button></div><div class="strategy-tabs"><button data-mode="core">Core</button><button data-mode="aggressive">Aggressive</button><button data-mode="custom">Custom</button></div><div class="strategy-body"><section class="strategy-section"><div class="strategy-section-title">Portfolio</div><div class="strategy-grid"><label>Holdings<input id="st-holdings" type="number" min="3" max="20"></label><label>Entry Top<input id="st-entry" type="number" min="3" max="50"></label><label>Exit Rank<input id="st-exit" type="number" min="4" max="100"></label><label>Rebalance<select id="st-rebalance"><option value="1">Monthly</option><option value="2">Every 2 months</option><option value="3">Quarterly</option></select></label></div></section><section class="strategy-section"><div class="strategy-section-title row-title"><span>Factors</span><span class="factor-total" id="factor-total"></span></div><div id="factor-list"></div></section><section class="strategy-section"><div class="strategy-section-title">Filters</div><div class="filter-list"><label><input id="fl-pos" type="checkbox"> Positive momentum only</label><label><input id="fl-sma" type="checkbox"> Price above trend SMA</label><label class="filter-with"><input id="fl-dd-on" type="checkbox"> Max drawdown ≥ -<input id="fl-dd" type="number" min="5" max="90">%</label><label class="filter-with"><input id="fl-liq-on" type="checkbox"> Avg $ volume ≥ $<input id="fl-liq" type="number" min="0" max="1000">M</label></div></section><section class="strategy-section preview-section"><div class="strategy-section-title">Current signal preview</div><div id="strategy-preview" class="strategy-preview">Preview를 누르면 현재 신호 기준 랭킹을 계산합니다.</div></section></div><div class="strategy-actions"><button id="strategy-preview-btn" class="strategy-btn secondary" type="button">Preview</button><button id="strategy-apply-btn" class="strategy-btn primary" type="button">Apply Strategy</button></div></div>`;document.body.appendChild(modal);
    const factorDefs=[
      ["momentum","Momentum","Lookback","lookback",3,24,"Skip","skip",1,3],
      ["trend","Trend / SMA","SMA","sma",20,250,null,null,null,null],
      ["relativeStrength","Relative Strength","Lookback","lookback",3,24,"Skip","skip",1,3],
      ["lowVol","Low Volatility","Window","window",20,180,null,null,null,null],
      ["drawdown","Max Drawdown","Window","window",40,252,null,null,null,null],
      ["high52","52W High Proximity","Window","window",60,300,null,null,null,null],
      ["liquidity","Liquidity","Window","window",5,90,null,null,null,null]
    ];
    const list=modal.querySelector("#factor-list");list.innerHTML=factorDefs.map(d=>`<div class="factor-card" data-factor="${d[0]}"><div class="factor-main"><label class="factor-toggle"><input type="checkbox" data-k="enabled"><span>${d[1]}</span></label><label class="factor-weight">Weight <input type="number" min="0" max="100" data-k="weight">%</label></div><div class="factor-params"><label>${d[2]} <input type="number" min="${d[4]}" max="${d[5]}" data-k="${d[3]}"></label>${d[6]?`<label>${d[6]} <input type="number" min="${d[8]}" max="${d[9]}" data-k="${d[7]}"></label>`:""}</div></div>`).join("");
    let editMode=activeMode,working=config(editMode);
    const q=s=>modal.querySelector(s),qa=s=>[...modal.querySelectorAll(s)];
    function pull(){const c=clone(working);c.mode=editMode;c.label=editMode==="core"?"SOMX Core":editMode==="aggressive"?"Aggressive":"Custom";c.holdings=Math.max(3,Math.min(20,Number(q("#st-holdings").value)||6));c.entryRank=Math.max(c.holdings,Math.min(50,Number(q("#st-entry").value)||c.holdings));c.exitRank=Math.max(c.entryRank+1,Math.min(100,Number(q("#st-exit").value)||16));c.rebalanceMonths=Number(q("#st-rebalance").value)||1;qa(".factor-card").forEach(card=>{const k=card.dataset.factor,obj=c.factors[k];card.querySelectorAll("[data-k]").forEach(inp=>{const p=inp.dataset.k;obj[p]=inp.type==="checkbox"?inp.checked:Number(inp.value)})});c.filters.positiveMomentum=q("#fl-pos").checked;c.filters.aboveSma=q("#fl-sma").checked;c.filters.maxDrawdownEnabled=q("#fl-dd-on").checked;c.filters.maxDrawdown=Number(q("#fl-dd").value)||40;c.filters.minDollarVolumeEnabled=q("#fl-liq-on").checked;c.filters.minDollarVolume=Number(q("#fl-liq").value)||0;return c}
    function fill(c){working=clone(c);q("#st-holdings").value=c.holdings;q("#st-entry").value=c.entryRank;q("#st-exit").value=c.exitRank;q("#st-rebalance").value=String(c.rebalanceMonths);qa(".factor-card").forEach(card=>{const obj=c.factors[card.dataset.factor];card.querySelectorAll("[data-k]").forEach(inp=>{const p=inp.dataset.k;if(inp.type==="checkbox")inp.checked=!!obj[p];else inp.value=obj[p]??0})});q("#fl-pos").checked=!!c.filters.positiveMomentum;q("#fl-sma").checked=!!c.filters.aboveSma;q("#fl-dd-on").checked=!!c.filters.maxDrawdownEnabled;q("#fl-dd").value=c.filters.maxDrawdown;q("#fl-liq-on").checked=!!c.filters.minDollarVolumeEnabled;q("#fl-liq").value=c.filters.minDollarVolume;const locked=editMode!=="custom";qa(".strategy-body input,.strategy-body select").forEach(x=>x.disabled=locked);qa(".strategy-tabs button").forEach(b=>b.classList.toggle("active",b.dataset.mode===editMode));updateTotal()}
    function updateTotal(){const c=pull(),sum=Object.values(c.factors).filter(x=>x.enabled).reduce((a,x)=>a+(Number(x.weight)||0),0);q("#factor-total").textContent=`enabled weight ${sum}% · 자동 정규화`}
    function updateLaunch(){const c=config();launch.textContent=`Strategy · ${c.label}`}
    qa(".strategy-tabs button").forEach(b=>b.onclick=()=>{editMode=b.dataset.mode;working=editMode==="custom"?clone(custom):config(editMode);fill(working);q("#strategy-preview").textContent="Preview를 누르면 현재 신호 기준 랭킹을 계산합니다."});
    modal.addEventListener("input",()=>{if(editMode==="custom"){working=pull();updateTotal()}});
    launch.onclick=()=>{editMode=activeMode;working=config();fill(working);modal.classList.add("show")};q(".strategy-x").onclick=()=>modal.classList.remove("show");modal.addEventListener("click",e=>{if(e.target===modal)modal.classList.remove("show")});
    q("#strategy-preview-btn").onclick=async()=>{const c=pull(),box=q("#strategy-preview");box.textContent="현재 S&P 500을 계산하는 중…";try{if(!globalThis.SOMXLive?.previewStrategy)throw new Error("실시간 엔진 연결을 기다리는 중입니다.");const rows=await globalThis.SOMXLive.previewStrategy(c);box.innerHTML=rows.slice(0,Math.min(20,c.entryRank)).map((r,i)=>`<div class="preview-row"><span>${i+1}</span><strong>${r.s}</strong><em>${(r.score*100).toFixed(1)}</em></div>`).join("")||"조건을 통과한 종목이 없습니다."}catch(e){box.textContent=e.message||"Preview 실패"}};
    q("#strategy-apply-btn").onclick=async()=>{const c=pull();if(editMode==="custom"){custom=clone(c);saveCustom(custom)}activeMode=editMode;saveMode(activeMode);updateLaunch();q("#strategy-apply-btn").textContent="Applying…";try{if(globalThis.SOMXLive?.applyStrategy)await globalThis.SOMXLive.applyStrategy(config());modal.classList.remove("show")}catch(e){q("#strategy-preview").textContent=e.message||"전략 적용 실패"}finally{q("#strategy-apply-btn").textContent="Apply Strategy"}};
    updateLaunch();fill(config());window.addEventListener("somx:strategy-active",updateLaunch);
  }
  if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",initUI);else initUI();
})();