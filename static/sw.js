const CACHE_NAME = "driver-shield-360-v1";
const ASSETS = [
  "/motorista",
  "/cadastro",
  "/relatorio",
  "/termos",
  "/static/style.css",
  "/static/manifest.json",
  "/static/driver_shield_360.png",
  "/static/driver_shield_360_192.png",
  "/static/driver_shield_360_512.png"
];

self.addEventListener("install", (event)=>{
  event.waitUntil((async ()=>{
    const cache = await caches.open(CACHE_NAME);
    await cache.addAll(ASSETS);
    self.skipWaiting();
  })());
});

self.addEventListener("activate", (event)=>{
  event.waitUntil((async ()=>{
    const keys = await caches.keys();
    await Promise.all(keys.map(k => (k===CACHE_NAME)?null:caches.delete(k)));
    self.clients.claim();
  })());
});

self.addEventListener("fetch", (event)=>{
  const req = event.request;
  // Network-first for API
  if(req.url.includes("/api/")){
    event.respondWith(fetch(req).catch(()=>caches.match(req)));
    return;
  }
  event.respondWith((async ()=>{
    const cached = await caches.match(req);
    if(cached) return cached;
    try{
      const fresh = await fetch(req);
      const cache = await caches.open(CACHE_NAME);
      cache.put(req, fresh.clone());
      return fresh;
    }catch(e){
      return cached || new Response("Offline", {status: 503});
    }
  })());
});
