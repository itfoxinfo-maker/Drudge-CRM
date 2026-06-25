/* PestCare CRM service worker — offline app shell for field agents.
 * Strategy:
 *  - /api/* and non-GET: always go to network (the page's offline queue
 *    handles mutations; we never cache API responses).
 *  - same-origin GET (app shell + assets): stale-while-revalidate.
 *  - navigations: fall back to the cached shell when offline.
 */
const VERSION = "pestcare-v6";
const SHELL = [
  "/",
  "/index.html",
  "/css/styles.css",
  "/js/i18n.js",
  "/js/offline.js",
  "/js/api.js",
  "/js/app.js",
  "/foxsyslogo.png",
  "/manifest.webmanifest",
  "/icons/icon-192.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);

  // Never intercept API traffic or cross-origin or non-GET requests.
  if (req.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) {
    return; // default browser handling
  }

  // Navigations: try network, fall back to cached shell when offline.
  if (req.mode === "navigate") {
    e.respondWith(
      fetch(req).catch(() => caches.match("/index.html").then((r) => r || caches.match("/")))
    );
    return;
  }

  // Static assets: stale-while-revalidate.
  e.respondWith(
    caches.open(VERSION).then(async (cache) => {
      const cached = await cache.match(req);
      const network = fetch(req)
        .then((res) => {
          if (res && res.status === 200 && res.type === "basic") cache.put(req, res.clone());
          return res;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
});
