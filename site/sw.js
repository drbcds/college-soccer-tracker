/* Service worker: makes the site installable and usable offline.
   App files: served from cache, refreshed in the background.
   Data (scores): always fetched from the network first, cache used only when offline. */
const VERSION = "v1";
const SHELL = `shell-${VERSION}`;
const DATA = `data-${VERSION}`;
const SHELL_FILES = ["./", "index.html", "app.js", "style.css", "manifest.webmanifest", "icons/icon-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== SHELL && k !== DATA).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

const isData = (url) => url.pathname.includes("/data/") || url.hostname === "raw.githubusercontent.com";

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);

  if (isData(url)) {
    // Network first; strip the cache-busting query so offline lookups still match.
    const key = url.origin + url.pathname;
    e.respondWith(
      fetch(req).then((res) => {
        if (res.ok) caches.open(DATA).then((c) => c.put(key, res.clone()));
        return res;
      }).catch(() => caches.match(key).then((hit) => hit || Response.error()))
    );
    return;
  }

  if (url.origin !== location.origin) return;  // fonts etc.: let the browser handle them

  // App shell: page navigations always try the network first so a new release shows up right away.
  if (req.mode === "navigate") {
    e.respondWith(
      fetch(req).then((res) => {
        caches.open(SHELL).then((c) => c.put("index.html", res.clone()));
        return res;
      }).catch(() => caches.match("index.html"))
    );
    return;
  }
  e.respondWith(
    caches.match(req, { ignoreSearch: true }).then((hit) => {
      const net = fetch(req).then((res) => {
        if (res.ok) caches.open(SHELL).then((c) => c.put(req, res.clone()));
        return res;
      }).catch(() => hit);
      return hit && !url.search ? hit : net;
    })
  );
});
