(()=>{
"use strict";
const LAYOUT_KEY="somx.layout.v1";
const THEME_KEY="somx.theme.v1";
const SETTINGS_TAB_KEY="somx.settings.tab.v1";
const SETTINGS_CRED_KEY="somx.alpaca.credentials.v1";
const validLayouts=new Set(["classic","vertical"]),validThemes=new Set(["light","dark"]),validTabs=new Set(["api","design","strategy"]);
function fitClassic(){const board=document.querySelector('.board');if(!board)return;if(document.body.dataset.layout!=="classic"){board.style.zoom="";document.body.style.minHeight="";return}const scale=Math.min(1,Math.max(.18,(window.innerWidth-8)/1792));board.style.zoom=String(scale);document.body.style.minHeight=(1380*scale+8)+"px"}
function applyLayout(layout){const value=validLayouts.has(layout)?layout:"vertical";document.documentElement.dataset.layout=value;document.body.dataset.layout=value;document.querySelectorAll("[data-layout-choice]").forEach(btn=>{const active=btn.dataset.layoutChoice===value;btn.classList.toggle("active",active);btn.setAttribute("aria-pressed",active?"true":"false")});try{localStorage.setItem(LAYOUT_KEY,value)}catch{}requestAnimationFrame(fitClassic)}
function applyTheme(theme){const value=validThemes.has(theme)?theme:"light";document.documentElement.dataset.theme=value;document.body.dataset.theme=value;document.querySelectorAll("[data-theme-choice]").forEach(btn=>{const active=btn.dataset.themeChoice===value;btn.classList.toggle("active",active);btn.setAttribute("aria-pressed",active?"true":"false")});try{localStorage.setItem(THEME_KEY,value)}catch{}const meta=document.querySelector('meta[name="theme-color"]');if(meta)meta.setAttribute("content",value==="dark"?"#0b1017":"#0b1730")}
function stored(key,fallback){try{return localStorage.getItem(key)||fallback}catch{return fallback}}
function setSettingsTab(tab){const value=validTabs.has(tab)?tab:"api";document.querySelectorAll("[data-settings-tab]").forEach(btn=>{const active=btn.dataset.settingsTab===value;btn.classList.toggle("active",active);btn.setAttribute("aria-selected",active?"true":"false")});document.querySelectorAll("[data-settings-pane]").forEach(pane=>pane.hidden=pane.dataset.settingsPane!==value);try{localStorage.setItem(SETTINGS_TAB_KEY,value)}catch{}}
function updateApiStatus(){const el=document.getElementById("settings-api-status");if(!el)return;let connected=false;try{const c=JSON.parse(localStorage.getItem(SETTINGS_CRED_KEY)||"null");connected=!!(c?.keyId&&c?.secretKey)}catch{}el.classList.toggle("connected",connected);const span=el.querySelector("span");if(span)span.textContent=connected?"API 키 저장됨":"API 연결 설정"}
applyLayout(stored(LAYOUT_KEY,"vertical"));applyTheme(stored(THEME_KEY,"light"));
document.querySelectorAll("[data-layout-choice]").forEach(btn=>btn.addEventListener("click",()=>applyLayout(btn.dataset.layoutChoice)));
document.querySelectorAll("[data-theme-choice]").forEach(btn=>btn.addEventListener("click",()=>applyTheme(btn.dataset.themeChoice)));
document.querySelectorAll("[data-settings-tab]").forEach(btn=>btn.addEventListener("click",()=>setSettingsTab(btn.dataset.settingsTab)));
const settingsModal=document.getElementById("modal"),settingsBtn=document.getElementById("settingsBtn"),settingsClose=document.getElementById("settingsCloseBtn");
setSettingsTab(stored(SETTINGS_TAB_KEY,"api"));updateApiStatus();
settingsBtn?.addEventListener("click",()=>{setSettingsTab(stored(SETTINGS_TAB_KEY,"api"));updateApiStatus()});settingsClose?.addEventListener("click",()=>settingsModal?.classList.remove("show"));
document.getElementById("saveBtn")?.addEventListener("click",()=>setTimeout(updateApiStatus,0));document.getElementById("clearBtn")?.addEventListener("click",()=>setTimeout(updateApiStatus,0));
document.addEventListener("keydown",e=>{if(e.key==="Escape"&&settingsModal?.classList.contains("show"))settingsModal.classList.remove("show")});
window.addEventListener("resize",fitClassic);window.addEventListener("orientationchange",()=>setTimeout(fitClassic,120));

function currentNYMonth(){const p=new Intl.DateTimeFormat("en-CA",{timeZone:"America/New_York",year:"numeric",month:"2-digit"}).formatToParts(new Date());return`${p.find(x=>x.type==="year").value}-${p.find(x=>x.type==="month").value}`}
function readObj(key){try{return JSON.parse(localStorage.getItem(key)||"{}")||{}}catch{return{}}}
async function forceApplyQuqu(btn,box){
  btn.disabled=true;btn.textContent="Applying…";
  try{
    const r=await fetch(`./ququ-latest.json?v=${Date.now()}`,{cache:"no-store"});
    if(!r.ok)throw new Error(`QUQU snapshot HTTP ${r.status}`);
    const j=await r.json();
    if(j.strategy!=="QUQU v2")throw new Error(`QUQU v2 snapshot expected, got ${j.strategy||"unknown"}`);
    const rows=(j.rows||[]).map(x=>({ticker:String(x.ticker||"").trim().toUpperCase(),weight:Number(x.weight)}));
    if(rows.length<100||rows.some(x=>!x.ticker||!Number.isFinite(x.weight)||x.weight<=0))throw new Error("QUQU 목표비중 데이터 오류");
    const sum=rows.reduce((a,x)=>a+x.weight,0);if(Math.abs(sum-1)>1e-6)throw new Error(`QUQU 비중합 오류 ${sum}`);
    const ym=currentNYMonth(),allocation=/^\d{4}-\d{2}$/.test(String(j.allocationMonth||""))?String(j.allocationMonth):ym;
    const holdings=rows.map(x=>x.ticker),targetWeights=Object.fromEntries(rows.map(x=>[x.ticker,x.weight]));
    const sig="ququ-nasdaq-composite-v2",key=kind=>`somx.${kind}.v2.${sig}`;
    const hh=readObj(key("holdings")),wh=readObj(key("weights"));
    hh[allocation]=[...holdings];wh[allocation]={...targetWeights};hh[ym]=[...holdings];wh[ym]={...targetWeights};
    const st={rebalanceMonth:ym,strategyAnchor:ym,initialized:true,holdings,statuses:Object.fromEntries(holdings.map(s=>[s,"IN"])),targetWeights,basePrices:{},updatedAt:new Date().toISOString(),snapshotAt:j.generatedAt||null,snapshotAllocationMonth:allocation};
    localStorage.setItem("somx.strategy.active.v1","ququ");
    localStorage.setItem(key("state"),JSON.stringify(st));
    localStorage.setItem(key("holdings"),JSON.stringify(hh));
    localStorage.setItem(key("weights"),JSON.stringify(wh));
    try{globalThis.SOMXStrategy?.setMode?.("ququ")}catch{}
    if(box)box.textContent=`QUQU v2 적용 완료 · ${rows.length}종목 · 다시 불러오는 중…`;
    setTimeout(()=>location.reload(),80);
  }catch(e){
    if(box)box.textContent=e?.message||String(e);
    btn.disabled=false;btn.textContent="Apply Strategy";
  }
}
const applyBtn=document.getElementById("strategy-apply-btn");
if(applyBtn){
  const originalApply=applyBtn.onclick;
  applyBtn.onclick=function(ev){
    const selected=document.querySelector('.strategy-tabs button.active')?.dataset?.mode;
    if(selected!=="ququ")return originalApply?.call(this,ev);
    const box=document.getElementById("strategy-preview");
    return forceApplyQuqu(this,box);
  };
}

if("serviceWorker"in navigator)window.addEventListener("load",async()=>{try{fitClassic();const reg=await navigator.serviceWorker.register("./sw.js?v=38",{updateViaCache:"none"});await reg.update()}catch(e){console.error(e)}});
})();