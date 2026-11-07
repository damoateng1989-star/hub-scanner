/* NextShelf service worker: makes the app installable and quick to open.
   Pages: network first (always fresh), cached copy when offline.
   Fonts, icons, chart library: cache first.  API calls: never cached (prices must be live). */
const V = "ns-v3";
const SHELL = ["/app", "/manifest.webmanifest", "/icon-192.png", "/icon-512.png", "/favicon.svg",
               "/fonts/Inter.woff2", "/fonts/InstrumentSerifItalic.woff2", "/fonts/JetBrainsMono.woff2", "/vendor/lightweight-charts.js"];
self.addEventListener("install", (e) => { e.waitUntil(caches.open(V).then((c) => c.addAll(SHELL).catch(() => {})).then(() => self.skipWaiting())); });
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== V).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const r = e.request, u = new URL(r.url);
  if (r.method !== "GET" || u.origin !== location.origin) return;
  if (u.pathname.startsWith("/api/") || u.pathname.startsWith("/auth/") || u.pathname === "/owner") return;   // always live
  const asset = /^\/(fonts|vendor)\//.test(u.pathname) || /\.(png|svg|webmanifest)$/.test(u.pathname);
  if (asset) {
    e.respondWith(caches.match(r).then((hit) => hit || fetch(r).then((res) => { const cp = res.clone(); caches.open(V).then((c) => c.put(r, cp)); return res; })));
    return;
  }
  if (r.mode === "navigate") {
    e.respondWith(fetch(r).then((res) => { if (res.ok) { const cp = res.clone(); caches.open(V).then((c) => c.put(r, cp)); } return res; })
      .catch(() => caches.match(r).then((hit) => hit || caches.match("/app"))));
  }
});
