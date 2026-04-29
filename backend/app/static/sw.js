// Bump VERSION with every deployment to invalidate the old cache.
const VERSION = 'v1';
const CACHE = `pa-${VERSION}`;

const PRECACHE_URLS = [
  '/',
  '/manifest.json',
  '/static/icon-192.png',
  '/static/icon-512.png',
];

// ── Install ──────────────────────────────────────────────────────────────────
// Precache the app shell.  Do NOT call skipWaiting() here — we let the user
// decide when to activate the new version (via the "Update" toast).
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
// The app sends 'SKIP_WAITING' when the user clicks the "Update" toast button.
// This is the only place skipWaiting() is called so the user stays in control.
self.addEventListener('message', event => {
  if (event.data === 'SKIP_WAITING') self.skipWaiting();
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
    // Serve the cached version instantly, then update the cache in the background.
    // The user sees the new version on their *next* visit (or immediately after
    // clicking the "Update" toast).
    event.respondWith(staleWhileRevalidate(request));
  } else {
    // Static assets (icons, fonts from Google CDN, highlight.js…): cache-first.
    // These are versioned by filename so cache-first is safe and fast.
    event.respondWith(cacheFirst(request));
  }
});

// ── Strategies ───────────────────────────────────────────────────────────────

async function staleWhileRevalidate(request) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(request);

  // Kick off a background network fetch regardless of cache hit.
  const networkPromise = fetch(request).then(response => {
    if (response.ok) cache.put(request, response.clone());
    return response;
  }).catch(() => null);

  // Return cached immediately if available; otherwise wait for the network.
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
    // Asset not in cache and network failed — nothing we can do.
    return new Response('', { status: 408 });
  }
}
