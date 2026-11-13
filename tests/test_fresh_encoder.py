"""
방송을 켤 때마다 인코더 새로 띄우기 · 끝난 방송 때문에 OBS 를 끄지 않기(2026-11-13) — OBS·유튜브 없이 돈다.

    python tests/test_fresh_encoder.py

"자꾸 OBS 강제종료 되버리는데? 방송 하나 끝나고나서" — /health: Match239 가 시작부터 유튜브 noData → noData 감시가
송출을 껐다 켜도 그대로 → OBS 를 taskkill → 60초 안에 OBS 가 다시 안 떴다. 첫 방송은 멀쩡하고 그 뒤 방송만 그랬다:
방송과 리플레이 버퍼가 인코더 하나를 같이 쓰고(11-11) 리플레이 버퍼를 늘 켜 두니, 다음 방송이 앞 방송의 인코더에 붙는다.
  ① 방송 켜기: 키 넣기 → 리플레이 버퍼 끄기(인코더 내림) → 송출 → 리플레이 버퍼 다시
  ② noData 1단계(송출 다시)도 인코더를 새로 띄운다
  ③ 자동 감시가 OBS 를 다시 켜려는 사이 그 방송이 끝났으면 OBS 를 안 건드린다
  ④ obs_controller.fresh_encoder — 끄고 멈출 때까지 기다린다 · 꺼져 있으면 그대로 · 안 멈춰도/못 붙어도 던지지 않는다 · config 로 끔
  ⑤ OBS 다시 띄우기 — 작업 /End 뒤 /Run · 프로세스가 안 보이면 한 번 더
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import test_stream_server as tss  # noqa: E402

fails = []


def check(cond, msg, detail=''):
    print(('  ok   ' if cond else '  FAIL ') + msg + (('\n        ' + str(detail)) if detail and not cond else ''))
    if not cond:
        fails.append(msg)


ss = tss.load_server()
ss.time.sleep = lambda s: None
cl = ss.app.test_client()
ss.youtube.end_broadcast = lambda bid: True
ss.youtube.stream_health = lambda bid: {'status': 'noData', 'issues': []}
ss.obs.fresh_encoder = lambda: (ss.obs.calls.append('fresh'), {'did': True, 'released': True, 'error': ''})[1]
ss.obs.start_replay_buffer = lambda: ss.obs.calls.append('replay')
restarts = []
ss.obs.restart_obs_process = lambda: (restarts.append(1), True)[1]


def start(n):
    ss.obs.calls.clear()
    r = cl.post('/start-stream', json={'teamA': ['가', '나'], 'teamB': ['다', '라'], 'league': 'PSiL 26S3',
                                       'matchNumber': n, 'matchId': f'm{n}'})
    assert r.get_json().get('success'), r.get_json()
    return ss.stream_state['broadcast_id'], ss.datetime.fromisoformat(ss.stream_state['started_at']).timestamp()


print('\n[①] 방송 켜기 — 인코더를 새로 띄우고 송출')
start(1)
c = list(ss.obs.calls)
check(c == ['set', 'refresh', 'fresh', 'start', 'replay'], '키 넣기 → 카메라 → 리플레이 버퍼 끄기 → 송출 → 리플레이 버퍼 다시', c)
cl.post('/stop-stream', json={})
start(2)
c = list(ss.obs.calls)
check(c.index('fresh') < c.index('start') < c.index('replay'), '방송 하나가 끝난 뒤의 방송도 같다(여기가 죽던 자리)', c)
check('encoder_reset' in cl.get('/health').get_json(), '/health encoder_reset')
ss.obs.fresh_encoder = lambda: (_ for _ in ()).throw(RuntimeError('OBS 가 이상해요'))
cl.post('/stop-stream', json={})
bid, t0 = start(3)
check(ss.obs.streaming and ss.stream_state['active'], '새로 띄우기가 던져도 방송은 켠다')
ss.obs.fresh_encoder = lambda: (ss.obs.calls.append('fresh'), {'did': True, 'released': True, 'error': ''})[1]

print('\n[②] noData 1단계 — 송출 다시도 인코더를 새로')
ss.obs.calls.clear()
ss._nodata_tick(t0 + 31); ss._nodata_tick(t0 + 47)
c = list(ss.obs.calls)
check(c == ['stop', 'set', 'fresh', 'start', 'replay'], '끄기 → 키 → 리플레이 버퍼 끄기 → 송출 → 리플레이 버퍼 다시', c)
check(restarts == [], 'OBS 는 안 껐다')

print('\n[③] 그 방송이 끝났으면 OBS 를 안 건드린다')
res = ss._restart_obs_and_resume('유튜브 noData', only_bid='없는방송')
check(res.get('skipped') and not res['ok'] and restarts == [], '다른 방송이면 그대로', res)
cl.post('/stop-stream', json={})
res = ss._restart_obs_and_resume('유튜브 noData', only_bid=bid)
check(res.get('skipped') and restarts == [], '끝난 방송이면 그대로(예전엔 OBS 를 껐다 켰다)', res)
res = ss._restart_obs_and_resume('운영자 요청')
check(res['ok'] and restarts == [1], '운영자 [OBS 다시 켜기] 는 방송이 없어도 된다', res)

print('\n[④] obs_controller.fresh_encoder')
sys.modules.pop('obs_controller', None)
import importlib  # noqa: E402
oc = importlib.import_module('obs_controller')
oc.time.sleep = lambda s: None


class R:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Client:
    def __init__(self, active=True, lag=2, stuck=False):
        self.active, self.lag, self.stuck, self.calls = active, lag, stuck, []

    def get_replay_buffer_status(self):
        if self.calls and self.calls[-1] == 'stop' and not self.stuck:
            self.lag -= 1
            if self.lag <= 0:
                self.active = False
        return R(output_active=self.active)

    def stop_replay_buffer(self):
        self.calls.append('stop')


k = oc.OBSController({'obs': {}})
k.client = Client()
st = k.fresh_encoder()
check(st['did'] and st['released'] and not st['error'] and k.client.calls == ['stop'], '끄고 멈출 때까지 기다린다', st)
check(k.encoder_reset_status is st, 'encoder_reset_status 에 남는다')
k.client = Client(active=False)
st = k.fresh_encoder()
check(not st['did'] and st['released'] and k.client.calls == [], '꺼져 있으면 그대로', st)
k.client = Client(stuck=True)
st = k.fresh_encoder(wait_s=0.6)
check(st['did'] and not st['released'] and '안 멈췄' in st['error'], '안 멈추면 이유를 적고 돌아온다(방송은 켠다)', st)
k.client = None
k.connect = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('OBS 없음'))
st = k.fresh_encoder()
check(not st['did'] and 'OBS 없음' in st['error'], '못 붙어도 던지지 않는다', st)
k2 = oc.OBSController({'obs': {'fresh_encoder_on_start': False}})
k2.client = Client()
st = k2.fresh_encoder()
check(not st['did'] and k2.client.calls == [], 'config obs.fresh_encoder_on_start: false 면 안 건드린다', st)

print('\n[⑤] OBS 다시 띄우기')


class RR:
    def __init__(self, out=b''):
        self.returncode, self.stdout, self.stderr = 0, out, b''


k3 = oc.OBSController({'obs': {}})
ran = []
alive = [2]            # taskkill 뒤 두 번은 아직 떠 있다


def run(cmd, timeout=20):
    ran.append(cmd[0] + (':' + cmd[1].strip('/') if cmd[0] == 'schtasks' else ''))
    if cmd[0] == 'tasklist':
        alive[0] -= 1
        return RR(b'obs64.exe  1234 Console' if alive[0] >= 0 else b'INFO: No tasks')
    return RR()


k3._run = run
k3.connect = lambda *a, **kw: setattr(k3, 'client', object())
check(k3.restart_obs_process(wait_s=30) is True
      and ran == ['taskkill', 'tasklist', 'tasklist', 'tasklist', 'schtasks:End', 'schtasks:Run'],
      'taskkill → 프로세스가 사라질 때까지 → 작업 /End → /Run', ran)
k4 = oc.OBSController({'obs': {}})
ran.clear()
alive[0] = -1
clock = [1000.0]
oc.time.time = lambda: clock[0]
oc.time.sleep = lambda s: clock.__setitem__(0, clock[0] + s)
k4._run = run
k4.connect = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('안 뜸'))
check(k4.restart_obs_process(wait_s=60) is False and ran.count('schtasks:Run') == 2 and k4.obs_restart_status['launches'] == 2,
      '20초 뒤에도 프로세스가 없으면 한 번 더 띄운다(한 번만)', (ran, k4.obs_restart_status))
check('다시 뜨지 않았' in k4.obs_restart_status['error'], '끝내 안 뜨면 이유', k4.obs_restart_status)

print('\n' + ('FAIL %d' % len(fails) if fails else '전부 통과'))
sys.exit(1 if fails else 0)
