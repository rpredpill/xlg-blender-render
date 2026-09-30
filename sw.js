const CACHE="somx-pwa-v9";
const BASE=self.registration.scope;
const SHELL=["./","./index.html","./style.css?v=9","./benchmark.css?v=9","./app.js?v=9","./benchmark.js?v=9","./pwa.js?v=9","./manifest.webmanifest?v=9","./icon-192.png?v=9","./icon-512.png?v=9"].map(p=>new URL(p,BASE).href);
self.addEventListener("install",e=>{self.skipWaiting();e.waitUntil(caches.open(CACHE).then(c=>c.addAll(SHELL)))});
self.addEventListener("activate",e=>{e.waitUntil(Promise.all([self.clients.claim(),caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k))))]))});
self.addEventListener("fetch",e=>{if(e.request.method!=="GET")return;const u=new URL(e.request.url);if(u.origin!==location.origin)return;e.respondWith(fetch(e.request,{cache:"no-store"}).then(r=>{const clone=r.clone();caches.open(CACHE).then(c=>c.put(e.request,clone));return r}).catch(()=>caches.match(e.request).then(r=>r||caches.match(new URL("./",BASE).href))))});