// Minimal, honest service worker for BPP (Provider App).
//
// This app manages live inventory, orders, and real-time updates — none of
// that is safe or honest to serve from a stale cache. This service worker's
// only real jobs are:
//   1. Satisfy PWA install criteria (a registered service worker).
//   2. Show a real, friendly offline page instead of a broken browser
//      error if a navigation happens with no network.

const CACHE_NAME = 'bpp-shell-v1';
const OFFLINE_URL = '/offline.html';

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.add(OFFLINE_URL))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  // Only intercept page navigations — API/WebSocket traffic passes straight
  // through untouched; this app's real data (orders, availability) is
  // always live, never safe to serve stale from a cache.
  if (event.request.mode !== 'navigate') return;

  event.respondWith(
    fetch(event.request).catch(() => caches.match(OFFLINE_URL))
  );
});
