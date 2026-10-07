// 코트 태블릿의 **PAR 반영** — 팀을 정하기 전에(STEP 2) 고르고, 팀 화면(STEP 3)에 그 값이 보인다.
//
//     node tests/par_toggle.test.cjs
//
// 사용자 결정 2026-09-19: "경쟁경기를 매치생성때 정하는게 아니라 태블릿에서 경기 시작 전
// 정하면 어떨까? 경쟁이라고 칭하지 말고 Par 반영할껀지 선택하고 랜덤 팀 선택을 하는거지."
// 사용자 2026-10-07: "par 선택하는거 팀 선택 전으로 부탁해" (c12).
//
// ⚠️ **소스를 그대로 떼어 와 돌린다.** 로직을 테스트에 베껴 두면 화면과 갈라지고,
//    그러면 통과하는 테스트가 아무것도 지켜 주지 않는다(스코어보드 듀스 테스트와 같은 방식).
//
// 여기서 못 박는 것:
//  · 값이 비어 있으면 **PAR 에 들어갈 수 있다** — 백엔드 countsForPar 와 같은 규칙
//  · 켜짐은 **태블릿에서 켠 경기(parForced)** 뿐 — 백엔드 parForcedOn 과 같은 규칙.
//    경쟁인데 고른 적이 없으면(옛 설정) 혼성·브릿지에서 빠지므로 '반영' 이라고만 그리지 않는다
//  · 점수가 하나라도 들어가면 **잠긴다** — 선수는 시작할 때의 규칙으로 뛴다
//  · 팀 화면의 줄은 팀이 없어도 그린다(이미 STEP 2 에서 정했다) — 누르면 STEP 2 로
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
eval(['parApplyOn', 'parStateOf', 'parApplyLocked', 'renderParToggle'].map(pick).join('\n'));

let fail = 0;
const ok = (c, m) => { if (!c) { console.log('  ✗ ' + m); fail++; } else console.log('  ✓ ' + m); };

console.log('값이 없으면 PAR 에 들어갈 수 있다 (옛 매치·구버전 서버 — countsForPar)');
selectedMatch = {}; ok(parApplyOn() === true, 'matchType 없음 → 반영 대상');
selectedMatch = { matchType: null }; ok(parApplyOn() === true, 'null → 반영 대상');
selectedMatch = { matchType: 'Competitive' }; ok(parApplyOn() === true, 'Competitive → 반영 대상');
selectedMatch = { matchType: 'Friendly' }; ok(parApplyOn() === false, 'Friendly → 반영 안 함');

console.log('서버가 실제로 할 일 — parForcedOn 과 같은 규칙');
ok(parStateOf({ matchType: 'Competitive', parForced: true }) === 'on', '태블릿에서 켬 → on (혼성·브릿지도 반영)');
ok(parStateOf({ matchType: 'Friendly', parForced: false }) === 'off', '친선 → off');
ok(parStateOf({ matchType: 'Friendly', parForced: true }) === 'off', '친선은 표시가 남아 있어도 off');
ok(parStateOf({ matchType: 'Competitive', parForced: false }) === 'legacy', '⚠️ 경쟁인데 고른 적 없음 → legacy (혼성·브릿지면 빠진다)');
ok(parStateOf({ matchType: null }) === 'legacy', '빈 값 → legacy');
ok(parStateOf(null) === 'legacy', '매치 없음 → legacy (켜졌다고 그리지 않는다)');

console.log('점수가 들어가면 잠긴다');
selectedMatch = { sets: [] }; ok(parApplyLocked() === false, '빈 세트 → 안 잠김');
selectedMatch = { sets: [{ a: 0, b: 0 }] }; ok(parApplyLocked() === false, '0-0 은 점수가 아니다');
selectedMatch = { sets: [{ a: 6, b: 4 }] }; ok(parApplyLocked() === true, '6-4 → 잠김');
selectedMatch = { status: 'completed' }; ok(parApplyLocked() === true, '완료 → 잠김');
selectedMatch = { parApplied: true }; ok(parApplyLocked() === true, 'PAR 반영됨 → 잠김');
selectedMatch = null; ok(parApplyLocked() === true, '매치 없음 → 잠김');

console.log('팀 화면 줄 — 팀이 없어도 그린다(STEP 2 에서 이미 정했다)');
teamA = []; teamB = []; selectedMatch = { matchType: 'Friendly', sets: [] };
renderParToggle(); ok(/par-toggle off/.test(wrap.innerHTML), '팀 없음 → 그래도 그린다');
selectedMatch = null; renderParToggle(); ok(wrap.innerHTML === '', '매치 없음 → 빈 칸');

console.log('그릴 때 — 두 가지를 같이 적는다');
teamA = ['가', '나']; teamB = ['다', '라'];
selectedMatch = { matchType: 'Competitive', parForced: true, sets: [] };
renderParToggle();
ok(/par-toggle on/.test(wrap.innerHTML), '켜짐 클래스');
ok(wrap.innerHTML.includes('onclick="goParStep()"'), '누르면 STEP 2 로 돌아가 바꾼다');
ok(wrap.innerHTML.includes('PAR') && wrap.innerHTML.includes('순위 추가점수'),
   '⚠️ PAR 과 순위 추가점수를 **같이** 적는다 (한 값이 둘을 정한다)');
ok(!wrap.innerHTML.includes('경쟁'), "⚠️ '경쟁' 이라고 쓰지 않는다 (사용자 결정)");

selectedMatch = { matchType: 'Friendly', sets: [] };
renderParToggle();
ok(/par-toggle off/.test(wrap.innerHTML), '꺼짐 클래스');
ok(wrap.innerHTML.includes('반영되지 않고'), '꺼졌을 때 결과를 말한다');

selectedMatch = { matchType: 'Competitive', parForced: false, sets: [] };
renderParToggle();
ok(/legacy/.test(wrap.innerHTML), "⚠️ 고른 적 없는 경쟁 → '반영' 이라고만 그리지 않는다");
ok(wrap.innerHTML.includes('혼성·브릿지'), '빠지는 경우를 적는다');
ok(!wrap.innerHTML.includes('경쟁'), "legacy 도 '경쟁' 이라고 쓰지 않는다");

selectedMatch = { matchType: 'Competitive', parForced: true, sets: [{ a: 6, b: 4 }] };
renderParToggle();
ok(wrap.innerHTML.includes('locked'), '잠김 클래스');
ok(!wrap.innerHTML.includes('onclick='), '⚠️ 잠기면 누를 수 없다');
ok(wrap.innerHTML.includes('점수가 들어가서'), '잠긴 이유를 적는다');
ok(/par-toggle on/.test(wrap.innerHTML), '잠겨도 서버 값(켜짐) 그대로');

console.log(fail ? `\n${fail}건 실패` : '\n전부 통과');
process.exit(fail ? 1 : 0);
