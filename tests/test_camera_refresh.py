"""
방송을 켤 때마다 카메라(RTSP) 소스를 다시 시작한다 — OBS 없이 돈다(가짜 클라이언트).

    python tests/test_camera_refresh.py

2026-11-07 "라이브 소리 또 느려짐": RTSP 카메라는 오래 켜 두면 소리가 화면보다 점점 밀린다.
고정 싱크 값은 처음 어긋남만 맞추므로, 켤 때마다 소스를 다시 시작해 매 경기를 처음 상태로 둔다.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from obs_controller import OBSController  # noqa: E402

fails = []


def check(cond, msg, detail=""):
    print(("  ok   " if cond else "  FAIL ") + msg + ("" if cond or not detail else f" — {detail}"))
    if not cond:
        fails.append(msg)


class R:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeClient:
    """inputs: {이름: 주소} · local: 로컬 파일 이름들 · never: 다시 안 켜지는 이름들 · fail: 다시 시작이 실패하는 이름들"""
    def __init__(self, inputs, local=(), never=(), fail=()):
        self.inputs = dict(inputs)
        self.local, self.never, self.fail = set(local), set(never), set(fail)
        self.actions = []
        self.polls = {}

    def get_input_list(self, kind=None):
        return R(inputs=[{'inputName': n, 'inputKind': 'ffmpeg_source'} for n in self.inputs])

    def get_input_settings(self, name):
        return R(input_settings={'input': self.inputs[name], 'is_local_file': name in self.local})

    def trigger_media_input_action(self, name, action):
        if name in self.fail:
            raise RuntimeError('604')
        self.actions.append((name, action))

    def get_media_input_status(self, name):
        self.polls[name] = self.polls.get(name, 0) + 1
        if name in self.never or self.polls[name] < 2:
            return R(media_state='OBS_MEDIA_STATE_OPENING')
        return R(media_state='OBS_MEDIA_STATE_PLAYING')


def ctl(**obs):
    return OBSController({'obs': obs})


print('\n[1] 네트워크 카메라만 다시 시작한다 · 로컬 파일(인트로 영상)은 건드리지 않는다')
c = ctl()
c.client = FakeClient({'미디어': 'rtsp://u:x@10.0.0.2:554/101', '인트로': 'C:/intro.mp4'}, local={'인트로'})
st = c.refresh_cameras(wait_s=3)
check(c.client.actions == [('미디어', OBSController.CAMERA_RESTART_ACTION)], '미디어만 RESTART')
check(st['restarted'] == ['미디어'] and st['ready'] == ['미디어'] and st['error'] == '', '다시 들어올 때까지 기다림', str(st))
check(c.camera_refresh_status is st, '/health 에 실을 결과를 남긴다')

print('\n[2] 영상이 안 돌아와도 던지지 않는다 — 오류만 적는다')
c = ctl()
c.client = FakeClient({'미디어': 'rtsp://cam'}, never={'미디어'})
st = c.refresh_cameras(wait_s=1)
check(st['restarted'] == ['미디어'] and st['ready'] == [] and '안 들어옴' in st['error'], '시간 초과를 적는다', str(st))

print('\n[3] 다시 시작이 실패해도 던지지 않는다')
c = ctl()
c.client = FakeClient({'미디어': 'rtsp://cam'}, fail={'미디어'})
st = c.refresh_cameras(wait_s=1)
check(st['restarted'] == [] and '실패' in st['error'], '실패를 적는다', str(st))

print('\n[4] 카메라가 없으면 · OBS 미연결이면 · 꺼 두면 아무것도 안 한다')
c = ctl(); c.client = FakeClient({'인트로': 'C:/a.mp4'}, local={'인트로'})
st = c.refresh_cameras(wait_s=1)
check(c.client.actions == [] and '못 찾음' in st['error'], '카메라 없음')
c = ctl(); st = c.refresh_cameras(wait_s=1)
check(st['error'] == 'OBS 미연결', '미연결')
c = ctl(camera_refresh_on_start=False); c.client = FakeClient({'미디어': 'rtsp://cam'})
st = c.refresh_cameras(wait_s=1)
check(c.client.actions == [] and '꺼 둠' in st['error'], 'camera_refresh_on_start: false')

print('\n[5] 캐시가 있어도 켤 때는 다시 본다(소스를 바꿨을 수 있다)')
c = ctl(); c.client = FakeClient({'미디어': 'rtsp://cam'})
c._net_inputs = {'옛카메라': True}; import time as _t; c._net_inputs_at = _t.time()
c.refresh_cameras(wait_s=1)
check([a[0] for a in c.client.actions] == ['미디어'], '지금 OBS 의 카메라를 다시 시작')

print('\n[6] stream_server 가 송출 직전에 부르고 /health 에 싣는다')
src = open(os.path.join(ROOT, 'stream_server.py'), encoding='utf-8').read()
check(re.search(r"obs\.set_stream_settings\(rtmp_url, stream_key\)\s*\n(\s*#[^\n]*\n)*\s*obs\.refresh_cameras\(\)\s*\n(\s*#[^\n]*\n)*\s*_fresh_encoder\(\)\s*\n\s*obs\.start_stream\(\)", src) is not None,
      'set_stream_settings → refresh_cameras → (인코더 새로) → start_stream 순서')
check("'camera_refresh': getattr(obs, 'camera_refresh_status', None)" in src, '/health camera_refresh')

print('\n[7] 다시 연 카메라가 초록이면 켜기 전에 고친다 — 하드웨어 디코딩이 켜져 있으면 끈다 (2026-11-11 "또 유투브 초록화면이야")')
import base64, io  # noqa: E401,E402
from PIL import Image  # noqa: E402

W, H = 32, 18
GREEN = [(0, 135, 0)] * (W * H)
COURT = [((x * 7) % 256, (y * 13) % 256, (x * y) % 256) for y in range(H) for x in range(W)]


def png(pixels):
    img = Image.new('RGB', (W, H))
    img.putdata(pixels)
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


class ShotClient(FakeClient):
    """frames: {이름: [찍을 때마다 내줄 화면 …]} (마지막 화면이 계속) · hw: 하드웨어 디코딩 켠 이름들"""
    def __init__(self, inputs, frames, hw=(), heal_on_hw_off=True):
        super().__init__(inputs)
        self.frames = {k: list(v) for k, v in frames.items()}
        self.hw = set(hw)
        self.heal_on_hw_off = heal_on_hw_off
        self.sets = []

    def get_input_settings(self, name):
        st = {'input': self.inputs[name], 'is_local_file': name in self.local}
        if name in self.hw:
            st['hw_decode'] = True
        return R(input_settings=st)

    def get_media_input_status(self, name):
        return R(media_state='OBS_MEDIA_STATE_PLAYING')

    def get_source_screenshot(self, name, fmt, w, h, q):
        seq = self.frames.get(name) or [COURT]
        px = seq.pop(0) if len(seq) > 1 else seq[0]
        return R(image_data=png(px))

    def set_input_settings(self, name, settings, overlay):
        self.sets.append((name, dict(settings)))
        if settings.get('hw_decode') is False:
            self.hw.discard(name)
            if self.heal_on_hw_off:
                self.frames[name] = [COURT]


def quick(c):
    c.CAMERA_FRAME_SETTLE_S = c.CAMERA_FRAME_RECHECK_S = c.CAMERA_FIX_SETTLE_S = 0
    return c


c = quick(ctl())
c.client = ShotClient({'미디어': 'rtsp://cam'}, {'미디어': [GREEN]}, hw={'미디어'})
st = c.refresh_cameras(wait_s=1)
fr = st.get('frame') or []
check(c.client.sets == [('미디어', {'hw_decode': False})], '초록 + 하드웨어 디코딩 켬 → 끈다(소스가 다시 열린다)', str(c.client.sets))
check(len(fr) == 1 and fr[0]['action'] == 'hw_off' and fr[0]['checked'] and fr[0]['after'] == '', '고친 뒤 다시 찍어 정상', str(fr))
check('초록' in fr[0]['label'], '무엇이었는지 적는다', fr[0]['label'])

c = quick(ctl())
c.client = ShotClient({'미디어': 'rtsp://cam'}, {'미디어': [GREEN, GREEN, COURT]})
st = c.refresh_cameras(wait_s=1)
fr = st['frame']
check(c.client.sets == [] and [a[0] for a in c.client.actions] == ['미디어', '미디어'],
      '하드웨어 디코딩이 꺼져 있으면 한 번 더 다시 연다', str(c.client.actions))
check(fr and fr[0]['action'] == 'restart' and fr[0]['after'] == '', '다시 연 뒤 정상', str(fr))

c = quick(ctl())
c.client = ShotClient({'미디어': 'rtsp://cam'}, {'미디어': [GREEN, COURT]}, hw={'미디어'})
st = c.refresh_cameras(wait_s=1)
check(st['frame'] == [] and c.client.sets == [] and len(c.client.actions) == 1,
      '처음 한 장만 단색이면(켜지는 순간) 손대지 않는다', str(st['frame']))

c = quick(ctl())
c.client = ShotClient({'미디어': 'rtsp://cam'}, {'미디어': [COURT]}, hw={'미디어'})
st = c.refresh_cameras(wait_s=1)
check(st['frame'] == [] and c.client.sets == [], '정상 화면이면 아무것도 안 한다(하드웨어 디코딩도 그대로)')

c = quick(ctl())
c.client = ShotClient({'미디어': 'rtsp://cam'}, {'미디어': [GREEN]}, hw={'미디어'}, heal_on_hw_off=False)
st = c.refresh_cameras(wait_s=1)
fr = st['frame']
check(fr and fr[0]['action'] == 'hw_off' and fr[0]['checked'] and '초록' in fr[0]['after'],
      '고쳐도 그대로면 그렇다고 적는다(방송은 켠다 — 20초 감시가 이어 받는다)', str(fr))

c = quick(ctl())
c.client = FakeClient({'미디어': 'rtsp://cam'})   # 스크린샷을 못 주는 OBS
st = c.refresh_cameras(wait_s=3)
check(st['frame'] == [] and st['error'] == '', '찍지 못하면 아무것도 안 한다(던지지 않는다)', str(st))

print('\n[8] 카메라를 다시 여는 곳은 한 함수(_restart_media)를 지난다')
osrc = open(os.path.join(ROOT, 'obs_controller.py'), encoding='utf-8').read()
check(osrc.count('trigger_media_input_action(') == 1, 'RESTART 를 보내는 곳은 하나', str(osrc.count('trigger_media_input_action(')))
check('MEDIA_RESTART' not in osrc.replace('MEDIA_INPUT_ACTION_RESTART', ''), '같은 값의 상수가 두 벌이 아니다')

print()
if fails:
    print(f'❌ {len(fails)}개 실패')
    sys.exit(1)
print('✅ 전부 통과')
