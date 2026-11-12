"""
유튜브가 영상을 못 받는 방송 살리기(2026-11-12) — OBS·유튜브 없이 돈다.

    python tests/test_nodata_guard.py

Match230: OBS 는 '송출 시작됨', 유튜브는 처음부터 noData — 방송이 '예정' 에 머물렀고 53분 동안 아무도 몰랐다.
  ① 시작 30초 전엔 안 묻는다 · 받는 중이면 그 방송은 더 안 본다(손대지 않는다)
  ② noData 한 번은 넘기고, 두 번이면 송출만 껐다 같은 키로 다시 켠다(유튜브 방송은 끝내지 않는다)
  ③ 그래도 noData 면 OBS 를 다시 켠다 · 그래도면 사람에게 말하고 멈춘다(끝없이 껐다 켜지 않는다)
  ④ 유튜브에 못 물었으면 손대지 않는다 · 방송이 끝나면 기억을 비운다
  ⑤ OBS 웹소켓 라이브러리의 INFO 로그(비밀번호가 찍힌다)를 받지 않는다 · /health 에 키가 없다
"""
import logging
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
yt = {'status': 'noData', 'asked': 0}


def health(bid):
    yt['asked'] += 1
    return None if yt['status'] is None else {'status': yt['status'], 'issues': []}


ss.youtube.stream_health = health
ended = []
ss.youtube.end_broadcast = lambda bid: ended.append(bid) or True
restarts = []


def restart():
    restarts.append(1)
    ss.obs.streaming = False
    ss.obs.obs_restart_status = {'ok': True, 'error': '', 'seconds': 10.0}
    return True


ss.obs.restart_obs_process = restart


def start(n):
    r = cl.post('/start-stream', json={'teamA': ['가', '나'], 'teamB': ['다', '라'], 'league': 'PSiL 26S3',
                                       'matchNumber': n, 'matchId': f'm{n}'})
    assert r.get_json().get('success'), r.get_json()
    ss.obs.calls.clear()
    return ss.stream_state['broadcast_id'], ss.datetime.fromisoformat(ss.stream_state['started_at']).timestamp()


def names():
    return [c[0] if isinstance(c, (tuple, list)) else c for c in ss.obs.calls]


print('\n[①] 받는 중이면 손대지 않는다')
bid, t0 = start(1)
yt.update(status='good', asked=0)
check(ss._nodata_tick(t0 + 10) is None and yt['asked'] == 0, '시작 30초 전엔 안 묻는다')
check(ss._nodata_tick(t0 + 31) == 'good' and names() == [] and ss.nodata_state['done'], '받는 중 → 끝(OBS 를 안 건드린다)', names())
check(ss._nodata_tick(t0 + 500) is None and yt['asked'] == 1, '그 방송은 더 안 묻는다')
cl.post('/stop-stream', json={})
ss._nodata_tick(t0 + 600)
check(ss._nodata_mem['bid'] is None, '방송이 끝나면 기억을 비운다')

print("\n[②] noData — 송출만 껐다 같은 키로 다시")
bid, t0 = start(2)
key = ss._ingest['key']
yt.update(status='noData', asked=0)
check(ss._nodata_tick(t0 + 31) == 'noData' and names() == [], '한 번은 넘긴다(받기 직전일 수 있다)', names())
check(ss._nodata_tick(t0 + 40) is None, '15초 안엔 다시 안 묻는다')
ended.clear()
ss._nodata_tick(t0 + 47)
c = names()
check(c == ['stop', 'set', 'start'], '껐다 다시 켠다(끄기 → 키 넣기 → 켜기)', c)
check(ss.obs.pending_key == key and ss.obs.streaming, '같은 방송 키로', ss.obs.pending_key)
check(ended == [] and ss.stream_state['broadcast_id'] == bid and restarts == [], '유튜브 방송은 그대로 · OBS 는 안 껐다', (ended, restarts))
check(ss.nodata_state['step'] == 1 and '송출' in ss.nodata_state['note'], '/health 에 무엇을 하는지', ss.nodata_state)
now = ss.time.time()
yt['status'] = 'good'
check(ss._nodata_tick(now + 36) == 'good' and ss.nodata_state['done'] and ss.nodata_state['note'] == '', '받기 시작하면 끝')
cl.post('/stop-stream', json={})

print('\n[③] 그래도 noData — OBS 다시 켜기 → 사람에게')
bid, t0 = start(3)
yt.update(status='noData')
ss._nodata_tick(t0 + 31); ss._nodata_tick(t0 + 47)
check(ss._nodata_mem['step'] == 1 and restarts == [], '1단계 = 송출 다시')
now = ss.time.time()
check(ss._nodata_tick(now + 10) is None, '손댄 뒤 35초는 기다린다')
ss._nodata_tick(now + 36); ss.obs.calls.clear(); ended.clear()
ss._nodata_tick(now + 52)
check(restarts == [1] and ss._nodata_mem['step'] == 2 and ss.obs.streaming, '2단계 = OBS 다시 켜고 이어 보내기', (restarts, ss._nodata_mem))
check(ended == [] and ss.stream_state['broadcast_id'] == bid, '유튜브 방송은 끝내지 않는다')
now = ss.time.time()
ss._nodata_tick(now + 36); ss.obs.calls.clear()
ss._nodata_tick(now + 52)
check(ss._nodata_mem['step'] == 9 and restarts == [1] and names() == [], '그래도면 멈춘다(끝없이 껐다 켜지 않는다)', (ss._nodata_mem, names()))
check(ss.output_state['bad'] and '예정' in ss.output_state['note'], '태블릿 상태 줄에 뜬다', ss.output_state)
check(ss._nodata_tick(now + 900) is None, '더 안 묻는다')
cl.post('/stop-stream', json={})

print('\n[④] 유튜브에 못 물었으면 손대지 않는다')
bid, t0 = start(4)
yt.update(status=None)
for dt in (31, 47, 63, 79):
    ss._nodata_tick(t0 + dt)
check(names() == [] and ss._nodata_mem['step'] == 0, '모르면 그대로', names())
cl.post('/stop-stream', json={})

print('\n[⑤] 비밀이 로그·/health 에 안 남는다')
check(logging.getLogger('obsws_python').level >= logging.WARNING and not logging.getLogger('obsws_python.baseclient').isEnabledFor(logging.INFO),
      "obsws_python 의 INFO('Connecting with parameters … password=…')를 받지 않는다")
bid, t0 = start(5)
check(ss._ingest['key'] not in str(cl.get('/health').get_json()) and 'nodata_guard' in cl.get('/health').get_json(), '/health 에 키 없음 · nodata_guard 있음')

print('\n' + ('FAIL %d' % len(fails) if fails else '전부 통과'))
sys.exit(1 if fails else 0)
