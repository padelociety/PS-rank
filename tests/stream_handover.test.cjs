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
    ${src.match(/var _stoppingFor = '';[^\n]*/)[0]}
    ${src.match(/var _starts = \[\];[^\n]*/)[0]}
    ${src.match(/var _starting = null;[^\n]*/)[0]}
    ${src.match(/var _startSeq = 0;[^\n]*/)[0]}
    ${['_updateStreamStatus', 'startStream', '_startStreamOnce', 'stopStream', '_stopStreamOnce', 'voidStreamOf',
       'streamOfAll', 'streamOfGet', 'streamOfSet', 'streamOfDel'].map(body).join('\n')}
    return {
      startStream, stopStream, voidStreamOf, streamOfGet, streamOfSet, streamOfDel, _updateStreamStatus,
      get: () => ({ _streamActive, _liveMatchId, _liveMatchNum, _liveMode, starting: _starting, starts: _starts }),
      setLive: (num, mode, id) => { _liveMatchNum = num; _liveMode = mode; if (id !== undefined) _liveMatchId = id; },
      leave: () => { selectedMatch = null; },
      swap: (m) => { selectedMatch = m; },
      setOnline: (v) => { _streamServerOnline = v; },
      setActive: (v) => { _streamActive = v; },
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

  await t('⑦ 켜는 중(10~20초)에 [매치 취소] — 끄기를 버리지 않고 다 켜진 뒤 그 경기 방송을 끄고 [무효] (검토)', async () => {
    let releaseStart;
    const ctx = mkCtx({ match: { _id: 'm40' } });
    ctx.fetch = (url, o) => {
      ctx.calls.push([url.replace('http://obs', ''), o.body]);
      if (url.endsWith('/start-stream')) return new Promise((r) => { releaseStart = () => r({ json: async () => ({ success: true, watch_url: 'https://youtu.be/v40', broadcast_id: 'v40' }) }); });
      if (url.endsWith('/stop-stream')) return Promise.resolve({ json: async () => ({ success: true, voided: 'v40' }) });
      return Promise.resolve({ json: async () => ({ success: true }) });
    };
    const tab = tablet(ctx);
    const pStart = tab.startStream();                 // goToScore — 기다리지 않는다
    await new Promise((r) => setImmediate(r));
    if (!tab.get().starting || tab.get().starting.matchId !== 'm40') throw new Error('켜는 중 표시가 없다');
    const pVoid = tab.voidStreamOf('m40');            // 바로 [매치 취소]
    tab.leave();                                      // cancelMatch → resetAll 이 선택을 비운다
    await new Promise((r) => setImmediate(r));
    if (ctx.calls.some((c) => c[0] === '/stop-stream')) throw new Error('켜기가 끝나기 전에 끄기를 보냈다(서버는 아직 방송 없음)');
    releaseStart();
    await Promise.all([pStart, pVoid]);
    ctx.timers.forEach((fn) => fn());
    await new Promise((r) => setImmediate(r));
    const stop = ctx.calls.find((c) => c[0] === '/stop-stream');
    if (!stop || !/"matchId":"m40"/.test(stop[1]) || !/"void":true/.test(stop[1])) throw new Error('끄기 몸: ' + JSON.stringify(ctx.calls));
    if (tab.get()._streamActive) throw new Error('방송 표시가 남았다');
  });

  await t('⑦ 끄기를 기다리는 사이 그 매치를 떠나면 켜지 않는다', async () => {
    let releaseStop;
    const ctx = mkCtx({ active: true, match: { _id: 'm40' } });
    ctx.fetch = (url, o) => {
      ctx.calls.push([url.replace('http://obs', ''), o.body]);
      if (url.endsWith('/stop-stream')) return new Promise((r) => { releaseStop = () => r({ json: async () => ({ success: true }) }); });
      return Promise.resolve({ json: async () => ({ success: true, watch_url: 'w' }) });
    };
    const tab = tablet(ctx);
    const pStop = tab.stopStream({ matchId: 'm39' });
    const pStart = tab.startStream();
    tab.leave();
    releaseStop();
    await Promise.all([pStop, pStart]);
    if (ctx.calls.some((c) => c[0] === '/start-stream')) throw new Error('떠난 매치의 방송을 켰다');
  });

  await t('⑦ 켜는 사이 매치를 떠났는데 끄기를 부른 곳이 없으면 — 켜진 뒤 스스로 끈다', async () => {
    let releaseStart;
    const ctx = mkCtx({ match: { _id: 'm40' } });
    ctx.fetch = (url, o) => {
      ctx.calls.push([url.replace('http://obs', ''), o.body]);
      if (url.endsWith('/start-stream')) return new Promise((r) => { releaseStart = () => r({ json: async () => ({ success: true, watch_url: 'https://youtu.be/v40' }) }); });
      return Promise.resolve({ json: async () => ({ success: true }) });
    };
    const tab = tablet(ctx);
    const pStart = tab.startStream();
    await new Promise((r) => setImmediate(r));
    tab.leave();
    releaseStart();
    await pStart;
    ctx.timers.forEach((fn) => fn());
    await new Promise((r) => setImmediate(r));
    const stop = ctx.calls.find((c) => c[0] === '/stop-stream');
    if (!stop || !/"matchId":"m40"/.test(stop[1])) throw new Error(JSON.stringify(ctx.calls));
    if (tab.streamOfGet('m40') !== 'https://youtu.be/v40') throw new Error('그 경기 영상 주소를 기억하지 않았다');
  });

  await t('⑧ 끄기 응답이 시간 초과여도 앱 라이브 배너(isLive:false)는 끄고 서버 상태를 다시 읽는다 (검토)', async () => {
    const ctx = mkCtx({ active: true, match: { _id: 'm41' } });
    ctx.fetch = () => Promise.reject(Object.assign(new Error('The operation was aborted due to timeout'), { name: 'TimeoutError' }));
    const tab = tablet(ctx);
    await tab.stopStream({ matchId: 'm41' });
    if (!ctx.puts.some((p) => p[0] === '/league/update/L1' && /"isLive":false/.test(p[1]))) throw new Error('isLive:false 없음');
    ctx.timers.forEach((fn) => fn());
    if (!ctx.healthChecks) throw new Error('서버 상태를 다시 묻지 않는다');
    if (ctx.els['stream-status'] && /서버 꺼짐/.test(ctx.els['stream-status'].textContent)) throw new Error("'서버 꺼짐' 으로 단정했다");
  });

  await t('⑧ 기다리는 시간 — 켜기 60초 · 끄기 45초(서버가 OBS 멈춤을 다시 붙어 확인하고 답한다 — 최악 약 33초)', () => {
    if (!body('_startStreamOnce').includes('signal: AbortSignal.timeout(60000),')) throw new Error('켜기 60초');
    if (!/signal: AbortSignal.timeout\(45000\),/.test(body('_stopStreamOnce'))) throw new Error('끄기 45초');
  });

  await t('⑧ 끄기가 실패하면 상태 줄이 LIVE 로 남지 않는다(⚠️ 오류 — 서버 상태를 다시 읽을 때까지) (검토 2차)', async () => {
    const ctx = mkCtx({ active: true, match: { _id: 'm41' } });
    ctx.fetch = () => Promise.reject(Object.assign(new Error('timeout'), { name: 'TimeoutError' }));
    const tab = tablet(ctx);
    tab.setLive(41, 'league');
    tab._updateStreamStatus('live');
    await tab.stopStream({ matchId: 'm41' });
    const txt = ctx.els['stream-status'].textContent;
    if (/LIVE/.test(txt)) throw new Error('LIVE 가 남았다: ' + txt);
    if (!/오류/.test(txt)) throw new Error('오류 표시가 아니다: ' + txt);
  });

  await t('⑨ 켜는 중 [취소] → 같은 경기 다시 켜기 — 기다리던 끄기가 새 방송을 끄지 않는다 (검토 2차)', async () => {
    const releases = [];
    const ctx = mkCtx({ match: { _id: 'm40' } });
    ctx.fetch = (url, o) => {
      ctx.calls.push([url.replace('http://obs', ''), o.body]);
      if (url.endsWith('/start-stream')) return new Promise((r) => releases.push(() => r({ json: async () => ({ success: true, watch_url: 'https://youtu.be/v40', reused: releases.length > 1 }) })));
      return Promise.resolve({ json: async () => ({ success: true }) });
    };
    const tab = tablet(ctx);
    const p1 = tab.startStream();                       // goToScore
    await new Promise((r) => setImmediate(r));
    const pStop = tab.stopStream({ matchId: 'm40' });   // cancelScore — 켜기를 기다린다
    const p2 = tab.startStream();                       // 곧바로 다시 [시작]
    await new Promise((r) => setImmediate(r));
    while (releases.length) releases.shift()();          // 먼저 보낸 켜기부터 끝난다
    await new Promise((r) => setImmediate(r));
    while (releases.length) releases.shift()();
    await Promise.all([p1, pStop, p2]);
    ctx.timers.forEach((fn) => fn());
    await new Promise((r) => setImmediate(r));
    if (ctx.calls.some((c) => c[0] === '/stop-stream')) throw new Error('다시 켠 방송을 옛 끄기가 껐다: ' + JSON.stringify(ctx.calls.map((c) => c[0])));
    if (!tab.get()._streamActive) throw new Error('방송 표시가 꺼졌다');
  });

  await t('⑨ 끄기를 기다리는 사이 같은 매치를 다시 읽어 와도(객체가 바뀜) 켠다 — 매치는 id 로 본다 (검토 2차)', async () => {
    let releaseStop;
    const ctx = mkCtx({ active: true, match: { _id: 'm40' } });
    ctx.fetch = (url, o) => {
      ctx.calls.push([url.replace('http://obs', ''), o.body]);
      if (url.endsWith('/stop-stream')) return new Promise((r) => { releaseStop = () => r({ json: async () => ({ success: true }) }); });
      return Promise.resolve({ json: async () => ({ success: true, watch_url: 'w' }) });
    };
    const tab = tablet(ctx);
    const pStop = tab.stopStream({ matchId: 'm39' });
    const pStart = tab.startStream();
    tab.swap({ _id: 'm40', fresh: true });             // fetchFreshMatch — 같은 매치, 새 객체
    releaseStop();
    await Promise.all([pStop, pStart]);
    const st = ctx.calls.find((c) => c[0] === '/start-stream');
    if (!st || !/"matchId":"m40"/.test(st[1])) throw new Error('같은 매치인데 켜지 않았다: ' + JSON.stringify(ctx.calls.map((c) => c[0])));
  });

  await t('⑨ 켜기가 실패한 경기의 [매치 취소] — 영상이 없으니 [무효] 를 \'직접 붙이라\' 고 하지 않는다 (검토 2차)', async () => {
    let releaseStart;
    const ctx = mkCtx({ match: { _id: 'm40' } });
    ctx.fetch = (url, o) => {
      ctx.calls.push([url.replace('http://obs', ''), o.body]);
      if (url.endsWith('/start-stream')) return new Promise((r) => { releaseStart = () => r({ json: async () => ({ success: false, error: 'OBS 연결 실패' }) }); });
      return Promise.resolve({ json: async () => ({ success: true }) });
    };
    const tab = tablet(ctx);
    const pStart = tab.startStream();
    await new Promise((r) => setImmediate(r));
    const pVoid = tab.voidStreamOf('m40');
    tab.leave();
    releaseStart();
    await Promise.all([pStart, pVoid]);
    ctx.timers.forEach((fn) => fn());
    if (ctx.calls.some((c) => c[0] === '/stop-stream' || c[0] === '/void-video')) throw new Error('없는 방송을 끄거나 [무효] 를 보냈다: ' + JSON.stringify(ctx.calls.map((c) => c[0])));
    if (ctx.toasts.some((m) => /무효/.test(m))) throw new Error('[무효] 안내를 띄웠다: ' + JSON.stringify(ctx.toasts));
  });

  await t('⑩ 켜는 중 [취소] → 서버 꺼짐에서 다시 [시작](보내지 않고 끝남) → [취소] — 앞 켜기가 끝나면 끈다 (검토 2차 P1)', async () => {
    let releaseStart;
    const ctx = mkCtx({ match: { _id: 'm40' } });
    ctx.fetch = (url, o) => {
      ctx.calls.push([url.replace('http://obs', ''), o.body]);
      if (url.endsWith('/start-stream')) return new Promise((r) => { releaseStart = () => r({ json: async () => ({ success: true, watch_url: 'https://youtu.be/v40' }) }); });
      return Promise.resolve({ json: async () => ({ success: true }) });
    };
    const tab = tablet(ctx);
    const p1 = tab.startStream();
    await new Promise((r) => setImmediate(r));
    const s1 = tab.stopStream({ matchId: 'm40' });     // [취소] — 앞 켜기를 기다린다
    tab.setOnline(false);                              // /health 두 번 실패
    await tab.startStream();                           // 다시 [시작] — 보내지 않고 곧바로 끝난다
    if (!tab.get().starts.some((s) => s.matchId === 'm40' && s.sent)) throw new Error('곧바로 끝난 켜기가 앞 켜기 기억을 지웠다');
    const s2 = tab.stopStream({ matchId: 'm40' });     // 다시 [취소]
    tab.setOnline(true);
    releaseStart();
    await Promise.all([p1, s1, s2]);
    const stops = ctx.calls.filter((c) => c[0] === '/stop-stream');
    if (stops.length !== 1) throw new Error('/stop-stream ' + stops.length + '번: ' + JSON.stringify(ctx.calls.map((c) => c[0])));
    if (tab.get()._streamActive) throw new Error('방송이 남았다');
  });

  await t('⑩ 앞 경기 끄기를 기다리는 켜기 — 같은 경기 [취소]·뒤로가 오면 보내지 않는다(몇 초짜리 영상 없음) (검토 2차 P3)', async () => {
    let releaseStop;
    const ctx = mkCtx({ active: true, match: { _id: 'm40' } });
    ctx.fetch = (url, o) => {
      ctx.calls.push([url.replace('http://obs', ''), o.body]);
      if (url.endsWith('/stop-stream')) return new Promise((r) => { releaseStop = () => r({ json: async () => ({ success: true }) }); });
      return Promise.resolve({ json: async () => ({ success: true, watch_url: 'w' }) });
    };
    const tab = tablet(ctx);
    const pStop = tab.stopStream({ matchId: 'm39' });  // 앞 경기 저장
    const pStart = tab.startStream();                  // m40 켜기 — 끄기를 기다린다
    const pCancel = tab.stopStream({ matchId: 'm40' });// m40 [취소]·뒤로
    tab.swap({ _id: 'm40', fresh: true });             // 다시 같은 매치로 들어온다(새 객체)
    releaseStop();
    await Promise.all([pStop, pStart, pCancel]);
    ctx.timers.forEach((fn) => fn());
    await new Promise((r) => setImmediate(r));
    const order = ctx.calls.map((c) => c[0]);
    if (order.join(',') !== '/stop-stream') throw new Error('순서: ' + order.join(','));
  });

  await t('⑩ 켜는 사이 /health 가 \'방송 중\' 을 읽었고 켜기는 실패 — [매치 취소] 는 서버 idle 답을 보고 조용히 (검토 2차 P2)', async () => {
    let releaseStart;
    const ctx = mkCtx({ match: { _id: 'm40' } });
    ctx.fetch = (url, o) => {
      ctx.calls.push([url.replace('http://obs', ''), o.body]);
      if (url.endsWith('/start-stream')) return new Promise((r) => { releaseStart = () => r({ json: async () => ({ success: false, error: 'OBS 가 이전 방송을 계속 내보내고 있어' }) }); });
      if (url.endsWith('/stop-stream')) return Promise.resolve({ json: async () => ({ success: true, idle: true, message: '스트리밍 중이 아니에요' }) });
      return Promise.resolve({ json: async () => ({ success: true }) });
    };
    const tab = tablet(ctx);
    const pStart = tab.startStream();
    await new Promise((r) => setImmediate(r));
    tab.setActive(true);                              // 30초 주기 /health 가 켜는 중의 서버 상태를 읽었다
    const pVoid = tab.voidStreamOf('m40');
    tab.leave();
    releaseStart();
    await Promise.all([pStart, pVoid]);
    ctx.timers.forEach((fn) => fn());
    if (ctx.toasts.some((m) => /무효|종료됨/.test(m))) throw new Error('없는 방송을 끝냈다·[무효] 안내: ' + JSON.stringify(ctx.toasts));
    if (ctx.calls.some((c) => c[0] === '/void-video')) throw new Error('/void-video 를 보냈다');
    if (!ctx.healthChecks) throw new Error('켜기 실패 뒤 서버 상태를 다시 읽지 않는다');
  });

  await t('⑩ 끄기 응답이 JSON 이 아닌 500 이면 \'껐다\' 고 하지 않는다(오류 표시 · 서버 다시 읽기) (검토 2차)', async () => {
    const ctx = mkCtx({ active: true, match: { _id: 'm41' } });
    ctx.fetch = () => Promise.resolve({ ok: false, status: 500, json: async () => { throw new SyntaxError('Unexpected token <'); } });
    const tab = tablet(ctx);
    tab.setLive(41, 'league');
    await tab.stopStream({ matchId: 'm41' });
    if (ctx.toasts.some((m) => /종료됨/.test(m))) throw new Error('종료됨 이라 했다: ' + JSON.stringify(ctx.toasts));
    if (!/오류/.test(ctx.els['stream-status'].textContent)) throw new Error(ctx.els['stream-status'].textContent);
    ctx.timers.forEach((fn) => fn());
    if (!ctx.healthChecks) throw new Error('서버 상태를 다시 읽지 않는다');
  });

  await t('⑩ [무효] 따로 붙이기(/void-video)는 60초 기다린다 — 서버에서 다음 경기 넘겨받기 뒤에 설 수 있다', () => {
    const b = body('voidStreamOf');
    const i = b.indexOf('/void-video');
    if (i < 0 || !/signal: AbortSignal\.timeout\(60000\)/.test(b.slice(i))) throw new Error('/void-video 60초');
  });

  await t('⑪ 다른 경기 끄기가 도는 중의 [취소] — 거기 얹히지 않고 끝나길 기다렸다 이 경기 방송을 끈다 (검토 3차 S21)', async () => {
    const releases = [];
    const ctx = mkCtx({ active: true, match: { _id: 'mA' } });
    ctx.fetch = (url, o) => {
      ctx.calls.push([url.replace('http://obs', ''), o.body]);
      if (url.endsWith('/stop-stream')) {
        const skipped = /"matchId":"mZ"/.test(o.body);
        return new Promise((r) => releases.push(() => r({ json: async () => (skipped ? { success: true, skipped: true } : { success: true }) })));
      }
      return Promise.resolve({ json: async () => ({ success: true }) });
    };
    const tab = tablet(ctx);
    tab.setLive(0, 'league');
    const pZ = tab.stopStream({ matchId: 'mZ' });       // 떠난 경기 Z 의 자동 끄기 — 서버는 A 방송이라 skipped
    const pA = tab.stopStream({ matchId: 'mA' });       // A [취소]
    await new Promise((r) => setImmediate(r));
    releases.shift()();
    for (let i = 0; i < 5 && !releases.length; i++) await new Promise((r) => setImmediate(r));
    if (!releases.length) throw new Error('A 의 끄기를 보내지 않았다(Z 의 답을 돌려받았다): ' + JSON.stringify(ctx.calls.map((c) => c[1])));
    releases.shift()();
    await Promise.all([pZ, pA]);
    const stops = ctx.calls.filter((c) => c[0] === '/stop-stream').map((c) => JSON.parse(c[1]).matchId);
    if (stops.join(',') !== 'mZ,mA') throw new Error('끄기: ' + stops.join(','));
    if (tab.get()._streamActive) throw new Error('A 방송 표시가 남았다');
  });

  await t('⑪ 같은 경기 끄기가 도는 중이면 한 번만 보낸다(같은 약속) — [매치 취소] 의 void 뒤 resetAll', async () => {
    let release;
    const ctx = mkCtx({ active: true, match: { _id: 'mA' } });
    ctx.fetch = (url, o) => { ctx.calls.push([url, o.body]); return new Promise((r) => { release = () => r({ json: async () => ({ success: true, voided: 'vA' }) }); }); };
    const tab = tablet(ctx);
    const p1 = tab.stopStream({ void: true, matchId: 'mA' });
    const p2 = tab.stopStream({ matchId: 'mA' });
    await new Promise((r) => setImmediate(r));
    release();
    const [d1, d2] = await Promise.all([p1, p2]);
    if (ctx.calls.length !== 1) throw new Error('/stop-stream ' + ctx.calls.length + '번');
    if (d1 !== d2) throw new Error('같은 약속이 아니다');
  });

  await t('⑪ 저장 → 다시하기 → 앞 경기를 끄는 사이 [매치 취소] — 방송한 적 없는 경기라 [무효] 안내 없음 (검토 3차 S8)', async () => {
    let releaseStop;
    const ctx = mkCtx({ active: true, match: { _id: 'm39' } });
    ctx.fetch = (url, o) => {
      ctx.calls.push([url.replace('http://obs', ''), o.body]);
      if (url.endsWith('/stop-stream')) return new Promise((r) => { releaseStop = () => r({ json: async () => ({ success: true }) }); });
      return Promise.resolve({ json: async () => ({ success: true, watch_url: 'https://youtu.be/v40' }) });
    };
    const tab = tablet(ctx);
    tab.setLive(39, 'league', 'm39');                  // 지금 방송은 m39(태블릿이 켰다)
    tab.streamOfSet('m39', 'https://youtu.be/v39');
    const pStop = tab.stopStream({ matchId: 'm39' });  // showDone
    tab.swap({ _id: 'm40' });                          // 다른 페어로 다시하기
    const pStart = tab.startStream();                  // m40 — 앞 끄기를 기다린다
    const pVoid = tab.voidStreamOf('m40');             // [매치 취소]
    tab.leave();
    releaseStop();
    await Promise.all([pStop, pStart, pVoid]);
    ctx.timers.forEach((fn) => fn());
    const order = ctx.calls.map((c) => c[0]);
    if (order.join(',') !== '/stop-stream') throw new Error('보낸 것: ' + order.join(','));
    if (ctx.toasts.some((m) => /무효/.test(m))) throw new Error('[무효] 안내를 띄웠다: ' + JSON.stringify(ctx.toasts));
    if (tab.streamOfGet('m39') !== 'https://youtu.be/v39') throw new Error('앞 경기 영상 주소를 건드렸다');
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
