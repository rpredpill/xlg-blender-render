const CACHE="somx-pwa-v26";
const BASE=self.registration.scope;
const SHELL=["./","./index.html","./style.css?v=26","./benchmark.css?v=26","./strategy.css?v=26","./settings.css?v=26","./strategy-core-custom.js?v=26","./app.js?v=26","./core-backfill.js?v=26","./benchmark.js?v=26","./month-live-fix.js?v=26","./pwa.js?v=26","./manifest.webmanifest?v=26","./icon-192.png?v=26","./icon-512.png?v=26"].map(p=>new URL(p,BASE).href);
self.addEventListener("install",e=>{self.skipWaiting();e.waitUntil(caches.open(CACHE).then(c=>c.addAll(SHELL)))});
self.addEventListener("activate",e=>{e.waitUntil(Promise.all([self.clients.claim(),caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k))))]))});
self.addEventListener("fetch",e=>{if(e.request.method!=="GET")return;const u=new URL(e.request.url);if(u.origin!==location.origin)return;e.respondWith(fetch(e.request,{cache:"no-store"}).then(r=>{const clone=r.clone();caches.open(CACHE).then(c=>c.put(e.request,clone));return r}).catch(()=>caches.match(e.request).then(r=>r||caches.match(new URL("./",BASE).href))))});
