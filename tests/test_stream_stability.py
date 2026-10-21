"""
유튜브 방송 끊김 줄이기 (2026-10-21 — *"요즘 유투브가 자주 끊긴대"*) — OBS·YouTube 없이 돈다.

    python tests/test_stream_stability.py

여기서 못 박는 것:
  ① 지연 — 방송은 normal(일반)로 만든다. config 에 남은 ultraLow(초저지연 · 시청자 화면이 자주 멈춘다)는 normal 로.
  ② 자동 종료를 끈다 — 업로드가 1분 남짓 끊겨도 YouTube 가 방송을 끝내지 않는다. 끝내기는 서버가 한다:
     이미 끝났으면(redundantTransition) 됐다 · 한 번도 영상을 못 받은 빈 방송은 지운다 · 실패하면 뒤에서 다시.
  ③ 진행 중인 방송을 디스크에 적는다 — 서버가 죽었다 뜨면 OBS 가 송출 중인 방송은 이어받고, 아니면 끝낸다.
  ④ OBS '자동으로 다시 연결' 을 켜 둔다(꺼져 있으면 한 번 흔들릴 때 송출이 그대로 멈춘다).
  ⑤ 방송 상태 감시 — 재연결 · 업로드 · 인코딩 · 카메라 · YouTube 경고를 한 줄로(/health stream_health).
"""
import io
import json
import os
import sys
import time

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


PLAY = 'OBS_MEDIA_STATE_PLAYING'

# ═══════════════════════════════════════════════════════════════
print('\n[⑤-1] 상태 판정 — 숫자 → 지금 문제 하나')
base = {'active': True, 'reconnecting': False, 'congestion': 0, 'bytes': 0, 'net_skipped': 0, 'net_total': 0,
        'enc_skipped': 0, 'enc_total': 0, 'render_skipped': 0, 'render_total': 0, 'cpu': 20, 'fps': 30, 'media': {}}


def snap(**kw):
    d = dict(base)
    d.update(kw)
    return d


h = sh.evaluate(snap(), snap(bytes=7_500_000, net_total=300, enc_total=300, render_total=300), elapsed_s=10)
check(h['issue'] == '' and h['text'] == '', '정상이면 문제 없음', h)
check(h['kbps'] == 6000, '10초에 7.5MB → 6000kbps', h['kbps'])

h = sh.evaluate(snap(), snap(reconnecting=True), elapsed_s=10)
check(h['issue'] == 'reconnecting' and h['short'] == '재연결 중', '재연결 중이면 그게 첫 문제', h)
check(h['kbps'] == 0, '재연결 중엔 속도 0', h['kbps'])

h = sh.evaluate(snap(net_skipped=0, net_total=0), snap(net_skipped=12, net_total=300), elapsed_s=10)
check(h['issue'] == 'network' and h['net_drop_pct'] == 4.0, '프레임 4% 버림 → 업로드 불안정', h)
h = sh.evaluate(snap(net_skipped=0, net_total=0), snap(net_skipped=3, net_total=300), elapsed_s=10)
check(h['issue'] == '', '1% 버림은 넘긴다(문턱 2%)', h)
h = sh.evaluate(snap(), snap(congestion=0.7, net_total=300), elapsed_s=10)
check(h['issue'] == 'network', '혼잡 0.7 → 업로드 불안정', h)
h = sh.evaluate(snap(enc_total=0), snap(enc_skipped=15, enc_total=300, net_total=300), elapsed_s=10)
check(h['issue'] == 'encoder' and h['short'] == 'PC 과부하', '인코딩 5% 밀림 → PC 과부하', h)
h = sh.evaluate(snap(), snap(render_skipped=30, render_total=300, net_total=300), elapsed_s=10)
check(h['issue'] == 'render', '그리기 10% 밀림 → 화면 밀림', h)

# 누적 숫자가 0 으로 돌아가면(송출 다시 시작) 음수 차이 대신 지금 값
h = sh.evaluate(snap(net_skipped=500, net_total=90000), snap(net_skipped=0, net_total=300), elapsed_s=10)
check(h['net_drop_pct'] == 0.0 and h['issue'] == '', '숫자가 처음으로 돌아가도 거짓 경보 없음', h)

# 카메라 — 두 번 연속 안 돌아야
cams = {}
h1 = sh.evaluate(snap(), snap(media={'코트 카메라': 'OBS_MEDIA_STATE_ENDED'}), camera_bad_ticks=cams)
check(h1['issue'] == '', '카메라 한 번 끊김은 넘긴다(장면 전환·잠깐 버퍼링)', h1)
h2 = sh.evaluate(snap(), snap(media={'코트 카메라': 'OBS_MEDIA_STATE_ERROR'}), camera_bad_ticks=cams)
check(h2['issue'] == 'camera' and '코트 카메라' in h2['text'], '두 번 연속 → 카메라 끊김(이름까지)', h2)
h3 = sh.evaluate(snap(), snap(media={'코트 카메라': PLAY}), camera_bad_ticks=cams)
check(h3['issue'] == '' and cams == {}, '다시 돌면 풀린다', (h3, cams))

# 순서 — 재연결이 카메라·업로드보다 먼저
cams = {'cam': 5}
h = sh.evaluate(snap(), snap(reconnecting=True, media={'cam': 'OBS_MEDIA_STATE_ERROR'}, congestion=0.9),
                camera_bad_ticks=cams)
check(h['issue'] == 'reconnecting' and h['issues'][:2] == ['reconnecting', 'camera'],
      '여럿이면 재연결 → 카메라 순(고칠 순서)', h['issues'])
check('network' not in h['issues'], '재연결 중엔 업로드 불안정을 따로 세지 않는다', h['issues'])

# YouTube 경고
yt = {'status': 'bad', 'issues': [{'type': 'gopSizeLong', 'severity': 'error', 'reason': 'x'},
                                  {'type': 'bitrateLow', 'severity': 'info', 'reason': 'y'}]}
h = sh.evaluate(snap(), snap(net_total=300), youtube=yt)
check(h['issue'] == 'youtube' and '키프레임' in h['text'], 'YouTube bad → 유튜브 경고 + 이유(키프레임)', h)
check(h['youtube']['lines'] == ['키프레임 간격이 너무 길어요(OBS 키프레임 간격 2초)'],
      'info 수준은 줄에서 뺀다', h['youtube'])
h = sh.evaluate(snap(), snap(net_total=300), youtube={'status': 'good', 'issues': []})
check(h['issue'] == '', 'YouTube good → 문제 없음', h)
h = sh.evaluate(snap(), snap(net_total=300),
                youtube={'status': 'ok', 'issues': [{'type': 'newThing', 'severity': 'warning', 'reason': 'Odd thing'}]})
check(h['issue'] == '' and h['youtube']['lines'] == ['Odd thing'], '경고(warning)는 줄에만 · 모르는 type 은 reason 그대로', h)

check(sh.is_network_input({'is_local_file': False, 'input': 'rtsp://u:p@10.0.0.64:554/Streaming/Channels/101'}),
      'RTSP 입력은 카메라')
check(not sh.is_network_input({'is_local_file': True, 'local_file': 'C:/intro.mp4'}), '로컬 파일은 카메라가 아니다')
check(not sh.is_network_input({'input': ''}) and not sh.is_network_input(None), '빈 입력·None 은 아니다')

line = sh.transition_line('', 'reconnecting', sh.evaluate(snap(), snap(reconnecting=True)))
check(line.startswith('⚠️ 방송 상태:') and '재연결' in line, '문제가 생기면 ⚠️ 로그 한 줄', line)
line = sh.transition_line('network', '', sh.evaluate(snap(), snap()))
check(line.startswith('✅') and '업로드 불안정' in line, '풀리면 ✅ 로그 한 줄(무엇이 풀렸는지)', line)
check(sh.transition_line('network', 'network', {}) == '', '같으면 로그 없음')

# ═══════════════════════════════════════════════════════════════
print('\n[①②] YouTube — normal 지연 · 자동 종료 끔 · 끝내기')
import youtube_api  # noqa: E402

check(youtube_api.latency_of({}) == 'normal', '기본은 normal')
check(youtube_api.latency_of({'latency': 'ultraLow'}) == 'normal', 'config 의 ultraLow 는 normal 로(시청자 화면이 자주 멈춘다)')
check(youtube_api.latency_of({'latency': 'low'}) == 'low', 'low 는 그대로')
check(youtube_api.latency_of({'latency': 'normal'}) == 'normal', 'normal 은 그대로')
check(youtube_api.latency_of({'latency': 'fast!'}) == 'normal', '모르는 값은 normal')


class HttpErr(Exception):
    def __init__(self, reason):
        super().__init__(f'<HttpError 403 "{reason}">')
        self.content = json.dumps({'error': {'errors': [{'reason': reason}]}}).encode()


class Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeYTClient:
    def __init__(self):
        self.inserted = []
        self.transition_err = None
        self.life = 'live'
        self.deleted = []
        self.stream_list = {'items': [{'status': {'healthStatus': {
            'status': 'bad', 'configurationIssues': [{'type': 'gopSizeLong', 'severity': 'error', 'reason': 'r'}]}}}]}
        self.bound = 'stream-9'
        self.calls = []

    def liveBroadcasts(self):
        cl = self

        class B:
            def insert(s, **k):
                cl.inserted.append(k['body'])
                return Req(lambda: {'id': 'b1'})

            def bind(s, **k):
                return Req(lambda: {})

            def transition(s, **k):
                cl.calls.append('transition')

                def go():
                    if cl.transition_err:
                        raise HttpErr(cl.transition_err)
                    return {}
                return Req(go)

            def list(s, **k):
                cl.calls.append('list:' + k.get('part', ''))
                if k.get('part') == 'contentDetails':
                    return Req(lambda: {'items': [{'contentDetails': {'boundStreamId': cl.bound}}]})
                return Req(lambda: {'items': [{'status': {'lifeCycleStatus': cl.life}}]})

            def delete(s, **k):
                cl.deleted.append(k['id'])
                return Req(lambda: {})
        return B()

    def liveStreams(self):
        cl = self

        class S:
            def insert(s, **k):
                return Req(lambda: {'id': 'stream-1', 'cdn': {'ingestionInfo': {'ingestionAddress': 'rtmp://a', 'streamName': 'k'}}})

            def list(s, **k):
                cl.calls.append('streams:' + k.get('id', ''))
                return Req(lambda: cl.stream_list)
        return S()


api = youtube_api.YouTubeAPI({'youtube': {'latency': 'ultraLow'}})
fy = FakeYTClient()
api.youtube = fy
bid, rtmp, key = api.create_broadcast_and_stream('T', 'D')
cd = fy.inserted[0]['contentDetails']
check(cd['enableAutoStop'] is False, '자동 종료를 끈다(업로드가 잠깐 끊겨도 방송이 안 끝난다)', cd)
check(cd['enableAutoStart'] is True, '자동 시작은 그대로')
check(cd['latencyPreference'] == 'normal', 'config 가 ultraLow 여도 normal 로 만든다', cd)
check(api._stream_of.get('b1') == 'stream-1', '방송 → 스트림 id 를 기억한다')

check(api.end_broadcast('b1') is True and fy.calls[-1] == 'transition', '끝내기 성공 → True')
fy.transition_err = 'redundantTransition'
check(api.end_broadcast('b1') is True, '이미 끝나 있었으면 True(다시 안 해 본다)')
fy.transition_err = 'invalidTransition'; fy.life = 'ready'
check(api.end_broadcast('b2') is True and fy.deleted == ['b2'], '한 번도 영상을 못 받은 빈 방송은 지운다', fy.deleted)
fy.life = 'liveStarting'; fy.deleted.clear()
check(api.end_broadcast('b3') is False and fy.deleted == [], '라이브로 넘어가는 중이면 False(나중에 다시) · 지우지 않는다')
fy.life = 'complete'
check(api.end_broadcast('b4') is True, '이미 complete 면 True')
fy.transition_err = 'backendError'
check(api.end_broadcast('b5') is False, '다른 오류면 False(나중에 다시)')
fy.transition_err = None
check(api.end_broadcast('') is False, '방송 id 가 없으면 False')
api_none = youtube_api.YouTubeAPI({'youtube': {}})
t0 = time.time()
check(api_none.end_broadcast('bX') is False and time.time() - t0 < 5, '인증이 없으면 기다리지 않고 False(무인 PC 에서 브라우저를 띄우지 않는다)')

fy.calls.clear()
check('b1' not in api._stream_of, '끝낸 방송의 스트림 id 는 잊는다')
api._stream_of['b1'] = 'stream-1'   # 다시 진행 중인 방송으로
hs = api.stream_health('b1')
check(hs == {'status': 'bad', 'issues': [{'type': 'gopSizeLong', 'severity': 'error', 'reason': 'r'}]},
      '스트림 건강을 읽는다(status · issues)', hs)
check('streams:stream-1' in fy.calls, '기억한 스트림 id 로 묻는다', fy.calls)
fy.calls.clear()
hs = api.stream_health('b-restarted')
check(hs is not None and 'list:contentDetails' in fy.calls and 'streams:stream-9' in fy.calls,
      '기억이 없으면(서버 재시작) 방송에서 스트림 id 를 읽는다', fy.calls)
fy.stream_list = {'items': []}
check(api.stream_health('b1') is None, '없으면 None')

# ═══════════════════════════════════════════════════════════════
print('\n[④] OBS 자동 재연결 — 연결할 때마다 맞춘다')
import obs_controller  # noqa: E402
from obs_controller import OBSController  # noqa: E402


class FakeClient:
    def __init__(self, params, fail=False):
        self.params = dict(params)
        self.sets = []
        self.fail = fail
        self.media = {}
        self.settings = {}
        self.input_list_calls = 0

    def get_profile_parameter(self, cat, name):
        if self.fail:
            raise RuntimeError('no')
        v = self.params.get(name)
        return R(parameter_value=v, default_parameter_value={'Reconnect': 'true', 'RetryDelay': '2', 'MaxRetries': '25'}[name])

    def set_profile_parameter(self, cat, name, val):
        self.sets.append((cat, name, val))
        self.params[name] = val

    def get_stream_status(self):
        return R(output_active=True, output_reconnecting=True, output_congestion=0.25, output_bytes=1000,
                 output_skipped_frames=4, output_total_frames=200)

    def get_stats(self):
        return R(cpu_usage=33.3, active_fps=29.97, output_skipped_frames=1, output_total_frames=200,
                 render_skipped_frames=0, render_total_frames=200)

    def get_input_list(self, kind=None):
        self.input_list_calls += 1
        return R(inputs=[{'inputName': '코트 카메라'}, {'inputName': '인트로'}])

    def get_input_settings(self, name):
        return R(input_settings=self.settings.get(name, {}))

    def get_media_input_status(self, name):
        return R(media_state=self.media.get(name, PLAY))

    def disconnect(self):
        pass


c = OBSController({'obs': {}})
c.client = FakeClient({'Reconnect': 'false', 'RetryDelay': '20', 'MaxRetries': '5'})
st = c.ensure_reconnect()
check(('Output', 'Reconnect', 'true') in c.client.sets, "꺼져 있으면 '자동으로 다시 연결' 을 켠다", c.client.sets)
check(('Output', 'RetryDelay', '3') in c.client.sets, '재시도 간격이 10초 넘으면 3초로', c.client.sets)
check(('Output', 'MaxRetries', '25') in c.client.sets, '재시도 횟수가 20 아래면 25로', c.client.sets)
check(st['reconnect'] is True and st['retry_delay'] == 3 and st['max_retries'] == 25 and len(st['changed']) == 3,
      '상태에 맞춘 값과 바꾼 것을 남긴다', st)
c.client = FakeClient({'Reconnect': 'true', 'RetryDelay': '2', 'MaxRetries': '25'})
st = c.ensure_reconnect()
check(c.client.sets == [] and st['changed'] == [] and st['reconnect'] is True, '이미 맞으면 건드리지 않는다', st)
c.client = FakeClient({'Reconnect': None, 'RetryDelay': None, 'MaxRetries': None})
st = c.ensure_reconnect()
check(c.client.sets == [] and st['reconnect'] is True, '값이 비면 OBS 기본값(켜짐)을 읽는다', st)
c.client = FakeClient({}, fail=True)
st = c.ensure_reconnect()
check(st['error'] and st['reconnect'] is None, '못 읽으면 던지지 않고 오류만 남긴다', st)
c.client = None
check(c.ensure_reconnect()['error'] == 'OBS 미연결', '연결이 없으면 그대로')

print('\n[⑤-2] OBS 송출 숫자 한 벌')
fc = FakeClient({'Reconnect': 'true', 'RetryDelay': '2', 'MaxRetries': '25'})
fc.settings = {'코트 카메라': {'is_local_file': False, 'input': 'rtsp://cam'}, '인트로': {'is_local_file': True}}
fc.media = {'코트 카메라': 'OBS_MEDIA_STATE_ERROR', '인트로': 'OBS_MEDIA_STATE_ENDED'}
c = OBSController({'obs': {}})
c.client = fc
s1 = c.stream_stats()
check(s1['reconnecting'] is True and s1['congestion'] == 0.25 and s1['net_skipped'] == 4 and s1['net_total'] == 200,
      '송출 상태(재연결·혼잡·프레임)', s1)
check(s1['cpu'] == 33.3 and s1['enc_skipped'] == 1 and s1['render_total'] == 200, 'OBS 통계(CPU·인코딩·그리기)', s1)
check(s1['media'] == {'코트 카메라': 'OBS_MEDIA_STATE_ERROR'}, '카메라(네트워크 소스)만 본다 — 끝난 인트로 영상은 안 본다', s1['media'])
c.stream_stats()
check(fc.input_list_calls == 1, '카메라 목록은 5분에 한 번만 다시 읽는다', fc.input_list_calls)


class Dead(FakeClient):
    def get_stream_status(self):
        raise ConnectionError('gone')


c.client = Dead({})
obs_controller.OBSController.connect = lambda self, retries=3, delay=2.0: (_ for _ in ()).throw(RuntimeError('off'))
check(c.stream_stats() is None and c.client is None, '연결이 죽었으면 None(던지지 않는다) · 다음에 새로 붙는다')

# ═══════════════════════════════════════════════════════════════
print('\n[③⑤] 방송 서버 — 기록 파일 · 끝내기 다시 · 이어받기 · 감시')
import test_stream_server as tss  # noqa: E402

ss = tss.load_server()
cl = ss.app.test_client()
check(not os.path.exists(ss.LIVE_FILE), '처음엔 기록 파일이 없다')
r = cl.post('/start-stream', json={'teamA': ['가', '나'], 'teamB': ['다', '라'], 'league': 'PSiL 26S3',
                                   'category': 'Gold+', 'matchNumber': 7, 'matchId': 'm7'})
d = r.get_json()
check(r.status_code == 200 and d.get('success'), '방송 시작', d)
bid = d['broadcast_id']
rec = ss._live_file_read()
check(rec['current'] and rec['current']['broadcast_id'] == bid and rec['current']['match_id'] == 'm7',
      '켜면 진행 중인 방송을 파일에 적는다', rec)

# 감시 — 재연결 중
seq = [
    {'active': True, 'reconnecting': False, 'congestion': 0, 'bytes': 0, 'net_skipped': 0, 'net_total': 0, 'media': {}},
    {'active': True, 'reconnecting': True, 'congestion': 0, 'bytes': 0, 'net_skipped': 0, 'net_total': 0, 'media': {}},
    {'active': True, 'reconnecting': False, 'congestion': 0, 'bytes': 7_500_000, 'net_skipped': 0, 'net_total': 300, 'media': {}},
    {'active': True, 'reconnecting': True, 'congestion': 0, 'bytes': 7_500_000, 'net_skipped': 0, 'net_total': 300, 'media': {}},
]
ss.obs.stream_stats = lambda: seq.pop(0) if seq else None
yt_calls = []
ss.youtube.stream_health = lambda b: yt_calls.append(b) or {'status': 'good', 'issues': []}
ss._health_tick(1000.0)
check(yt_calls == [bid], '첫 바퀴에 YouTube 건강을 묻는다', yt_calls)
h = ss._health_tick(1010.0)
check(h and h['issue'] == 'reconnecting', '재연결 중을 잡는다', h)
hv = cl.get('/health').get_json()
sh_ = hv.get('stream_health') or {}
check(sh_.get('short') == '재연결 중' and sh_.get('reconnects') == 1 and sh_.get('since'),
      '/health 에 stream_health(짧은 말 · 재연결 횟수 · 언제부터)', sh_)
check((hv.get('broadcast_settings') or {}).get('auto_stop') is False, '/health 에 자동 종료 꺼짐이 보인다', hv.get('broadcast_settings'))
h = ss._health_tick(1020.0)
check(h['issue'] == '', '다시 붙으면 풀린다', h)
check(cl.get('/health').get_json()['stream_health']['since'] is None, '풀리면 since 도 비운다')
ss._health_tick(1030.0)
check(ss.health_state['reconnects'] == 2, '재연결은 들어갈 때마다 센다(2번)', ss.health_state)
check(len(yt_calls) == 1, 'YouTube 는 1분(6바퀴)에 한 번만', yt_calls)

# 끄면 파일이 지워진다
r = cl.post('/stop-stream', json={'matchId': 'm7'})
check(r.get_json().get('success') and not os.path.exists(ss.LIVE_FILE), '끄고 끝냈으면 기록 파일을 지운다')
check(ss._health_tick(1040.0) is None and ss.health_state['health'] is None, '방송이 없으면 감시 기억을 비운다')
check(cl.get('/health').get_json().get('stream_health') is None, '방송이 없으면 /health stream_health 는 null')

# 끝내기 실패 → 남겨 두고 뒤에서 다시
r = cl.post('/start-stream', json={'teamA': ['가', '나'], 'teamB': ['다', '라'], 'league': 'PSiL 26S3',
                                   'matchNumber': 8, 'matchId': 'm8'})
bid8 = r.get_json()['broadcast_id']
real_end = ss.youtube.end_broadcast
answers = [False, True]
ss.youtube.end_broadcast = lambda b: answers.pop(0) if answers else True
ss.END_RETRY_DELAYS = (0.05,)
r = cl.post('/stop-stream', json={'matchId': 'm8'}).get_json()
check(any('YouTube' in w for w in (r.get('warnings') or [])), '못 끝냈으면 태블릿에 경고(서버가 다시 해 봐요)', r)
rec = ss._live_file_read()
check(bid8 in rec['pending_end'] and rec['current'] is None, "못 끝낸 방송은 '다시 끝낼 것' 에 남는다", rec)
for _ in range(40):
    if not os.path.exists(ss.LIVE_FILE):
        break
    time.sleep(0.05)
check(not os.path.exists(ss.LIVE_FILE), '뒤에서 다시 끝내고 성공하면 기록을 지운다')
ss.youtube.end_broadcast = real_end

# 서버가 다시 떴을 때 — OBS 가 송출 중이면 이어받는다
ss._live_file_set_current({'mode': 'league', 'broadcast_id': 'bLeft', 'watch_url': 'https://www.youtube.com/watch?v=bLeft',
                           'title': 'PSiL 26S3 Match9', 'match_id': 'm9', 'match_number': 9, 'team_a': ['a'], 'team_b': ['b']})
ss.obs.streaming = True
ended_before = list(ss.youtube.ended)
ss._recover_leftover()
hv = cl.get('/health').get_json()
check(hv['streaming'] is True and hv['match_id'] == 'm9' and hv['watch_url'].endswith('bLeft'),
      'OBS 가 송출 중이면 남은 방송을 이어받는다(태블릿이 다시 LIVE 로 본다)', hv)
check(ss.youtube.ended == ended_before, '이어받은 방송은 끝내지 않는다')
r = cl.post('/stop-stream', json={'matchId': 'm9'}).get_json()
check(r.get('success') and 'bLeft' in ss.youtube.ended and not os.path.exists(ss.LIVE_FILE),
      '이어받은 방송도 경기 저장으로 평소처럼 끝난다', r)

# OBS 가 송출 중이 아니면(정전·재부팅) 남은 방송을 끝낸다 + '다시 끝낼 것' 도
with ss._live_file_lock:
    ss._live_file_write({'current': {'broadcast_id': 'bOld', 'title': 'old'}, 'pending_end': ['bPend']})
ss.obs.streaming = False
ss._recover_leftover()
check('bOld' in ss.youtube.ended and 'bPend' in ss.youtube.ended and not os.path.exists(ss.LIVE_FILE),
      'OBS 가 꺼져 있으면 남은 방송·미뤄 둔 방송을 끝내고 기록을 지운다', ss.youtube.ended)
check(cl.get('/health').get_json()['streaming'] is False, '이어받지 않았으면 방송 없음 그대로')

# 깨진 파일은 무시
with io.open(ss.LIVE_FILE, 'w', encoding='utf-8') as f:
    f.write('{not json')
check(ss._live_file_read() == {'current': None, 'pending_end': []}, '기록 파일이 깨졌으면 빈 것으로 읽는다')
ss._recover_leftover()
os.remove(ss.LIVE_FILE)

# 설정 문서·예시가 ultraLow 를 더 권하지 않는다
ex = io.open(os.path.join(ROOT, 'streaming_config.json.example'), encoding='utf-8').read()
check('"latency": "normal"' in ex and '"latency": "ultraLow"' not in ex, 'config 예시는 normal')
setup = io.open(os.path.join(ROOT, 'setup', 'setup_livestream_pc.ps1'), encoding='utf-8').read()
check('"latency": "normal"' in setup and '"latency": "ultraLow"' not in setup, '설치 스크립트가 만드는 config 도 normal')
gi = io.open(os.path.join(ROOT, '.gitignore'), encoding='utf-8').read()
check('/live_broadcast.json' in gi, '기록 파일은 커밋하지 않는다(.gitignore)')

print()
if fails:
    print(f'FAIL {len(fails)}건')
    sys.exit(1)
print(f'ALL OK — {n_ok[0]} checks')
