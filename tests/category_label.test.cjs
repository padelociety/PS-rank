// 라이브 카테고리 표기 (c7 · 2026-10-01) — 썸네일·제목·설명이 전부 이 값 하나를 쓴다.
//
//     node tests/category_label.test.cjs
//
// 신고: 26S3 'Gold+' 경기가 썸네일·제목에 [G&P] 로 나갔다("카테고리 이상함"). 'Gold' 가 들어가면 무조건 G&P 로
// 줄이던 26S2 규칙 때문. 이제 부문 이름 그대로 · 줄임은 이름에 '&' 가 든 옛 부문만 · 여러 부문이면 Bridge.
// ⚠️ 소스에서 catLabel 을 떼어 와 돌린다(규칙을 테스트에 베끼지 않는다).
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'ps_court', 'ps_court_playus.html'), 'utf8');
const ss = fs.readFileSync(path.join(__dirname, '..', 'stream_server.py'), 'utf8');
const mk = fs.readFileSync(path.join(__dirname, '..', 'make_thumbnail.py'), 'utf8');
let fail = 0;
const t = (name, fn) => { try { fn(); console.log('  ok  ', name); } catch (e) { fail++; console.log('  FAIL', name, '—', e.message); } };
const eq = (a, b) => { if (a !== b) throw new Error(`${JSON.stringify(a)} !== ${JSON.stringify(b)}`); };

const i = src.indexOf('const catLabel = (c) => {');
const j = src.indexOf('};', i) + 2;
const catLabel = new Function(src.slice(i, j) + '\nreturn catLabel;')();

t('26S3 부문 이름은 그대로', () => { eq(catLabel('Gold+'), 'Gold+'); eq(catLabel('Silver'), 'Silver'); eq(catLabel('Bronze'), 'Bronze'); });
t("옛 부문('&')만 줄인다", () => { eq(catLabel('Gold&Platinum'), 'G&P'); eq(catLabel('Bronze & Silver'), 'B&S'); });
t('빈 값은 빈 값(호출부가 Bridge)', () => { eq(catLabel(''), ''); });
t("⚠️ 'Gold' 만 보고 G&P 로 줄이는 옛 규칙이 남아 있지 않다", () => {
  if (/\? 'B&S'\s*:\s*\(c\.includes\('Gold'\)/.test(src)) throw new Error('옛 규칙');
});
t('make_thumbnail.py 도 같은 규칙', () => { if (!mk.includes("if '&' not in v:\n        return v")) throw new Error('규칙 다름'); });
t("make_thumbnail --video: 'live' 는 stream_server /status · 못 알아본 값은 올리기 전에 멈춘다", () => {
  if (!mk.includes("if s.lower() == 'live':") || !mk.includes("127.0.0.1:5000/status")) throw new Error('live');
  if (!mk.includes('if not vid:')) throw new Error('검증');
});
t('설명 해시태그에 #파델', () => { eq((ss.match(/#빠델 #파델 /g) || []).length, 2); });
t('빌드 배지 c12 · BUILD · SW v46 같이', () => {
  if (!src.includes('<!--COURTBUILD:c12-->') || !src.includes('var BUILD = "c12";')) throw new Error('배지');
  const sw = fs.readFileSync(path.join(__dirname, '..', 'ps_court', 'ps_court_sw.js'), 'utf8');
  if (!sw.includes("const CACHE = 'ps-court-v46';")) throw new Error('SW');
});
console.log(fail ? `\n${fail}개 실패` : '\n전부 통과');
process.exit(fail ? 1 : 0);
