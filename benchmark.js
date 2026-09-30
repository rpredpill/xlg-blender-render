(()=>{
  const panel=document.getElementById("benchmark-panel");
  const svg=document.getElementById("benchmark-chart");
  const stateBox=document.getElementById("benchmark-state");
  const somxValue=document.getElementById("benchmark-somx-value");
  const spyValue=document.getElementById("benchmark-spy-value");
  const historyList=document.getElementById("history-list");
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

  let busy=false,lastKey="",retryTimer=null;
  const NS="http://www.w3.org/2000/svg";
  const monthAdd=(ym,n)=>{const [y,m]=ym.split("-").map(Number),d=new Date(Date.UTC(y,m-1+n,1));return `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,"0")}`};
  const nyMonth=()=>{const p=Object.fromEntries(new Intl.DateTimeFormat("en-US",{timeZone:"America/New_York",year:"numeric",month:"2-digit"}).formatToParts(new Date()).filter(x=>x.type!=="literal").map(x=>[x.type,x.value]));return `${p.year}-${p.month}`};
  const pct=text=>{const n=Number(String(text||"").replace(/[^0-9+-.]/g,""));return Number.isFinite(n)?n:null};
  function svgEl(name,attrs={}){const n=document.createElementNS(NS,name);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,String(v));return n}

  function historyMap(){
    const out=new Map();
    if(!historyList)return out;
    for(const el of historyList.querySelectorAll(".history-month")){
      const month=el.querySelector(".history-month-label")?.textContent.trim();
      const value=pct(el.querySelector(".history-port")?.textContent);
      if(/^\d{4}-\d{2}$/.test(month||"")&&value!==null)out.set(month,value);
    }
    return out;
  }

  async function stockMonthReturn(month,symbols){
    const alpaca=globalThis.alpaca;
    if(typeof alpaca!=="function")throw new Error("Alpaca 연결 후 비교 그래프가 표시됩니다.");
    const q=new URLSearchParams({symbols:symbols.join(","),timeframe:"1Day",start:`${month}-01`,end:`${monthAdd(month,1)}-01`,adjustment:"all",feed:"iex",limit:"10000",sort:"asc"});
    const data=await alpaca(`/stocks/bars?${q}`);
    const ratios=[];
    for(const s of symbols){
      const bars=data.bars?.[s]||[];
      if(bars.length&&Number(bars[0].o)>0&&Number.isFinite(Number(bars[bars.length-1].c)))ratios.push(Number(bars[bars.length-1].c)/Number(bars[0].o));
    }
    if(!ratios.length)return null;
    return (ratios.reduce((a,b)=>a+b,0)/ratios.length-1)*100;
  }

  async function spyReturns(months){
    const alpaca=globalThis.alpaca;
    if(typeof alpaca!=="function")throw new Error("Alpaca 연결 후 비교 그래프가 표시됩니다.");
    const first=months[0],last=months[months.length-1];
    const q=new URLSearchParams({symbols:"SPY",timeframe:"1Day",start:`${first}-01`,end:`${monthAdd(last,1)}-01`,adjustment:"all",feed:"iex",limit:"1000",sort:"asc"});
    const data=await alpaca(`/stocks/bars?${q}`),grouped=new Map();
    for(const b of(data.bars?.SPY||[])){const ym=String(b.t||"").slice(0,7);if(!grouped.has(ym))grouped.set(ym,[]);grouped.get(ym).push(b)}
    const out=new Map();
    for(const m of months){const bars=grouped.get(m)||[];if(bars.length&&Number(bars[0].o)>0)out.set(m,(Number(bars[bars.length-1].c)/Number(bars[0].o)-1)*100)}
    return out;
  }

  function draw(monthly){
    const indexed=[{month:"시작",somx:100,spy:100}];
    let s=100,p=100;
    for(const r of monthly){s*=1+r.somx/100;p*=1+r.spy/100;indexed.push({month:r.month,somx:s,spy:p})}
    svg.innerHTML="";svg.hidden=false;stateBox.hidden=true;
    const W=760,H=300,L=50,R=20,T=24,B=42;
    const vals=indexed.flatMap(r=>[r.somx,r.spy]);
    let min=Math.min(...vals),max=Math.max(...vals);if(min===max){min-=2;max+=2}const pad=Math.max(2,(max-min)*.13);min-=pad;max+=pad;
    const x=i=>L+i*((W-L-R)/Math.max(1,indexed.length-1));
    const y=v=>T+(max-v)*(H-T-B)/(max-min);
    for(let i=0;i<=5;i++){
      const v=min+(max-min)*i/5,yy=y(v);
      svg.appendChild(svgEl("line",{x1:L,x2:W-R,y1:yy,y2:yy,stroke:"#e8edf3","stroke-width":1}));
      const t=svgEl("text",{x:L-8,y:yy+4,"text-anchor":"end","font-size":10,fill:"#7b8798"});t.textContent=v.toFixed(0);svg.appendChild(t);
    }
    const y100=y(100);svg.appendChild(svgEl("line",{x1:L,x2:W-R,y1:y100,y2:y100,stroke:"#b8c2cf","stroke-width":1.4,"stroke-dasharray":"4 4"}));
    indexed.forEach((r,i)=>{const t=svgEl("text",{x:x(i),y:H-15,"text-anchor":"middle","font-size":10,fill:"#748196"});t.textContent=i===0?"100":`${Number(r.month.slice(5))}월`;svg.appendChild(t)});
    function series(key,color,label){
      svg.appendChild(svgEl("polyline",{points:indexed.map((r,i)=>`${x(i)},${y(r[key])}`).join(" "),fill:"none",stroke:color,"stroke-width":3.2,"stroke-linecap":"round","stroke-linejoin":"round"}));
      indexed.forEach((r,i)=>{const c=svgEl("circle",{cx:x(i),cy:y(r[key]),r:4,fill:"#fff",stroke:color,"stroke-width":2.4}),title=svgEl("title");title.textContent=`${r.month} ${label}: ${r[key].toFixed(2)}`;c.appendChild(title);svg.appendChild(c)})
    }
    series("somx","#174f95","SOMX");series("spy","#8b96a8","S&P 500");
    somxValue.textContent=`SOMX ${s.toFixed(1)}`;spyValue.textContent=`S&P 500 ${p.toFixed(1)}`;
  }

  async function refresh(){
    if(busy)return;
    const current=nyMonth();
    const months=Object.keys(HOLDINGS).filter(m=>m<current).sort();
    if(!months.length){stateBox.hidden=false;stateBox.textContent="완료된 월간 기록이 생기면 그래프가 표시됩니다.";svg.hidden=true;return}
    const hist=historyMap();
    const key=months.map(m=>`${m}:${hist.get(m)??"x"}`).join("|");
    if(key===lastKey&&svg.childNodes.length)return;
    busy=true;stateBox.hidden=false;stateBox.textContent="SOMX와 S&P 500 누적 성과를 계산하는 중…";svg.hidden=true;
    try{
      const somxMap=new Map(hist);
      for(const m of months){if(!somxMap.has(m)){const r=await stockMonthReturn(m,HOLDINGS[m]);if(Number.isFinite(r))somxMap.set(m,r)}}
      const spyMap=await spyReturns(months);
      const rows=months.map(m=>({month:m,somx:somxMap.get(m),spy:spyMap.get(m)})).filter(r=>Number.isFinite(r.somx)&&Number.isFinite(r.spy));
      if(!rows.length)throw new Error("비교 가능한 월간 데이터가 없습니다.");
      lastKey=key;draw(rows);
    }catch(e){svg.hidden=true;stateBox.hidden=false;stateBox.textContent=e?.message||"그래프를 불러오지 못했습니다.";clearTimeout(retryTimer);retryTimer=setTimeout(refresh,2500)}finally{busy=false}
  }

  if(historyList)new MutationObserver(()=>{clearTimeout(retryTimer);retryTimer=setTimeout(refresh,250)}).observe(historyList,{childList:true,subtree:true,characterData:true});
  window.addEventListener("load",()=>setTimeout(refresh,700));
  document.addEventListener("visibilitychange",()=>{if(!document.hidden)refresh()});
  setTimeout(refresh,900);
})();