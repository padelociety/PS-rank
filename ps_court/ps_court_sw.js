const CACHE = 'ps-court-v20';

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll([
      '/PS-rank/ps_court/ps_court_playus.html',
      '/PS-rank/ps_court/ps_court_playus_manifest.json',
      '/PS-rank/ps_court/ps_court.html',
      '/PS-rank/ps_court/ps_court_manifest.json',
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
  const url = e.request.url;
  // 운영 도구 HTML은 네트워크 우선 + 캐시 갱신 → 항상 최신, 오프라인엔 캐시 폴백.
  if (url.includes('ps_court.html') || url.includes('ps_court_playus.html')) {
    e.respondWith(
      fetch(e.request)
        .then(res => {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
          return res;
        })
        .catch(() => caches.match(e.request))
    );
    return;
  }
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
