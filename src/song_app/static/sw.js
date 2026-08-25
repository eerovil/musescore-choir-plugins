/* Choir tracks service worker.
 *
 * The app is live and file-backed: only the immutable-ish frontend shell belongs
 * in Cache Storage. Song state, API responses, WebSockets, score/PDF data, and
 * rendered media always go straight to the network so a phone can never display
 * a cached version as current.
 */
importScripts("/pwa-assets.js");

const CACHE = self.SONG_PWA.cache;
const ASSETS = self.SONG_PWA.assets;
const OFFLINE_PAGE = "/offline.html";
const GATEWAY_DOWN = new Set([502, 503, 504]);

function livePath(pathname) {
  return pathname === "/healthz"
    || pathname.startsWith("/api/")
    || pathname.startsWith("/ws/")
    || pathname.startsWith("/songs/")
    || pathname.startsWith("/media/");
}

async function cachedOffline(fallback) {
  try {
    const cache = await caches.open(CACHE);
    return (await cache.match(OFFLINE_PAGE)) || fallback || Response.error();
  } catch (_) {
    return fallback || Response.error();
  }
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => Promise.allSettled(
        ASSETS.map(async (url) => {
          const response = await fetch(url, { cache: "reload" });
          if (!response.ok) throw new Error(`${url}: ${response.status}`);
          await cache.put(url, response);
        }),
      ))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys
          .filter((key) => key.startsWith("song-static-") && key !== CACHE)
          .map((key) => caches.delete(key)),
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Navigations are always network-first and are never written to Cache Storage.
  // The cached document is only a temporary reconnecting screen.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => (
          GATEWAY_DOWN.has(response.status)
            ? cachedOffline(response)
            : response
        ))
        .catch(() => cachedOffline(null))
    );
    return;
  }

  // Do not even wrap live data in respondWith(): leaving it untouched preserves
  // streaming/range semantics and guarantees there is no stale fallback.
  if (livePath(url.pathname)) return;

  if (!ASSETS.includes(url.pathname)) return;

  // Shell assets are network-first. A normal online load updates the cache; only
  // an unreachable server falls back to the last successfully fetched generation.
  event.respondWith(
    caches.open(CACHE).then((cache) =>
      fetch(request)
        .then((response) => {
          if (response.ok) cache.put(url.pathname, response.clone());
          return response;
        })
        .catch(() => cache.match(url.pathname))
    )
  );
});
