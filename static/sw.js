const CACHE_NAME = "driver-shield-360-v1";
const ASSETS = [
  "/motorista",
  "/static/style.css",
  "/static/manifest.json",
  "/static/driver_shield_360.png",
  "/static/driver_shield_360_192.png",
  "/static/driver_shield_360_1024.png"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  event.respondWith(
    caches.match(req).then((cached) => cached || fetch(req).then((resp) => {
      const copy = resp.clone();
      if (req.method === "GET" && resp.status === 200 && resp.type === "basic") {
        caches.open(CACHE_NAME).then((cache) => cache.put(req, copy));
      }
      return resp;
    }).catch(() => cached))
  );
});
