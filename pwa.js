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
if("serviceWorker"in navigator)window.addEventListener("load",async()=>{try{fitClassic();const reg=await navigator.serviceWorker.register("./sw.js?v=23",{updateViaCache:"none"});await reg.update()}catch(e){console.error(e)}});
})();