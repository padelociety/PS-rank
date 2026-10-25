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
2026-10-25 (*"라이브가 하나 끝나고 두번째 리그할때 연결이 안되네? Obs보면 종료중이라고 뜨고 종료가 안되더라고"*):
  · 잠깐(grace) 안에 안 멈추면 **한 번 더 끈다** — '종료 중' 에 다시 보낸 끄기는 OBS 가 강제 종료로 받는다.
  · 강제 종료 뒤에도 안 멈추면 False + 최근 OBS 로그의 송출 줄을 남긴다(stop_report.log) · 재연결 중이었나도 남긴다.
  · 방송 서버는 OBS 를 끈 **뒤에** YouTube 방송을 끝낸다(켜기 실패 정리도) · 못 껐으면 무엇을 할지 말한다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import obs_controller  # noqa: E402
from obs_controller import OBSController  # noqa: E402

fails = []


def check(cond, msg, detail=''):
    print(('  ok   ' if cond else '  FAIL ') + msg + ((' — ' + str(detail)) if detail and not cond else ''))
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
check(c.stop_stream(timeout=2) is False and cl.stop_requests == 2, '끄기를 보냈는데 계속 송출 중이면 한 번 더(강제 종료) 끄고 그래도면 False')
check(c.stop_report['forced'] is True and c.stop_report['stopped'] is False and c.stop_report['reached'] is True,
      'stop_report — 강제 종료를 보냈고 안 멈췄다')
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

print('\n[6] 연결이 **있는데 죽어 있으면** 버리고 새로 붙어 끈다(검토 2026-10-08)')


class DeadClient:
    def __init__(self): self.calls = 0
    def get_stream_status(self):
        self.calls += 1
        raise ConnectionError('socket dead')
    def stop_stream(self): raise ConnectionError('socket dead')
    def disconnect(self): pass


obs_controller.time.sleep = lambda s: None
fresh = FakeClient(active=True, lag=1)
ws = FakeWS(fresh); with_ws(ws)
c = OBSController({'obs': {}})
dead = DeadClient()
c.client = dead
ok = c.stop_stream(timeout=5)
check(dead.calls >= 1 and ws.made == 1, '죽은 연결에서 던지면 한 번 새로 붙는다')
check(fresh.stop_requests == 1 and ok is True and fresh.active is False, '새 연결로 끄기를 보내고 멈춘 걸 확인한다')

print('\n[7] is_streaming — 죽은 연결이면 새로 붙어 한 번 더 묻는다(바로 아니오 라고 하지 않는다)')
live = FakeClient(active=True)
ws = FakeWS(live); with_ws(ws)
c = OBSController({'obs': {}})
c.client = DeadClient()
check(c.is_streaming() is True and ws.made == 1, '새 연결로 다시 물어 송출 중이라고 답한다')

print('\n[8] wait_until_stopped — 죽은 연결로 시간을 다 쓰지 않고 새로 붙어 확인한다')
quiet = FakeClient(active=False)
ws = FakeWS(quiet); with_ws(ws)
c = OBSController({'obs': {}})
c.client = DeadClient()
check(c.wait_until_stopped(timeout=5) is True and ws.made == 1, '새 연결로 꺼진 걸 확인한다')

print('\n[9] 죽은 연결 뒤 다시 붙을 때는 한 번만 — OBS 가 멈춰 있으면 붙기마다 기다려 태블릿 기다림(45초)을 넘긴다(검토 2차)')
obs_controller.time.sleep = lambda s: None
ws = FakeWS(None, fail=True); with_ws(ws)
c = OBSController({'obs': {}})
c.client = DeadClient()
seen = []
_orig_connect = c.connect
def _spy(retries=3, delay=2.0):
    seen.append(retries)
    return _orig_connect(retries=retries, delay=delay)
c.connect = _spy
ok = c.stop_stream(timeout=5)
check(ok is False and seen == [1] and ws.made == 1, '두 번째 붙기는 retries=1 로 한 번만 시도하고 False')

print("\n[10] '종료 중' 에서 안 끝나면 한 번 더 끈다 — 두 번째 끄기(강제 종료)로 멈춘다 (2026-10-25)")


class StuckClient(FakeClient):
    """첫 끄기는 받아 놓고 '종료 중' 에서 안 끝난다(남은 영상을 못 보냄) · 두 번째 끄기(강제)에 lag 번 뒤 멈춘다."""
    def __init__(self, reconnecting=False, force_works=True, **kw):
        super().__init__(**kw)
        self.reconnecting = reconnecting
        self.force_works = force_works

    def get_stream_status(self):
        r = super().get_stream_status()
        r.output_reconnecting = self.reconnecting if self.active else False
        return r

    def stop_stream(self):
        self.stop_requests += 1
        if self.stop_requests >= 2 and self.force_works:
            self._countdown = self.lag


t = [0.0]
obs_controller.time.time = lambda: t[0]
obs_controller.time.sleep = lambda s: t.__setitem__(0, t[0] + s)
cl = StuckClient(active=True, lag=2)
ws = FakeWS(cl); with_ws(ws)
c = OBSController({'obs': {}}); c.client = cl
t0 = t[0]
ok = c.stop_stream()
check(ok is True and cl.active is False and cl.stop_requests == 2, '두 번째 끄기(강제 종료)로 멈추면 True')
check(c.stop_report['forced'] is True and c.stop_report['stopped'] is True, 'stop_report — 강제 종료로 멈췄다')
check(4.5 <= t[0] - t0 <= 7, f'처음엔 {OBSController.STOP_GRACE_S:.0f}초만 기다린다(경기는 끝났다 — 마지막 몇 초는 버려도 된다)')

cl = StuckClient(active=True, lag=1, reconnecting=True)
ws = FakeWS(cl); with_ws(ws)
c = OBSController({'obs': {}}); c.client = cl
c.stop_stream()
check(c.stop_report['reconnecting'] is True, '끌 때 재연결 중이었으면 남긴다(종료 중에 다시 붙으려던 흔적)')

cl = FakeClient(active=True, lag=0)
ws = FakeWS(cl); with_ws(ws)
c = OBSController({'obs': {}}); c.client = cl
check(c.stop_stream() is True and cl.stop_requests == 1 and c.stop_report['forced'] is False,
      '바로 멈추면 한 번만 끈다(강제 종료 없음)')

print("\n[11] 강제 종료 뒤에도 안 멈추면 False + 최근 OBS 로그의 송출 줄을 남긴다")
import tempfile, shutil  # noqa: E401,E402
tmp = tempfile.mkdtemp(prefix='obslog_')
logs = os.path.join(tmp, 'logs')
os.makedirs(logs)
with open(os.path.join(logs, '2026-10-24 19-00-00.txt'), 'w', encoding='utf-8') as f:
    f.write('[rtmp stream: old] 옛 파일\n')
newest = os.path.join(logs, '2026-10-25 19-00-00.txt')
with open(newest, 'w', encoding='utf-8') as f:
    f.write('\n'.join([
        '19:00:01.000: ==== Streaming Start ===============================================',
        '19:00:01.100: [rtmp stream: \'adv_stream\'] Connecting to RTMP URL rtmp://a.rtmp.youtube.com/live2...',
        '19:30:00.000: audio settings reset',
        '19:31:00.000: [rtmp stream: \'adv_stream\'] User stopped the stream',
        '19:31:10.000: Output \'adv_stream\': Reconnecting in 3.00 seconds..',
        '19:31:12.000: [obs_qsv11_v2] warning: something',
    ]) + '\n')
os.utime(os.path.join(logs, '2026-10-24 19-00-00.txt'), (1, 1))
cl = StuckClient(active=True, force_works=False)
ws = FakeWS(cl); with_ws(ws)
c = OBSController({'obs': {'config_dir': tmp}}); c.client = cl
ok = c.stop_stream()
log = c.stop_report['log']
check(ok is False and cl.stop_requests == 2 and c.stop_report['stopped'] is False, '두 번 끄고도 송출 중이면 False')
check(any('User stopped the stream' in x for x in log) and any('Reconnecting in' in x for x in log),
      '가장 최근 OBS 로그의 송출·재연결 줄을 남긴다', log)
check(not any('audio settings reset' in x for x in log) and not any('옛 파일' in x for x in log),
      '상관없는 줄 · 옛 로그 파일은 안 싣는다', log)
c2 = OBSController({'obs': {'config_dir': os.path.join(tmp, 'none')}})
check(c2.obs_log_lines() == [], 'OBS 로그 폴더가 없으면 []')

print('\n[11-2] OBS 를 다시 띄운 뒤에도 멈췄던 앞 파일을 읽는다 · 비밀번호를 가린다 · 시작 잡음은 뺀다 (2026-10-25)')
# 신고 그대로: 멈춘 OBS 를 다시 띄우고 점검하니 새 파일(방송 없음)만 읽어 'Crash or unclean shutdown' 과 모듈 목록만 나왔다.
#   그리고 카메라 RTSP 주소가 비밀번호째 찍혔다.
with open(newest, 'w', encoding='utf-8') as f:
    f.write('\n'.join([
        '19:00:00.500: [rtmp stream: \'adv_stream\'] 앞 방송(잘려야 한다)',
        '19:00:01.000: ==== Streaming Start ===============================================',
        '19:00:01.200:         input:                   rtsp://admin:QkEpf2501@192.168.1.7:554/Streaming/Channels/101',
        '19:31:00.000: [rtmp stream: \'adv_stream\'] User stopped the stream',
        '19:31:10.000: Output \'adv_stream\': Reconnecting in 3.00 seconds..',
    ]) + '\n')
restart = os.path.join(logs, '2026-10-25 19-40-00.txt')
with open(restart, 'w', encoding='utf-8') as f:
    f.write('\n'.join([
        '19:40:00.000: Crash or unclean shutdown detected',
        "19:40:01.000: Failed to initialize module 'aja.dll'",
        '19:40:01.100: LoadLibrary failed for \'nvEncodeAPI64.dll\': The specified module could not be found.',
        '19:40:01.200:     obs-outputs.dll',
        '19:40:02.000: ==== Replay Buffer Start ===========================================',
    ]) + '\n')
os.utime(newest, (100, 100))
os.utime(restart, (200, 200))
c3 = OBSController({'obs': {'config_dir': tmp}})
check(c3.obs_log_file() == newest, "가장 새 파일에 방송이 없으면(다시 띄운 직후) 마지막으로 방송한 앞 파일", c3.obs_log_file())
lines = c3.obs_log_lines()
check(any('User stopped the stream' in x for x in lines) and not any('앞 방송' in x for x in lines),
      '그 파일의 **마지막 방송부터**', lines)
check(any('rtsp://admin:***@192.168.1.7' in x for x in lines) and not any('QkEpf2501' in x for x in lines),
      '카메라 주소의 비밀번호를 가린다', lines)
noisy = c3.obs_log_lines(path=restart)
check(not any('aja.dll' in x or 'nvEncodeAPI64' in x or 'obs-outputs.dll' in x for x in noisy)
      and any('Replay Buffer Start' in x for x in noisy), '시작 때 늘 찍히는 모듈 줄은 뺀다', noisy)
check(OBSController.mask_secrets('rtmp://a.rtmp.youtube.com/live2') == 'rtmp://a.rtmp.youtube.com/live2',
      '비밀번호 없는 주소는 그대로')
os.remove(newest)
os.remove(os.path.join(logs, '2026-10-24 19-00-00.txt'))
check(c3.obs_log_file() == restart, '방송한 파일이 없으면 가장 새 파일')
src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'obs_check.py'), encoding='utf-8').read()
check('obs_log_file()' in src and 'obs_log_lines(80, path=path)' in src, '점검 화면도 같은 파일 고르기 · 가림을 쓴다')
shutil.rmtree(tmp, ignore_errors=True)

print('\n[12] 끄기와 강제 종료 사이에 꺼졌으면 True(두 번째는 안 보낸다)')


class LateStop(FakeClient):
    def stop_stream(self):
        self.stop_requests += 1
        self._countdown = 10 ** 6  # grace 안엔 안 멈춘다

    def get_stream_status(self):
        r = super().get_stream_status()
        if t[0] >= self.off_at:
            r.output_active = False
            self.active = False
        return r


cl = LateStop(active=True)
cl.off_at = t[0] + OBSController.STOP_GRACE_S + 0.1
ws = FakeWS(cl); with_ws(ws)
c = OBSController({'obs': {}}); c.client = cl
check(c.stop_stream() is True and cl.stop_requests == 1, 'grace 직후 꺼져 있으면 강제 종료를 안 보낸다')
obs_controller.time.time = real_time
obs_controller.time.sleep = lambda s: None

print('\n[13] 방송 서버 — OBS 를 끈 뒤에 YouTube 를 끝낸다 · 못 껐으면 할 일을 말한다')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_stream_server as tss  # noqa: E402

ss = tss.load_server()
cl_ = ss.app.test_client()
order = []
_stop0, _end0 = ss.obs.stop_stream, ss.youtube.end_broadcast
ss.obs.stop_stream = lambda timeout=None, grace=None: (order.append('obs-stop'), _stop0())[1]
ss.youtube.end_broadcast = lambda b: (order.append('yt-end'), _end0(b))[1]
r = cl_.post('/start-stream', json={'teamA': ['가', '나'], 'teamB': ['다', '라'], 'league': 'PSiL 26S3',
                                    'matchNumber': 1, 'matchId': 'm1'})
check(r.status_code == 200, '방송 시작', r.get_json())
order.clear()
cl_.post('/stop-stream', json={'matchId': 'm1'})
check(order[:2] == ['obs-stop', 'yt-end'], '끄기 — OBS 먼저, 그다음 YouTube', order)

_start0 = ss.obs.start_stream
ss.obs.start_stream = lambda: (_ for _ in ()).throw(RuntimeError('OBS 가 시작을 거절'))
order.clear()
r = cl_.post('/start-stream', json={'teamA': ['가', '나'], 'teamB': ['다', '라'], 'league': 'PSiL 26S3',
                                    'matchNumber': 2, 'matchId': 'm2'})
check(r.status_code == 500 and order[-2:] == ['obs-stop', 'yt-end'], '켜기 실패 정리도 OBS 먼저, 그다음 빈 방송 닫기', order)
ss.obs.start_stream = _start0

ss.obs.stop_report = {'at': 1.0, 'reached': True, 'stopped': False, 'forced': True, 'reconnecting': True, 'seconds': 20.1,
                      'log': ['비밀 아님']}
check("'종료 중' 에서 멈췄어요" in ss._obs_stop_hint() and 'OBS 를 껐다 켜' in ss._obs_stop_hint(),
      '강제 종료 뒤에도 안 멈췄으면 — OBS 를 껐다 켜 달라고', ss._obs_stop_hint())
ss.obs.stop_report = {'reached': False}
check('켜져 있는지' in ss._obs_stop_hint(), 'OBS 에 못 닿았으면 — 켜져 있는지')
ss.obs.stop_report = None
check('[방송 중단]' in ss._obs_stop_hint(), '기록이 없으면 예전 문장')

ss.obs.stop_report = {'at': 1.0, 'reached': True, 'stopped': False, 'forced': True, 'reconnecting': True, 'seconds': 20.1,
                      'log': ['줄']}
ss.obs.streaming = True
ss.obs.stop_stream = lambda timeout=None, grace=None: False
r = cl_.post('/start-stream', json={'teamA': ['가', '나'], 'teamB': ['다', '라'], 'league': 'PSiL 26S3',
                                    'matchNumber': 3, 'matchId': 'm3'})
err = (r.get_json() or {}).get('error') or ''
check(r.status_code == 500 and "'종료 중' 에서 멈췄어요" in err, "다음 경기 켜기가 막히면 태블릿에 'OBS 를 껐다 켜' 를 말한다", err)
hv = cl_.get('/health').get_json().get('obs_stop') or {}
check(hv.get('forced') is True and hv.get('stopped') is False and hv.get('reconnecting') is True and 'log' not in hv,
      '/health obs_stop — 로그 줄은 안 싣는다(서버 로그에만)', hv)
ss.obs.streaming = False

print('\n' + ('전부 통과' if not fails else f'실패 {len(fails)}건'))
sys.exit(1 if fails else 0)
