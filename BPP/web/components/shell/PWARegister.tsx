'use client';

import { useEffect } from 'react';

/** Registers the real service worker (`public/sw.js`) — the PWA install
 * criteria's own requirement. A dev-time failure (e.g. no HTTPS on a non-
 * localhost host) is logged, not surfaced to the user — this is a real
 * enhancement, never something that should block the app from working. */
export function PWARegister() {
  useEffect(() => {
    if (!('serviceWorker' in navigator)) return;
    navigator.serviceWorker.register('/sw.js').catch((err) => {
      console.error('Service worker registration failed:', err);
    });
  }, []);

  return null;
}
