"""
방송 화면 검사 · 카메라 다시 연결 (2026-11-01 — *"유투브 또 이러네ㅜㅜ 나 외부여서 가서 고치지도 못하는데"*) — OBS 없이 돈다.

    python tests/test_camera_frame.py

유튜브엔 초록 화면만 나갔는데 송출 숫자(6,100kbps · 드롭 0 · YouTube good)도, 카메라 소스 상태('재생 중')도 정상이라
아무 경고가 없었다 — 카메라 영상을 푸는 디코더가 빈 프레임(초록)을 냈다. 여기서 못 박는 것:
  ① stream_health.frame_verdict — 작게 찍은 화면이 한 가지 색인가(초록 · 검정) · 점수판이 겹쳐도 · 진짜 코트 화면은 아니다.
  ② camera_frame_step — 두 번 연속 단색 → 다시 열기 · 네 번째 → 하드웨어 디코딩 끄기(켜져 있을 때만) · 그 뒤 3분마다 다시 열기.
  ③ evaluate(frame=) — '카메라 화면 깨짐' · 문장에 소스 이름과 서버가 한 일.
  ④ obs_controller — 카메라 소스 고르기(네트워크 먼저) · 스크린샷 PNG → 점 · 다시 열기 · 하드웨어 디코딩 끄기 · 던지지 않는다.
  ⑤ stream_server — 방송 중 감시가 20초마다 찍고 스스로 고친다 · /health camera_frame · 방송 장면만 단색이면 알리기만.
  ⑥ POST /camera-restart — 다시 열고 확인 · 그래도 단색이면 하드웨어 디코딩 끄고 다시 확인 · 운영자만.
  ⑦ 태블릿 — [📷 카메라 다시 연결] 버튼 · 서버 문장 그대로 · 이스케이프.
"""
import base64
import io
import os
import random
import sys

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


class R:
    def __init__(self, **kw):
        self.__dict__.update(kw)


W, H = 32, 18
GREEN = (0, 135, 0)          # 디코더가 빈 프레임을 내면 이 색(YUV 0)
DARK_GREEN = (4, 74, 0)      # 신고 화면의 색 그대로


def solid(c, n=W * H):
    return [c] * n


def court(seed=1):
    """진짜 코트 화면 흉내 — 파란 바닥 · 유리 · 조명 · 사람(칸마다 색이 다르다)."""
    rnd = random.Random(seed)
    out = []
    for y in range(H):
        for x in range(W):
            if y < 6:
                base = (40 + x * 3, 50 + y * 4, 70 + x * 2)        # 벽·조명
            elif y < 15:
                base = (20 + x, 70 + y * 3, 140 - x)               # 파란 바닥(그라데이션)
            else:
                base = (200, 200 - x * 2, 190)                     # 앞 유리 반사
            if rnd.random() < 0.1:
                base = (rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255))   # 사람·공
            out.append(base)
    return out


def with_overlay(c):
    """단색 위에 점수판(아래 한 줄)을 겹친 방송 장면."""
    px = solid(c)
    for i in range(W * H - W, W * H):
        px[i] = (250, 250, 250)
    return px


# ═══════════════════════════════════════════════════════════════
print('\n[①] 화면 판정')
v = sh.frame_verdict(solid(GREEN))
check(v['ok'] and v['uniform'] and v['green'] and v['color'] == '#008700', '초록 단색 → 깨짐 · 초록', v)
v = sh.frame_verdict(solid(DARK_GREEN))
check(v['uniform'] and v['green'], '신고 화면의 짙은 초록도 초록', v)
check(sh.frame_label(v) == '초록 화면', "이름표 '초록 화면'", sh.frame_label(v))
v = sh.frame_verdict(solid((0, 0, 0)))
check(v['uniform'] and not v['green'] and sh.frame_label(v) == '단색 화면(#000000)', '검정도 단색(초록은 아님)', v)
v = sh.frame_verdict(with_overlay(GREEN))
check(v['uniform'] and v['green'] and v['color'] == '#008700', '점수판이 겹쳐도 바탕이 한 색이면 깨짐', v)
noisy = [(c[0] + random.Random(i).randint(-4, 4), c[1] + random.Random(i + 7).randint(-4, 4), c[2]) for i, c in enumerate(solid(GREEN))]
check(sh.frame_verdict(noisy)['uniform'], '압축 잡티(±4)는 같은 색으로 본다')
for seed in (1, 2, 3):
    v = sh.frame_verdict(court(seed))
    check(v['ok'] and not v['uniform'] and not v['green'] and sh.frame_label(v) == '', f'진짜 코트 화면은 깨짐이 아니다({seed})', v)
turf = [(30 + (x % 8) * 4, 120 + y * 3, 40 + x) for y in range(H) for x in range(W)]
check(not sh.frame_verdict(turf)['uniform'], '초록 인조잔디 코트(명암이 있다)는 깨짐이 아니다')
check(sh.frame_verdict(None)['ok'] is False and sh.frame_verdict([(0, 0, 0)] * 5)['ok'] is False,
      '못 찍었거나 점이 너무 적으면 판정하지 않는다')
check(sh.frame_verdict([(1, 2), 'x', None] + solid(GREEN, 10))['uniform'], '이상한 점은 건너뛴다')

# ═══════════════════════════════════════════════════════════════
print('\n[②] 할 일 순서')
steps_on = [sh.camera_frame_step(n, True) for n in range(0, 25)]
check(steps_on[:2] == ['', ''], '한 번은 넘긴다(장면 전환·켜지는 순간)', steps_on)
check(steps_on[2] == 'restart', '두 번 연속 → 다시 열기', steps_on)
check(steps_on[3] == '' and steps_on[4] == 'hw_off', '다시 열어도 단색 → 하드웨어 디코딩 끄기', steps_on)
check(steps_on[5:13] == [''] * 8 and steps_on[13] == 'restart' and steps_on[22] == 'restart',
      '그 뒤로는 3분(9번)마다 다시 열기 — 매번 흔들지 않는다', steps_on)
steps_off = [sh.camera_frame_step(n, False) for n in range(0, 6)]
check(steps_off[4] == 'restart', '하드웨어 디코딩이 이미 꺼져 있으면 끄기 대신 다시 열기', steps_off)

# ═══════════════════════════════════════════════════════════════
print('\n[③] 방송 상태 한 줄')
base = {'active': True, 'reconnecting': False, 'congestion': 0, 'bytes': 0, 'net_skipped': 0, 'net_total': 0,
        'enc_skipped': 0, 'enc_total': 0, 'render_skipped': 0, 'render_total': 0, 'cpu': 3, 'fps': 30, 'media': {}}
cur = dict(base, bytes=7_600_000, net_total=300, enc_total=300, render_total=300)
h = sh.evaluate(base, cur, frame={'bad': ['코트 카메라'], 'note': '카메라를 다시 열었어요(1번)'})
check(h['issue'] == 'frame' and h['short'] == '카메라 화면 깨짐', "숫자는 정상이어도 화면이 단색이면 '카메라 화면 깨짐'", h)
check('코트 카메라' in h['text'] and '다시 열었어요' in h['text'] and h['frame'] == ['코트 카메라'],
      '문장에 소스 이름 · 서버가 한 일', h['text'])
h = sh.evaluate(base, cur, frame={'bad': [], 'note': 'x'})
check(h['issue'] == '' and h['frame'] == [], '깨진 소스가 없으면 정상', h)
h = sh.evaluate(base, dict(cur, reconnecting=True), frame={'bad': ['캠']})
check(h['issue'] == 'reconnecting' and 'frame' in h['issues'], '재연결이 더 먼저(둘 다 issues 엔 있다)', h)
check(sh.evaluate(base, cur)['frame'] == [], '옛 호출(frame 없음)도 그대로')

# ═══════════════════════════════════════════════════════════════
print('\n[④] obs_controller — 찍기 · 다시 열기 · 디코딩 끄기')
from obs_controller import OBSController  # noqa: E402
from PIL import Image  # noqa: E402


def png_uri(pixels, w=W, h=H):
    img = Image.new('RGB', (w, h))
    img.putdata(pixels)
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


class FakeClient:
    def __init__(self, inputs, frames=None, scene='경기'):
        self.inputs = inputs
        self.frames = frames or {}
        self.scene = scene
        self.calls = []

    def get_input_list(self, kind=None):
        self.calls.append(('list', kind))
        return R(inputs=[{'inputName': n} for n in self.inputs])

    def get_input_settings(self, name):
        return R(input_settings=dict(self.inputs[name]))

    def get_source_screenshot(self, name, fmt, w, h, q):
        self.calls.append(('shot', name, fmt, w, h))
        if name not in self.frames:
            raise RuntimeError('no source')
        return R(image_data=png_uri(self.frames[name]))

    def get_current_program_scene(self):
        return R(current_program_scene_name=self.scene)

    def trigger_media_input_action(self, name, action):
        self.calls.append(('media', name, action))

    def set_input_settings(self, name, settings, overlay):
        self.calls.append(('set', name, settings, overlay))
        self.inputs[name].update(settings)

    def disconnect(self):
        pass


CAMS = {'코트 카메라': {'is_local_file': False, 'input': 'rtsp://u:p@10.0.0.64/101', 'hw_decode': True},
        '옆 카메라': {'is_local_file': False, 'input': 'rtsp://u:p@10.0.0.65/101'},
        '인트로': {'is_local_file': True, 'local_file': 'C:/intro.mp4'}}
c = OBSController({'obs': {}})
c.client = FakeClient({k: dict(v) for k, v in CAMS.items()}, {'코트 카메라': solid(GREEN), '경기': court()})
check(c.camera_sources() == [('코트 카메라', True), ('옆 카메라', False)],
      '카메라 = 네트워크 미디어 소스(로컬 영상은 뺀다) · 하드웨어 디코딩 칸이 없으면 꺼짐', c.camera_sources())
c2 = OBSController({'obs': {}})
c2.client = FakeClient({'미디어': {'is_local_file': True, 'local_file': 'C:/cam.mp4'}})
check(c2.camera_sources() == [('미디어', False)], '네트워크 소스가 없으면 미디어 소스 전부', c2.camera_sources())
px = c.frame_pixels('코트 카메라')
check(px and len(px) == W * H and sh.frame_verdict(px)['green'], '스크린샷 PNG(data URI) → 점 → 초록', (px or [None])[:1])
check(('shot', '코트 카메라', 'png', 32, 18) in c.client.calls, '32x18 로 작게 찍는다', c.client.calls)
check(c.frame_pixels('없는 소스') is None and c.frame_pixels('') is None, '못 찍으면 None(던지지 않는다)')
check(c.program_scene() == '경기', '지금 방송 장면 이름')
check(c.restart_camera('코트 카메라') and ('media', '코트 카메라', 'OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART') in c.client.calls,
      '다시 열기 = 미디어 소스 RESTART', c.client.calls[-1])
check(c.set_hw_decode('코트 카메라', False) and c.client.calls[-1] == ('set', '코트 카메라', {'hw_decode': False}, True)
      and c.camera_sources()[0] == ('코트 카메라', False),
      '하드웨어 디코딩 끄기 = 그 칸만 덮어쓰기(다른 설정은 그대로)', c.client.calls[-1])
nc = OBSController({'obs': {}})
nc.connect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('OBS 꺼짐'))
check(nc.camera_sources() == [] and nc.frame_pixels('x') is None and nc.restart_camera('x') is False
      and nc.set_hw_decode('x', False) is False and nc.program_scene() == '', 'OBS 에 못 닿으면 조용히 빈 값')

# ═══════════════════════════════════════════════════════════════
print('\n[⑤] 방송 서버 감시 — 찍고 스스로 고친다')
import test_stream_server as tss  # noqa: E402

ss = tss.load_server()
cl = ss.app.test_client()
ss.CAMERA_SETTLE_S = 0
r = cl.post('/start-stream', json={'teamA': ['가', '나'], 'teamB': ['다', '라'], 'league': 'PSiL 26S3',
                                   'matchNumber': 184, 'matchId': 'm184'})
check(r.get_json().get('success'), '방송 시작', r.get_json())


class Cam:
    """가짜 OBS 카메라 — 다시 열거나 디코딩을 꺼야 낫는다(fix_by)."""
    def __init__(self, fix_by='restart'):
        self.hw = True
        self.broken = True
        self.fix_by = fix_by
        self.log = []
        self.scene_px = None

    def install(self, o):
        o.camera_sources = lambda: [('코트 카메라', self.hw)]
        o.frame_pixels = lambda name, width=32, height=18: (
            (solid(GREEN) if self.broken else court()) if name == '코트 카메라'
            else (self.scene_px if self.scene_px is not None else (with_overlay(GREEN) if self.broken else court())))
        o.program_scene = lambda: '경기'

        def restart(name):
            self.log.append('restart')
            if self.fix_by == 'restart':
                self.broken = False
            return True

        def hw(name, on):
            self.log.append(f'hw:{on}')
            self.hw = on
            if self.fix_by == 'hw_off' and not on:
                self.broken = False
            return True
        o.restart_camera = restart
        o.set_hw_decode = hw


stat = {'active': True, 'reconnecting': False, 'congestion': 0, 'bytes': 0, 'net_skipped': 0, 'net_total': 0,
        'enc_skipped': 0, 'enc_total': 0, 'render_skipped': 0, 'render_total': 0, 'media': {}}
nbytes = [0]


def stats():
    nbytes[0] += 7_600_000
    return dict(stat, bytes=nbytes[0], net_total=nbytes[0] // 25000, enc_total=nbytes[0] // 25000,
                render_total=nbytes[0] // 25000)


ss.obs.stream_stats = stats
ss.youtube.stream_health = lambda b: {'status': 'good', 'issues': []}

cam = Cam(fix_by='hw_off')
cam.install(ss.obs)
t = [2000.0]


def tick():
    t[0] += 10
    return ss._health_tick(t[0])


h = tick()
check(h['issue'] == '' and cam.log == [], '첫 단색은 넘긴다', (h['issue'], cam.log))
hv = cl.get('/health').get_json()
cf = hv.get('camera_frame') or {}
check(cf.get('sources') and cf['sources'][0]['label'] == '초록 화면' and cf['sources'][0]['hw'] is True,
      '/health camera_frame — 카메라마다 판정(초록 · 하드웨어 디코딩 켜짐)', cf)
h = tick()   # 20초 간격 사이 바퀴 — 지난 결과
check(cam.log == [], '사이 바퀴(10초)엔 찍지 않는다', cam.log)
h = tick()
check(cam.log == ['restart'] and h['issue'] == 'frame' and '다시 열었어요(1번)' in h['text'],
      '두 번 연속 → 카메라 다시 열기 · 상태 줄 카메라 화면 깨짐', (cam.log, h))
hv = cl.get('/health').get_json()
check((hv['stream_health'] or {}).get('short') == '카메라 화면 깨짐' and hv['camera_frame']['last_action'],
      '/health — 짧은 말 · 서버가 마지막으로 한 일', hv['camera_frame'])
tick(); tick()   # 3번째 검사
check(cam.log == ['restart'], '세 번째는 기다린다', cam.log)
tick(); h = tick()   # 4번째 검사
check(cam.log == ['restart', 'hw:False'] and '하드웨어 디코딩' in h['text'], '그래도 단색 → 하드웨어 디코딩 끄기', (cam.log, h['text']))
tick(); h = tick()
check(h['issue'] == '' and cl.get('/health').get_json()['camera_frame']['sources'][0]['label'] == '',
      '화면이 돌아오면 풀린다', h)

# 다시 열기로 낫는 경우
cam2 = Cam(fix_by='restart')
cam2.install(ss.obs)
for _ in range(6):
    h = tick()
check(cam2.log == ['restart'] and h['issue'] == '', '다시 열어서 나으면 디코딩은 건드리지 않는다', cam2.log)

# 카메라는 멀쩡한데 방송 장면만 단색 — 알리기만
cam3 = Cam(fix_by='never')
cam3.broken = False
cam3.scene_px = solid((0, 0, 0))
cam3.install(ss.obs)
for _ in range(6):
    h = tick()
check(cam3.log == [] and h['issue'] == 'frame' and '방송 화면' in h['frame'] and "장면 '경기'" in h['text'],
      '카메라 정상 · 장면만 단색 → 손대지 않고 장면을 보라고 알린다', (cam3.log, h['text']))
cam3.scene_px = court()
for _ in range(2):
    h = tick()
check(h['issue'] == '', '장면이 돌아오면 풀린다', h)

# 못 찍으면(옛 OBS · 연결 끊김) 판정하지 않는다
ss.obs.frame_pixels = lambda *a, **k: None
for _ in range(6):
    h = tick()
check(h['issue'] == '', '못 찍으면 아무 일도 하지 않는다', h)

# ═══════════════════════════════════════════════════════════════
print('\n[⑥] POST /camera-restart')
cam4 = Cam(fix_by='restart')
cam4.install(ss.obs)
r = cl.post('/camera-restart', json={})
d = r.get_json()
check(r.status_code == 200 and d['success'] and d['ok'] and cam4.log == ['restart'] and '화면 정상' in d['text'],
      '다시 열고 확인 → 화면 정상', d)
check(d['sources'][0]['before'] == '초록 화면' and d['sources'][0]['after'] == '' and not d['sources'][0]['hw_off'],
      '카메라마다 전·후', d['sources'])
cam5 = Cam(fix_by='hw_off')
cam5.install(ss.obs)
d = cl.post('/camera-restart', json={}).get_json()
check(d['ok'] and cam5.log == ['restart', 'hw:False'] and '하드웨어 디코딩을 껐어요' in d['text'],
      '다시 열어도 단색 → 하드웨어 디코딩 끄고 다시 확인', d)
cam6 = Cam(fix_by='hw_off')
cam6.install(ss.obs)
d = cl.post('/camera-restart', json={'hwDecodeOff': True}).get_json()
check(d['ok'] and cam6.log == ['hw:False'], 'hwDecodeOff → 바로 디코딩부터 끈다', d)
cam7 = Cam(fix_by='never')
cam7.install(ss.obs)
d = cl.post('/camera-restart', json={}).get_json()
check(d['success'] and not d['ok'] and '아직 초록 화면' in d['text'] and '랜선' in d['text'], '그래도 안 되면 사람이 할 일', d)
ss.obs.camera_sources = lambda: []
r = cl.post('/camera-restart', json={})
check(r.status_code == 400 and '카메라' in r.get_json()['error'], '카메라 소스가 없으면 400 + 이유', r.get_json())
check(getattr(ss.camera_restart, '__wrapped__', None) is not None, '운영자 확인(_require_operator)이 붙어 있다')
ss.CONTROL_AUTH_ENABLED = True
r = cl.post('/camera-restart', json={}, headers={'X-Forwarded-For': '1.2.3.4', 'X-Real-IP': '1.2.3.4'})
check(r.status_code == 401, '터널(밖)에서 토큰 없이 부르면 401', r.status_code)

# ═══════════════════════════════════════════════════════════════
print('\n[⑦] 태블릿')
html = io.open(os.path.join(ROOT, 'ps_court', 'ps_court_playus.html'), encoding='utf-8').read()
check('id="cam-restart-btn"' in html and 'restartCamera(' in html, '[📷 카메라 다시 연결] 버튼')
fn = html[html.index('async function restartCamera'):]
fn = fn[:fn.index('\n}\n') + 3]
check("'camera-restart'" in fn and 'streamHeaders()' in fn and 'streamAuthFail(' in fn, '운영자 토큰 · 막히면 로그인 안내', fn[:200])
check('data.text' in fn and 'innerHTML' not in fn, '서버 문장 그대로(텍스트로 · innerHTML 아님)')
check("'frame'" in html, "상태 줄에서 '카메라 화면 깨짐' 이면 버튼을 강조")

print()
if fails:
    print(f'FAIL {len(fails)}건')
    sys.exit(1)
print(f'ALL OK — {n_ok[0]} checks')
