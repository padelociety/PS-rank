const CACHE = 'ps-court-v4';
// ASSETS 안
'/PS-rank/ps_court/ps_court.html',
'/PS-rank/ps_court/ps_court_manifest.json',

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll([
      '/PS-rank/ps_court.html',
      '/PS-rank/ps_court_manifest.json',
    ]))
  );
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  // HTML 파일은 항상 네트워크 우선 → 캐시는 오프라인 폴백만
  if (e.request.url.includes('ps_court.html')) {
    e.respondWith(
      fetch(e.request)
        .then(res => {
          // 새 버전 받으면 캐시도 업데이트
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
          return res;
        })
        .catch(() => caches.match(e.request))
    );
    return;
  }
  // 나머지는 캐시 우선
  e.respondWith(
    fetch(e.request).catch(() => caches.match(e.request))
  );
});
