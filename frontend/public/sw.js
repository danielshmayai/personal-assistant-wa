// VERSION is stamped with the build timestamp by vite.config.ts's
// stampServiceWorkerVersion plugin on every `npm run build` — no manual
// bump needed. This placeholder is only what a raw dev-server load sees.
const VERSION = 'dev';
const CACHE = `pa-product-${VERSION}`;

const PRECACHE_URLS = [
  '/',
  '/manifest.json',
  '/icon-192.png',
  '/icon-512.png',
];

// ── Install ──────────────────────────────────────────────────────────────────
// Precache the app shell. Do NOT call skipWaiting() here — the user decides
// when to activate the new version.
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE).then(cache => cache.addAll(PRECACHE_URLS))
  );
});

// ── Activate ─────────────────────────────────────────────────────────────────
// Delete every cache whose key doesn't match the current VERSION, then claim
// all open tabs so the new SW is in control immediately after the user reloads.
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(k => k !== CACHE).map(k => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

// ── Message ───────────────────────────────────────────────────────────────────
// The app sends 'SKIP_WAITING' when the user clicks an "Update" toast button.
self.addEventListener('message', event => {
  if (event.data === 'SKIP_WAITING') self.skipWaiting();
});

// ── Push ──────────────────────────────────────────────────────────────────────
// Reserved for when push delivery lands (currently only the owner's scheduler
// can send one — see PLANNING.md P6.5). Harmless to keep ready: shows nothing
// unless a push event actually arrives.
self.addEventListener('push', event => {
  if (!event.data) return;

  let payload = { title: 'Assistant 🔔', body: '' };
  try { payload = event.data.json(); } catch {
    payload.body = event.data.text();
  }

  event.waitUntil(
    self.clients
      .matchAll({ type: 'window', includeUncontrolled: true })
      .then(clients => {
        if (clients.some(c => c.focused)) return;

        return self.registration.showNotification(payload.title, {
          body: payload.body,
          icon: '/icon-192.png',
          badge: '/icon-192.png',
          tag: 'reminder',
          renotify: true,
          vibrate: [200, 100, 200],
          data: { url: '/' },
        });
      })
  );
});

// ── Notification click ────────────────────────────────────────────────────────
self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(
    self.clients
      .matchAll({ type: 'window', includeUncontrolled: true })
      .then(clients => {
        const existing = clients.find(c => c.url.startsWith(self.location.origin));
        if (existing) return existing.focus();
        return self.clients.openWindow('/');
      })
  );
});

// ── Fetch ─────────────────────────────────────────────────────────────────────
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  // Never intercept non-GET, API calls, WebSocket upgrades, or auth redirects.
  if (request.method !== 'GET') return;
  if (url.pathname.startsWith('/api/') ||
      url.pathname.startsWith('/ws/')  ||
      url.pathname.startsWith('/auth/')) return;

  if (request.mode === 'navigate') {
    // App shell (index.html): stale-while-revalidate.
    event.respondWith(staleWhileRevalidate(request));
  } else {
    // Static assets: cache-first.
    event.respondWith(cacheFirst(request));
  }
});

// ── Strategies ───────────────────────────────────────────────────────────────

async function staleWhileRevalidate(request) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(request);

  const networkPromise = fetch(request).then(response => {
    if (response.ok) cache.put(request, response.clone());
    return response;
  }).catch(() => null);

  return cached ?? await networkPromise;
}

async function cacheFirst(request) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(request);
  if (cached) return cached;

  try {
    const response = await fetch(request);
    if (response.ok) cache.put(request, response.clone());
    return response;
  } catch {
    return new Response('', { status: 408 });
  }
}
