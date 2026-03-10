const CACHE = 'ps-league-v1';
const ASSETS = ['/PS-rank/padel_app.html', '/PS-rank/manifest.json'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k)))
  ));
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  // 구글 시트 fetch는 캐시 안 함 (항상 최신 데이터)
  if (e.request.url.includes('googleapis') || e.request.url.includes('corsproxy') || e.request.url.includes('allorigins')) {
    return;
  }
  e.respondWith(
    fetch(e.request).catch(() => caches.match(e.request))
  );
});
