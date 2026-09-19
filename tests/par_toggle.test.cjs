// 코트 태블릿의 **PAR 반영 스위치** — 경기 시작 전에 고른다.
//
//     node tests/par_toggle.test.cjs
//
// 사용자 결정 2026-09-19: "경쟁경기를 매치생성때 정하는게 아니라 태블릿에서 경기 시작 전
// 정하면 어떨까? 경쟁이라고 칭하지 말고 Par 반영할껀지 선택하고 랜덤 팀 선택을 하는거지."
//
// ⚠️ **소스를 그대로 떼어 와 돌린다.** 로직을 테스트에 베껴 두면 화면과 갈라지고,
//    그러면 통과하는 테스트가 아무것도 지켜 주지 않는다(스코어보드 듀스 테스트와 같은 방식).
//
// 여기서 못 박는 것:
//  · 값이 비어 있으면 **반영**이다 — 백엔드 countsForPar 와 같은 규칙
//  · 점수가 하나라도 들어가면 **잠긴다** — 선수는 시작할 때의 규칙으로 뛴다
//  · 팀이 없으면 안 그린다 — 코트에서 먼저 할 일은 팀 배정이다
//  · 문구에 **PAR 과 순위 추가점수를 같이** 적는다 — 서버가 값 하나로 둘을 정한다
//  · '경쟁' 이라는 말을 쓰지 않는다
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(
  path.join(__dirname, '..', 'ps_court', 'ps_court_playus.html'), 'utf8');
const pick = (name) => {
  const i = src.indexOf(`function ${name}(`);
  if (i < 0) throw new Error('없다: ' + name);
  // 중괄호 균형으로 함수 끝 찾기
  let d = 0, started = false;
  for (let j = src.indexOf('{', i); j < src.length; j++) {
    if (src[j] === '{') { d++; started = true; }
    else if (src[j] === '}') { d--; if (started && d === 0) return src.slice(i, j + 1); }
  }
  throw new Error('끝을 못 찾음: ' + name);
};

let selectedMatch = null, teamA = [], teamB = [];
const wrap = { innerHTML: '' };
const document = { getElementById: (id) => (id === 'par-toggle-wrap' ? wrap : null) };
eval(pick('parApplyOn') + '\n' + pick('parApplyLocked') + '\n' + pick('renderParToggle'));

let fail = 0;
const ok = (c, m) => { if (!c) { console.log('  ✗ ' + m); fail++; } else console.log('  ✓ ' + m); };

console.log('값이 없으면 반영이다 (옛 매치·구버전 서버)');
selectedMatch = {}; ok(parApplyOn() === true, 'matchType 없음 → 반영');
selectedMatch = { matchType: null }; ok(parApplyOn() === true, 'null → 반영');
selectedMatch = { matchType: 'Competitive' }; ok(parApplyOn() === true, 'Competitive → 반영');
selectedMatch = { matchType: 'Friendly' }; ok(parApplyOn() === false, 'Friendly → 반영 안 함');

console.log('점수가 들어가면 잠긴다');
selectedMatch = { sets: [] }; ok(parApplyLocked() === false, '빈 세트 → 안 잠김');
selectedMatch = { sets: [{ a: 0, b: 0 }] }; ok(parApplyLocked() === false, '0-0 은 점수가 아니다');
selectedMatch = { sets: [{ a: 6, b: 4 }] }; ok(parApplyLocked() === true, '6-4 → 잠김');
selectedMatch = { status: 'completed' }; ok(parApplyLocked() === true, '완료 → 잠김');
selectedMatch = { parApplied: true }; ok(parApplyLocked() === true, 'PAR 반영됨 → 잠김');

console.log('팀이 없으면 안 그린다 — 먼저 할 일은 팀 배정이다');
teamA = []; teamB = []; selectedMatch = { matchType: 'Competitive' };
renderParToggle(); ok(wrap.innerHTML === '', '팀 없음 → 빈 칸');

console.log('그릴 때 — 두 가지를 같이 적는다');
teamA = ['가', '나']; teamB = ['다', '라'];
selectedMatch = { matchType: 'Competitive', sets: [] };
renderParToggle();
ok(/par-toggle on/.test(wrap.innerHTML), '켜짐 클래스');
ok(wrap.innerHTML.includes('onclick="toggleParApply()"'), '누를 수 있다');
ok(wrap.innerHTML.includes('PAR') && wrap.innerHTML.includes('순위 추가점수'),
   '⚠️ PAR 과 순위 추가점수를 **같이** 적는다 (한 값이 둘을 정한다)');
ok(!wrap.innerHTML.includes('경쟁'), "⚠️ '경쟁' 이라고 쓰지 않는다 (사용자 결정)");

selectedMatch = { matchType: 'Friendly', sets: [] };
renderParToggle();
ok(/par-toggle off/.test(wrap.innerHTML), '꺼짐 클래스');
ok(wrap.innerHTML.includes('반영되지 않고'), '꺼졌을 때 결과를 말한다');

selectedMatch = { matchType: 'Competitive', sets: [{ a: 6, b: 4 }] };
renderParToggle();
ok(wrap.innerHTML.includes('locked'), '잠김 클래스');
ok(!wrap.innerHTML.includes('onclick='), '⚠️ 잠기면 누를 수 없다');
ok(wrap.innerHTML.includes('점수가 들어가서'), '잠긴 이유를 적는다');

console.log(fail ? `\n${fail}건 실패` : '\n전부 통과');
process.exit(fail ? 1 : 0);
