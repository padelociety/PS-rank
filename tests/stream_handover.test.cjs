// 라이브 제목·썸네일이 지난 경기로 남던 것 — 태블릿 쪽 (c14 · 2026-10-08)
//   실행: node tests/stream_handover.test.cjs
//
// 신고(2026-10-08): 유튜브 라이브 제목·썸네일은 "PSiL 26S3 [Bridge] Match39"(구하경/성유나 vs 문서영/안리나)인데
// 화면 점수판은 "Match 41"(임우재/조준희 vs 이준우/설정수). 한 방송에 다음 경기들이 이어서 나갔다.
// 서버(stream_server.py)가 다른 경기면 넘겨받고 같은 경기면 이어 쓰게 고쳤고(tests/test_stream_server.py ⑪~⑯),
// 태블릿은 그 판단에 필요한 것을 보낸다:
//   ① 켜기 전에 끄기(경기 저장)가 끝나길 기다린다 — 겹치면 409 이거나 서버 상태가 꼬였다
//   ② 켜기·끄기에 매치 id 를 싣는다
//   ③ 서버가 '다른 경기라 그대로 뒀다'(skipped)면 LIVE 표시·앱 라이브 배너를 끄지 않는다
//   ④ 목록으로 나가면(뒤로) 이 매치 방송을 끈다
//   ⑤ 매치별 영상 주소(최근 20개) — 다음 경기가 넘겨받은 뒤 앞 경기를 취소해도 그 영상에 [무효]
//   ⑥ 상태 줄에 지금 방송의 경기 번호
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const src = fs.readFileSync(path.join(__dirname, '..', 'ps_court', 'ps_court_playus.html'), 'utf8');

let fails = 0;
const t = async (name, fn) => { try { await fn(); console.log('  ok   ' + name); } catch (e) { fails++; console.log('  FAIL ' + name + ' — ' + e.message); } };
const body = (name) => {
  const i = src.search(new RegExp('(async )?function ' + name + '\\('));
  if (i < 0) throw new Error(name + ' 없음');
  let j = src.indexOf('{', i), d = 0;
  for (; j < src.length; j++) { if (src[j] === '{') d++; else if (src[j] === '}' && --d === 0) return src.slice(i, j + 1); }
  throw new Error(name + ' 끝 없음');
};

// 태블릿 방송 함수들을 진짜로 돌리는 상자
function tablet(ctx) {
  // eslint-disable-next-line no-new-func
  const run = new Function('ctx', `
    let selectedMatch = ctx.match || null, selectedLeague = { _id: 'L1', name: 'PS iLeague 26S3' }, psToken = 'tok';
    let teamA = ['임우재', '조준희'], teamB = ['이준우', '설정수'], teamAPhotos = [], teamBPhotos = [], teamAPar = [], teamBPar = [];
    let matchNum = ctx.matchNum || 41;
    let _streamActive = !!ctx.active, _watchUrl = null, _liveMode = ctx.active ? 'league' : '', _liveEndsAt = null;
    let _streamServerOnline = true, _liveTitle = '', _liveTeams = { a: [], b: [] }, _liveMatchId = '', _liveMatchNum = 0;
    const STREAM_SERVER = 'http://obs';
    const AbortSignal = { timeout: () => null };
    const fetch = ctx.fetch, psFetch = (u, o) => { ctx.puts.push([u, o && o.body]); return Promise.resolve({}); };
    const localStorage = ctx.localStorage;
    const setTimeout = (fn) => { ctx.timers.push(fn); return 0; };
    function _hideWatchBtn() {} function _showWatchBtn() {} function renderHlRail() {} function renderFreeLive() {}
    function showToast(m) { ctx.toasts.push(m); }
    function rosterOf() { return []; }
    function checkStreamServer() { ctx.healthChecks = (ctx.healthChecks || 0) + 1; }
    const document = { getElementById: (id) => (ctx.els[id] = ctx.els[id] || { textContent: '', style: {} }) };
    ${src.match(/var _stopping = null;[^\n]*/)[0]}
    ${['_updateStreamStatus', 'startStream', 'stopStream', '_stopStreamOnce', 'voidStreamOf',
       'streamOfAll', 'streamOfGet', 'streamOfSet', 'streamOfDel'].map(body).join('\n')}
    return {
      startStream, stopStream, voidStreamOf, streamOfGet, streamOfSet, streamOfDel, _updateStreamStatus,
      get: () => ({ _streamActive, _liveMatchId, _liveMatchNum, _liveMode }),
      setLive: (num, mode) => { _liveMatchNum = num; _liveMode = mode; },
    };
  `);
  return run(ctx);
}
const mkCtx = (over) => {
  const store = {};
  return Object.assign({
    toasts: [], puts: [], timers: [], calls: [], els: {}, store,
    localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
  }, over);
};

(async () => {
  await t('① 켜기는 진행 중인 끄기(경기 저장)가 끝난 뒤에 나간다', async () => {
    let releaseStop;
    const ctx = mkCtx({ active: true, match: { _id: 'm40' } });
    ctx.fetch = (url, o) => {
      ctx.calls.push([url.replace('http://obs', ''), o.body]);
      if (url.endsWith('/stop-stream')) return new Promise((r) => { releaseStop = () => r({ json: async () => ({ success: true }) }); });
      return Promise.resolve({ json: async () => ({ success: true, watch_url: 'https://youtu.be/new41', broadcast_id: 'new41' }) });
    };
    const tab = tablet(ctx);
    const pStop = tab.stopStream({ matchId: 'm40' });   // showDone — 기다리지 않는다
    const pStart = tab.startStream();                    // '다른 페어로 다시하기' → 바로 켜기
    await new Promise((r) => setImmediate(r));
    if (ctx.calls.some((c) => c[0] === '/start-stream')) throw new Error('끄기가 끝나기 전에 /start-stream 이 나갔다');
    releaseStop();
    await Promise.all([pStop, pStart]);
    const order = ctx.calls.map((c) => c[0]);
    if (order.join(',') !== '/stop-stream,/start-stream') throw new Error('순서: ' + order.join(','));
  });

  await t('② 켜기·끄기에 매치 id 를 싣는다', async () => {
    const ctx = mkCtx({ match: { _id: 'm41' } });
    ctx.fetch = (url, o) => { ctx.calls.push([url.replace('http://obs', ''), o.body]); return Promise.resolve({ json: async () => ({ success: true, watch_url: 'https://youtu.be/v41' }) }); };
    const tab = tablet(ctx);
    await tab.startStream();
    const start = JSON.parse(ctx.calls.find((c) => c[0] === '/start-stream')[1]);
    if (start.matchId !== 'm41' || start.matchNumber !== 41) throw new Error(JSON.stringify(start));
    await tab.stopStream({ matchId: 'm41' });
    const stop = JSON.parse(ctx.calls.find((c) => c[0] === '/stop-stream')[1]);
    if (stop.matchId !== 'm41' || 'void' in stop) throw new Error(JSON.stringify(stop));
    // 자유 라이브 [종료] 처럼 id 없이 끄면 빈 몸 — 무엇이든 끈다(예전과 같다)
    ctx.calls.length = 0;
    await tab.startStream();
    await tab.stopStream();
    if (ctx.calls.find((c) => c[0] === '/stop-stream')[1] !== '{}') throw new Error('id 없는 끄기의 몸');
  });

  await t('② 경기 끝내는 자리마다 그 매치 id 로 끈다(저장·스코어 취소·매치 취소·목록·뒤로)', () => {
    const want = {
      showDone: 'stopStream({ matchId: selectedMatch && selectedMatch._id })',
      cancelScore: 'stopStream({ matchId: selectedMatch && selectedMatch._id })',
      voidStreamOf: 'stopStream({ void: true, matchId })',
      resetAll: 'stopStream(selectedMatch ? { matchId: selectedMatch._id } : undefined)',
    };
    for (const [fn, call] of Object.entries(want)) {
      if (!body(fn).includes(call)) throw new Error(fn + ' 에 ' + call + ' 없음');
      if (/stopStream\(\);/.test(body(fn))) throw new Error(fn + ' 에 id 없는 stopStream() 이 남았다');
    }
  });

  await t('④ 목록으로 나가면(팀·PAR 화면에서 뒤로) 이 매치 방송을 끈다 — 점수 화면 → 팀 화면은 그대로', () => {
    const b = body('goBack');
    const i = b.indexOf('if(curStep===2 || curStep===3)');
    const k = b.indexOf('else if(curStep===4)');
    if (i < 0 || k < 0) throw new Error('goBack 모양이 바뀌었다');
    const toList = b.slice(i, k), toTeams = b.slice(k);
    if (!/stopStream\(\{ matchId: selectedMatch\._id \}\)/.test(toList)) throw new Error('목록으로 갈 때 끄지 않는다');
    if (toList.indexOf('stopStream(') > toList.indexOf('selectedMatch=null')) throw new Error('selectedMatch 를 비운 뒤에 끈다(id 가 없다)');
    if (/stopStream\(/.test(toTeams)) throw new Error('팀 다시 뽑으러 갈 때(점수 → 팀) 방송을 끄면 안 된다 — 같은 경기다');
  });

  await t('③ 서버가 다른 경기라 그대로 뒀으면(skipped) LIVE·앱 배너를 끄지 않는다', async () => {
    const ctx = mkCtx({ active: true, match: { _id: 'm39' } });
    ctx.fetch = (url, o) => { ctx.calls.push([url, o.body]); return Promise.resolve({ json: async () => ({ success: true, skipped: true }) }); };
    const tab = tablet(ctx);
    const d = await tab.stopStream({ matchId: 'm39' });
    if (!d || !d.skipped) throw new Error('결과를 돌려주지 않는다');
    if (!tab.get()._streamActive) throw new Error('_streamActive 를 껐다');
    if (ctx.puts.some((p) => /isLive/.test(p[1] || ''))) throw new Error('앱 라이브 배너를 껐다: ' + JSON.stringify(ctx.puts));
    ctx.timers.forEach((fn) => fn());
    if (!ctx.healthChecks) throw new Error('서버 상태를 다시 읽지 않는다');
  });

  await t('③ 보통 끄기는 예전처럼 LIVE·앱 배너를 끈다 · 서버 경고는 그대로 보여 준다', async () => {
    const ctx = mkCtx({ active: true, match: { _id: 'm41' } });
    ctx.fetch = (url, o) => Promise.resolve({ json: async () => ({ success: true, warnings: ['OBS: 송출을 끄지 못했어요(OBS 를 확인해 주세요)'] }) });
    const tab = tablet(ctx);
    await tab.stopStream({ matchId: 'm41' });
    if (tab.get()._streamActive) throw new Error('_streamActive 가 남았다');
    if (!ctx.puts.some((p) => p[0] === '/league/update/L1' && /"isLive":false/.test(p[1]))) throw new Error('isLive:false 없음');
    if (!ctx.toasts.some((m) => m.includes('OBS: 송출을 끄지 못했어요'))) throw new Error('경고를 삼켰다: ' + JSON.stringify(ctx.toasts));
  });

  await t('① 같은 경기 이어 쓰기·다음 경기 넘겨받기를 다르게 알린다', async () => {
    for (const [resp, word] of [[{ reused: true }, '이어서'], [{ took_over: true }, '앞 경기 방송은 끝냈어요'], [{}, '라이브 시작됨']]) {
      const ctx = mkCtx({ match: { _id: 'm41' } });
      ctx.fetch = () => Promise.resolve({ json: async () => Object.assign({ success: true, watch_url: 'https://youtu.be/v' }, resp) });
      await tablet(ctx).startStream();
      if (!ctx.toasts.some((m) => m.includes(word))) throw new Error(JSON.stringify(resp) + ' → ' + JSON.stringify(ctx.toasts));
    }
  });

  await t('⑤ 매치별 영상 주소 — 옛 한 칸 모양도 읽고, 20개까지, 지우기', () => {
    const ctx = mkCtx({});
    ctx.store.ps_stream_of = JSON.stringify({ matchId: 'm38', watchUrl: 'https://youtu.be/v38' });
    const tab = tablet(ctx);
    if (tab.streamOfGet('m38') !== 'https://youtu.be/v38') throw new Error('옛 모양을 못 읽는다');
    tab.streamOfSet('m39', 'https://youtu.be/v39');
    tab.streamOfSet('m41', 'https://youtu.be/v41');
    if (tab.streamOfGet('m39') !== 'https://youtu.be/v39' || tab.streamOfGet('m38') !== 'https://youtu.be/v38') throw new Error('다음 경기가 앞 경기 주소를 덮었다');
    for (let i = 0; i < 25; i++) tab.streamOfSet('x' + i, 'https://youtu.be/x' + i);
    const all = JSON.parse(ctx.store.ps_stream_of);
    if (Object.keys(all).length !== 20 || 'm38' in all || !('x24' in all)) throw new Error('20개 상한(오래된 것부터): ' + Object.keys(all).join(','));
    tab.streamOfDel('x24');
    if (tab.streamOfGet('x24') !== '') throw new Error('지우기');
    ctx.store.ps_stream_of = '{깨진 json';
    if (tab.streamOfGet('m39') !== '') throw new Error('깨진 값에서 던졌다');
    ctx.store.ps_stream_of = JSON.stringify(['a']);
    if (tab.streamOfGet('0') !== '') throw new Error('배열을 맵으로 읽었다');
  });

  await t('⑤ 다음 경기가 넘겨받은 뒤 앞 경기 [매치 취소] — 지금 방송은 두고 앞 경기 영상에 [무효]', async () => {
    const ctx = mkCtx({ active: true, match: { _id: 'm41' } });
    ctx.fetch = (url, o) => {
      ctx.calls.push([url.replace('http://obs', ''), o.body]);
      if (url.endsWith('/stop-stream')) return Promise.resolve({ json: async () => ({ success: true, skipped: true }) });
      return Promise.resolve({ json: async () => ({ success: true, voided: 'v39' }) });
    };
    const tab = tablet(ctx);
    tab.streamOfSet('m39', 'https://youtu.be/v39');
    tab.streamOfSet('m41', 'https://youtu.be/v41');
    await tab.voidStreamOf('m39');
    const stop = ctx.calls.find((c) => c[0] === '/stop-stream');
    if (!stop || !/"matchId":"m39"/.test(stop[1]) || !/"void":true/.test(stop[1])) throw new Error('끄기 몸: ' + JSON.stringify(ctx.calls));
    const vv = ctx.calls.find((c) => c[0] === '/void-video');
    if (!vv || !/v39/.test(vv[1]) || /v41/.test(vv[1])) throw new Error('앞 경기 영상이 아닌 데 [무효]: ' + JSON.stringify(ctx.calls));
    if (!tab.get()._streamActive) throw new Error('지금 경기 방송 표시를 껐다');
    if (tab.streamOfGet('m39') !== '' || tab.streamOfGet('m41') === '') throw new Error('주소 정리가 엇나갔다');
  });

  await t('⑥ 상태 줄에 지금 방송의 경기 번호 — 리그만, 자유 라이브는 그대로', () => {
    const ctx = mkCtx({});
    const tab = tablet(ctx);
    tab.setLive(39, 'league');
    tab._updateStreamStatus('live');
    if (ctx.els['stream-status'].textContent !== '🔴 LIVE · Match 39') throw new Error(ctx.els['stream-status'].textContent);
    tab.setLive(0, 'free');
    tab._updateStreamStatus('live');
    if (ctx.els['stream-status'].textContent !== '🔴 LIVE') throw new Error(ctx.els['stream-status'].textContent);
    if (!body('checkStreamServer').includes('_liveMatchNum = data.streaming ? (Number(data.match_number) || 0) : 0')) throw new Error('/health 의 match_number 를 안 읽는다');
  });

  await t('페이지 스크립트 전체가 문법 오류 없이 뜬다(떼어 낸 함수만 보면 다른 자리 오류를 놓친다)', () => {
    const re = /<script>([\s\S]*?)<\/script>/g;
    let m, n = 0;
    while ((m = re.exec(src))) { new vm.Script(m[1], { filename: 'ps_court_playus.html#script' + (++n) }); }
    if (n < 2) throw new Error('일반 스크립트가 ' + n + '개뿐');
  });

  console.log(fails ? `\n실패 ${fails}건\n` : '\n전부 통과\n');
  process.exit(fails ? 1 : 0);
})();
