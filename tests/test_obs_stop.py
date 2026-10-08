"""
OBS 송출 끄기·켜기 약속 — OBS 없이 돈다(가짜 클라이언트).

    python tests/test_obs_stop.py

2026-10-08 (26S3 — Match39 제목·썸네일 방송에 Match 41 이 나갔다):
  · 끄기가 연결(client)이 없으면 **아무것도 안 하고** 돌아갔다 → OBS 는 지난 방송 키로 계속 송출.
  · 켜기가 '이미 스트리밍 중' 이면 성공으로 쳤다 → 새 키는 송출을 다시 시작해야 먹으므로
    영상은 계속 지난 방송으로 갔다.
여기서 못 박는 것:
  · stop_stream 은 연결이 없으면 붙여서 끄고, **실제로 멈춘 걸 확인해야** True.
  · 붙지 못하거나 시간 안에 안 멈추면 False(꺼졌다고 단정하지 않는다).
  · start_stream 은 이미 송출 중이면 던진다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import obs_controller  # noqa: E402
from obs_controller import OBSController  # noqa: E402

fails = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        fails.append(msg)


class R:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeClient:
    """stop_stream 요청 뒤 lag 번 더 물어볼 때까지 송출 중으로 답한다(실물 OBS 처럼 비동기로 멈춘다)."""
    def __init__(self, active=True, lag=2, never_stops=False):
        self.active = active
        self.lag = lag
        self.never_stops = never_stops
        self.stop_requests = 0
        self.start_requests = 0
        self._countdown = None

    def get_version(self):
        return R(obs_version='30.0')

    def get_stream_status(self):
        if self._countdown is not None and not self.never_stops:
            if self._countdown <= 0:
                self.active = False
                self._countdown = None
            else:
                self._countdown -= 1
        return R(output_active=self.active)

    def stop_stream(self):
        self.stop_requests += 1
        self._countdown = self.lag

    def start_stream(self):
        self.start_requests += 1
        self.active = True

    def get_input_list(self):
        return R(inputs=[])

    def disconnect(self):
        pass


class FakeWS:
    """obsws_python 대역 — connect() 가 ReqClient 를 만든다."""
    def __init__(self, client, fail=False):
        self.client = client
        self.fail = fail
        self.made = 0

    def ReqClient(self, **kw):
        self.made += 1
        if self.fail:
            raise ConnectionRefusedError('OBS 꺼짐')
        return self.client


def with_ws(fake):
    sys.modules['obsws_python'] = fake


# 기다림을 빠르게 — 실물은 0.4초 간격
obs_controller.time.sleep = lambda s: None

print('\n[1] 연결이 없어도 붙여서 끈다 (예전: 아무것도 안 하고 돌아갔다)')
cl = FakeClient(active=True, lag=2)
ws = FakeWS(cl); with_ws(ws)
c = OBSController({'obs': {}})
c.client = None   # 키프알라이브가 오류 한 번에 비운 직후
ok = c.stop_stream(timeout=5)
check(ws.made >= 1, '연결이 없으면 새로 붙는다')
check(cl.stop_requests == 1, 'OBS 에 끄기를 보낸다')
check(ok is True and cl.active is False, '실제로 멈춘 걸 확인하고 True')
check(c.client is None, '끝나면 연결을 놓는다(예전과 같다)')

print('\n[2] 이미 꺼져 있으면 True — 끄기를 보내지 않는다')
cl = FakeClient(active=False)
ws = FakeWS(cl); with_ws(ws)
c = OBSController({'obs': {}}); c.client = cl
check(c.stop_stream(timeout=1) is True and cl.stop_requests == 0, '꺼진 OBS 는 그대로 True')

print('\n[3] 붙지 못하면 False — 껐다고 믿지 않는다')
ws = FakeWS(None, fail=True); with_ws(ws)
c = OBSController({'obs': {}}); c.client = None
obs_controller.time.sleep = lambda s: None
check(c.stop_stream(timeout=1) is False, 'OBS 에 못 붙으면 False')

print('\n[4] 시간 안에 안 멈추면 False')
cl = FakeClient(active=True, never_stops=True)
ws = FakeWS(cl); with_ws(ws)
c = OBSController({'obs': {}}); c.client = cl
real_time = obs_controller.time.time
t = [0.0]
obs_controller.time.time = lambda: t[0]
obs_controller.time.sleep = lambda s: t.__setitem__(0, t[0] + s)
check(c.stop_stream(timeout=2) is False and cl.stop_requests == 1, '끄기를 보냈는데 계속 송출 중이면 False')
obs_controller.time.time = real_time
obs_controller.time.sleep = lambda s: None

print('\n[5] 켜기는 이미 송출 중이면 던진다 (예전: "이미 스트리밍 중이에요" 로 성공)')
cl = FakeClient(active=True)
ws = FakeWS(cl); with_ws(ws)
c = OBSController({'obs': {}}); c.client = cl
try:
    c.start_stream()
    check(False, '송출 중인데 start_stream 이 통과했다')
except RuntimeError as e:
    check('이미 다른 방송' in str(e) and cl.start_requests == 0, '던지고 OBS 에 시작을 안 보낸다')

cl = FakeClient(active=False)
ws = FakeWS(cl); with_ws(ws)
c = OBSController({'obs': {}}); c.client = cl
c.start_stream()
check(cl.start_requests == 1 and cl.active, '꺼져 있으면 시작한다')

print('\n' + ('전부 통과' if not fails else f'실패 {len(fails)}건'))
sys.exit(1 if fails else 0)
