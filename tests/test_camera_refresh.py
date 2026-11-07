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
check(re.search(r"obs\.set_stream_settings\(rtmp_url, stream_key\)\s*\n(\s*#[^\n]*\n)*\s*obs\.refresh_cameras\(\)\s*\n\s*obs\.start_stream\(\)", src) is not None,
      'set_stream_settings → refresh_cameras → start_stream 순서')
check("'camera_refresh': getattr(obs, 'camera_refresh_status', None)" in src, '/health camera_refresh')

print()
if fails:
    print(f'❌ {len(fails)}개 실패')
    sys.exit(1)
print('✅ 전부 통과')
