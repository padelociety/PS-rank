"""
리그앱 하이라이트 [직전 랠리] · [60초] (2026-11-11 · 태블릿 c20) — OBS·백엔드 없이 돈다.

    python tests/test_highlight_rally.py

사용자: "하이라이트는 직전랠리, 60초 이 두개로만 하자. 직전랠리는 인스타에 자동으로 올라가는거랑 앱 하이라이트에서 볼 수 있는거
둘다 가능하고 60초는 하이라이트에만 올라가는거고." 여기서 못 박는 것:
  ① POST /highlight — {mode:'rally'} 는 rally 길 · {seconds:60} 은 seconds 길(옛 태블릿 30·90 도) · 응답 mode · instagram.
  ② [60초] — 뒤쪽 60초를 하이라이트에만 · 스토리 파일을 만들지 않는다.
  ③ [직전 랠리] — 마지막 랠리로 자른 파일을 하이라이트로 올리고 **같은 파일**을 스토리로(autoPost=1) · 자르기 실패면 60초 하이라이트만(인스타 없음) ·
     auto_story: false 면 하이라이트만.
  ④ 스토리 업로드 — autoPost 칸은 직전 랠리일 때만 · 백엔드 게시 상태를 기억한다(태블릿 토스트).
  ⑤ 태블릿 — 버튼 둘(직전 랠리 · 60초) · 보내는 본문 · 직전 랠리는 미리보기 대신 결과 토스트.
"""
import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from test_stream_server import load_server  # noqa: E402

fails = []
n_ok = [0]


def check(cond, msg, detail=''):
    if cond:
        n_ok[0] += 1
        print('  ok   ' + msg)
    else:
        print('  FAIL ' + msg + (('\n        ' + str(detail)) if detail else ''))
        fails.append(msg)


ss = load_server()
cl = ss.app.test_client()

print('\n[①] POST /highlight')
threads = []


class FakeThread:
    def __init__(self, target=None, args=(), daemon=None, **k):
        self.target, self.args = target, args

    def start(self):
        threads.append((self.target.__name__, self.args))


real_thread = ss.threading.Thread
ss.threading.Thread = FakeThread
try:
    d = cl.post('/highlight', json={'mode': 'rally'}).get_json()
    check(d['success'] and d['mode'] == 'rally' and d['instagram'] is True and threads[-1][0] == '_save_and_upload_highlight'
          and threads[-1][1][1:] == (0, 'rally'), "직전 랠리 → rally 길 · 인스타 자동", (d, threads[-1]))
    d = cl.post('/highlight', json={'seconds': 60}).get_json()
    check(d['mode'] == 'seconds' and d['seconds'] == 60 and d['instagram'] is False and threads[-1][1][1:] == (60, 'seconds'),
          '60초 → seconds 길 · 인스타 없음', (d, threads[-1]))
    d = cl.post('/highlight', json={'seconds': 90}).get_json()
    check(d['mode'] == 'seconds' and threads[-1][1][1:] == (90, 'seconds'), '옛 태블릿 90초도 받는다(하이라이트만)', threads[-1])
    d = cl.post('/highlight', json={'mode': 'rally', 'seconds': 30}).get_json()
    check(threads[-1][1][1:] == (0, 'rally'), '직전 랠리는 seconds 를 무시한다', threads[-1])
    d = cl.post('/highlight', json={'seconds': 'abc'}).get_json()
    check(d['success'] and threads[-1][1][1:] == (0, 'seconds'), '이상한 seconds → 버퍼 전체(던지지 않는다)', threads[-1])
    ss.HIGHLIGHT_CFG['auto_story'] = False
    d = cl.post('/highlight', json={'mode': 'rally'}).get_json()
    check(d['instagram'] is False, 'auto_story: false 면 응답이 인스타 없음을 말한다', d)
    ss.HIGHLIGHT_CFG.pop('auto_story', None)
finally:
    ss.threading.Thread = real_thread
src = open(os.path.join(ROOT, 'stream_server.py'), encoding='utf-8').read()
i = src.index("@app.route('/highlight', methods=['POST'])")
check(src[i:i + 80].split('\n')[1].strip() == '@_require_operator', '운영자 확인')

print('\n[②③] 업로드 길')
log = []
tmp = tempfile.mkdtemp(prefix='hlr_')
raw = os.path.join(tmp, 'Replay.mp4')
open(raw, 'wb').write(b'x')
ss._trim_clip = lambda p, sec: (log.append(('trim', sec)), p + f'_{sec}s')[1]
ss._faststart_clip = lambda p: (log.append(('fs',)), p)[1]
ss._post_highlight = lambda p, meta: (log.append(('post', os.path.basename(p))), 'hid1')[1]
ss._upload_story_file = lambda hid, out, win, auto_post=False: log.append(('story', hid, os.path.basename(out), auto_post, win.get('why')))
meta = {'title': 'Match 7', 'recordedAt': '2026-11-11T10:00:00+00:00'}

log.clear()
ss._upload_highlight(raw, meta, 60)
check(log == [('trim', 60), ('post', 'Replay.mp4_60s')], '60초 — 뒤쪽 60초를 하이라이트에만(스토리 없음)', log)

log.clear()
ss._story_clip = lambda p, sec: (os.path.join(tmp, 'Replay_story.mp4'), {'start': 26.3, 'serve': 29.3, 'why': 'rally', 'duration': 58.2})
ss._upload_rally_highlight(raw, meta)
check(log == [('post', 'Replay_story.mp4'), ('story', 'hid1', 'Replay_story.mp4', True, 'rally')],
      '직전 랠리 — 자른 파일을 하이라이트로 · 같은 파일을 스토리로 자동 게시', log)
item = next(x for x in ss._recent_highlights if x['id'] == 'hid1')
check(item['path'].endswith('Replay_story.mp4') and item['seconds'] == 31, '최근 클립 = 자른 파일 · 길이 31초(58.2 − 26.3)', item)

log.clear()
def boom(p, sec):
    raise RuntimeError('ffmpeg 1')
ss._story_clip = boom
ss._upload_rally_highlight(raw, meta)
check(log == [('trim', 60), ('post', 'Replay.mp4_60s')], '자르기 실패 → 60초 하이라이트만(인스타 없음)', log)

log.clear()
ss._story_clip = lambda p, sec: (os.path.join(tmp, 'Replay_story.mp4'), {'start': 0, 'why': 'no_rally', 'duration': 50})
ss.HIGHLIGHT_CFG['auto_story'] = False
ss._upload_rally_highlight(raw, meta)
ss.HIGHLIGHT_CFG.pop('auto_story', None)
check(log == [('post', 'Replay_story.mp4')], 'auto_story: false — 하이라이트만', log)

log.clear()
ss._post_highlight = lambda p, meta: (log.append(('post',)), '')[1]
ss._upload_rally_highlight(raw, meta)
check(log == [('post',)], '하이라이트 업로드가 실패하면 스토리도 안 올린다', log)

calls = []
ss.obs.save_replay_buffer = lambda: raw
ss._upload_rally_highlight = lambda p, m: calls.append('rally')
ss._upload_highlight = lambda p, m, s: calls.append(('seconds', s))
ss._save_and_upload_highlight(meta, 0, 'rally')
ss._save_and_upload_highlight(meta, 60, 'seconds')
check(calls == ['rally', ('seconds', 60)], '저장 뒤 mode 대로 갈린다', calls)

print('\n[④] 스토리 업로드 — autoPost')
import importlib  # noqa: E402
ss2 = importlib.reload(ss)   # 위에서 바꿔 끼운 함수를 되돌린다
ss2.HIGHLIGHT_CFG['upload_key'] = 'k'
sent = []


class Resp:
    def __init__(self, status, data):
        self.ok = status < 300
        self.status_code = status
        self._d = data

    def json(self):
        return self._d


fake_requests = type(sys)('requests')
fake_requests.post = lambda url, headers=None, files=None, data=None, timeout=None: (
    sent.append((url, dict(data or {}))), Resp(200, {'data': {'storyUrl': 'https://x/a_story.mp4', 'status': 'posting'}}))[1]
sys.modules['requests'] = fake_requests
story = os.path.join(tmp, 's.mp4')
open(story, 'wb').write(b'x')
ss2._upload_story_file('h1', story, {'start': 1, 'serve': 4, 'why': 'rally', 'duration': 40}, auto_post=True)
check(sent[-1][0].endswith('/api/highlight/h1/story-file') and sent[-1][1].get('autoPost') == '1' and sent[-1][1]['cutWhy'] == 'rally',
      '직전 랠리 — autoPost=1 을 같이 보낸다', sent[-1])
job = ss2._story_view('h1')
check(job['state'] == 'ready' and job['post'] == 'posting' and job['auto'] is True, '백엔드 게시 상태를 기억한다(태블릿 토스트)', job)
ss2._upload_story_file('h2', story, {'why': 'rally'})
check('autoPost' not in sent[-1][1] and ss2._story_view('h2')['auto'] is False, '관리자 미리보기 길(태블릿 📱)은 autoPost 없음', sent[-1])
fake_requests.post = lambda *a, **k: Resp(500, {'message': '서버 오류'})
ss2._upload_story_file('h3', story, {'why': 'rally'}, auto_post=True)
check(ss2._story_view('h3')['state'] == 'failed' and '서버 오류' in ss2._story_view('h3')['error'], '실패하면 failed + 서버 문장')

print('\n[⑤] 태블릿')
html = open(os.path.join(ROOT, 'ps_court', 'ps_court_playus.html'), encoding='utf-8').read()
rail = html[html.index('<aside class="hl-rail off" id="hl-rail">'):html.index('</aside>', html.index('id="hl-rail"'))]
check(rail.count('class="hl-rail-btn"') == 2 and "saveHighlight('rally', this)" in rail and 'saveHighlight(60, this)' in rail
      and 'saveHighlight(30' not in rail and 'saveHighlight(90' not in rail, '버튼 둘 — 직전 랠리 · 60초')
check('앱 하이라이트 + 인스타' in rail and '앱 하이라이트만' in rail, '버튼이 어디로 가는지 말한다')
check("body: JSON.stringify(rally ? { mode: 'rally' } : { seconds: kind })," in html, '보내는 본문')
check('if (fresh.story.auto) {' in html and '센터 인스타 스토리에 올리는 중이에요' in html, '직전 랠리는 미리보기 대신 결과 토스트')

print()
if fails:
    print(f'FAIL {len(fails)}')
    sys.exit(1)
print(f'ALL OK — {n_ok[0]} checks')
