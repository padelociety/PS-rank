const CACHE_NAME = 'ps-league-v3';
const ASSETS = [
  '/PS-rank/padel_app/padel_app.html',
  '/PS-rank/padel_app/manifest.json',
  '/PS-rank/padel_app/icon-192.png',
  '/PS-rank/padel_app/icon-512.png',
];

self.addEventListener('install', e => {
  self.skipWaiting();
  e.waitUntil(
    caches.open(CACHE_NAME).then(c => c.addAll(ASSETS).catch(() => {}))
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  if (e.request.url.includes('googleapis.com') ||
      e.request.url.includes('spreadsheets') ||
      e.request.url.includes('corsproxy') ||
      e.request.url.includes('codetabs') ||
      e.request.url.includes('allorigins')) {
    return;
  }
  e.respondWith(
    fetch(e.request).then(res => {
      const clone = res.clone();
      caches.open(CACHE_NAME).then(c => c.put(e.request, clone));
      return res;
    }).catch(() => caches.match(e.request))
  );
});
