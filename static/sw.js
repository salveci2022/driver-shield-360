const CACHE = "driver-shield-360-v1";
const ASSETS = [
  "/motorista",
  "/termos",
  "/static/style.css",
  "/static/manifest.json",
  "/static/logo.png"
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c)=>c.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.origin !== location.origin) return;
  e.respondWith(
    caches.match(e.request).then((cached)=> cached || fetch(e.request).then((resp)=>{
      const copy = resp.clone();
      caches.open(CACHE).then((c)=>c.put(e.request, copy));
      return resp;
    }).catch(()=>cached))
  );
});