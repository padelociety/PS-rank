// 방송 이름 — 회원이 고른 이름(실명/닉네임)으로 바깥에 나간다 (c15 · 2026-10-10).
//   실행: node tests/on_air_name.test.cjs
//
// 사용자: "리그 썸네일에 이름 나오자나. 사람들마다 이름 노출하는거 꺼려할수도 있어서. 닉네임으로 선택하면
//          닉네임이 보여지게 할 수 있어?"
// 서버가 리그 매치 응답의 사람마다 `onAir: { name, showPhoto, mode }` 를 붙이고(backend utils/onAirName),
// 태블릿은 그걸로 **바깥으로 나가는 곳**을 채운다:
//   ① psAirName / psAirPhoto — onAir 가 있으면 그것, 없으면(옛 서버) 예전 실명 규칙 그대로
//   ② applyTeams 가 화면 배열(teamA — 실명)과 방송 배열(teamAAir)을 같이 만든다 · 팀을 정하는 자리는 전부 같이 비우고 채운다
//   ③ 바깥으로 나가는 곳은 전부 방송 배열 — Firebase(TV·OBS) · 앱 라이브 카드 · /start-stream(유튜브 제목·설명·썸네일·하이라이트)
//   ④ '다른 페어로 다시하기' 는 applyTeams 를 부른다(손으로 채우면 방송 배열이 앞 경기 것으로 남는다)
//   ⑤ 화면은 실명 그대로 + 다르면 작게 '📺 닉네임'(닉네임은 HTML 로 풀지 않는다)
//   ⑥ 빌드 배지 셋(COURTBUILD · 앱 표시 · BUILD)과 SW CACHE 를 같이 올렸다
//
// ⚠️ 소스를 그대로 떼어 와 돌린다(테스트에 로직을 베끼지 않는다).
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'ps_court', 'ps_court_playus.html'), 'utf8');
const sw = fs.readFileSync(path.join(__dirname, '..', 'ps_court', 'ps_court_sw.js'), 'utf8');

let fails = 0;
const t = async (name, fn) => { try { await fn(); console.log('  ok   ' + name); } catch (e) { fails++; console.log('  FAIL ' + name + ' — ' + e.message); } };
const assert = (c, m) => { if (!c) throw new Error(m); };
const eq = (a, b, m) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${m}: ${JSON.stringify(a)} ≠ ${JSON.stringify(b)}`); };
const body = (name) => {
  const i = src.search(new RegExp('(async )?function ' + name + '\\('));
  if (i < 0) throw new Error(name + ' 없음');
  let j = src.indexOf('{', i), d = 0;
  for (; j < src.length; j++) { if (src[j] === '{') d++; else if (src[j] === '}' && --d === 0) return src.slice(i, j + 1); }
  throw new Error(name + ' 끝 없음');
};

const NAME_FNS = ['psName', 'psAirName', 'psPhoto', 'psAirPhoto', 'psId', 'psEsc', 'airHint', 'airTeamHint', 'applyTeams'];

// 태블릿 한 대 — 이름·팀·송출·화면 함수들을 진짜로 돌린다. 화면 대신 기록.
function tablet(over = {}) {
  const ctx = Object.assign({ fb: [], posts: [], els: {} }, over);
  // eslint-disable-next-line no-new-func
  const run = new Function('ctx', `
    const document = { getElementById: (id) => (ctx.els[id] = ctx.els[id] || { innerHTML: '' }) };
    const window = { syncToFirebase: (o) => ctx.fb.push(o) };
    let selectedMatch = { _id: 'M1' }, selectedLeague = { _id: 'L1', name: 'PS iLeague 26S3' }, psToken = 'tok';
    let teamA = [], teamB = [], teamAAir = [], teamBAir = [], teamAIds = [], teamBIds = [], teamAPar = [], teamBPar = [];
    let teamAPhotos = [], teamBPhotos = [];
    let sets = [{ a: 3, b: 2 }], curSet = 0, matchNum = 39;
    const psFetch = async (u, o) => { ctx.posts.push([u, JSON.parse(o.body)]); return {}; };
    function parTag() { return ''; }
    function renderPairInfo() {}
    ${NAME_FNS.map(body).join('\n')}
    ${body('pushToFirebase')}
    ${body('broadcastLiveToApp')}
    ${body('renderTeamDisplay')}
    ${body('renderScoreBoard')}
    return {
      psName, psAirName, psPhoto, psAirPhoto, applyTeams, pushToFirebase, renderTeamDisplay, renderScoreBoard,
      get: () => ({ teamA, teamB, teamAAir, teamBAir, teamAIds, teamBIds, teamAPhotos, teamBPhotos }),
      set: (o) => { if ('teamA' in o) teamA = o.teamA; if ('teamB' in o) teamB = o.teamB; if ('teamAAir' in o) teamAAir = o.teamAAir; if ('teamBAir' in o) teamBAir = o.teamBAir; },
    };
  `);
  return Object.assign(run(ctx), { ctx });
}

// 서버 응답 모양 그대로의 사람들
const NICK = { _id: 'u1', lastNameKorean: '임', firstNameKorean: '우재', profile: 'https://cdn/p1.jpg',
  onAir: { name: '우재짱', showPhoto: false, mode: 'nickname' } };
const REAL = { _id: 'u2', lastNameKorean: '조', firstNameKorean: '준희', profile: 'https://cdn/p2.jpg',
  onAir: { name: '조준희', showPhoto: true, mode: 'real' } };
const OLD = { _id: 'u3', lastNameKorean: '이', firstNameKorean: '준우', profile: 'https://cdn/p3.jpg' };   // 옛 서버 — onAir 없음
const EMPTY_NICK = { _id: 'u4', lastNameKorean: '설', firstNameKorean: '정수', profile: 'https://cdn/p4.jpg',
  onAir: { name: '선수', showPhoto: false, mode: 'nickname' } };                                          // 닉네임 모드인데 닉네임이 비었다

(async () => {
  await t('① psAirName — onAir 이름이 먼저, 없으면 예전 실명 규칙(psName) 그대로', () => {
    const tab = tablet();
    assert(tab.psAirName(NICK) === '우재짱', '닉네임: ' + tab.psAirName(NICK));
    assert(tab.psAirName(REAL) === '조준희', '실명 모드: ' + tab.psAirName(REAL));
    assert(tab.psAirName(OLD) === tab.psName(OLD) && tab.psName(OLD) === '이준우', 'onAir 없음 = psName: ' + tab.psAirName(OLD));
    assert(tab.psAirName({ firstName: 'Ana', lastName: 'Kim' }) === 'Ana Kim', '영문 이름(옛 서버)');
    assert(tab.psAirName({ onAir: { name: '  공백닉  ' } }) === '공백닉', '공백을 턴다');
    assert(tab.psAirName({ lastNameKorean: '김', firstNameKorean: '하경', onAir: { name: '' } }) === '김하경', '빈 onAir 이름 → psName');
    assert(tab.psAirName({ lastNameKorean: '김', firstNameKorean: '하경', onAir: null }) === '김하경', 'onAir null → psName');
    assert(tab.psAirName('김하경') === '김하경' && tab.psAirName(null) === '', '문자열·null 은 psName 그대로');
  });

  await t("① 닉네임 모드인데 닉네임이 비면 서버가 준 '선수' — 실명으로 되돌아가지 않는다", () => {
    const tab = tablet();
    assert(tab.psAirName(EMPTY_NICK) === '선수', '실명이 나갔다: ' + tab.psAirName(EMPTY_NICK));
  });

  await t('① psAirPhoto — showPhoto:false 면 사진 없음(이니셜), 아니면 예전 psPhoto 그대로', () => {
    const tab = tablet();
    assert(tab.psAirPhoto(NICK) === '', '닉네임인데 사진이 나간다');
    assert(tab.psAirPhoto(EMPTY_NICK) === '', '닉네임(빈 닉) 인데 사진이 나간다');
    assert(tab.psAirPhoto(REAL) === 'https://cdn/p2.jpg', '실명 모드 사진');
    assert(tab.psAirPhoto(OLD) === tab.psPhoto(OLD) && tab.psPhoto(OLD) === 'https://cdn/p3.jpg', 'onAir 없음 = psPhoto');
    assert(tab.psAirPhoto({ profile: 'p.jpg', onAir: { showPhoto: true } }) === '', '파일 이름만 오면 예전처럼 사진 없음');
    assert(tab.psAirPhoto(null) === '' && tab.psAirPhoto('김하경') === '', 'null·문자열');
  });

  await t('② applyTeams — 화면 배열은 실명, 방송 배열은 onAir, 사진도 방송용', () => {
    const tab = tablet();
    tab.applyTeams([NICK, REAL], [OLD, EMPTY_NICK]);
    const s = tab.get();
    eq(s.teamA, ['임우재', '조준희'], '화면 A(실명)');
    eq(s.teamB, ['이준우', '설정수'], '화면 B(실명)');
    eq(s.teamAAir, ['우재짱', '조준희'], '방송 A');
    eq(s.teamBAir, ['이준우', '선수'], '방송 B');
    eq(s.teamAPhotos, ['', 'https://cdn/p2.jpg'], '사진 A');
    eq(s.teamBPhotos, ['https://cdn/p3.jpg', ''], '사진 B');
    eq([s.teamAIds, s.teamBIds], [['u1', 'u2'], ['u3', 'u4']], 'id');
  });

  await t('② 팀 배열을 정하거나 비우는 자리는 전부 방송 배열도 같이 — 하나라도 빠지면 앞 경기 이름이 나간다', () => {
    // 선언 줄(let/const/var — 맨 위 상태 · 자유 라이브의 지역 변수)을 뺀 대입만 센다.
    // teamA 를 고치는 자리 수 = teamAAir 를 고치는 자리 수.
    const code = src.split('\n').filter((l) => !/^\s*(let|const|var)\s/.test(l)).join('\n');
    const assigns = (name) => (code.match(new RegExp('(?<![\\w.])' + name + '\\s*=(?!=)', 'g')) || []).length;
    for (const [real, air] of [['teamA', 'teamAAir'], ['teamB', 'teamBAir']]) {
      const r = assigns(real), a = assigns(air);
      assert(r >= 4, real + ' 대입이 ' + r + '곳뿐(selectMatch·showTeamStep·applyTeams·resetAll)');
      assert(r === a, `${real} 대입 ${r}곳 · ${air} 대입 ${a}곳`);
    }
    for (const fn of ['selectMatch', 'showTeamStep', 'resetAll']) {
      assert(/teamAAir\s*=\s*\[\]/.test(body(fn)) && /teamBAir\s*=\s*\[\]/.test(body(fn)), fn + ' 가 방송 배열을 안 비운다');
    }
  });

  await t('③ Firebase(코트 TV·OBS) · 앱 라이브 카드 — 방송 이름으로 나간다', async () => {
    const tab = tablet();
    tab.applyTeams([NICK, REAL], [OLD, EMPTY_NICK]);
    tab.pushToFirebase({ fresh: true });
    await new Promise((r) => setImmediate(r));
    const fb = tab.ctx.fb[0];
    assert(fb, 'Firebase 로 안 나갔다');
    assert(fb.nameA === '우재짱 / 조준희' && fb.nameB === '이준우 / 선수', 'Firebase 이름: ' + fb.nameA + ' | ' + fb.nameB);
    const live = tab.ctx.posts.find((p) => p[0] === '/league/L1/livescore');
    assert(live, '앱 라이브로 안 나갔다');
    assert(live[1].a.name === '우재짱 / 조준희' && live[1].b.name === '이준우 / 선수', '앱 라이브 이름: ' + JSON.stringify([live[1].a.name, live[1].b.name]));
    const all = JSON.stringify([tab.ctx.fb, tab.ctx.posts]);
    assert(!all.includes('임우재') && !all.includes('설정수'), '닉네임을 고른 사람의 실명이 나갔다');
  });

  await t("③ 방송 배열이 비면 'Team A' — 화면 실명으로 채우지 않는다", () => {
    const tab = tablet();
    tab.set({ teamA: ['임우재', '조준희'], teamB: ['이준우', '설정수'], teamAAir: [], teamBAir: [] });
    tab.pushToFirebase();
    const fb = tab.ctx.fb[0];
    assert(fb.nameA === 'Team A' && fb.nameB === 'Team B', '실명으로 채웠다: ' + fb.nameA + ' | ' + fb.nameB);
  });

  await t('③ 송출 코드는 방송 배열만 쓴다 — Firebase · 앱 라이브 · /start-stream(제목·설명·썸네일·하이라이트)', () => {
    const fb = body('pushToFirebase');
    assert(fb.includes("nameA: teamAAir.join(' / ')") && fb.includes("nameB: teamBAir.join(' / ')"), 'pushToFirebase');
    assert(!/\bteam[AB]\.join/.test(fb), 'pushToFirebase 에 실명 배열이 남았다');
    const app = body('broadcastLiveToApp');
    assert(app.includes("name: teamAAir.join(' / ')") && app.includes("name: teamBAir.join(' / ')"), 'broadcastLiveToApp');
    assert(!/\bteam[AB]\.join/.test(app), 'broadcastLiveToApp 에 실명 배열이 남았다');
    const st = body('_startStreamOnce');
    assert(st.includes('teamA: teamAAir, teamB: teamBAir'), '/start-stream 이름 배열');
    assert(st.includes('rosterOf(teamAAir, teamAPhotos, teamAPar)') && st.includes('rosterOf(teamBAir, teamBPhotos, teamBPar)'), '/start-stream 썸네일 상세');
    assert(!/rosterOf\(team[AB],/.test(st) && !/\bteamA, teamB\b/.test(st), '/start-stream 에 실명 배열이 남았다');
  });

  await t("④ '다른 페어로 다시하기' 는 applyTeams 를 부른다 — 손으로 채우지 않는다", () => {
    const b = body('replayDifferentPair');
    const i = b.indexOf('selectedMatch = m;');
    assert(i > 0, '새 매치로 바꾸는 줄이 없다');
    assert(b.indexOf('applyTeams(m.teamA, m.teamB);') > i, '새 매치로 바꾼 뒤 applyTeams 가 없다');
    assert(!/\bteam(A|B|AIds|BIds|APar|BPar|APhotos|BPhotos)\s*=/.test(b), '팀 배열을 손으로 채운다');
  });

  await t('⑤ 화면은 실명 — 방송 이름이 다르면 그 아래 작게 📺, 같으면 아무것도', () => {
    const tab = tablet();
    tab.applyTeams([NICK, REAL], [OLD, EMPTY_NICK]);
    tab.renderTeamDisplay(true);
    const html = tab.ctx.els['team-display'].innerHTML;
    assert(html.includes('임우재') && html.includes('조준희') && html.includes('이준우') && html.includes('설정수'), '화면에 실명이 없다');
    assert(html.includes('📺 우재짱') && html.includes('📺 선수'), '방송 이름 안내가 없다');
    assert(!html.includes('📺 조준희') && !html.includes('📺 이준우'), '같은 이름에도 안내를 붙였다');
    assert((html.match(/class="air-hint"/g) || []).length === 2, '안내 수: ' + (html.match(/class="air-hint"/g) || []).length);
  });

  await t('⑤ 점수 화면 — 이름은 실명, 팀 중 누구든 다르면 TV 에 나가는 그대로 한 줄', () => {
    const tab = tablet();
    tab.applyTeams([NICK, REAL], [OLD, { ...OLD, _id: 'u5', firstNameKorean: '민수' }]);
    tab.renderScoreBoard();
    const html = tab.ctx.els['score-wrap'].innerHTML;
    assert(html.includes('임우재<br>조준희'), '점수 화면 실명');
    assert(html.includes('📺 우재짱 / 조준희'), 'A 팀 방송 줄');
    assert((html.match(/class="air-hint"/g) || []).length === 1, 'B 팀(전부 실명)에 안내가 붙었다');
  });

  await t('⑤ 닉네임은 회원이 적은 글자 — HTML 로 풀지 않는다', () => {
    const tab = tablet();
    tab.applyTeams([{ ...NICK, onAir: { name: '<img src=x>', showPhoto: false } }, REAL], [OLD, OLD]);
    tab.renderTeamDisplay(false);
    tab.renderScoreBoard();
    const html = tab.ctx.els['team-display'].innerHTML + tab.ctx.els['score-wrap'].innerHTML;
    assert(!html.includes('<img'), '태그가 살아 있다');
    assert(html.includes('📺 &lt;img src=x&gt;'), '이스케이프한 안내가 없다');
  });

  await t('⑥ 빌드 배지 셋(COURTBUILD · 앱 표시 · BUILD)이 같고 c15 · SW CACHE v49', () => {
    const a = (/<!--COURTBUILD:([A-Za-z0-9._]+)-->/.exec(src) || [])[1];
    const b = (/>앱 ([A-Za-z0-9._]+)<\/div>/.exec(src) || [])[1];
    const c = (/var BUILD = "([A-Za-z0-9._]+)";/.exec(src) || [])[1];
    assert(a && a === b && b === c, `배지가 갈라졌다: ${a} · ${b} · ${c}`);
    assert(a === 'c15', '배지 ' + a);
    assert(sw.includes("const CACHE = 'ps-court-v49';"), 'SW CACHE');
  });

  console.log(fails ? `\n실패 ${fails}건\n` : '\n전부 통과\n');
  process.exit(fails ? 1 : 0);
})();
