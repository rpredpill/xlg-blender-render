(()=>{
  const panel=document.getElementById("benchmark-panel");
  const svg=document.getElementById("benchmark-chart");
  const stateBox=document.getElementById("benchmark-state");
  const somxValue=document.getElementById("benchmark-somx-value");
  const compareValue=document.getElementById("benchmark-spy-value");
  const titleEl=document.querySelector(".benchmark-main-title");
  const legendLabel=document.getElementById("benchmark-legend-label");
  const noteEl=document.getElementById("benchmark-note");
  if(!panel||!svg||!stateBox)return;

  const HOLDINGS={
    "2026-01":["SNDK","WDC","WBD","ALB","TER","STX"],
    "2026-02":["SNDK","WDC","WBD","ALB","TER","STX"],
    "2026-03":["SNDK","WDC","WBD","ALB","TER","STX"],
    "2026-04":["SNDK","WDC","ALB","TER","STX","LITE"],
    "2026-05":["SNDK","WDC","TER","STX","LITE","CIEN"],
    "2026-06":["SNDK","WDC","STX","LITE","CIEN","MU"],
    "2026-07":["SNDK","WDC","STX","LITE","MU","DELL"],
    "2026-08":["SNDK","WDC","STX","MU","DELL","DDOG"],
    "2026-09":["SNDK","STX","MU","DELL","DDOG","MRNA"]
  };
  const BENCHMARKS={
    SPY:{label:"S&P 500",note:"S&P 500은 SPY 조정주가 프록시"},
    QQQ:{label:"QQQ",note:"QQQ 조정주가 기준"},
    QLD:{label:"QLD",note:"QLD 조정주가 기준"},
    TQQQ:{label:"TQQQ",note:"TQQQ 조정주가 기준"},
    SPMO:{label:"SPMO",note:"SPMO 조정주가 기준"}
  };
  const SELECT_KEY="somx.benchmark.v1";
  const NS="http://www.w3.org/2000/svg";
  let selected=(()=>{try{const v=localStorage.getItem(SELECT_KEY);return BENCHMARKS[v]?v:"SPY"}catch(e){return "SPY"}})();
  let somxCache=null;
  const benchmarkCache=new Map();
  let loadToken=0,retryTimer=null;

  const monthAdd=(ym,n)=>{const [y,m]=ym.split("-").map(Number),d=new Date(Date.UTC(y,m-1+n,1));return `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,"0")}`};
  const nyMonth=()=>{const p=Object.fromEntries(new Intl.DateTimeFormat("en-US",{timeZone:"America/New_York",year:"numeric",month:"2-digit"}).formatToParts(new Date()).filter(x=>x.type!=="literal").map(x=>[x.type,x.value]));return `${p.year}-${p.month}`};
  const monthsDone=()=>Object.keys(HOLDINGS).filter(m=>m<nyMonth()).sort();
  function svgEl(name,attrs={}){const n=document.createElementNS(NS,name);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,String(v));return n}

  function setStatus(text,type="loading"){
    stateBox.textContent=text||"";
    stateBox.dataset.type=type;
    stateBox.hidden=!text;
  }

  function updateBenchmarkUI(){
    const cfg=BENCHMARKS[selected];
    if(titleEl)titleEl.textContent=`SOMX vs ${cfg.label}`;
    if(legendLabel)legendLabel.textContent=cfg.label;
    if(noteEl)noteEl.textContent=`월별 수익률 복리 누적 · ${cfg.note}`;
    svg.setAttribute("aria-label",`SOMX와 ${cfg.label} 누적 성과 비교 그래프`);
    document.querySelectorAll("[data-benchmark]").forEach(btn=>{
      const active=btn.dataset.benchmark===selected;
      btn.classList.toggle("active",active);
      btn.setAttribute("aria-pressed",active?"true":"false");
    });
  }

  function drawScaffold(months){
    svg.innerHTML="";
    svg.hidden=false;
    const W=760,H=300,L=54,R=22,T=24,B=42;
    const y=v=>T+(130-v)*(H-T-B)/60;
    for(const v of [70,85,100,115,130]){
      const yy=y(v);
      svg.appendChild(svgEl("line",{x1:L,x2:W-R,y1:yy,y2:yy,stroke:v===100?"#bac4d0":"#edf1f5","stroke-width":v===100?1.4:1,"stroke-dasharray":v===100?"5 5":"0"}));
      const t=svgEl("text",{x:L-9,y:yy+4,"text-anchor":"end","font-size":10,fill:"#8994a5"});t.textContent=String(v);svg.appendChild(t);
    }
    const labels=["시작",...months];
    labels.forEach((m,i)=>{
      const x=L+i*((W-L-R)/Math.max(1,labels.length-1));
      const t=svgEl("text",{x,y:H-14,"text-anchor":"middle","font-size":10,fill:"#8994a5"});
      t.textContent=i===0?"100":`${Number(m.slice(5))}월`;svg.appendChild(t);
    });
  }

  async function waitForAlpaca(timeout=20000){
    const start=Date.now();
    while(Date.now()-start<timeout){
      if(typeof globalThis.alpaca==="function")return globalThis.alpaca;
      await new Promise(r=>setTimeout(r,120));
    }
    throw new Error("Alpaca 연결을 기다리는 중입니다. 잠시 후 다시 시도합니다.");
  }

  async function fetchSomx(months,alpaca){
    const universe=[...new Set(months.flatMap(m=>HOLDINGS[m]||[]))];
    const q=new URLSearchParams({symbols:universe.join(","),timeframe:"1Day",start:`${months[0]}-01`,end:`${monthAdd(months[months.length-1],1)}-01`,adjustment:"all",feed:"iex",limit:"10000",sort:"asc"});
    const data=await alpaca(`/stocks/bars?${q}`);
    const bySymbolMonth=new Map();
    for(const s of universe){
      const mm=new Map();
      for(const b of(data.bars?.[s]||[])){
        const ym=String(b.t||"").slice(0,7);
        if(!mm.has(ym))mm.set(ym,[]);
        mm.get(ym).push(b);
      }
      bySymbolMonth.set(s,mm);
    }
    const out=new Map();
    for(const m of months){
      const ratios=[];
      for(const s of HOLDINGS[m]||[]){
        const bars=bySymbolMonth.get(s)?.get(m)||[];
        const o=Number(bars[0]?.o),c=Number(bars[bars.length-1]?.c);
        if(bars.length&&o>0&&Number.isFinite(c))ratios.push(c/o);
      }
      if(ratios.length)out.set(m,(ratios.reduce((a,b)=>a+b,0)/ratios.length-1)*100);
    }
    return out;
  }

  async function fetchBenchmark(months,symbol,alpaca){
    const q=new URLSearchParams({symbols:symbol,timeframe:"1Day",start:`${months[0]}-01`,end:`${monthAdd(months[months.length-1],1)}-01`,adjustment:"all",feed:"iex",limit:"1000",sort:"asc"});
    const data=await alpaca(`/stocks/bars?${q}`);
    const grouped=new Map();
    for(const b of(data.bars?.[symbol]||[])){
      const ym=String(b.t||"").slice(0,7);
      if(!grouped.has(ym))grouped.set(ym,[]);
      grouped.get(ym).push(b);
    }
    const out=new Map();
    for(const m of months){
      const bars=grouped.get(m)||[];
      const o=Number(bars[0]?.o),c=Number(bars[bars.length-1]?.c);
      if(bars.length&&o>0&&Number.isFinite(c))out.set(m,(c/o-1)*100);
    }
    return out;
  }

  function draw(months){
    const cfg=BENCHMARKS[selected],bmap=benchmarkCache.get(selected);
    if(!somxCache||!bmap)throw new Error("비교 데이터를 불러오는 중입니다.");
    const rows=months.map(m=>({month:m,somx:somxCache.get(m),bench:bmap.get(m)})).filter(r=>Number.isFinite(r.somx)&&Number.isFinite(r.bench));
    if(!rows.length)throw new Error(`비교 가능한 ${cfg.label} 월간 데이터가 없습니다.`);

    const indexed=[{month:"시작",somx:100,bench:100}];
    let s=100,p=100;
    for(const r of rows){s*=1+r.somx/100;p*=1+r.bench/100;indexed.push({month:r.month,somx:s,bench:p})}
    svg.innerHTML="";svg.hidden=false;
    const W=760,H=300,L=54,R=22,T=24,B=42;
    const vals=indexed.flatMap(r=>[r.somx,r.bench]);
    let min=Math.min(...vals),max=Math.max(...vals);
    if(min===max){min-=2;max+=2}
    const pad=Math.max(2,(max-min)*.13);min-=pad;max+=pad;
    const x=i=>L+i*((W-L-R)/Math.max(1,indexed.length-1));
    const y=v=>T+(max-v)*(H-T-B)/(max-min);
    for(let i=0;i<=5;i++){
      const v=min+(max-min)*i/5,yy=y(v);
      svg.appendChild(svgEl("line",{x1:L,x2:W-R,y1:yy,y2:yy,stroke:"#e8edf3","stroke-width":1}));
      const t=svgEl("text",{x:L-9,y:yy+4,"text-anchor":"end","font-size":10,fill:"#7b8798"});t.textContent=v.toFixed(0);svg.appendChild(t);
    }
    const y100=y(100);svg.appendChild(svgEl("line",{x1:L,x2:W-R,y1:y100,y2:y100,stroke:"#b7c0cc","stroke-width":1.4,"stroke-dasharray":"5 5"}));
    indexed.forEach((r,i)=>{const t=svgEl("text",{x:x(i),y:H-14,"text-anchor":"middle","font-size":10,fill:"#748196"});t.textContent=i===0?"100":`${Number(r.month.slice(5))}월`;svg.appendChild(t)});
    function series(key,color,label){
      svg.appendChild(svgEl("polyline",{points:indexed.map((r,i)=>`${x(i)},${y(r[key])}`).join(" "),fill:"none",stroke:color,"stroke-width":3.6,"stroke-linecap":"round","stroke-linejoin":"round"}));
      indexed.forEach((r,i)=>{const c=svgEl("circle",{cx:x(i),cy:y(r[key]),r:4.2,fill:"#fff",stroke:color,"stroke-width":2.5}),title=svgEl("title");title.textContent=`${r.month} ${label}: ${r[key].toFixed(2)}`;c.appendChild(title);svg.appendChild(c)})
    }
    series("somx","#174f95","SOMX");series("bench","#7b8493",cfg.label);
    somxValue.textContent=`SOMX ${s.toFixed(1)}`;compareValue.textContent=`${cfg.label} ${p.toFixed(1)}`;
    setStatus("");
  }

  async function refresh(force=false){
    clearTimeout(retryTimer);
    const token=++loadToken,months=monthsDone();
    if(!months.length){drawScaffold([]);setStatus("완료된 월간 기록이 생기면 그래프가 표시됩니다.","info");return}
    if(!svg.childNodes.length)drawScaffold(months);
    panel.classList.add("is-loading");
    setStatus(`${BENCHMARKS[selected].label} 비교 데이터를 불러오는 중…`);
    try{
      const alpaca=await waitForAlpaca();
      if(token!==loadToken)return;
      if(force)somxCache=null;
      if(!somxCache)somxCache=await fetchSomx(months,alpaca);
      if(token!==loadToken)return;
      if(force)benchmarkCache.delete(selected);
      if(!benchmarkCache.has(selected))benchmarkCache.set(selected,await fetchBenchmark(months,selected,alpaca));
      if(token!==loadToken)return;
      draw(months);
    }catch(e){
      if(token!==loadToken)return;
      setStatus(e?.message||"그래프를 불러오지 못했습니다.","error");
      retryTimer=setTimeout(()=>refresh(false),2200);
    }finally{
      if(token===loadToken)panel.classList.remove("is-loading");
    }
  }

  document.querySelectorAll("[data-benchmark]").forEach(btn=>btn.addEventListener("click",()=>{
    const symbol=btn.dataset.benchmark;
    if(!BENCHMARKS[symbol]||symbol===selected)return;
    selected=symbol;
    try{localStorage.setItem(SELECT_KEY,selected)}catch(e){}
    updateBenchmarkUI();
    const months=monthsDone();
    if(somxCache&&benchmarkCache.has(selected)){
      try{draw(months)}catch(e){setStatus(e?.message||"그래프를 표시하지 못했습니다.","error")}
    }else{
      refresh(false);
    }
  }));

  updateBenchmarkUI();
  drawScaffold(monthsDone());
  setStatus("비교 데이터를 준비하는 중…");
  window.addEventListener("load",()=>setTimeout(()=>refresh(false),250));
  document.addEventListener("visibilitychange",()=>{if(!document.hidden&&(!somxCache||!benchmarkCache.has(selected)))refresh(false)});
  setTimeout(()=>refresh(false),500);
})();