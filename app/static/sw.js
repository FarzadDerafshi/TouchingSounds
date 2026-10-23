// TouchingSounds service worker: makes the app installable and keeps the
// shell (page, manifest, icons) available offline.  API calls always go to
// the network, since they control a live speaker.
const CACHE = 'ts-shell-v1';
const SHELL = ['/', '/static/manifest.json', '/static/icons/icon-192.png', '/static/icons/icon-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// Network first, cache as fallback, for the shell only.
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;
  if (url.pathname.startsWith('/api/')) return;
  e.respondWith(
    fetch(e.request)
      .then(r => {
        if (r.ok && SHELL.includes(url.pathname)) {
          const copy = r.clone();
          caches.open(CACHE).then(c => c.put(e.request, copy));
        }
        return r;
      })
      .catch(() => caches.match(e.request))
  );
});
