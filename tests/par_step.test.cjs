// 등급(PAR) 반영은 **팀을 정하기 전에**(STEP 2) 고르고, 화면은 **서버 값**만 그린다 (c12 · 2026-10-07).
//   실행: node tests/par_step.test.cjs
//
// 사용자 신고 2026-10-07: "그 par 켜져있는 리그 매치 이어서 할때 Par 가 켜져있는데 반영이 안되네?"
//   + "par 선택하는거 팀 선택 전으로 부탁해".
// 운영 DB(26S3): #36(PAR 반영 · parForced) 직후 '다른 페어로 다시하기' 로 만든 #37 이 Friendly 로 저장돼
// PAR 에 안 들어갔다 — 태블릿 화면엔 '반영' 이 보였다. 화면 값이 서버 값이 아니었다(직전 매치의 DOM ·
// 목록을 불러온 순간의 사본). 그래서:
//   ① 매치를 고르면(이어치기 포함) 서버에서 다시 읽고 그 값으로 그린다
//   ② 고르면 par-apply 로 적고, **서버가 그 값을 돌려줬을 때만** 팀 화면으로 넘어간다(실패하면 이유를 적고 그대로)
//   ③ 점수 전인데 이 화면에서 정하지 않은 매치는 시작하지 않는다
//   ④ 점수가 들어간 이어치기는 고르는 단계를 건너뛰고 서버 값 그대로 잠근다
//   ⑤ 다른 페어로 다시하기 → 새 매치는 STEP 2 에서 다시 고른다(직전 값은 안내만)
//
// ⚠️ 소스를 그대로 떼어 와 돌린다(테스트에 로직을 베끼지 않는다).
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'ps_court', 'ps_court_playus.html'), 'utf8');

let fails = 0;
const t = async (name, fn) => { try { await fn(); console.log('  ok   ' + name); } catch (e) { fails++; console.log('  FAIL ' + name + ' — ' + e.message); } };
const assert = (c, m) => { if (!c) throw new Error(m); };
const body = (name) => {
  const i = src.search(new RegExp('(async )?function ' + name + '\\('));
  if (i < 0) throw new Error(name + ' 없음');
  let j = src.indexOf('{', i), d = 0;
  for (; j < src.length; j++) { if (src[j] === '{') d++; else if (src[j] === '}' && --d === 0) return src.slice(i, j + 1); }
  throw new Error(name + ' 끝 없음');
};

const FNS = ['selectMatch', 'fetchFreshMatch', 'syncMatchRow', 'showTeamStep', 'parApplyOn', 'parStateOf',
  'parApplyLocked', 'renderParToggle', 'goParStep', 'renderParStep', 'chooseParApply', 'replayDifferentPair',
  'goToScore', 'goBack', 'applyTeams', 'psPhoto'];

// 가짜 서버 — 매치 저장소 하나. 태블릿이 부르는 주소만.
function makeServer(matches) {
  const db = new Map(matches.map((m) => [String(m._id), JSON.parse(JSON.stringify(m))]));
  const clone = (m) => JSON.parse(JSON.stringify(m));
  const calls = [];
  let nextId = 100;
  const srv = {
    db, calls,
    rejectParApply: null,     // 문자열이면 그 메시지로 400
    echoWrong: false,         // true 면 par-apply 가 바꾸지 않은 값을 돌려준다(옛 서버 흉내)
    parWarning: '',
    list: async () => ({ data: [...db.values()].map(clone) }),
    fetch: async (url, o = {}) => {
      calls.push([o.method || 'GET', url, o.body || '']);
      let m;
      if ((m = /\/league\/(\w+)\/matches\/(\w+)\/par-apply$/.exec(url))) {
        if (srv.rejectParApply) { const e = new Error(srv.rejectParApply); e.status = 400; throw e; }
        const row = db.get(m[2]);
        const apply = JSON.parse(o.body).apply !== false;
        if (!srv.echoWrong) { row.matchType = apply ? 'Competitive' : 'Friendly'; row.parForced = apply; }
        return { data: { ...clone(row), parWarning: apply ? srv.parWarning : '' } };
      }
      if (/\/matches\/from-players$/.test(url)) {
        const b = JSON.parse(o.body);
        const id = 'N' + (nextId++);
        const row = { _id: id, matchType: 'Friendly', parForced: false, status: 'scheduled', sets: [],
          teamA: [{ _id: b.players[0], name: 'p0' }, { _id: b.players[2], name: 'p2' }],
          teamB: [{ _id: b.players[1], name: 'p1' }, { _id: b.players[3], name: 'p3' }], players: [] };
        db.set(id, row);
        return { data: clone(row) };
      }
      if (/\/matches\/\w+$/.test(url) && o.method === 'PATCH') {
        const id = url.split('/').pop();
        Object.assign(db.get(id), JSON.parse(o.body));
        return { data: clone(db.get(id)) };
      }
      return { data: null };
    },
  };
  return srv;
}

// 태블릿 한 대 — 떼어 온 함수들 + 화면 대신 기록.
function makeTablet(srv, rows, opts = {}) {
  const els = {};
  const ctx = {
    srv, rows, toasts: [], alerts: [], steps: [], els,
    listGate: opts.listGate || null,
    document: { getElementById: (id) => (els[id] = els[id] || { innerHTML: '' }) },
  };
  // eslint-disable-next-line no-new-func
  const run = new Function('ctx', `
    const document = ctx.document;
    let curStep = 1, allMatches = ctx.rows, leagueMatchesById = {};
    let selectedMatch = null, selectedLeague = null;
    let teamA = [], teamB = [], teamAIds = [], teamBIds = [], teamAPar = [], teamBPar = [], teamAPhotos = [], teamBPhotos = [];
    let sets = [{a:0,b:0}], curSet = 0, phase = 'playing', matchNum = 0;
    ${src.match(/let parDecidedFor = null;[^\n]*/)[0]}
    ${src.match(/let parPrevState = null;[^\n]*/)[0]}
    ${src.match(/let parBusy = false;[^\n]*/)[0]}
    ${src.match(/let parError = '';[^\n]*/)[0]}
    ${src.match(/let teamsFresh = false;[^\n]*/)[0]}
    let psToken = 'tok';
    const psFetch = (u, o) => ctx.srv.fetch(u, o);
    const psFetchRetry = async (u, o) => { if (ctx.listGate) await ctx.listGate(); ctx.srv.calls.push(['GET', u, '']); return ctx.srv.list(); };
    const showToast = (m) => ctx.toasts.push(m);
    const alert = (m) => ctx.alerts.push(m);
    const setTimeout = () => 0;
    function openSettings() {}
    function renderStep() { ctx.steps.push(curStep); }
    function renderTeamDisplay(fresh) { ctx.teamFresh = fresh; }
    function renderPairInfo() {}
    function psName(u) { return (u && (u.name || u.nickName)) || '선수'; }
    function psId(u) { return String((u && u._id) || u || ''); }
    function parTag() { return ''; }
    async function refreshLeagueMatches() {}
    function renderScoreSection() {}
    async function ensureMatchNumber() { return 1; }
    function pushToFirebase() {} function startStream() {} function hideScoreboard() {}
    let _streamActive = false; var _stopping = null; function stopStream(o) { (ctx.stops = ctx.stops || []).push(o); }
    ${FNS.map(body).join('\n')}
    return {
      selectMatch, chooseParApply, replayDifferentPair, goToScore, goBack, goParStep,
      get: () => ({ curStep, selectedMatch, sets, parDecidedFor, parPrevState, parError, teamA, teamB }),
      done: (m, lg) => { selectedMatch = m; selectedLeague = lg; applyTeams(m.teamA, m.teamB); curStep = 5; },
    };
  `);
  return Object.assign(run(ctx), { ctx });
}

const LG = { _id: 'L1', name: '26S3' };
const P = (n) => ({ _id: 'u' + n, name: '선수' + n });
const base = (over) => ({ _id: 'M1', status: 'scheduled', sets: [], players: [P(1), P(2), P(3), P(4)],
  teamA: [P(1), P(2)], teamB: [P(3), P(4)], ...over });
const curBtn = (html) => (/class="par-opt (on|off) current"/.exec(html) || [])[1] || null;

(async () => {
  await t('① 이어치기 — 목록 사본이 아니라 서버 값으로 그린다(목록은 켜짐, 서버는 반영 안 함)', async () => {
    const srv = makeServer([base({ matchType: 'Friendly', parForced: false })]);
    const rows = [{ m: base({ matchType: 'Competitive', parForced: true }), lg: LG }]; // 옛 사본
    const tab = makeTablet(srv, rows);
    await tab.selectMatch(0);
    const s = tab.get();
    assert(s.curStep === 2, 'STEP 2 가 아니다: ' + s.curStep);
    assert(s.selectedMatch.matchType === 'Friendly', '서버 값으로 안 바꿨다');
    assert(curBtn(tab.ctx.els['par-step'].innerHTML) === 'off', "'지금' 이 서버 값(반영 안 함)이 아니다");
    assert(rows[0].m.matchType === 'Friendly', '목록 카드도 서버 값으로 갈아 끼운다');
    assert(srv.calls.some((c) => c[1] === '/league/L1/matches'), '서버에서 다시 안 읽었다');
  });

  await t('② 점수 전에 이 화면에서 정하지 않으면 시작하지 않는다', async () => {
    const srv = makeServer([base({ matchType: 'Competitive', parForced: true })]);
    const tab = makeTablet(srv, [{ m: base({ matchType: 'Competitive', parForced: true }), lg: LG }]);
    await tab.selectMatch(0);
    await tab.goToScore();
    assert(tab.get().curStep === 2, '정하지 않았는데 점수로 넘어갔다');
    assert(!srv.calls.some((c) => c[0] === 'PATCH'), '라이브 전환이 나갔다');
  });

  await t('③ 고르면 서버에 적고, 서버가 돌려준 값으로 팀 화면(STEP 3) — 거기서 시작', async () => {
    const srv = makeServer([base({ matchType: 'Friendly', parForced: false })]);
    const tab = makeTablet(srv, [{ m: base({ matchType: 'Friendly' }), lg: LG }]);
    await tab.selectMatch(0);
    await tab.chooseParApply(true);
    let s = tab.get();
    assert(srv.calls.some((c) => /par-apply$/.test(c[1]) && /"apply":true/.test(c[2])), 'par-apply 가 안 나갔다');
    assert(s.curStep === 3, '팀 화면으로 안 갔다: ' + s.curStep);
    assert(s.selectedMatch.parForced === true && s.selectedMatch.matchType === 'Competitive', '서버 값으로 안 갈아 끼웠다');
    assert(/par-toggle on/.test(tab.ctx.els['par-toggle-wrap'].innerHTML), '팀 화면 줄이 켜짐이 아니다');
    assert(srv.db.get('M1').parForced === true, '서버에 안 적혔다');
    await tab.goToScore();
    s = tab.get();
    assert(s.curStep === 4, '정했는데 시작이 안 된다');
    assert(srv.calls.some((c) => c[0] === 'PATCH' && /"status":"live"/.test(c[2])), '라이브 전환 없음');
  });

  await t('④ 서버가 거절하면 넘어가지 않고 서버 이유를 그대로 적는다', async () => {
    const srv = makeServer([base({ matchType: 'Friendly', parForced: false })]);
    srv.rejectParApply = '점수가 들어간 뒤에는 PAR 반영 여부를 바꿀 수 없어요. 경기 시작 전에 정해 주세요.';
    const tab = makeTablet(srv, [{ m: base({ matchType: 'Friendly' }), lg: LG }]);
    await tab.selectMatch(0);
    await tab.chooseParApply(true);
    const s = tab.get();
    assert(s.curStep === 2, '실패했는데 넘어갔다');
    assert(s.selectedMatch.matchType === 'Friendly', '실패했는데 화면 값이 바뀌었다');
    assert(tab.ctx.els['par-step'].innerHTML.includes('점수가 들어간 뒤에는'), '서버 이유가 화면에 없다');
    assert(curBtn(tab.ctx.els['par-step'].innerHTML) === 'off', "'지금' 이 그대로가 아니다");
    assert(s.parDecidedFor === null, '실패했는데 정한 것으로 쳤다');
  });

  await t('⑤ 서버가 고른 값을 확인해 주지 않으면(옛 서버) 넘어가지 않는다', async () => {
    const srv = makeServer([base({ matchType: 'Friendly', parForced: false })]);
    srv.echoWrong = true;
    const tab = makeTablet(srv, [{ m: base({ matchType: 'Friendly' }), lg: LG }]);
    await tab.selectMatch(0);
    await tab.chooseParApply(true);
    assert(tab.get().curStep === 2, '확인 안 됐는데 넘어갔다');
    assert(/확인해 주지 않았어요/.test(tab.ctx.els['par-step'].innerHTML), '이유가 없다');
  });

  await t('⑥ 켰는데 서버가 경고(일시정지)를 주면 막아 세운다(c9 parWarning)', async () => {
    const srv = makeServer([base({ matchType: 'Friendly', parForced: false })]);
    srv.parWarning = 'PAR 이 일시정지 중이라 지금은 반영되지 않아요.';
    const tab = makeTablet(srv, [{ m: base({ matchType: 'Friendly' }), lg: LG }]);
    await tab.selectMatch(0);
    await tab.chooseParApply(true);
    assert(tab.ctx.alerts.some((a) => a.includes('일시정지')), 'alert 없음');
  });

  await t('⑦ 점수가 들어간 이어치기 — 고르는 단계를 건너뛰고 서버 값 그대로 잠근다(세트 복원)', async () => {
    const live = { matchType: 'Competitive', parForced: true, status: 'live', sets: [{ a: 6, b: 4 }, { a: 2, b: 1 }] };
    const srv = makeServer([base(live)]);
    const tab = makeTablet(srv, [{ m: base({ matchType: 'Friendly', sets: [] }), lg: LG }]); // 옛 사본
    await tab.selectMatch(0);
    let s = tab.get();
    assert(s.curStep === 3, '팀 화면으로 바로 안 갔다: ' + s.curStep);
    assert(JSON.stringify(s.sets) === '[{"a":6,"b":4},{"a":2,"b":1}]', '세트 복원이 서버 값이 아니다: ' + JSON.stringify(s.sets));
    const html = tab.ctx.els['par-toggle-wrap'].innerHTML;
    assert(/par-toggle on/.test(html) && /locked/.test(html), '서버 값(켜짐)·잠김이 아니다');
    assert(!html.includes('onclick='), '잠겼는데 누를 수 있다');
    await tab.goToScore();
    s = tab.get();
    assert(s.curStep === 4, '이어치기가 시작되지 않는다');
    assert(!srv.calls.some((c) => /par-apply$/.test(c[1])), '잠긴 매치에 par-apply 를 보냈다');
  });

  await t('⑧ 다른 페어로 다시하기 — 직전 매치에서 고른 값(반영)을 서버에 적고 이어받는다', async () => {
    const srv = makeServer([base({ matchType: 'Competitive', parForced: true, status: 'completed', sets: [{ a: 6, b: 1 }, { a: 6, b: 2 }] })]);
    const tab = makeTablet(srv, []);
    tab.done(JSON.parse(JSON.stringify(srv.db.get('M1'))), LG);
    await tab.replayDifferentPair();
    const s = tab.get();
    assert(s.selectedMatch._id !== 'M1', '새 매치가 아니다');
    assert(s.curStep === 3, '이어받았는데 팀 화면으로 안 갔다: ' + s.curStep);
    assert(srv.db.get(s.selectedMatch._id).parForced === true, '서버에 안 적혔다 — 화면만 켜졌다');
    assert(srv.calls.some(c => /par-apply$/.test(c[1]) && c[2].includes('true')), 'par-apply 를 안 불렀다');
    assert(tab.ctx.teamFresh === true, "팀 배지가 '새 팀' 이 아니다");
  });

  await t('⑧-2 직전이 반영 안 함이면 반영 안 함으로 이어받는다', async () => {
    const srv = makeServer([base({ matchType: 'Friendly', parForced: false, status: 'completed', sets: [{ a: 6, b: 1 }] })]);
    const tab = makeTablet(srv, []);
    tab.done(JSON.parse(JSON.stringify(srv.db.get('M1'))), LG);
    await tab.replayDifferentPair();
    const s = tab.get();
    assert(s.curStep === 3 && srv.db.get(s.selectedMatch._id).matchType === 'Friendly', '반영 안 함으로 안 이어받았다');
  });

  await t('⑧-3 고른 적 없는 옛 값(legacy)은 이어받지 않고 STEP 2 에서 고르게 한다', async () => {
    const srv = makeServer([base({ matchType: 'Competitive', parForced: false, status: 'completed', sets: [{ a: 6, b: 1 }] })]);
    const tab = makeTablet(srv, []);
    tab.done(JSON.parse(JSON.stringify(srv.db.get('M1'))), LG);
    await tab.replayDifferentPair();
    let s = tab.get();
    assert(s.curStep === 2, 'STEP 2 로 안 갔다: ' + s.curStep);
    const html = tab.ctx.els['par-step'].innerHTML;
    assert(curBtn(html) === 'off', "새 매치의 '지금' 이 반영 안 함이 아니다");
    assert(html.includes('옛 설정이라 이어받지 않았어요'), '안내가 없다');
    await tab.goToScore();
    assert(tab.get().curStep === 2, '고르지 않고 시작했다');
    await tab.chooseParApply(true);
    s = tab.get();
    assert(s.curStep === 3 && srv.db.get(s.selectedMatch._id).parForced === true, '새 매치에 안 적혔다');
  });

  await t('⑧-4 이어받기를 서버가 확인해 주지 않으면 STEP 2 에 남아 다시 고르게 한다', async () => {
    const srv = makeServer([base({ matchType: 'Competitive', parForced: true, status: 'completed', sets: [{ a: 6, b: 1 }] })]);
    const tab = makeTablet(srv, []);
    tab.done(JSON.parse(JSON.stringify(srv.db.get('M1'))), LG);
    srv.echoWrong = true;
    await tab.replayDifferentPair();
    const s = tab.get();
    assert(s.curStep === 2, '확인 없이 팀 화면으로 갔다: ' + s.curStep);
    const html = tab.ctx.els['par-step'].innerHTML;
    assert(html.includes('이어받지 못했어요'), '실패 안내가 없다');
  });

  await t('⑨ 팀 화면에서 뒤로 — 점수 전이면 STEP 2(PAR)로, 거기서 뒤로 — 목록', async () => {
    const srv = makeServer([base({ matchType: 'Friendly', parForced: false })]);
    const tab = makeTablet(srv, [{ m: base({ matchType: 'Friendly' }), lg: LG }]);
    await tab.selectMatch(0);
    await tab.chooseParApply(false);
    assert(tab.get().curStep === 3, '팀 화면이 아니다');
    tab.goBack();
    assert(tab.get().curStep === 2, 'STEP 2 로 안 갔다');
    tab.goBack();
    assert(tab.get().curStep === 1 && tab.get().selectedMatch === null, '목록으로 안 갔다');
  });

  await t('⑩ 늦게 온 서버 응답이 다른 화면을 덮지 않는다', async () => {
    let release;
    const gate = () => new Promise((r) => { release = r; });
    const srv = makeServer([base({ matchType: 'Friendly', parForced: false })]);
    const tab = makeTablet(srv, [{ m: base({ matchType: 'Friendly' }), lg: LG }], { listGate: gate });
    const p = tab.selectMatch(0);
    assert(tab.ctx.els['par-step'].innerHTML.includes('읽는 중'), '읽는 동안 옛 값으로 먼저 그렸다');
    tab.goBack(); // 목록으로
    release();
    await p;
    assert(tab.get().curStep === 1 && tab.get().selectedMatch === null, '늦은 응답이 매치를 되살렸다');
  });

  await t('⑪ 순서 — STEP 2 등급(PAR) → STEP 3 팀 → STEP 4 점수 (화면·점·라벨)', () => {
    const s2 = src.indexOf('id="screen-2"'), s3 = src.indexOf('id="screen-3"'), s4 = src.indexOf('id="screen-4"');
    assert(s2 > 0 && s3 > s2 && s4 > s3, '화면 순서');
    const par = src.indexOf('id="par-step"'), team = src.indexOf('id="team-display"');
    assert(par > s2 && par < s3, 'par-step 이 STEP 2 화면에 없다');
    assert(team > s3 && team < s4, 'team-display 가 STEP 3 화면에 없다');
    assert(src.includes('id="dot-4"') && src.includes('STEP ${curStep} / 4'), '점 4개 · 라벨 / 4');
    assert(src.includes('id="screen-5"'), '완료 화면');
    // 팀 뽑기는 STEP 3 에만 — STEP 2 꼬리에 팀 버튼이 있으면 순서가 무너진다
    const rs = body('renderStep');
    const b2 = rs.slice(rs.indexOf('curStep===2'), rs.indexOf('curStep===3'));
    assert(!b2.includes('doAssignTeams'), 'STEP 2 에 팀 뽑기 버튼이 있다');
    assert(!body('selectMatch').includes('doAssignTeams'), '매치를 고르자마자 팀을 뽑는다');
  });

  await t('빌드 배지 c14 · BUILD · SW v48 같이', () => {
    const sw = fs.readFileSync(path.join(__dirname, '..', 'ps_court', 'ps_court_sw.js'), 'utf8');
    assert(src.includes('<!--COURTBUILD:c14-->') && src.includes('var BUILD = "c14";') && src.includes('>앱 c14</div>'), '배지');
    assert(sw.includes("const CACHE = 'ps-court-v48';"), 'SW');
  });

  console.log(fails ? `\n실패 ${fails}건\n` : '\n전부 통과\n');
  process.exit(fails ? 1 : 0);
})();
