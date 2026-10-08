// PAR 이 안 움직인 이유를 태블릿이 말한다 (c9 · 2026-10-01).
//   실행: node tests/par_skip_note.test.cjs
//
// 사용자: "PAR 반영경기 했는데 실제로 PAR는 반영이 안된거같아". 서버는 저장 응답에
// '(혼성 매치 — PAR 반영 안 됨)' 같은 이유를 주는데 태블릿이 버리고 '저장 완료' 만 보여 줬다.
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'ps_court', 'ps_court_playus.html'), 'utf8');

let fails = 0;
const t = (name, fn) => { try { fn(); console.log('  ok   ' + name); } catch (e) { fails++; console.log('  FAIL ' + name + ' — ' + e.message); } };
const eq = (a, b) => { if (a !== b) throw new Error(JSON.stringify(a) + ' !== ' + JSON.stringify(b)); };
const fnSrc = (name) => {
  const i = src.indexOf('function ' + name + '(');
  let j = src.indexOf('{', i), d = 0;
  for (; j < src.length; j++) { if (src[j] === '{') d++; else if (src[j] === '}' && --d === 0) return src.slice(i, j + 1); }
  throw new Error(name + ' 없음');
};
// eslint-disable-next-line no-new-func
const parSkipNote = new Function(fnSrc('parSkipNote') + '; return parSkipNote;')();

t('서버 문장 그대로 — 혼성·브릿지·친선·일시정지·대상 아님', () => {
  eq(parSkipNote('경기 결과 저장됨 (혼성 매치 — PAR 반영 안 됨)'), '혼성 매치');
  eq(parSkipNote('경기 결과 저장됨 (브릿지 매치 — PAR 반영 안 됨)'), '브릿지 매치');
  eq(parSkipNote('경기 결과 저장됨 (친선 매치 — PAR 반영 안 됨)'), '친선 매치');
  eq(parSkipNote('경기 결과 저장됨 (PAR 일시정지 중 — 점수 미반영)'), 'PAR 일시정지 중 — 점수 미반영');
  eq(parSkipNote('경기 결과 저장됨 (PAR 반영 대상 아님)'), 'PAR 반영 대상 아님');
});
t('반영됐으면 아무 말 없다', () => {
  eq(parSkipNote('경기 결과 저장됨 (PAR 반영 완료)'), '');
  eq(parSkipNote(''), '');
  eq(parSkipNote(undefined), '');
});
t('서버 문장이 태블릿 패턴과 맞다 — 백엔드 소스 대조(모노repo 에서만)', () => {
  const be = path.join(__dirname, '..', '..', 'backend', 'services', 'leagueService.js');
  if (!fs.existsSync(be)) return; // PS-rank 단독 저장소에선 건너뛴다
  const s = fs.readFileSync(be, 'utf8');
  const msgs = [...s.matchAll(/'경기 결과 저장됨 \(([^)]*)\)'/g)].map((m) => `경기 결과 저장됨 (${m[1]})`);
  if (msgs.length < 5) throw new Error('서버 문장을 못 찾음: ' + msgs.length);
  for (const m of msgs) {
    const note = parSkipNote(m);
    if (/완료/.test(m) ? note !== '' : note === '') throw new Error('패턴 어긋남: ' + m);
  }
});
t('저장·토글 둘 다 이유를 보여 준다', () => {
  if (!src.includes('const parNote = parSkipNote(saved && saved.message);')) throw new Error('저장 뒤 이유');
  if (!src.includes('const warnMsg = res && res.data && res.data.parWarning;')) throw new Error('토글 경고');
});
t('빌드 c14 · SW v48', () => {
  if (!src.includes('<!--COURTBUILD:c14-->') || !src.includes('var BUILD = "c14";')) throw new Error('배지');
  const sw = fs.readFileSync(path.join(__dirname, '..', 'ps_court', 'ps_court_sw.js'), 'utf8');
  if (!sw.includes("const CACHE = 'ps-court-v48';")) throw new Error('SW');
});
console.log(fails ? `\n실패 ${fails}건\n` : '\n전부 통과\n');
process.exit(fails ? 1 : 0);
