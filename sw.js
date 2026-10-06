const CACHE="gpt-trading-pwa-v85";
const BASE=self.registration.scope;
const SHELL=["./","./index.html","./sidebar.js?v=1","./status-notice.js?v=3","./weights.html","./compare.html","./schedule.html","./settings.html","./portfolio-math.js?v=3","./portfolio-pages.js?v=5","./pulse.css?v=16","./pulse-view.js?v=14","./flow-paper-core.js?v=6","./flow-history.js?v=7","./flow-paper-visit.js?v=20","./manifest.webmanifest?v=31","./gpt-logo.svg","./gpt-icon.png"].map(p=>new URL(p,BASE).href);
self.addEventListener("install",e=>{self.skipWaiting();e.waitUntil(caches.open(CACHE).then(c=>c.addAll(SHELL)))});
self.addEventListener("activate",e=>{e.waitUntil(Promise.all([self.clients.claim(),caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k))))]))});
self.addEventListener("fetch",e=>{
 if(e.request.method!=="GET")return;const u=new URL(e.request.url);if(u.origin!==location.origin)return;
 const shell=SHELL.includes(u.href);
 e.respondWith(fetch(e.request,{cache:"no-store"}).then(async r=>{
  if(shell&&r.ok){const clone=r.clone();await caches.open(CACHE).then(c=>c.put(e.request,clone)).catch(()=>{});}return r;
 }).catch(async()=>{
  const cached=await caches.match(e.request);if(cached)return cached;
  if(e.request.mode==="navigate")return await caches.match(new URL("./",BASE).href)||Response.error();
  return Response.error();
 }));
});
