/* Klipometr: działa jak aplikacja i pokazuje ostatnie dane bez internetu.

   - strona i ikony: z sieci, a gdy jej nie ma, z pamięci telefonu,
   - dane (wyniki bota, lista filmów, kurs NBP): zawsze najpierw świeże z sieci, bez sieci ostatnie zapisane,
   - okładki klipów i czcionka: z pamięci, jeśli już są (rzadko się zmieniają).
   Po zmianie tego pliku podbij VERSION, żeby telefony wzięły nową wersję. */
const VERSION = "klipometr-v1";
const SHELL = ["./", "index.html", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png", "icons/maskable-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// klucz bez "?t=..." (strona dokleja go, żeby ominąć pamięć przeglądarki)
const keyOf = (req) => {
  const u = new URL(req.url);
  u.searchParams.delete("t");
  u.searchParams.delete("x");
  return u.toString();
};

async function networkFirst(req) {
  const cache = await caches.open(VERSION);
  try {
    const res = await fetch(req);
    if (res.ok || res.type === "opaque") cache.put(keyOf(req), res.clone());
    return res;
  } catch (err) {
    const hit = await cache.match(keyOf(req));
    if (hit) return hit;
    if (req.mode === "navigate") {
      const page = await cache.match(new URL("index.html", self.registration.scope).toString());
      if (page) return page;
    }
    throw err;
  }
}

async function cacheFirst(req) {
  const cache = await caches.open(VERSION);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok || res.type === "opaque") cache.put(req, res.clone());
  return res;
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.hostname.endsWith("tiktokcdn.com") || url.pathname.includes("/assets/covers/") ||
      url.hostname === "fonts.gstatic.com" || url.hostname === "fonts.googleapis.com") {
    e.respondWith(cacheFirst(req));
    return;
  }
  e.respondWith(networkFirst(req));
});
