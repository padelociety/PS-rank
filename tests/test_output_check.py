"""
유튜브 초록 화면 — 송출 영상 검사 · OBS 다시 켜기 (2026-11-11) — OBS 없이 돈다.

    python tests/test_output_check.py

2026-11-11 *"obs 미리보기 화면은 유투브 초록화면 나와도 원래 정상이야"* · *"라이브 시작할때 초록색 되는거같아. 그 이후부터는
계속 새로운 라이브도 초록색이고"* → OBS 만 다시 켜자 풀렸다(사용자 확인). 그때 방송·녹화 인코더가 둘 다 QuickSync 였다.
OBS 스크린샷(화면 검사)은 인코더 앞이라 이걸 못 본다. 여기서 못 박는 것:
  ① output_verdict — 리플레이 파일의 끝쪽 키프레임이 전부 한 가지 색이면 깨짐(점수판까지 통째로).
  ② encoding_advice — 방송·녹화 인코더가 같은 그래픽 인코더면 'hw_double'(초록 화면 의심 1순위 · 맨 앞).
  ③ evaluate(output=) — '유튜브 초록 화면' · 고칠 것 = hw_double 권고.
  ④ _small_frames — 진짜 ffmpeg 로 초록 클립 / 정상 클립을 풀어 판정(ffmpeg 가 있으면).
  ⑤ stream_server — 시작 45초 뒤 검사 · 한 번은 넘기고 30초 뒤 다시 · 두 번 연속이면 OBS 다시 켜기 → 같은 키로 이어 보내기 ·
     방송 하나에 한 번 · 녹화 인코더가 따로면 검사 안 함 · OBS 화면부터 단색이면 손대지 않음 · 검사 파일은 지운다.
  ⑥ POST /obs-restart — 운영자만 · 같은 방송으로 이어 보냄 · 유튜브 방송은 끝내지 않는다.
  ⑦ 태블릿 — 'output' 이면 같은 버튼이 [🔄 OBS 다시 켜기].
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import stream_health as sh  # noqa: E402

fails = []
n_ok = [0]


def check(cond, msg, detail=''):
    if cond:
        n_ok[0] += 1
        print('  ok   ' + msg)
    else:
        print('  FAIL ' + msg + (('\n        ' + str(detail)) if detail else ''))
        fails.append(msg)


W, H = 32, 18
GREEN = (0, 135, 0)


def solid(c):
    return [c] * (W * H)


def court():
    return [((x * 7) % 256, (60 + y * 9) % 256, (140 - x * 3) % 256) for y in range(H) for x in range(W)]


print('\n[①] 송출 영상 판정')
v = sh.output_verdict([court(), solid(GREEN), solid(GREEN), solid(GREEN)])
check(v['ok'] and v['bad'] and v['green'] and v['label'] == '초록 화면', '끝쪽 세 장이 전부 초록 → 깨짐(초록)', v)
v = sh.output_verdict([solid(GREEN), solid(GREEN), court()])
check(v['ok'] and not v['bad'], '마지막 장이 정상이면 깨짐이 아니다', v)
v = sh.output_verdict([court()] * 5)
check(v['ok'] and not v['bad'] and v['label'] == '', '정상 화면', v)
v = sh.output_verdict([solid((0, 0, 0))] * 3)
check(v['bad'] and not v['green'], '검은 단색도 깨짐(초록은 아님)', v)
check(sh.output_verdict([])['ok'] is False and sh.output_verdict(None)['bad'] is False, '못 풀면 판정 안 함')

print('\n[②] 인코딩 권고 — 같은 그래픽 인코더 둘')
adv = sh.encoding_advice({'mode': 'Advanced', 'encoder': 'obs_qsv11_v2', 'rec_encoder': 'obs_qsv11_v2', 'fps': 30,
                          'output': [1920, 1080], 'base': [1920, 1080]})
check([a['key'] for a in adv] == ['hw_double'] and adv[0].get('green') and '스트리밍 인코더 사용' in adv[0]['text']
      and 'QuickSync' in adv[0]['text'], 'QuickSync + QuickSync → hw_double 하나(초록 화면 의심)', adv)
adv = sh.encoding_advice({'mode': 'Advanced', 'encoder': 'jim_nvenc', 'rec_encoder': 'jim_nvenc', 'fps': 60})
check(adv[0]['key'] == 'hw_double' and 'NVENC' in adv[0]['text'] and 'fps' in [a['key'] for a in adv],
      'NVENC 둘도 같은 규칙 · 맨 앞', [a['key'] for a in adv])
adv = sh.encoding_advice({'mode': 'Advanced', 'encoder': 'obs_qsv11_v2', 'rec_encoder': 'obs_x264'})
check([a['key'] for a in adv] == ['double_encode'], '종류가 다르면 예전 double_encode', adv)
adv = sh.encoding_advice({'mode': 'Advanced', 'encoder': 'obs_qsv11_v2', 'rec_encoder': 'none', 'sw_decode_cameras': ['미디어']})
check(adv == [], "녹화 = 스트리밍 인코더 · 카메라 소프트웨어 디코딩 → 고칠 것 없음(하드웨어 디코딩을 권하지 않는다)", adv)

print('\n[③] 방송 상태 한 줄')
snap = {'active': True, 'bytes': 7_600_000, 'net_total': 300, 'enc_total': 300, 'render_total': 300}
h = sh.evaluate({'active': True}, snap, elapsed_s=10, output={'bad': True, 'note': '서버가 OBS 를 다시 켜는 중'},
                advice=[{'key': 'hw_double', 'green': True, 'text': '녹화 인코더 고치기'}])
check(h['issue'] == 'output' and h['short'] == '유튜브 초록 화면' and '서버가 OBS 를 다시 켜는 중' in h['text']
      and h['fix'] == '녹화 인코더 고치기' and '고칠 것: 녹화 인코더 고치기' in h['text'] and h['output'] is True,
      "'유튜브 초록 화면' · 서버가 하는 일 · 고칠 것", h)
h = sh.evaluate({'active': True}, snap, elapsed_s=10, output={'bad': True}, advice=[])
check(h['issue'] == 'output' and h['fix'] == '', '권고가 없으면 고칠 것 없이', h)
h = sh.evaluate({'active': True}, dict(snap, reconnecting=True), elapsed_s=10, output={'bad': True})
check(h['issues'][:2] == ['reconnecting', 'output'], '재연결이 먼저(그 사이 영상은 원래 없다)', h['issues'])
check(sh.evaluate({'active': True}, snap, elapsed_s=10, output={'bad': False})['issue'] == '', '정상이면 비어 있다')

print('\n[④] 진짜 ffmpeg 로 풀기')
import test_stream_server as tss  # noqa: E402

ss = tss.load_server()
ff = shutil.which('ffmpeg')
if ff:
    ss._ffmpeg_exe = lambda: ff
    tmp = tempfile.mkdtemp(prefix='oc_')

    def clip(name, src):
        out = os.path.join(tmp, name)
        r = subprocess.run([ff, '-y', '-loglevel', 'error', '-f', 'lavfi', '-i', src, '-t', '8', '-r', '30', '-g', '60',
                            '-pix_fmt', 'yuv420p', out], capture_output=True, timeout=120)
        return out if r.returncode == 0 and os.path.exists(out) else ''

    g = clip('green.mp4', 'color=c=0x008700:s=320x180')
    n = clip('court.mp4', 'testsrc2=s=320x180')
    if g and n:
        fg = ss._small_frames(g)
        fn = ss._small_frames(n)
        check(len(fg) >= 3 and len(fg[0]) == W * H, '키프레임만 32x18 로', (len(fg), len(fg[0]) if fg else 0))
        check(sh.output_verdict(fg)['green'], '초록 클립 → 초록 화면', sh.output_verdict(fg))
        check(not sh.output_verdict(fn)['bad'], '정상 클립 → 정상', sh.output_verdict(fn))
        check(ss._small_frames(os.path.join(tmp, '없음.mp4')) == [], '없는 파일 → [] (던지지 않는다)')
    else:
        print('  (건너뜀 — 이 ffmpeg 로 시험 클립을 못 만듦)')
else:
    print('  (건너뜀 — ffmpeg 없음)')

print('\n[⑤] 방송 서버 — 검사 · OBS 다시 켜기 · 같은 방송으로 이어 보내기')
cl = ss.app.test_client()
r = cl.post('/start-stream', json={'teamA': ['가', '나'], 'teamB': ['다', '라'], 'league': 'PSiL 26S3',
                                   'matchNumber': 77, 'matchId': 'm77'})
check(r.get_json().get('success'), '방송 시작', r.get_json())
bid = ss.stream_state['broadcast_id']
key = ss._ingest.get('key')
check(ss._ingest.get('broadcast_id') == bid and key == f'key-{bid}', '방송 키는 메모리에만(이어 보내기용)', ss._ingest)
check(key not in str(cl.get('/health').get_json()), '/health 엔 키가 없다')


class Out:
    """가짜 인코더 출력 — broken 이면 리플레이 파일이 초록. OBS 를 다시 켜면 낫는다."""
    def __init__(self):
        self.broken = True
        self.log = []
        self.files = []
        self.canvas = court()

    def install(self, o, server):
        o.encoding_status = {'config': {'mode': 'Advanced', 'rec_encoder': 'none'}}
        o.is_replay_buffer_active = lambda: True
        o.program_scene = lambda: '장면'
        o.frame_pixels = lambda name, width=32, height=18: self.canvas

        def save():
            p = tempfile.mktemp(prefix='replay_', suffix='.mp4')
            open(p, 'wb').write(b'x')
            self.files.append(p)
            self.log.append('save')
            return p
        o.save_replay_buffer_nowait = save

        def restart():
            self.log.append('obs-restart')
            o.streaming = False
            self.broken = False
            o.obs_restart_status = {'ok': True, 'error': '', 'seconds': 12.0}
            return True
        o.restart_obs_process = restart
        server._small_frames = lambda path, seconds=12: [solid(GREEN)] * 4 if self.broken else [court()] * 4


out = Out()
out.install(ss.obs, ss)
ss.obs.calls.clear()
t0 = ss.datetime.fromisoformat(ss.stream_state['started_at']).timestamp()
check(ss._output_tick(t0 + 10) is None and out.log == [], '시작 45초 전엔 검사하지 않는다', out.log)
v = ss._output_tick(t0 + 50)
check(out.log == ['save'] and v['bad'] and not ss.output_state['bad'], '첫 초록은 넘긴다(30초 뒤 다시)', (out.log, ss.output_state))
check(all(not os.path.exists(p) for p in out.files), '검사용 리플레이 파일은 지운다', out.files)
check(ss._output_tick(t0 + 60) is None, '30초 안엔 다시 안 본다')
ss.obs.calls.clear()
v = ss._output_tick(t0 + 85)
check(out.log == ['save', 'save', 'obs-restart'], '두 번 연속 → OBS 다시 켜기', out.log)
check(ss.obs.calls == ['stop', 'set', 'start'] and ss.obs.key == key, '송출 끄고 → 같은 키로 → 다시 켬', (ss.obs.calls, ss.obs.key))
check(bid not in ss.youtube.ended and ss.stream_state['active'] and ss.stream_state['broadcast_id'] == bid,
      '유튜브 방송은 끝내지 않는다(같은 방송으로 이어진다)', ss.youtube.ended)
hv = cl.get('/health').get_json()['output_check']
check(hv['covers_stream'] and not hv['bad'] and '같은 유튜브 방송' in hv['last_action'] and hv['restart']['ok'],
      '/health output_check — 서버가 한 일', hv)

# 다시 깨져도 방송 하나에 한 번만 다시 켠다 — 그 뒤엔 알리기만
out.broken = True
nt = ss._output_mem['next_at'] + 1
ss._output_tick(nt)
ss._output_tick(nt + 31)
check(out.log.count('obs-restart') == 1 and ss.output_state['bad'] and '이미 한 번' in ss.output_state['note'],
      '방송 하나에 한 번만 — 그 뒤엔 알린다', (out.log, ss.output_state))
ss.obs.stream_stats = lambda: {'active': True, 'bytes': 0}
ss.youtube.stream_health = lambda b: {'status': 'good', 'issues': []}
h = ss._health_tick(nt + 40)
check(h and h['issue'] == 'output' and '이미 한 번' in h['text'], "상태 줄 '유튜브 초록 화면'", h)

# OBS 화면부터 단색이면 인코더가 아니다 — 손대지 않는다(화면 검사 몫)
out.canvas = solid(GREEN)
ss._output_mem.update({'bad': 0, 'restarted': False})
nt = ss._output_mem['next_at'] + 1
ss._output_tick(nt)
ss._output_tick(nt + 31)
check(out.log.count('obs-restart') == 1 and not ss.output_state['bad'], 'OBS 화면도 단색이면 OBS 를 다시 켜지 않는다', ss.output_state)
out.canvas = court()

# 녹화 인코더가 따로면 검사하지 않는다(리플레이 파일이 유튜브 영상이 아니다)
ss.obs.encoding_status = {'config': {'mode': 'Advanced', 'rec_encoder': 'obs_qsv11_v2'}}
n_save = out.log.count('save')
v = ss._output_tick(ss._output_mem['next_at'] + 1)
check(v and '녹화 인코더가 따로' in v['skipped'] and out.log.count('save') == n_save, '녹화 인코더가 따로면 검사 안 함', v)
check(cl.get('/health').get_json()['output_check']['covers_stream'] is False, '/health covers_stream false')
ss.obs.encoding_status = {'config': {'mode': 'Advanced', 'rec_encoder': ''}}

# 하이라이트를 막 저장했으면 그 사이엔 저장하지 않는다
ss._last_highlight_at[0] = ss.time.time()
v = ss._output_tick(ss._output_mem['next_at'] + 1)
check(v and '하이라이트' in v['skipped'] and out.log.count('save') == n_save, '하이라이트 직후엔 건너뛴다', v)
ss._last_highlight_at[0] = 0.0

print('\n[⑥] POST /obs-restart')
out.broken = True
ss.obs.calls.clear()
n_restart = out.log.count('obs-restart')
r = cl.post('/obs-restart', json={})
d = r.get_json()
check(r.status_code == 200 and d['ok'] and out.log.count('obs-restart') == n_restart + 1
      and ss.obs.calls == ['stop', 'set', 'start'] and ss.obs.key == key, '방송 중 → 다시 켜고 같은 키로 이어 보냄', (d, ss.obs.calls))
check(bid not in ss.youtube.ended, '유튜브 방송은 그대로')
# 서버가 다시 켜져 메모리에 키가 없으면 유튜브에 묻는다
ss._ingest.update({'broadcast_id': None, 'rtmp': '', 'key': ''})
ss.youtube.ingest_of = lambda b: ('rtmp://x/live2', f'key-{b}')
ss.obs.calls.clear()
d = cl.post('/obs-restart', json={}).get_json()
check(d['ok'] and ss.obs.key == key, '키를 잊었으면 유튜브에서 다시 읽는다', d)
ss.youtube.ingest_of = lambda b: ('', '')
ss._ingest.update({'broadcast_id': None, 'rtmp': '', 'key': ''})
n_restart = out.log.count('obs-restart')
d = cl.post('/obs-restart', json={}).get_json()
check(not d['ok'] and out.log.count('obs-restart') == n_restart and '방송 주소' in d['text'],
      '주소를 못 읽으면 OBS 를 끄지 않는다(이어 보낼 수 없다)', d)
src = open(os.path.join(ROOT, 'stream_server.py'), encoding='utf-8').read()
check("@app.route('/obs-restart', methods=['POST'])\n@_require_operator" in src, '운영자만')

print('\n[⑦] obs_controller — OBS 다시 켜기 · 검사용 저장')
sys.modules.pop('obs_controller', None)
import importlib  # noqa: E402
oc = importlib.import_module('obs_controller')
c = oc.OBSController({'obs': {}})
ran = []


class RR:
    def __init__(self, rc=0):
        self.returncode, self.stdout, self.stderr = rc, b'', b''


c._run = lambda cmd, timeout=20: (ran.append(cmd[0]), RR())[1]
tries = [0]


def fake_connect(retries=3, delay=2.0):
    tries[0] += 1
    if tries[0] < 2:
        raise RuntimeError('아직')
    c.client = object()
oc.time.sleep = lambda s: None
c.connect = fake_connect
check(c.restart_obs_process(wait_s=30) is True and ran == ['taskkill', 'schtasks'] and c.obs_restart_status['ok'],
      'taskkill → 작업 스케줄러(PS-OBS-Studio) → 웹소켓이 붙을 때까지', (ran, c.obs_restart_status))
c2 = oc.OBSController({'obs': {'obs_task': '내OBS'}})
cmds = []
c2._run = lambda cmd, timeout=20: (cmds.append(cmd), RR(1))[1]
c2.connect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('안 뜸'))
check(c2.restart_obs_process(wait_s=6) is False and cmds[1][-1] == '내OBS' and '내OBS' in c2.obs_restart_status['error'],
      '작업 이름은 config(obs.obs_task) · 실패는 이유를 적고 던지지 않는다', c2.obs_restart_status)
c3 = oc.OBSController({'obs': {}})
c3._replay_save_lock.acquire()
check(c3.save_replay_buffer_nowait() == '', '하이라이트 저장 중이면 검사 저장은 기다리지 않고 건너뛴다')
c3._replay_save_lock.release()

print('\n[⑧] 태블릿')
html = open(os.path.join(ROOT, 'ps_court', 'ps_court_playus.html'), encoding='utf-8').read()
check("function camBtnObsMode() { return !!(_liveIssue && _liveIssue.issue === 'output'); }" in html,
      "'유튜브 초록 화면' 이면 OBS 다시 켜기 모드")
check("${obsMode ? 'obs-restart' : 'camera-restart'}" in html and '🔄 OBS 다시 켜기' in html, '같은 버튼이 /obs-restart 로')
check('String(data.text ||' in html, '결과 문장은 서버 것 그대로(글자로만)')

print()
if fails:
    print(f'FAIL {len(fails)}')
    sys.exit(1)
print(f'ALL OK — {n_ok[0]} checks')
