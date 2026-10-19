/* BizViz service worker: app shell only. It never touches /api/*. */
const VERSION = new URL(self.location.href).searchParams.get('v') || 'dev';
// Replaced at build time so a changed bundle also changes this file's bytes.
const BUILD_ID = '__BUILD_ID__';
const PREFIX = 'bizviz-shell-';
const CACHE = `${PREFIX}${VERSION}-${BUILD_ID}`;
const SHELL = [
  '/',
  '/index.html',
  '/manifest.webmanifest',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/apple-touch-icon.png',
];
const PASS_THROUGH = ['/sw.js', '/version.json', '/asset-manifest.json'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      let assets = [];
      try {
        const response = await fetch('/asset-manifest.json', { cache: 'no-store' });
        if (response.ok) assets = (await response.json()).assets || [];
      } catch (error) {
        assets = [];
      }
      const cache = await caches.open(CACHE);
      await cache.addAll([...SHELL, ...assets]);
    })(),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(names.filter((n) => n.startsWith(PREFIX) && n !== CACHE).map((n) => caches.delete(n)));
      await self.clients.claim();
    })(),
  );
});

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') self.skipWaiting();
});

async function fromCache(request, cacheKey) {
  const cache = await caches.open(CACHE);
  return cache.match(cacheKey || request);
}

async function cacheFirst(request) {
  const cached = await fromCache(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok && response.type === 'basic') {
    const copy = response.clone();
    caches.open(CACHE).then((cache) => cache.put(request, copy));
  }
  return response;
}

async function navigation(request) {
  const cached = (await fromCache(request, '/index.html')) || (await fromCache(request, '/'));
  return cached || fetch(request);
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/') || PASS_THROUGH.includes(url.pathname)) return;
  if (request.mode === 'navigate') {
    event.respondWith(navigation(request));
    return;
  }
  event.respondWith(cacheFirst(request));
});
