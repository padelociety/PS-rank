// 끝까지 못 친 경기 — 매치는 지우고 영상은 [무효] 로 남긴다 (c8 · 2026-10-01).
//   실행: node tests/void_video.test.cjs
//
// 사용자: "매치를 못끝낸 경기는 삭제되어야 하는게 맞아, PAR나 전적 순위에서 다빠져야 하는게 맞고.
//          그런데 동영상만 남기고 싶은거거든. 대신 [무효] 가 제목이나 설명에 붙어야 하는거지."
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'ps_court', 'ps_court_playus.html'), 'utf8');

let fails = 0;
const t = (name, fn) => { try { fn(); console.log('  ok   ' + name); } catch (e) { fails++; console.log('  FAIL ' + name + ' — ' + e.message); } };
const body = (name) => {
  const i = src.indexOf('async function ' + name + '(');
  if (i < 0) throw new Error(name + ' 없음');
  let j = src.indexOf('{', i), d = 0;
  for (; j < src.length; j++) { if (src[j] === '{') d++; else if (src[j] === '}' && --d === 0) return src.slice(i, j + 1); }
  throw new Error(name + ' 끝 없음');
};

t('매치 취소는 여전히 매치를 지운다(DELETE) — 순위·PAR·전적에서 빠진다', () => {
  const b = body('cancelMatch');
  if (!b.includes("method: 'DELETE'")) throw new Error('DELETE 가 없다');
  if (!b.includes('voidStreamOf(selectedMatch._id)')) throw new Error('영상 [무효] 를 안 부른다');
  if (/stopStream\(\);/.test(b)) throw new Error('void 없이 방송만 끈다');
});

t('방송 중이면 끄면서 void · 끝났으면 기억한 이 매치 영상에 /void-video', () => {
  const b = body('voidStreamOf');
  if (!b.includes('stopStream({ void: true })')) throw new Error('stopStream void');
  if (!b.includes('/void-video')) throw new Error('/void-video');
  if (!b.includes('saved.matchId === matchId')) throw new Error('다른 매치 영상에 붙이면 안 된다');
});

t('옛 서버(voided 없음)면 직접 붙이라고 말한다', () => {
  const b = body('voidStreamOf');
  if (!b.includes('data && data.voided')) throw new Error('voided 확인');
  if (!b.includes('유튜브 스튜디오에서 직접')) throw new Error('안내 문구');
});

t('방송 시작 때 이 매치의 영상 주소를 기억한다', () => {
  if (!/ps_stream_of', JSON\.stringify\(\{ matchId: selectedMatch\._id, watchUrl: data\.watch_url \}\)/.test(src)) throw new Error('저장 없음');
});

t('스코어 화면 취소(예정으로 되돌리기)는 [무효] 를 붙이지 않는다 — 다시 칠 경기다', () => {
  const b = body('cancelScore');
  if (b.includes('void')) throw new Error('cancelScore 가 void 를 건다');
});

console.log(fails ? `\n실패 ${fails}건\n` : '\n전부 통과\n');
process.exit(fails ? 1 : 0);
