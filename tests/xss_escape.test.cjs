// 태블릿 보안 점검 (c17 · 2026-10-19) — 남의 글자는 이스케이프 · 방송 PC 요청엔 운영자 토큰.
//   실행: node tests/xss_escape.test.cjs
//
// 왜: 회원 이름·닉네임·리그/부문 이름·서버 오류 문장이 innerHTML 에 그대로 들어갔다. 이름 하나에 태그를 넣으면
//     운영자 태블릿에서 스크립트가 돌았다 — 운영자 토큰(localStorage ps_token)이 바로 옆에 있다.
//     그리고 stream_server 는 인증이 없어 obs.padelsociety.co.kr 터널로 누구나 방송을 켜고 껐다(서버 쪽은
//     tests/test_stream_server.py ⑲⑳). 태블릿은 이제 방송을 바꾸는 요청마다 운영자 토큰을 싣는다.
//   ① psEsc 가 < > & " ' 를 전부 바꾼다(속성 안에서도 안전)
//   ② innerHTML 을 만드는 함수의 ${…} 는 psEsc 를 거치거나, 이유가 적힌 허용 목록(숫자·상수·이미 이스케이프한 조각)뿐
//   ③ 서버 글자를 그대로 싣는 옛 모양(${psName( · ${lg.name · ${e.message} …)이 파일 어디에도 없다
//   ④ 방송 PC 로 가는 바꾸는 요청(켜기 ×2 · 끄기 · [무효] · 하이라이트)은 전부 streamHeaders() — /health 만 토큰 없이
//   ⑤ 운영자 확인에서 막힌 끄기는 '종료됨' 으로 읽지 않는다(방송은 그대로 나가는 중)
//   ⑥ 아메리카노 보드 창은 opener 를 끊는다 · 팝업이 막히면 예전처럼 같은 창
//   ⑦ 옛 태블릿(ps_court.html)도 같은 두 가지
//
// ⚠️ 소스를 그대로 떼어 와 돌린다(테스트에 로직을 베끼지 않는다).
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'ps_court', 'ps_court_playus.html'), 'utf8');
const old = fs.readFileSync(path.join(__dirname, '..', 'ps_court', 'ps_court.html'), 'utf8');

let fails = 0;
const t = async (name, fn) => { try { await fn(); console.log('  ok   ' + name); } catch (e) { fails++; console.log('  FAIL ' + name + ' — ' + e.message); } };
const assert = (c, m) => { if (!c) throw new Error(m); };
const bodyOf = (text) => (name) => {
  const i = text.search(new RegExp('(async )?function ' + name + '\\('));
  if (i < 0) throw new Error(name + ' 없음');
  let j = text.indexOf('{', i), d = 0;
  for (; j < text.length; j++) { if (text[j] === '{') d++; else if (text[j] === '}' && --d === 0) return text.slice(i, j + 1); }
  throw new Error(name + ' 끝 없음');
};
const body = bodyOf(src);
// 템플릿의 ${…} 를 전부(안쪽 템플릿 것까지) 꺼낸다. 안에 또 템플릿이 있는 겉 식(map 등)은 빼고 안쪽만 본다.
const interps = (b) => {
  const out = [];
  for (let i = 0; i < b.length; i++) {
    if (b[i] === '$' && b[i + 1] === '{') {
      let d = 0, j = i + 1;
      for (; j < b.length; j++) { if (b[j] === '{') d++; else if (b[j] === '}' && --d === 0) break; }
      const e = b.slice(i + 2, j).replace(/\s+/g, ' ').trim();
      if (!e.includes('`')) out.push(e);
    }
  }
  return out;
};
// 메인 스크립트(설정 · 화면 · 방송)만 — 빌드 배지·Firebase 모듈 스크립트는 따로다.
const main = src.slice(src.indexOf('<script>'), src.indexOf('</script>', src.indexOf('<script>')));

// ② innerHTML 을 만드는 함수마다 — psEsc 로 감싸지 않은 ${…} 의 허용 목록과 그 이유.
//    여기 없는 식이 생기면 실패한다: 새 칸을 넣으면 이스케이프하거나, 안전한 이유를 이 표에 적는다.
const SAFE = {
  loadMatches: ['lg._id', 'm.date'],                         // URL 경로 · 정렬 키(화면 아님)
  renderMatchList: ['parTag(u.par)', 'idx', 'div', 'statusTag', 'chips', 'dateStr',
    "timeStr ? ' ' + psEsc(timeStr) : ''"],                   // 숫자 · 위에서 이스케이프한 조각
  renderPairInfo: ["c.a.map(nm).join('·')", "c.b.map(nm).join('·')", // nm 이 이스케이프한다(아래 따로 확인)
    "cMatchLabel(m) || '완료'", 'score', 'cls', 'names', 'nm(roster[i])', 'nm(roster[j])', 'refs.length',
    "seen.join(' / ')", 'rows', 'usedLine'],                // state 는 psEsc(state) 로 실린다
  renderParToggle: ['what', 'title', 'sub', "cls.join(' ')", "locked ? '' : ' onclick=\"goParStep()\"'"], // 상수
  renderParStep: ['parTag(u.par)', "apply ? 'on' : 'off'", "isCur ? ' current' : ''", 'dis', 'apply',
    "apply ? '✅ 등급(PAR) 반영' : '🚫 등급(PAR) 반영 안 함'", "isCur ? now : ''",
    "parPrevState === 'off' ? '등급(PAR) 반영 안 함' : '등급(PAR) 반영'", 'lgName', 'no', 'chips',
    "apply ? '이 경기 결과가 등급(PAR)에 반영되고 순위 추가점수를 받아요. 혼성·브릿지 경기도 반영돼요.' : '이 경기 결과가 등급(PAR)에 반영되지 않고 순위 추가점수도 없어요.'",
    'opt(true)', 'opt(false)'],                               // lgName·no 는 만들 때 psEsc(아래 따로 확인)
  doAssignTeams: ['selectedLeague._id', 'selectedMatch._id'],  // URL 경로
  cancelScore: ['selectedLeague._id', 'selectedMatch._id'],    // URL 경로
  replayDifferentPair: ['selectedLeague._id'],                 // URL 경로
  saveHighlight: ['seconds', 'STREAM_SERVER'],                 // 숫자 · 방송 PC 주소(textContent 줄 · URL)
  renderTeamDisplay: ['badge', 'parTag(teamAPar[i])', 'parTag(teamBPar[i])', 'airHint(p, teamAAir[i])', 'airHint(p, teamBAir[i])'],
  renderScoreBoard: ["i===curSet?'active':''", 'i', 'i+1', 'tabsHtml', 'airTeamHint(teamA, teamAAir)',
    'airTeamHint(teamB, teamBAir)', 'sets[cs].a', 'sets[cs].b', 'cs'],  // 숫자 · 이스케이프하는 도우미
  showDone: ['selectedMatch.division', 'date', 'lgName', 'divStr', 'winner'], // 앞 넷은 textContent 줄 · winner 는 psEsc 조각
  renderStep: ['i', 'curStep', 'assignBtn', 'startBtn', 'cancelBtn', 'backBtn', 'mainBtn'], // 상수 버튼
  renderFreeLive: ["_liveEndsAt ? _fmtLeft(left) : ''", 'names'],  // 숫자 · psEsc 조각
};
// 변수를 통째로 되돌리는 자리(저장해 둔 버튼 모양) — 템플릿이 아니다.
const RESTORE_ONLY = { saveHighlight: /btn\.innerHTML = original/ };

(async () => {
  await t('① psEsc — < > & " \' 를 전부 바꾼다(속성 안에서도 안전) · 보통 이름은 그대로', () => {
    // eslint-disable-next-line no-new-func
    const psEsc = new Function(body('psEsc') + '; return psEsc;')();
    assert(psEsc(`<a href="x" onclick='y'>&</a>`) === '&lt;a href=&quot;x&quot; onclick=&#39;y&#39;&gt;&amp;&lt;/a&gt;', psEsc(`<a href="x" onclick='y'>&</a>`));
    assert(psEsc('김하경') === '김하경' && psEsc('PS iLeague 26S3') === 'PS iLeague 26S3' && psEsc('Gold+') === 'Gold+', '보통 글자가 바뀌었다');
    assert(psEsc(null) === '' && psEsc(undefined) === '' && psEsc(41) === '41', 'null·숫자');
  });

  await t('② innerHTML 을 만드는 함수마다 ${…} 는 psEsc 이거나 이유가 적힌 허용 목록', () => {
    const names = new Set();
    const re = /\.innerHTML\s*=/g; let m;
    while ((m = re.exec(main))) {
      const before = main.slice(0, m.index);
      const fm = [...before.matchAll(/(?:async )?function ([A-Za-z0-9_$]+)\(/g)].pop();
      assert(fm, 'innerHTML 을 쓰는 함수 이름을 못 찾았다 @' + m.index);
      names.add(fm[1]);
    }
    assert(names.size >= 12, '찾은 함수가 너무 적다: ' + [...names].join(','));
    const bad = [];
    for (const fn of names) {
      const b = body(fn);
      if (RESTORE_ONLY[fn]) { assert(RESTORE_ONLY[fn].test(b), fn + ' 되돌리기 모양'); }
      const allow = new Set(SAFE[fn] || []);
      for (const e of interps(b)) {
        // psEsc(…) · 배열을 하나씩 psEsc 해 잇는 모양(teamA.map(psEsc).join('<br>')) · 허용 목록
        if (e.startsWith('psEsc(') || /^[A-Za-z0-9_$.]+\.map\(psEsc\)\.join\('[^']*'\)$/.test(e) || allow.has(e)) continue;
        bad.push(`${fn}: \${${e}}`);
      }
    }
    assert(!bad.length, '이스케이프 안 된 칸 — ' + bad.join(' | '));
  });

  await t('② 미리 이스케이프해 두는 자리 — nm · lgName · no · state · winner', () => {
    assert(/const nm = \(id\) => psEsc\(/.test(body('renderPairInfo')), 'renderPairInfo nm');
    assert(body('renderPairInfo').includes('${psEsc(state)}'), 'renderPairInfo state');
    const ps = body('renderParStep');
    assert(/const lgName = psEsc\(/.test(ps) && ps.includes('#${psEsc(m.matchNumber)}'), 'renderParStep lgName·no');
    const sd = body('showDone');
    assert(sd.includes("${psEsc(teamA.join(' / '))} 승리!") && sd.includes("${psEsc(teamB.join(' / '))} 승리!"), 'showDone winner');
    assert(/textContent = `\$\{date\} · \$\{lgName\}\$\{divStr\}`/.test(sd), 'showDone 의 리그·부문 줄은 textContent');
  });

  await t('③ 서버 글자를 그대로 싣는 옛 모양이 파일 어디에도 없다', () => {
    // (${m.date}T00:00:00 은 정렬 키 · ${_watchUrl ? 버튼 : ''} 은 상수라 뺀다)
    const banned = /\$\{\s*(psName\(|psAirName\(|lg\.name|selectedLeague\.name|e\.message|e && e\.message|parError\}|_watchUrl\}|_liveTeams|p\}|c\}|m\.date\}(?!T00)|teamA\.join|teamB\.join)/g;
    const hits = main.match(banned) || [];
    assert(!hits.length, '남은 자리: ' + hits.join(' · '));
  });

  await t('③ 진짜로 돌려 본다 — 이름·리그·부문·날짜에 태그를 넣어도 태그가 안 생긴다', () => {
    const els = {};
    // eslint-disable-next-line no-new-func
    const run = new Function('els', `
      const document = { getElementById: (id) => (els[id] = els[id] || { innerHTML: '' }) };
      let allMatches = [], selectedMatch = null, selectedLeague = null, parBusy = false, parError = '', parPrevState = null;
      ${['psEsc', 'psName', 'parColor', 'parTag', 'parStateOf', 'renderMatchList', 'renderParStep'].map(body).join('\n')}
      const PAR_COLOR = {}, PAR_GRADIENT = {};
      return {
        list: (rows) => { allMatches = rows; renderMatchList(); return els['match-list-wrap'].innerHTML; },
        step: (m, lg, err) => { selectedMatch = m; selectedLeague = lg; parError = err; renderParStep(); return els['par-step'].innerHTML; },
      };
    `);
    const tab = run(els);
    const X = '<img src=x onerror=alert(1)>';
    const u = { _id: 'u1', lastNameKorean: X, firstNameKorean: '' };
    const m = { _id: 'M1', status: 'live', players: [u, u, u, u], categories: [X], date: X, matchTime: '2026-10-19T18:30:00', matchNumber: 3 };
    const html = tab.list([{ m, lg: { _id: 'L1', name: X } }]) + tab.step(m, { _id: 'L1', name: X }, X);
    assert(!html.includes('<img'), '태그가 살아 있다');
    assert(html.includes('&lt;img src=x onerror=alert(1)&gt;'), '이스케이프된 글자가 안 보인다');
    const plain = tab.list([{ m: { ...m, players: [{ _id: 'u2', lastNameKorean: '김', firstNameKorean: '하경' }], categories: ['Gold+'], date: '2026-10-19' }, lg: { _id: 'L1', name: 'PS iLeague 26S3' } }]);
    assert(plain.includes('>김하경<') && plain.includes('>Gold+<') && plain.includes('>PS iLeague 26S3<') && plain.includes('📅 2026-10-19 18:30'), '보통 이름이 바뀌었다');
  });

  await t('④ 방송 PC 로 가는 바꾸는 요청은 전부 streamHeaders() — /health 만 토큰 없이', () => {
    const re = /fetch\(`\$\{STREAM_SERVER\}\/([a-z-]+)`/g; let m; const seen = {};
    while ((m = re.exec(main))) {
      const opts = main.slice(m.index, main.indexOf('})', m.index) + 2);
      seen[m[1]] = (seen[m[1]] || 0) + 1;
      if (m[1] === 'health') { assert(!opts.includes('streamHeaders') && !opts.includes('Authorization'), '/health 에 토큰을 실었다'); continue; }
      assert(opts.includes('headers: streamHeaders()'), `/${m[1]} 가 streamHeaders() 를 안 쓴다`);
    }
    assert(seen['start-stream'] === 2 && seen['stop-stream'] === 1 && seen['void-video'] === 1 && seen['highlight'] === 1 && seen['health'] === 1,
      '요청 수: ' + JSON.stringify(seen));
    assert(!/'Content-Type': ?'application\/json'\s*\}?,\s*\n?\s*body: JSON\.stringify\(\{ (seconds|watchUrl|mode)/.test(main), '손으로 쓴 머리가 남았다');
  });

  await t('④ streamHeaders — 로그인했으면 Bearer, 안 했으면 머리 없이(이 PC 직접은 서버가 들인다)', () => {
    // eslint-disable-next-line no-new-func
    const mk = (tok) => new Function('psToken', body('streamHeaders') + '; return streamHeaders();')(tok);
    const a = mk('T0k');
    assert(a.Authorization === 'Bearer T0k' && a['Content-Type'] === 'application/json', JSON.stringify(a));
    assert(!('Authorization' in mk('')), '토큰이 없는데 Authorization');
  });

  await t('④ streamAuthFail — 401 은 서버 말 + ⚙ 설정, 403·503 은 서버 말만, 그 밖은 건드리지 않는다', () => {
    const toasts = [], opened = [], cls = [];
    // eslint-disable-next-line no-new-func
    const f = new Function('ctx', `
      const document = { getElementById: () => ({ classList: { remove: (c) => ctx.cls.push(c) } }) };
      function showToast(m) { ctx.toasts.push(m); } function openSettings() { ctx.opened.push(1); }
      ${body('streamAuthFail')}; return streamAuthFail;`)({ toasts, opened, cls });
    const MSG = '운영자 로그인이 필요해요 — 태블릿 ⚙ 설정에서 로그인해 주세요';
    assert(f({ status: 401 }, { success: false, error: MSG, auth: 'required' }) === true, '401');
    assert(toasts.pop() === '🔒 ' + MSG && opened.length === 1 && cls.includes('show'), '401 은 서버 말 + 설정 열기(자유 라이브 창은 닫는다)');
    assert(f({ status: 403 }, { success: false, error: '권한 없음', auth: 'forbidden' }) === true && opened.length === 1, '403 은 설정을 안 연다');
    assert(f({ status: 503 }, { success: false, error: '못 닿음', auth: 'unavailable' }) === true && toasts.pop() === '🔒 못 닿음', '503');
    assert(f({ status: 409 }, { success: false, error: '리그 경기 방송 중이에요' }) === false, '평범한 실패는 부른 쪽이 처리');
    assert(f({}, { success: true }) === false && f(null, null) === false, '성공·응답 없음');
  });

  await t('⑤ 운영자 확인에서 막힌 끄기 — 방송은 그대로라 \'종료됨\' · 앱 LIVE 끄기를 하지 않는다', async () => {
    const ctx = { toasts: [], puts: [], status: [] };
    // eslint-disable-next-line no-new-func
    const run = new Function('ctx', `
      let selectedLeague = { _id: 'L1' }, psToken = 'tok';
      let _streamActive = true, _watchUrl = 'w', _liveMode = 'league', _liveEndsAt = null, _streamServerOnline = true;
      let _liveMatchId = 'M1', _liveMatchNum = 3;
      const STREAM_SERVER = 'http://obs';
      const AbortSignal = { timeout: () => null };
      const fetch = (u, o) => { ctx.sent = o; return Promise.resolve({ ok: false, status: 401, json: async () => ({ success: false, error: '운영자 로그인이 필요해요', auth: 'required' }) }); };
      const psFetch = (u, o) => { ctx.puts.push([u, o.body]); return Promise.resolve({}); };
      const setTimeout = (fn) => 0;
      const document = { getElementById: () => null };
      function _hideWatchBtn() {} function _updateStreamStatus(s) { ctx.status.push(s); }
      function showToast(m) { ctx.toasts.push(m); } function checkStreamServer() {} function openSettings() { ctx.opened = true; }
      ${src.match(/var _stopping = null;[^\n]*/)[0]}
      ${src.match(/var _stoppingFor = '';[^\n]*/)[0]}
      ${src.match(/var _starts = \[\];[^\n]*/)[0]}
      ${src.match(/var _starting = null;[^\n]*/)[0]}
      ${src.match(/var _startSeq = 0;[^\n]*/)[0]}
      ${['stopStream', '_stopStreamOnce', 'streamHeaders', 'streamAuthFail'].map(body).join('\n')}
      return { stopStream, active: () => _streamActive };`);
    const tab = run(ctx);
    await tab.stopStream({ matchId: 'M1' });
    assert(ctx.sent.headers.Authorization === 'Bearer tok', '끄기에 토큰이 없다');
    assert(tab.active() === true, '막혔는데 방송이 꺼진 걸로 적었다');
    assert(!ctx.toasts.some((m) => m.includes('종료됨')), "'종료됨' 이라고 했다: " + ctx.toasts.join(' | '));
    assert(ctx.toasts.includes('🔒 운영자 로그인이 필요해요') && ctx.opened, '서버 말·설정 열기');
    assert(!ctx.puts.length, '방송이 나가는데 앱 LIVE 배너를 껐다');
    assert(ctx.status.pop() === 'error', '상태 줄');
  });

  await t('⑤ 막힌 하이라이트·켜기는 서버 말을 띄운다(한 번만) · 자유 라이브는 상태 줄에도', () => {
    const hl = body('saveHighlight');
    assert(hl.includes('streamAuthFail(res, data)') && hl.includes('if (!e.shown) showToast('), '하이라이트');
    const st = body('_startStreamOnce');
    assert(st.includes("if (!streamAuthFail(res, data)) showToast('⚠️ 스트리밍 오류: '"), '리그 켜기');
    const fl = body('startFreeLive');
    assert(fl.includes('streamAuthFail(res, data);') && fl.includes("throw new Error(data.error || '시작 실패')"), '자유 라이브');
    const v = body('voidStreamOf');
    assert(v.includes('if (streamAuthFail(res, data)) authErr = data.error') && v.includes('유튜브 스튜디오에서 직접'), '[무효]');
  });

  await t('⑥ 아메리카노 보드 — 새 창의 opener 를 끊는다 · 팝업이 막히면 같은 창', () => {
    const run = (ret) => {
      const loc = { href: 'tablet' };
      // eslint-disable-next-line no-new-func
      new Function('ctx', `
        const psToken = 'T', window = { open: () => ctx.ret }, location = ctx.loc;
        function boardOrigin() { return 'https://api.padelsociety.co.kr'; } function showToast() {} function openSettings() {}
        ${body('openAmericano')}; openAmericano('A1');`)({ ret, loc });
      return loc.href;
    };
    const w = { opener: 'tablet-window' };
    assert(run(w) === 'tablet' && w.opener === null, '열린 창의 opener 가 남았다');
    assert(run(null) === 'https://api.padelsociety.co.kr/am/A1#t=T', '팝업이 막히면 같은 창으로');
    assert(!/window\.open\([^)]*noopener/.test(body('openAmericano')), "'noopener' 인자를 주면 늘 null 이라 같은 창 길만 탄다");
  });

  await t('⑦ 옛 태블릿(ps_court.html) — 이름 이스케이프 · 켜기·끄기에 streamHeaders()', () => {
    const ob = bodyOf(old);
    // eslint-disable-next-line no-new-func
    const psEsc = new Function(ob('psEsc') + '; return psEsc;')();
    assert(psEsc(`<b x="1" y='2'>&`) === '&lt;b x=&quot;1&quot; y=&#39;2&#39;&gt;&amp;', 'psEsc');
    assert(ob('renderTeamDisplay').includes('${psEsc(p)}') && ob('renderScoreBoard').includes("teamA.map(psEsc).join('<br>')"), '팀 이름');
    assert(ob('showDone').includes("${psEsc(teamA.join(' / '))}"), '완료 화면');
    assert(ob('startStream').includes('headers: streamHeaders()') && ob('stopStream').includes('headers: streamHeaders()'), '토큰 머리');
    assert(/localStorage\.getItem\('ps_token'\)/.test(ob('streamHeaders')), '리그앱 태블릿과 같은 토큰');
    const banned = old.match(/\$\{\s*(p\}|teamA\.join|teamB\.join)/g) || [];
    assert(!banned.length, '남은 자리: ' + banned.join(' · '));
  });

  console.log(fails ? `\n실패 ${fails}건\n` : '\n전부 통과\n');
  process.exit(fails ? 1 : 0);
})();
