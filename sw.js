const CACHE="gpt-trading-pwa-v73";
const BASE=self.registration.scope;
const SHELL=["./","./index.html","./sidebar.js?v=1","./weights.html","./compare.html","./schedule.html","./portfolio-math.js?v=2","./portfolio-pages.js?v=2","./pulse.css?v=9","./pulse-view.js?v=8","./flow-paper-core.js?v=5","./flow-history.js?v=2","./flow-paper-visit.js?v=17","./manifest.webmanifest?v=31","./gpt-logo.svg","./gpt-icon.png"].map(p=>new URL(p,BASE).href);
self.addEventListener("install",e=>{self.skipWaiting();e.waitUntil(caches.open(CACHE).then(c=>c.addAll(SHELL)))});
self.addEventListener("activate",e=>{e.waitUntil(Promise.all([self.clients.claim(),caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k))))]))});
self.addEventListener("fetch",e=>{if(e.request.method!=="GET")return;const u=new URL(e.request.url);if(u.origin!==location.origin)return;e.respondWith(fetch(e.request,{cache:"no-store"}).then(r=>{const clone=r.clone();caches.open(CACHE).then(c=>c.put(e.request,clone));return r}).catch(()=>caches.match(e.request).then(r=>r||caches.match(new URL("./",BASE).href))))});
