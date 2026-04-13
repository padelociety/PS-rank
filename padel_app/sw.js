const CACHE_NAME = 'ps-league-v4';
const ASSETS = [
  '/PS-rank/padel_app/padel_app.html',
  '/PS-rank/padel_app/manifest.json',
  '/PS-rank/padel_app/icon-192.png',
  '/PS-rank/padel_app/icon-512.png',
];

self.addEventListener('install', e => {
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

// 페이지에서 SKIP_WAITING 메시지 받으면 즉시 활성화
self.addEventListener('message', e => {
  if (e.data?.type === 'SKIP_WAITING') self.skipWaiting();
});

self.addEventListener('fetch', e => {
  if (e.request.url.includes('googleapis.com') ||
      e.request.url.includes('spreadsheets') ||
      e.request.url.includes('corsproxy') ||
      e.request.url.includes('codetabs') ||
      e.request.url.includes('allorigins') ||
      e.request.url.includes('thingproxy')) {
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
