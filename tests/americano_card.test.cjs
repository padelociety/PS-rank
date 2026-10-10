// 코트 태블릿의 **오늘의 아메리카노** 카드 (c6 · 2026-09-30).
//
//     node tests/americano_card.test.cjs
//
// 사용자: "리그앱에 뜨게 못하나?" → 운영 계정 로그인으로 오늘 아메리카노를 고르고 보드(/am/:id)를 연다.
// ⚠️ 소스를 떼어 와 돌린다(par_toggle 과 같은 방식). 여기서 못 박는 것:
//  · 보드 주소는 **공개 API 호스트**(obs.* 는 /api 만 프록시 — 보드 페이지가 없다)
//  · 토큰은 `#t=` 뒤에만(쿼리·경로에 두면 서버 요청 로그에 남는다)
//  · 목록은 서버 `/event/americano-today` 그대로 — 여기서 거르거나 다시 세지 않는다
//  · 빌드 배지 COURTBUILD · BUILD · SW CACHE 를 함께 올렸다
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'ps_court', 'ps_court_playus.html'), 'utf8');
const sw = fs.readFileSync(path.join(__dirname, '..', 'ps_court', 'ps_court_sw.js'), 'utf8');
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
const eq = (a, b) => { if (a !== b) throw new Error(`${JSON.stringify(a)} !== ${JSON.stringify(b)}`); };
const has = (s, x) => { if (!s.includes(x)) throw new Error('없음: ' + x); };

const originOf = (api) => new Function('PS_API', 'location', `${pick('boardOrigin')}; return boardOrigin();`)(api, { href: 'https://obs.padelsociety.co.kr/' });

t('보드 주소 — api·staging 은 그 호스트, obs 프록시·이상한 값은 공개 API', () => {
  eq(originOf('https://api.padelsociety.co.kr/api'), 'https://api.padelsociety.co.kr');
  eq(originOf('https://staging.api.padelsociety.co.kr/api'), 'https://staging.api.padelsociety.co.kr');
  eq(originOf('https://obs.padelsociety.co.kr/api'), 'https://api.padelsociety.co.kr');
  eq(originOf('https://evil.example/api'), 'https://api.padelsociety.co.kr');
});

t('토큰은 # 뒤에만 · 목록은 서버 것 그대로', () => {
  const open = pick('openAmericano');
  has(open, "'/am/' + encodeURIComponent(id) + '#t=' + encodeURIComponent(psToken)");
  if (/[?&]t=/.test(open)) throw new Error('토큰이 쿼리에 있다');
  const load = pick('loadAmericano');
  has(load, "psFetchRetry('/event/americano-today')");
  if (/\.filter\(/.test(load)) throw new Error('앱이 목록을 다시 거른다');
  has(pick('loadMatches'), 'loadAmericano();');
});

t('사용자 값은 textContent — innerHTML 에 이름을 싣지 않는다', () => {
  const r = pick('renderAmericano');
  has(r, 'textContent');
  if (r.includes('innerHTML')) throw new Error('innerHTML 을 쓴다');
});

t('빌드 배지 · BUILD · SW 같이(c15 · v49)', () => {
  has(src, '<!--COURTBUILD:c15-->');
  has(src, 'var BUILD = "c15";');
  has(sw, "const CACHE = 'ps-court-v49';");
});

console.log(fail ? `\n${fail}개 실패` : '\n전부 통과');
process.exit(fail ? 1 : 0);
