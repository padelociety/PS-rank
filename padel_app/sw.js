const CACHE_NAME = 'ps-league-v4';
const STATIC_ASSETS = [
  '/PS-rank/padel_app/manifest.json',
  '/PS-rank/padel_app/icon-192.png',
  '/PS-rank/padel_app/icon-512.png',
];
// HTML은 캐시 안 함 — 항상 네트워크에서 최신 버전 받아옴
const NEVER_CACHE = [
  'padel_app.html',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE_NAME).then(c => c.addAll(STATIC_ASSETS).catch(() => {}))
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('message', e => {
  if (e.data?.type === 'SKIP_WAITING') self.skipWaiting();
});

self.addEventListener('fetch', e => {
  const url = e.request.url;

  // 항상 네트워크 우선 (캐시 안 함)
  const neverCache =
    NEVER_CACHE.some(p => url.includes(p)) ||
    url.includes('googleapis.com') ||
    url.includes('spreadsheets') ||
    url.includes('corsproxy') ||
    url.includes('codetabs') ||
    url.includes('allorigins') ||
    url.includes('thingproxy');

  if (neverCache) {
    e.respondWith(fetch(e.request));
    return;
  }

  // 나머지 정적 파일은 캐시 우선
  e.respondWith(
    caches.match(e.request).then(cached => {
      const network = fetch(e.request).then(res => {
        caches.open(CACHE_NAME).then(c => c.put(e.request, res.clone()));
        return res;
      });
      return cached || network;
    })
  );
});
