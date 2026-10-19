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
// c16(2026-10-11 — 사용자 *"리그 PAR 켜놓은걸 기준으로 하자. 그리고 PAR 반영된 경기는 결과에 표시해줘."*):
//   · 가짜 서버도 진짜처럼 **새 매치를 리그 설정으로** 만든다(켬 리그 = Competitive + parForced · 끔 리그 = Friendly).
//     그래서 다시하기의 이어받기가 **켬 → 끔 · 끔 → 켬** 을 실제로 뒤집는지 본다(예전엔 새 매치가 늘 꺼진 채라
//     '반영 안 함' 이어받기는 아무것도 안 바꿔도 통과했다).
//   · 저장 확인 창은 이 매치 값대로 말한다 · 완료 화면(STEP 5)에 **서버에서 다시 읽은** 등급(PAR) 한 줄
//     (반영됨 / 반영 안 됨 — 이유 · 옛 서버면 아무것도 안 그린다 · 늦은 응답은 새 경기 화면을 안 덮는다).
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
  'goToScore', 'goBack', 'applyTeams', 'psPhoto', 'psName', 'psAirName', 'psAirPhoto',
  'parSkipNote', 'saveConfirmText', 'parDoneLine', 'showDonePar', 'showDone', 'saveMatch', 'psEsc'];

// 새 매치의 PAR 칸 — 진짜 서버(backend utils/leagueMatchType.newMatchParFields)와 같은 모양(c16).
// 켬 리그(기본) = 태블릿의 '켬' 그대로, 끔 리그 = '반영 안 함'. 모노repo 에선 아래 ⑫ 가 백엔드 함수와 대조한다.
const NEW_MATCH_PAR = {
  on: { matchType: 'Competitive', parForced: true },
  off: { matchType: 'Friendly', parForced: false },
};

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
    leagueParDefault: 'on',   // 리그 'PAR 반영 기본'(웹어드민) — 새 매치가 이 값으로 태어난다
    saveMessage: '경기 결과 저장됨 (PAR 반영 완료)', // 결과 저장(PATCH completed) 응답 문장
    // 끝난 경기에 서버가 싣는 PAR 상태(목록 parStatus · parStatusText). null 이면 안 싣는다(옛 서버).
    statusOf: (row) => (row.parApplied ? { parStatus: 'applied', parStatusText: 'PAR 반영' } : null),
    list: async () => ({ data: [...db.values()].map((r) => {
      const c = clone(r);
      const st = c.status === 'completed' ? srv.statusOf(c) : null;
      return st ? Object.assign(c, st) : c;
    }) }),
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
        // 서버가 사람마다 onAir(방송 이름)를 붙인다 — 한 명은 닉네임(사진 없음), 한 명은 실명, 둘은 옛 서버처럼 없음.
        // ⚠️ 진짜 서버처럼 **리그 설정으로** 태어난다(c16 — 예전 가짜 서버는 늘 Friendly 였다).
        const row = { _id: id, ...NEW_MATCH_PAR[srv.leagueParDefault], status: 'scheduled', sets: [],
          teamA: [{ _id: b.players[0], lastNameKorean: '임', firstNameKorean: '우재', profile: 'https://x/p0.jpg',
                    onAir: { name: '우재짱', showPhoto: false, mode: 'nickname' } },
                  { _id: b.players[2], lastNameKorean: '조', firstNameKorean: '준희', profile: 'https://x/p2.jpg',
                    onAir: { name: '조준희', showPhoto: true, mode: 'real' } }],
          teamB: [{ _id: b.players[1], lastNameKorean: '이', firstNameKorean: '준우' },
                  { _id: b.players[3], lastNameKorean: '설', firstNameKorean: '정수' }], players: [] };
        db.set(id, row);
        return { data: clone(row) };
      }
      if (/\/matches\/\w+$/.test(url) && o.method === 'PATCH') {
        const id = url.split('/').pop();
        const b = JSON.parse(o.body);
        Object.assign(db.get(id), b);
        // 결과 저장 — 진짜 서버는 반영되면 parApplied 를 찍는다(가짜는 문장에 '반영 완료' 가 있고 끔이 아니면).
        if (b.status === 'completed') {
          const row = db.get(id);
          if (/반영 완료/.test(srv.saveMessage) && row.matchType !== 'Friendly') row.parApplied = true;
          return { data: clone(row), message: srv.saveMessage };
        }
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
    srv, rows, toasts: [], alerts: [], steps: [], confirms: [], els,
    listGate: opts.listGate || null,
    document: { getElementById: (id) => (els[id] = els[id] || { innerHTML: '', textContent: '', className: '', style: {} }) },
  };
  // eslint-disable-next-line no-new-func
  const run = new Function('ctx', `
    const document = ctx.document;
    let curStep = 1, allMatches = ctx.rows, leagueMatchesById = {};
    let selectedMatch = null, selectedLeague = null;
    let teamA = [], teamB = [], teamAAir = [], teamBAir = [], teamAIds = [], teamBIds = [], teamAPar = [], teamBPar = [], teamAPhotos = [], teamBPhotos = [];
    let sets = [{a:0,b:0}], curSet = 0, phase = 'playing', matchNum = 0;
    ${src.match(/let parDecidedFor = null;[^\n]*/)[0]}
    ${src.match(/let parPrevState = null;[^\n]*/)[0]}
    ${src.match(/let parBusy = false;[^\n]*/)[0]}
    ${src.match(/let parError = '';[^\n]*/)[0]}
    ${src.match(/let teamsFresh = false;[^\n]*/)[0]}
    let psToken = 'tok';
    const psFetch = (u, o) => ctx.srv.fetch(u, o);
    // 재시도 래퍼 — 저장(PATCH)은 가짜 서버로, 목록(GET)은 저장소 그대로(listGate 로 늦출 수 있다).
    const psFetchRetry = async (u, o) => {
      if (o && o.method && o.method !== 'GET') return ctx.srv.fetch(u, o);
      if (ctx.listGate) await ctx.listGate(); ctx.srv.calls.push(['GET', u, '']); return ctx.srv.list();
    };
    const confirm = (m) => { ctx.confirms.push(m); return true; };
    const showToast = (m) => ctx.toasts.push(m);
    const alert = (m) => ctx.alerts.push(m);
    const setTimeout = () => 0;
    function openSettings() {}
    function renderStep() { ctx.steps.push(curStep); }
    function renderTeamDisplay(fresh) { ctx.teamFresh = fresh; }
    function renderPairInfo() {}
    function psId(u) { return String((u && u._id) || u || ''); }
    function parTag() { return ''; }
    async function refreshLeagueMatches() {}
    function renderScoreSection() {}
    async function ensureMatchNumber() { return 1; }
    function pushToFirebase() {} function startStream() {} function hideScoreboard() {}
    let _streamActive = false; var _stopping = null; var _starting = null; function stopStream(o) { (ctx.stops = ctx.stops || []).push(o); }
    ${FNS.map(body).join('\n')}
    return {
      selectMatch, chooseParApply, replayDifferentPair, goToScore, goBack, goParStep, saveMatch, saveConfirmText, parDoneLine,
      get: () => ({ curStep, selectedMatch, sets, parDecidedFor, parPrevState, parError, teamA, teamB, teamAAir, teamBAir, teamAIds, teamBIds, teamAPhotos }),
      done: (m, lg) => { selectedMatch = m; selectedLeague = lg; applyTeams(m.teamA, m.teamB); curStep = 5; },
      play: (s) => { sets = s; curSet = s.length - 1; },
    };
  `);
  return Object.assign(run(ctx), { ctx });
}

const LG = { _id: 'L1', name: '26S3' };
// 이름은 진짜 psName 이 읽는 모양 그대로(lastNameKorean+firstNameKorean) — 가짜 psName 으로 덮지 않는다(c15).
const P = (n) => ({ _id: 'u' + n, lastNameKorean: '김', firstNameKorean: '선수' + n });
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
    // 화면은 실명, 방송은 onAir(c15) — 닉네임을 고른 사람은 닉네임 + 사진 없음, onAir 가 없으면 실명.
    assert(JSON.stringify(s.teamA) === '["임우재","조준희"]', '화면 이름(실명): ' + JSON.stringify(s.teamA));
    assert(JSON.stringify(s.teamAAir) === '["우재짱","조준희"]', '방송 이름: ' + JSON.stringify(s.teamAAir));
    assert(JSON.stringify(s.teamBAir) === '["이준우","설정수"]', 'onAir 없는 사람은 실명: ' + JSON.stringify(s.teamBAir));
    assert(JSON.stringify(s.teamAPhotos) === '["","https://x/p2.jpg"]', '닉네임은 사진 없음: ' + JSON.stringify(s.teamAPhotos));
  });

  await t('⑧-2 직전이 반영 안 함이면 — 켬 리그의 새 매치(켜진 채로 태어남)를 반영 안 함으로 **뒤집어** 이어받는다', async () => {
    const srv = makeServer([base({ matchType: 'Friendly', parForced: false, status: 'completed', sets: [{ a: 6, b: 1 }] })]);
    srv.leagueParDefault = 'on'; // 리그 'PAR 반영 기본' 켬 — 새 매치는 Competitive + parForced 로 태어난다(c16)
    const tab = makeTablet(srv, []);
    tab.done(JSON.parse(JSON.stringify(srv.db.get('M1'))), LG);
    await tab.replayDifferentPair();
    const s = tab.get();
    const fp = srv.calls.findIndex((c) => /from-players$/.test(c[1]));
    const pa = srv.calls.findIndex((c) => /par-apply$/.test(c[1]));
    assert(fp >= 0 && pa > fp, '새 매치를 만든 뒤 par-apply 를 안 불렀다');
    assert(/"apply":false/.test(srv.calls[pa][2]), 'par-apply 가 끔이 아니다: ' + srv.calls[pa][2]);
    const row = srv.db.get(s.selectedMatch._id);
    assert(s.curStep === 3 && row.matchType === 'Friendly' && row.parForced === false,
      '반영 안 함으로 안 뒤집혔다: ' + JSON.stringify({ step: s.curStep, matchType: row.matchType, parForced: row.parForced }));
    assert(/par-toggle off/.test(tab.ctx.els['par-toggle-wrap'].innerHTML), '팀 화면 줄이 꺼짐이 아니다');
  });

  await t('⑧-5 직전이 반영이면 — 끔 리그의 새 매치(꺼진 채로 태어남)를 반영으로 **뒤집어** 이어받는다', async () => {
    const srv = makeServer([base({ matchType: 'Competitive', parForced: true, status: 'completed', sets: [{ a: 6, b: 1 }] })]);
    srv.leagueParDefault = 'off';
    const tab = makeTablet(srv, []);
    tab.done(JSON.parse(JSON.stringify(srv.db.get('M1'))), LG);
    await tab.replayDifferentPair();
    const s = tab.get();
    const pa = srv.calls.find((c) => /par-apply$/.test(c[1]));
    assert(pa && /"apply":true/.test(pa[2]), 'par-apply 가 켬이 아니다');
    const row = srv.db.get(s.selectedMatch._id);
    assert(s.curStep === 3 && row.matchType === 'Competitive' && row.parForced === true, '반영으로 안 뒤집혔다');
  });

  await t('⑧-3 고른 적 없는 옛 값(legacy)은 이어받지 않고 STEP 2 에서 고르게 한다', async () => {
    const srv = makeServer([base({ matchType: 'Competitive', parForced: false, status: 'completed', sets: [{ a: 6, b: 1 }] })]);
    const tab = makeTablet(srv, []);
    tab.done(JSON.parse(JSON.stringify(srv.db.get('M1'))), LG);
    await tab.replayDifferentPair();
    let s = tab.get();
    assert(s.curStep === 2, 'STEP 2 로 안 갔다: ' + s.curStep);
    const html = tab.ctx.els['par-step'].innerHTML;
    // c16: 새 매치는 **리그 설정**으로 태어난다 — 켬 리그(기본)면 '지금' 은 반영(예전엔 늘 반영 안 함이었다).
    assert(curBtn(html) === 'on', "새 매치의 '지금' 이 리그 설정(반영)이 아니다: " + curBtn(html));
    assert(html.includes('옛 설정이라 이어받지 않았어요'), '안내가 없다');
    // STEP 2 에 남아도 팀 배열은 **새 매치** 것 — 방송 이름까지(손으로 채우던 시절엔 방송 배열이 앞 경기 것으로 남았다 · c15)
    assert(JSON.stringify(s.teamAAir) === '["우재짱","조준희"]', '방송 이름이 앞 경기 것: ' + JSON.stringify(s.teamAAir));
    assert(JSON.stringify(s.teamAIds) === '["u1","u3"]' && JSON.stringify(s.teamBIds) === '["u2","u4"]', 'id: ' + JSON.stringify([s.teamAIds, s.teamBIds]));
    await tab.goToScore();
    assert(tab.get().curStep === 2, '고르지 않고 시작했다');
    // 리그 설정(켬)과 반대로 골라 본다 — 서버에 실제로 적혀야 넘어간다.
    await tab.chooseParApply(false);
    s = tab.get();
    const row = srv.db.get(s.selectedMatch._id);
    assert(s.curStep === 3 && row.matchType === 'Friendly' && row.parForced === false, '새 매치에 안 적혔다');
  });

  await t('⑧-4 이어받기를 서버가 확인해 주지 않으면 STEP 2 에 남아 다시 고르게 한다', async () => {
    const srv = makeServer([base({ matchType: 'Competitive', parForced: true, status: 'completed', sets: [{ a: 6, b: 1 }] })]);
    // 끔 리그 — 새 매치가 꺼진 채로 태어나야 '바꾸지 않고 돌려준 값'(옛 서버 흉내)이 고른 값과 다르다.
    srv.leagueParDefault = 'off';
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

  // ── c16: 저장 확인 · 완료 화면(STEP 5) 등급(PAR) 한 줄 ──────────────────────────
  const flush = async (n = 6) => { for (let i = 0; i < n; i++) await new Promise((r) => setImmediate(r)); };
  // 매치 고르기 → STEP 2 → 시작 → 6-3 6-4 → 저장. 완료 화면 한 줄은 기다리지 않고 채워지므로 몇 번 흘려보낸다.
  async function playAndSave(srv, apply = true, played = [{ a: 6, b: 3 }, { a: 6, b: 4 }]) {
    const tab = makeTablet(srv, [{ m: base({ matchType: 'Friendly' }), lg: LG }]);
    await tab.selectMatch(0);
    await tab.chooseParApply(apply);
    await tab.goToScore();
    tab.play(played);
    await tab.saveMatch();
    return tab;
  }
  const donePar = (tab) => tab.ctx.els['done-par'] || { textContent: '', className: '' };

  await t('⑫ 저장 확인 창 — 이 매치 값대로 말한다(등급(PAR) · 순위 추가점수 같이 · 경쟁 금지)', async () => {
    const srv = makeServer([]);
    const tab = makeTablet(srv, []);
    const on = tab.saveConfirmText({ matchType: 'Competitive', parForced: true }, '6-3');
    const off = tab.saveConfirmText({ matchType: 'Friendly', parForced: false }, '6-3');
    const legacy = tab.saveConfirmText({ matchType: 'Competitive', parForced: false }, '6-3');
    assert(on.includes('등급(PAR)에 반영되고 순위 추가점수를 받아요'), '켬: ' + on);
    assert(off.includes('등급(PAR)에 반영되지 않고 순위 추가점수도 없어요'), '끔: ' + off);
    assert(legacy.includes('혼성·브릿지') && legacy.includes('순위 추가점수'), '옛 설정: ' + legacy);
    for (const x of [on, off, legacy]) {
      assert(x.includes('6-3') && x.includes('되돌리기 어려워요'), '점수·되돌리기 경고: ' + x);
      assert(!x.includes('경쟁'), "'경쟁' 이라고 썼다: " + x);
    }
    // 예전 문장('저장하면 PAR이 반영되고')은 반영 안 함 경기에도 반영된다고 했다
    assert(!/PAR이 반영되고/.test(off), '끔인데 반영된다고 한다');
    // 실제 저장 흐름이 그 문장을 쓴다
    const srv2 = makeServer([base({ matchType: 'Friendly', parForced: false })]);
    const tab2 = await playAndSave(srv2, false);
    assert(tab2.ctx.confirms[0] && tab2.ctx.confirms[0].includes('반영되지 않고'), '저장이 이 매치 값대로 묻지 않는다: ' + tab2.ctx.confirms[0]);
  });

  await t('⑫-2 무승부(세트 승수 같음)면 등급(PAR)을 약속하지 않는다 — 서버 엔진이 무승부는 0명으로 끝낸다', async () => {
    // 예전: 확인 창 '반영되고' → 몇 초 뒤 c9 알림·완료 화면 '반영 안 됨 — 무승부' — 태블릿이 스스로 뒤집었다.
    const srv = makeServer([]);
    const tab = makeTablet(srv, []);
    const ON = { matchType: 'Competitive', parForced: true };
    const LEGACY = { matchType: 'Competitive', parForced: false };
    const OFF = { matchType: 'Friendly', parForced: false };
    const draw = [{ a: 6, b: 3 }, { a: 3, b: 6 }];
    for (const m of [ON, LEGACY]) {
      const x = tab.saveConfirmText(m, '6-3, 3-6', draw);
      assert(x.includes('무승부라 등급(PAR)에는 반영되지 않아요') && x.includes('순위 추가점수는 그대로'), '무승부: ' + x);
      assert(!x.includes('반영되고'), '무승부인데 반영된다고 한다: ' + x);
      assert(!x.includes('경쟁'), "'경쟁' 이라고 썼다: " + x);
    }
    // 1세트만 비긴 것(6-6 한 세트)도 무승부 — 서버와 같은 세트 승수 셈
    assert(tab.saveConfirmText(ON, '6-6', [{ a: 6, b: 6 }]).includes('무승부'), '6-6 한 세트');
    // 이긴 경기는 그대로 약속한다 · 반영 안 함은 무승부여도 '반영되지 않고 순위 추가점수도 없어요'
    assert(tab.saveConfirmText(ON, '6-3, 3-6, 6-4', [...draw, { a: 6, b: 4 }]).includes('반영되고 순위 추가점수를 받아요'), '3세트 승');
    assert(tab.saveConfirmText(OFF, '6-3, 3-6', draw).includes('반영되지 않고 순위 추가점수도 없어요'), '끔 + 무승부');
    // 실제 저장 흐름이 저장할 세트를 넘긴다
    const srv2 = makeServer([base({ matchType: 'Friendly', parForced: false })]);
    const tab2 = await playAndSave(srv2, true, draw);
    assert(tab2.ctx.confirms[0] && tab2.ctx.confirms[0].includes('무승부라'), '저장이 세트를 안 넘긴다: ' + tab2.ctx.confirms[0]);
  });

  await t('⑬ 완료 화면 — 서버가 반영했으면 "등급(PAR) 반영됨"(다시 읽은 값)', async () => {
    const srv = makeServer([base({ matchType: 'Friendly', parForced: false })]);
    const tab = await playAndSave(srv, true);
    assert(tab.get().curStep === 5, '완료 화면이 아니다');
    await flush();
    const el = donePar(tab);
    assert(el.textContent === '✅ 등급(PAR) 반영됨', '줄: ' + el.textContent);
    assert(/\bon\b/.test(el.className), '클래스: ' + el.className);
    // 저장한 **뒤에** 목록을 다시 읽었다
    const save = srv.calls.findIndex((c) => c[0] === 'PATCH' && /"status":"completed"/.test(c[2]));
    const reread = srv.calls.findIndex((c, i) => i > save && c[0] === 'GET' && c[1] === '/league/L1/matches');
    assert(save >= 0 && reread > save, '저장 뒤에 다시 안 읽었다');
  });

  await t('⑬-2 반영 안 됐으면 "등급(PAR) 반영 안 됨 — 이유"(서버 parStatusText 의 · 뒤)', async () => {
    const srv = makeServer([base({ matchType: 'Friendly', parForced: false })]);
    srv.saveMessage = '경기 결과 저장됨 (혼성 매치 — PAR 반영 안 됨)';
    srv.statusOf = () => ({ parStatus: 'mixed', parStatusText: 'PAR 미반영 · 혼성' });
    const tab = await playAndSave(srv, true);
    await flush();
    const el = donePar(tab);
    assert(el.textContent === '⚠️ 등급(PAR) 반영 안 됨 — 혼성', '줄: ' + el.textContent);
    assert(/\boff\b/.test(el.className), '클래스: ' + el.className);
  });

  await t('⑬-3 저장 문장은 "반영 완료" 인데 서버 기록은 미반영(엔진 실패) — 다시 읽은 값을 믿는다', async () => {
    const srv = makeServer([base({ matchType: 'Friendly', parForced: false })]);
    srv.statusOf = () => ({ parStatus: 'pending', parStatusText: 'PAR 미반영' });
    const tab = await playAndSave(srv, true);
    await flush();
    assert(donePar(tab).textContent === '⚠️ 등급(PAR) 반영 안 됨 — 운영자 확인이 필요해요', '줄: ' + donePar(tab).textContent);
    // 이유 없는 pending 은 저장 문장의 괄호를 빌린다
    const line = tab.parDoneLine({ parStatus: 'pending', parStatusText: 'PAR 미반영' }, '경기 결과 저장됨 (PAR 반영 대상 아님)');
    assert(line && !line.applied && line.text === '등급(PAR) 반영 안 됨 — PAR 반영 대상 아님', JSON.stringify(line));
  });

  await t('⑬-4 옛 서버(parStatus 없음)·다시 못 읽음 — 아무것도 안 그린다', async () => {
    const srv = makeServer([base({ matchType: 'Friendly', parForced: false })]);
    srv.statusOf = () => null;
    const tab = await playAndSave(srv, true);
    await flush();
    const el = donePar(tab);
    assert(el.textContent === '' && el.className === 'done-par', '옛 서버인데 그렸다: ' + JSON.stringify(el));
    assert(tab.parDoneLine(null, '경기 결과 저장됨 (혼성 매치 — PAR 반영 안 됨)') === null, '매치 없음 → null');
    assert(tab.parDoneLine({ status: 'completed' }) === null, 'parStatus 없음 → null');
  });

  await t('⑬-5 늦게 온 응답은 새 경기 화면을 덮지 않는다(저장 → 바로 다른 페어로 다시하기)', async () => {
    const srv = makeServer([base({ matchType: 'Friendly', parForced: false })]);
    const tab = makeTablet(srv, [{ m: base({ matchType: 'Friendly' }), lg: LG }]);
    await tab.selectMatch(0);
    await tab.chooseParApply(true);
    await tab.goToScore();
    tab.play([{ a: 6, b: 3 }, { a: 6, b: 4 }]);
    let release;
    tab.ctx.listGate = () => new Promise((r) => { release = r; }); // 저장 뒤 다시 읽기를 붙잡는다
    await tab.saveMatch();
    await flush();
    assert(typeof release === 'function', '다시 읽기가 안 나갔다');
    tab.ctx.listGate = null;
    await tab.replayDifferentPair();
    assert(tab.get().curStep === 3, '다시하기가 팀 화면으로 안 갔다: ' + tab.get().curStep);
    release();
    await flush();
    assert(donePar(tab).textContent === '', '늦은 응답이 줄을 그렸다: ' + donePar(tab).textContent);
  });

  // 모노repo 에서만 — 가짜 서버·완료 화면이 **진짜 백엔드 규칙**과 같은 모양인지 대조한다(PS-rank 단독이면 건너뛴다).
  const BE = path.join(__dirname, '..', '..', 'backend', 'utils');
  const inMono = fs.existsSync(path.join(BE, 'leagueMatchType.js')) && fs.existsSync(path.join(BE, 'leagueParStatus.js'));

  await t('⑭ 가짜 서버의 새 매치 = 백엔드 newMatchParFields(켬·끔·빈 값) — 모노repo 에서만', () => {
    if (!inMono) return;
    const { newMatchParFields } = require(path.join(BE, 'leagueMatchType.js'));
    assert(typeof newMatchParFields === 'function', '백엔드에 newMatchParFields 가 없다');
    const pick = (lg) => { const f = newMatchParFields(undefined, lg); return JSON.stringify({ matchType: f.matchType, parForced: f.parForced }); };
    assert(pick({ matchType: 'Competitive' }) === JSON.stringify(NEW_MATCH_PAR.on), '켬: ' + pick({ matchType: 'Competitive' }));
    assert(pick({}) === JSON.stringify(NEW_MATCH_PAR.on), '빈 값은 켬: ' + pick({}));
    assert(pick({ matchType: 'Friendly' }) === JSON.stringify(NEW_MATCH_PAR.off), '끔: ' + pick({ matchType: 'Friendly' }));
  });

  await t('⑮ 완료 화면 한 줄 — 백엔드 STATUS_TEXT 전부를 읽어 낸다 — 모노repo 에서만', () => {
    if (!inMono) return;
    const { STATUSES, STATUS_TEXT } = require(path.join(BE, 'leagueParStatus.js'));
    const tab = makeTablet(makeServer([]), []);
    for (const st of STATUSES) {
      const line = tab.parDoneLine({ parStatus: st, parStatusText: STATUS_TEXT[st] }, '');
      assert(line, st + ' → 줄 없음');
      if (st === 'applied') { assert(line.applied && line.text === '등급(PAR) 반영됨', st + ': ' + line.text); continue; }
      assert(!line.applied && line.text.startsWith('등급(PAR) 반영 안 됨 — '), st + ': ' + line.text);
      const why = line.text.slice('등급(PAR) 반영 안 됨 — '.length);
      const tail = STATUS_TEXT[st].includes('·') ? STATUS_TEXT[st].split('·').slice(1).join('·').trim() : '';
      assert(why && (tail ? why === tail : st === 'pending'), st + ' 이유: ' + why + ' / 서버: ' + STATUS_TEXT[st]);
    }
  });

  await t('빌드 배지 c17 · BUILD · SW v51 같이', () => {
    const sw = fs.readFileSync(path.join(__dirname, '..', 'ps_court', 'ps_court_sw.js'), 'utf8');
    assert(src.includes('<!--COURTBUILD:c17-->') && src.includes('var BUILD = "c17";') && src.includes('>앱 c17</div>'), '배지');
    assert(sw.includes("const CACHE = 'ps-court-v51';"), 'SW');
  });

  console.log(fails ? `\n실패 ${fails}건\n` : '\n전부 통과\n');
  process.exit(fails ? 1 : 0);
})();
