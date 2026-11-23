// Keeps the guide readable without a connection: the page and its own files, newest first when online.
const CACHE = 'guide-e88dcd5af1';
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(['./', 'manifest.webmanifest', 'icons/icon-192.png'])).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET' || new URL(request.url).origin !== location.origin) return;
  event.respondWith(
    fetch(request).then(answer => {
      const copy = answer.clone();
      caches.open(CACHE).then(cache => cache.put(request, copy));
      return answer;
    }).catch(() => caches.match(request).then(found => found || caches.match('./')))
  );
});
