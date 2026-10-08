// 태블릿 작은 버그 점검 (c11 · 2026-10-05 · ①은 c12 에서 STEP 2 로).
//   실행: node tests/court_tools.test.cjs
//   ① '다른 페어로 다시하기' 가 PAR 스위치를 새 매치로 다시 그린다
//   ② [매치 취소] 로 방송을 끄는 사이 resetAll() 이 리그를 비워도 isLive:false 가 나간다
//   ③ 그냥 끄는 중(cancelScore)에 [매치 취소] 가 오면 다 꺼진 뒤 /void-video 로 [무효] 를 붙인다
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'ps_court', 'ps_court_playus.html'), 'utf8');
const sw = fs.readFileSync(path.join(__dirname, '..', 'ps_court', 'ps_court_sw.js'), 'utf8');

let fails = 0;
const t = async (name, fn) => { try { await fn(); console.log('  ok   ' + name); } catch (e) { fails++; console.log('  FAIL ' + name + ' — ' + e.message); } };
const body = (name) => {
  const i = src.search(new RegExp('(async )?function ' + name + '\\('));
  if (i < 0) throw new Error(name + ' 없음');
  let j = src.indexOf('{', i), d = 0;
  for (; j < src.length; j++) { if (src[j] === '{') d++; else if (src[j] === '}' && --d === 0) return src.slice(i, j + 1); }
  throw new Error(name + ' 끝 없음');
};

(async () => {
  await t('① 다른 페어로 다시하기 — 새 매치는 STEP 2 에서 직전 값을 서버에 적어 이어받는다 (c13)', () => {
    const b = body('replayDifferentPair');
    const i = b.indexOf('selectedMatch = m;'), k = b.indexOf('renderParStep()');
    if (i < 0) throw new Error('새 매치로 바꾸는 줄이 없다');
    // ⚠️ 안 그리면 직전 매치의 'PAR 반영 ON' 이 남고, 서버엔 새 매치 기본(반영 안 함)이 적혀 조용히 빠진다.
    if (k < i) throw new Error('selectedMatch = m 뒤에 renderParStep() 이 없다');
    if (!/curStep = 2;/.test(b.slice(i))) throw new Error('STEP 2 로 가지 않는다');
    if (!/parDecidedFor = null;/.test(b.slice(i))) throw new Error('직전 매치의 결정이 새 매치로 넘어간다');
    // 이어받기는 화면만 옮기지 않고 STEP 2 와 같은 길(par-apply → 서버 확인)로 적는다.
    if (!/await chooseParApply\(prevPar === 'on', true\)/.test(b.slice(i))) throw new Error('이어받기가 서버 확인 길을 안 탄다');
  });

  // 진짜로 돌려 본다 — /stop-stream 이 느린 사이 resetAll() 이 selectedLeague 를 비운다.
  const run = new Function('ctx', `
    let selectedLeague = ctx.league, psToken = 'tok';
    let _streamActive = true, _watchUrl = 'w', _liveMode = 'league', _liveEndsAt = null, _streamServerOnline = true;
    let _liveMatchId = '', _liveMatchNum = 0;
    const STREAM_SERVER = 'http://obs';
    const AbortSignal = { timeout: () => null };
    const fetch = ctx.fetch, psFetch = ctx.psFetch;
    function _hideWatchBtn() {} function _updateStreamStatus() {} function showToast() {} function checkStreamServer() {}
    ${src.match(/var _stopping = null;[^\n]*/)[0]}
    ${src.match(/var _starting = null;[^\n]*/)[0]}
    ${src.match(/var _startSeq = 0;[^\n]*/)[0]}
    ${body('stopStream')}
    ${body('_stopStreamOnce')}
    return { stopStream, nullLeague: () => { selectedLeague = null; } };
  `);

  await t('② 매치 취소 중 리그가 비워져도 앱 LIVE 배지를 끈다(isLive:false)', async () => {
    let release;
    const stopCalls = [], puts = [];
    const ctx = {
      league: { _id: 'L1' },
      fetch: (url, o) => { stopCalls.push([url, o.body]); return new Promise((r) => { release = () => r({ json: async () => ({ voided: true }) }); }); },
      psFetch: (url, o) => { puts.push([url, o.body]); return Promise.resolve({}); },
    };
    const tab = run(ctx);
    const p1 = tab.stopStream({ void: true });      // voidStreamOf — await 하지 않고 던진다
    const p2 = tab.stopStream();                    // resetAll() 의 stopStream()
    tab.nullLeague();                               // resetAll(): selectedLeague = null
    release();
    const [d1, d2] = await Promise.all([p1, p2]);
    if (stopCalls.length !== 1) throw new Error('/stop-stream 이 ' + stopCalls.length + '번 나갔다');
    if (!/"void":true/.test(stopCalls[0][1])) throw new Error('void 가 빠졌다');
    if (!(d1 && d1.voided && d2 && d2.voided)) throw new Error('두 호출이 같은 결과를 못 받았다');
    if (puts.length !== 1 || puts[0][0] !== '/league/update/L1' || !/"isLive":false/.test(puts[0][1])) {
      throw new Error('isLive:false 가 안 나갔다: ' + JSON.stringify(puts));
    }
    // 끈 뒤엔 다시 null — 다음 방송을 막으면 안 된다
    if (await tab.stopStream() !== null) throw new Error('꺼진 뒤 stopStream 은 null');
  });

  await t('③ 그냥 끄는 중에 [매치 취소] — 그 약속을 받아도 /void-video 로 [무효] 를 붙인다', async () => {
    let release;
    const calls = [], toasts = [];
    // 옛 모양(한 칸) 그대로 — 태블릿을 올린 날 남아 있는 값도 읽어야 한다(c14 부터 매치별 맵)
    const store = { ps_stream_of: JSON.stringify({ matchId: 'M1', watchUrl: 'https://youtu.be/x' }) };
    const run3 = new Function('ctx', `
      let selectedLeague = { _id: 'L1' }, psToken = 'tok';
      let _streamActive = true, _watchUrl = 'w', _liveMode = 'league', _liveEndsAt = null, _streamServerOnline = true;
      let _liveMatchId = '', _liveMatchNum = 0;
      const STREAM_SERVER = 'http://obs';
      const AbortSignal = { timeout: () => null };
      const fetch = ctx.fetch, psFetch = () => Promise.resolve({});
      const localStorage = ctx.localStorage, setTimeout = (fn) => fn();
      function _hideWatchBtn() {} function _updateStreamStatus() {} function showToast(m) { ctx.toasts.push(m); } function checkStreamServer() {}
      ${src.match(/var _stopping = null;[^\n]*/)[0]}
    ${src.match(/var _starting = null;[^\n]*/)[0]}
    ${src.match(/var _startSeq = 0;[^\n]*/)[0]}
      ${body('stopStream')}
      ${body('_stopStreamOnce')}
      ${body('voidStreamOf')}
      ${body('streamOfAll')}
      ${body('streamOfGet')}
      ${body('streamOfDel')}
      return { stopStream, voidStreamOf };
    `);
    const tab = run3({
      toasts,
      localStorage: { getItem: (k) => store[k] || null, setItem: (k, v) => { store[k] = v; }, removeItem: (k) => { delete store[k]; } },
      fetch: (url, o) => {
        calls.push([url, o.body]);
        if (url.endsWith('/stop-stream')) return new Promise((r) => { release = () => r({ json: async () => ({ ok: true }) }); });
        return Promise.resolve({ json: async () => ({ voided: true }) });
      },
    });
    const p1 = tab.stopStream();                 // cancelScore() — void 없이, await 안 함
    const p2 = tab.voidStreamOf('M1');           // 곧바로 [매치 취소]
    release();
    await Promise.all([p1, p2]);
    if (calls.filter((c) => c[0].endsWith('/stop-stream')).length !== 1) throw new Error('/stop-stream 이 두 번');
    if (!calls.some((c) => c[0].endsWith('/void-video') && /youtu\.be\/x/.test(c[1]))) {
      throw new Error('/void-video 가 안 나갔다: ' + JSON.stringify(calls));
    }
    if (!toasts.some((m) => m.includes('[무효] 를 붙여요'))) throw new Error('성공 안내 없음: ' + JSON.stringify(toasts));
  });

  await t('빌드 배지 c14 · BUILD · SW v48 같이', () => {
    if (!src.includes('<!--COURTBUILD:c14-->') || !src.includes('var BUILD = "c14";')) throw new Error('배지');
    if (!sw.includes("const CACHE = 'ps-court-v48';")) throw new Error('SW');
  });

  console.log(fails ? `\n실패 ${fails}건\n` : '\n전부 통과\n');
  process.exit(fails ? 1 : 0);
})();
