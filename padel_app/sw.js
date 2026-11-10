// 2026-11-10 공개 순위 앱(padel_app)을 내렸다(대표 결정 25 · 검토 S4-01).
// 홈 화면에 설치해 둔 기기에서 옛 서비스워커가 남지 않게 — 캐시를 비우고 스스로 등록을 푼다.
// ⚠️ fetch 를 가로채지 않는다(페이지는 늘 네트워크의 안내 한 장).
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.map((k) => caches.delete(k))))
      .then(() => self.registration.unregister())
      .then(() => self.clients.matchAll({ type: 'window' }))
      .then((cs) => Promise.all(cs.map((c) => c.navigate(c.url).catch(() => null))))
      .catch(() => null)
  );
});
