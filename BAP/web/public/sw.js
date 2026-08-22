// Minimal, honest service worker for BAP (Buyer App).
//
// This app is fundamentally a *live* booking system — search results,
// availability, and confirmations all require a real network round trip.
// This service worker deliberately does NOT pretend the app works fully
// offline (that would be misleading for something whose entire point is
// live availability). Its real, honest job is just two things:
//   1. Satisfy PWA install criteria (a registered service worker).
//   2. Show a real, friendly offline page instead of a broken browser
//      error if a navigation happens with no network.

const CACHE_NAME = 'bap-shell-v1';
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
  // Only intercept page navigations — API calls and assets pass straight
  // through to the network untouched, since this app's real data is always
  // live, never something safe to serve stale from a cache.
  if (event.request.mode !== 'navigate') return;

  event.respondWith(
    fetch(event.request).catch(() => caches.match(OFFLINE_URL))
  );
});
