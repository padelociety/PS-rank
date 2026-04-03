const CACHE = 'ps-league-v3';
const ASSETS = ['/PS-rank/padel_app/padel_app.html', '/PS-rank/padel_app/manifest.json'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)));
  self.skipWaiting();
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
  ));
  self.clients.claim();
});
self.addEventListener('fetch', e => {
  if (e.request.url.includes('googleapis') || e.request.url.includes('corsproxy') || e.request.url.includes('allorigins')) return;
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});