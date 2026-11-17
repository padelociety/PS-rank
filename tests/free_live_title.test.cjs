// 자유 라이브 — 유튜브 제목 한 칸 + 넣기 버튼 (c22 · 2026-11-17).
//
//     node tests/free_live_title.test.cjs
//
// 사용자: "팀 A, 팀 B 선택하는 거 빼버리고 그냥 유튜브 제목 쓴 대로 그대로 들어가게 … 날짜 넣기 버튼 … 코치 이름도
// 지금 우리 센터에 등록된 코치 이름 누르면 들어가게" → "개인 레슨, PSA 그룹 레슨 넣는 버튼 … 60분짜리도 … 날짜에 요일도 넣고 OOO 코치로".
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'ps_court', 'ps_court_playus.html'), 'utf8');
const py = fs.readFileSync(path.join(__dirname, '..', 'stream_server.py'), 'utf8');
const pick = (name) => {
  const i = src.indexOf(`function ${name}(`);
  if (i < 0) throw new Error('없다: ' + name);
  let d = 0, started = false;
  for (let j = src.indexOf('{', i); j < src.length; j++) {
    if (src[j] === '{') { d++; started = true; }
    else if (src[j] === '}') { d--; if (started && d === 0) return src.slice(i, j + 1); }
  }
  throw new Error('끝을 못 찾음: ' + name);
};
let fail = 0;
const t = (name, fn) => { try { fn(); console.log('  ✓ ' + name); } catch (e) { fail++; console.log('  ✗ ' + name + '\n    ' + e.message); } };
const eq = (a, b) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${JSON.stringify(a)} !== ${JSON.stringify(b)}`); };
const has = (s, x) => { if (!s.includes(x)) throw new Error('없음: ' + x); };

const lib = new Function(`const FL_TITLE_MAX = 100; const FL_WEEK = ['일','월','화','수','목','금','토'];
  ${pick('flToday')}; ${pick('flCleanTitle')}; ${pick('flInsertAt')};
  return { flToday, flCleanTitle, flInsertAt };`)();

t('오늘 날짜 = KST · 요일까지', () => {
  eq(lib.flToday(Date.parse('2026-11-17T05:00:00Z')), '2026.11.17 (화)');
  eq(lib.flToday(Date.parse('2026-11-17T15:30:00Z')), '2026.11.18 (수)'); // UTC 15:30 = KST 다음 날 00:30
});
t('커서 자리에 넣고 띄어쓰기를 맞춘다', () => {
  let r = lib.flInsertAt('', 0, '2026.11.17 (화)');
  eq(r, { value: '2026.11.17 (화) ', caret: 15 });
  r = lib.flInsertAt(r.value, r.caret, '김민준 코치');
  r = lib.flInsertAt(r.value, r.caret, '개인 레슨');
  eq(lib.flCleanTitle(r.value), '2026.11.17 (화) 김민준 코치 개인 레슨');
  eq(lib.flInsertAt('레슨', 0, '김민준 코치').value, '김민준 코치 레슨');
  eq(lib.flInsertAt('A B', 1, 'X').value, 'A X B');
});
t('보낼 제목 — 줄바꿈·겹친 칸·<> 정리 · 100자', () => {
  eq(lib.flCleanTitle('  PSA 그룹\n레슨  <b> '), 'PSA 그룹 레슨 b');
  eq(lib.flCleanTitle('가'.repeat(130)).length, 100);
  eq(lib.flCleanTitle('   '), '');
});
t('창: 팀 칸이 없고 제목 한 칸 · 넣기 버튼 셋 · 60분', () => {
  if (src.includes('id="fl-team-a"') || src.includes('id="fl-team-b"')) throw new Error('팀 칸이 남았다');
  has(src, 'id="fl-title" maxlength="100"');
  has(src, 'onclick="flInsert(flToday())"');
  has(src, `onclick="flInsert('개인 레슨')"`);
  has(src, `onclick="flInsert('PSA 그룹 레슨')"`);
  has(src, 'data-min="60" onclick="pickDuration(60)"');
  has(src, 'const FREE_LIVE_MINUTES = [60, 90, 120, 180];');
  has(pick('startFreeLive'), "JSON.stringify({ mode: 'free', durationMinutes: _flMinutes, title, teamA, teamB })");
});
t('코치 버튼 = 서버 /stream/coaches · "OOO 코치" · 글자는 textContent', () => {
  const f = pick('flLoadCoaches');
  has(f, 'psFetch(flCoachPath())');
  has(f, "flInsert(n + ' 코치')");
  has(f, 'btn.textContent = n');
  has(pick('flCoachPath'), "'/stream/coaches?centerId=' + encodeURIComponent(_flCenterId)");
  has(src, "_flCenterId = String(data.center_id || '');");
});
t('방송 PC: 60분 · title 받기 · /health center_id', () => {
  has(py, 'FREE_LIVE_MINUTES = (60, 90, 120, 180)');
  has(py, "free_title = clean_free_title(data.get('title')) if mode == 'free' else ''");
  has(py, 'build_free_title_and_desc(team_a, team_b, minutes, free_title)');
  has(py, "'center_id': CONTROL_CENTER_ID,");
  if (!/FREE_TITLE_MAX = 100\b/.test(py)) throw new Error('방송 PC 한도 100 이 태블릿과 다르다');
});

if (fail) { console.log(`\n${fail}개 실패`); process.exit(1); }
console.log('\n모두 통과');
