"""
stream_server 자유 라이브 규칙 — OBS·YouTube 없이 돈다(둘 다 가짜로 바꿔 끼운다).

    python tests/test_stream_server.py

여기서 못 박는 것:
  · 자유 라이브는 90/120/180분만 받는다(그 외 400). 팀 이름은 없어도 된다.
  · 시작하면 ends_at 이 정확히 그만큼 뒤고, /health 가 mode·ends_at 을 준다(태블릿 복원용).
  · 자유 라이브 중 또 자유 라이브 → 409 (남은 분 안내). 리그 방송 중 자유 라이브 → 409.
  · 자유 라이브 중 **리그 경기가 오면 자유 라이브를 끄고 리그 방송을 연다**(리그 우선).
  · 시간이 지나면 워치독 한 바퀴가 방송을 끈다(태블릿 없이).
  · 자유 라이브 중 🎬 하이라이트 메타 제목이 자유 라이브 제목이다.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


class FakeOBS:
    """실물 obs_controller 와 같은 약속: start_stream 은 **이미 송출 중이면 던진다**(지난 방송 키에 붙지
    않는다) · stop_stream 은 꺼진 걸 확인했으면 True. key 는 지금 송출이 나가는 방송 키다."""
    def __init__(self, cfg):
        self.streaming = False
        self.stops = 0
        self.key = None          # 송출이 실제로 나가는 키(= 그 방송)
        self.pending_key = None  # 넣어 둔 키 — 송출을 다시 시작해야 먹는다(실물 OBS 와 같다)
        self.calls = []          # 부른 순서
        self.stop_fail = False   # True 면 끄기가 안 먹는다(연결 끊김 흉내)
        self.stop_delay = 0.0    # 끄는 데 걸리는 시간(겹침 흉내)

    def connect(self, *a, **k): pass
    def set_stream_settings(self, rtmp, key):
        self.calls.append('set'); self.pending_key = key
    def start_stream(self):
        self.calls.append('start')
        if self.streaming:
            raise RuntimeError('OBS 가 이미 다른 방송을 내보내는 중이에요')
        self.streaming = True; self.key = self.pending_key
    def stop_stream(self, timeout=10.0):
        self.calls.append('stop')
        if self.stop_delay:
            import time as _t; _t.sleep(self.stop_delay)
        if self.stop_fail:
            return False
        self.streaming = False; self.stops += 1; self.key = None
        return True
    def is_streaming(self): return self.streaming
    def wait_until_streaming(self, timeout=8.0): return self.streaming
    def start_replay_buffer(self): pass
    def ensure_replay_buffer(self): return True
    def save_replay_buffer(self): return ''


class FakeYT:
    thumbnails = []          # (broadcast_id, 파일경로) — 썸네일이 실제로 올라갔는지
    def __init__(self, cfg):
        self.n = 0
        self.ended = []
        self.retitled = []   # (broadcast_id, 제목, 설명) — 같은 매치를 이어 쓰며 고친 것
        self.titles = {}     # broadcast_id → 만들 때 제목

    def create_broadcast_and_stream(self, title, desc):
        self.n += 1
        bid = f'bcast{self.n}'
        self.titles[bid] = title
        return (bid, 'rtmp://x/live2', f'key-{bid}')

    def update_snippet(self, bid, title, desc):
        self.retitled.append((bid, title, desc))
        return True

    def end_broadcast(self, bid): self.ended.append(bid)

    @staticmethod
    def get_watch_url(bid): return f'https://www.youtube.com/watch?v={bid}'

    def set_thumbnail(self, broadcast_id, path):
        FakeYT.thumbnails.append((broadcast_id, path))
        return True

    voided = []              # [무효] 를 붙인 영상 id
    def mark_void(self, vid):
        FakeYT.voided.append(vid)
        return True


def load_server():
    # config.json 이 있어야 import 가 된다 — 임시 폴더에 사본을 만들어 거기서 읽힌다.
    tmp = tempfile.mkdtemp(prefix='ss_')
    shutil.copy(os.path.join(ROOT, 'stream_server.py'), tmp)
    # stream_server 가 직접 import 하는 우리 모듈은 같이 옮겨 놓는다 (OBS·YouTube 는
    # 아래에서 가짜로 바꿔 끼우지만, 썸네일은 실물이 돌아도 되고 돌아야 한다).
    shutil.copy(os.path.join(ROOT, 'thumbnail.py'), tmp)
    with io.open(os.path.join(tmp, 'config.json'), 'w', encoding='utf-8') as f:
        json.dump({'obs': {}, 'youtube': {}, 'highlight': {}}, f)
    # 시작 시 GitHub 에서 페이지를 받는다 — 네트워크 없이도 되게 로컬 파일을 둔다.
    court = os.path.join(ROOT, 'ps_court', 'ps_court_playus.html')
    if os.path.exists(court):
        shutil.copy(court, os.path.join(tmp, 'ps_court_playus.html'))
    fake_obs = types.ModuleType('obs_controller'); fake_obs.OBSController = FakeOBS
    fake_yt = types.ModuleType('youtube_api'); fake_yt.YouTubeAPI = FakeYT
    sys.modules['obs_controller'] = fake_obs
    sys.modules['youtube_api'] = fake_yt
    # 페이지 다운로드는 건너뛴다(15초 타임아웃을 기다릴 이유가 없다)
    import urllib.request
    urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(OSError('offline'))
    sys.path.insert(0, tmp)
    import stream_server
    return stream_server


def main():
    ss = load_server()
    app = ss.app
    c = app.test_client()
    ok = 0

    def check(cond, msg, detail=''):
        nonlocal ok
        if not cond:
            print('FAIL:', msg, ('\n      ' + detail) if detail else '')
            sys.exit(1)
        ok += 1
        print('  ✓', msg)

    # 1) 시간 검증
    r = c.post('/start-stream', json={'mode': 'free', 'durationMinutes': 60})
    check(r.status_code == 400, '자유 라이브 60분은 거절(90/120/180 만)')
    check(not ss.stream_state['active'], '거절된 요청이 자리를 차지하지 않는다')

    # 2) 시작 — 이름 없이도 된다
    before = datetime.now(timezone.utc)
    r = c.post('/start-stream', json={'mode': 'free', 'durationMinutes': 90})
    d = r.get_json()
    check(r.status_code == 200 and d['success'] and d['mode'] == 'free', '자유 라이브 90분 시작')
    ends = datetime.fromisoformat(d['ends_at'])
    check(timedelta(minutes=89) < (ends - before) <= timedelta(minutes=91), 'ends_at 이 90분 뒤')
    check('Padel Society 라이브' in d['title'], '제목이 자유 라이브 제목')

    h = c.get('/health').get_json()
    check(h['streaming'] and h['mode'] == 'free' and h['ends_at'] == d['ends_at'] and h['duration_minutes'] == 90,
          '/health 가 mode·ends_at·duration 을 준다(태블릿 복원)')

    # 3) 겹침
    r = c.post('/start-stream', json={'mode': 'free', 'durationMinutes': 120})
    check(r.status_code == 409 and '자유 라이브가 진행 중' in r.get_json()['error'], '자유 라이브 중 또 자유 라이브 → 409')

    # 4) 🎬 메타 — 자유 라이브 제목
    captured = {}
    ss._save_and_upload_highlight = lambda meta, seconds=0: captured.update(meta)
    r = c.post('/highlight', json={'seconds': 60})
    import time; time.sleep(0.2)
    check(r.get_json()['success'] and 'Padel Society 라이브' in captured.get('title', ''), '하이라이트 메타 제목 = 자유 라이브 제목')
    check(bool(captured.get('recordedAt')), '하이라이트 메타에 recordedAt')

    # 5) 리그 경기가 오면 자유 라이브를 끄고 리그 방송
    r = c.post('/start-stream', json={'teamA': ['a', 'b'], 'teamB': ['c', 'd'], 'league': 'PS iLeague 26S3', 'category': 'B&S', 'matchNumber': 3})
    d2 = r.get_json()
    check(r.status_code == 200 and d2['mode'] == 'league', '리그 경기 시작이 자유 라이브를 넘겨받는다')
    check(ss.youtube.ended == ['bcast1'], '넘겨받을 때 자유 라이브 유튜브 방송을 닫는다')
    check(ss.stream_state['ends_at'] is None and ss.stream_state['duration_minutes'] == 0, '리그 방송엔 만료 시각이 없다')

    r = c.post('/start-stream', json={'mode': 'free', 'durationMinutes': 90})
    check(r.status_code == 409 and '리그 경기 방송 중' in r.get_json()['error'], '리그 방송 중 자유 라이브 → 409')

    # 6) 종료
    r = c.post('/stop-stream')
    check(r.get_json()['success'] and not ss.stream_state['active'], '/stop-stream')
    check(c.get('/health').get_json()['mode'] == '', '끝나면 mode 가 빈다')

    # 7) 워치독 — 시간이 지나면 끈다(태블릿 없이)
    c.post('/start-stream', json={'mode': 'free', 'durationMinutes': 180, 'teamA': ['김빠델', '이소사']})
    check(ss.stream_state['active'] and ss.stream_state['team_a'] == ['김빠델', '이소사'], '이름 있는 자유 라이브 시작')
    check(not ss._auto_stop_once(), '아직 시간 전이면 워치독이 안 끈다')
    late = datetime.now(timezone.utc) + timedelta(minutes=181)
    check(ss._auto_stop_once(now=late), '시간이 지나면 워치독이 끈다')
    check(not ss.stream_state['active'] and ss.obs.stops >= 2, '워치독 종료 뒤 OBS 송출도 꺼진다')

    # 8) 리그 방송은 워치독이 건드리지 않는다
    c.post('/start-stream', json={'teamA': ['a', 'b'], 'teamB': ['c', 'd']})
    check(not ss._auto_stop_once(now=datetime.now(timezone.utc) + timedelta(hours=9)), '리그 방송은 9시간 뒤에도 워치독이 안 끈다(점수 저장이 끝낸다)')
    c.post('/stop-stream')

    # 9) 썸네일 — 선수 사진·등급이 실린 rosterA/rosterB 를 받는다
    #    ⚠️ **이름만 보내는 구버전 태블릿도 그대로 돌아야 한다.** 새 칸을 필수로 만들면
    #       업데이트 전 태블릿이 그날 저녁 방송을 통째로 못 켠다.
    import time
    FakeYT.thumbnails.clear()
    r = c.post('/start-stream', json={
        'teamA': ['김하경', '이준우'], 'teamB': ['박서연', '최나래'],
        'rosterA': [{'name': '김하경', 'photo': '', 'par': 4.2, 'tier': 'gold'},
                    {'name': '이준우', 'photo': '', 'par': 3.4, 'tier': 'silver'}],
        'rosterB': [{'name': '박서연', 'photo': '', 'par': 5.6, 'tier': 'platinum'},
                    {'name': '최나래', 'photo': '', 'par': 2.8, 'tier': 'bronze'}],
        'league': 'PS iLeague 26S3', 'category': 'G&P', 'matchNumber': 7,
    })
    check(r.status_code == 200 and r.get_json()['success'], 'roster 를 함께 보내도 방송이 시작된다')
    bid = r.get_json()['broadcast_id']
    # 썸네일은 별도 스레드다 — **그 방송 것이** 올라올 때까지 기다린다.
    # (앞 경기의 늦은 스레드가 먼저 들어올 수 있어서 '뭐라도 오면 끝'으로 기다리면 놓친다.)
    for _ in range(80):
        if any(t[0] == bid for t in FakeYT.thumbnails):
            break
        time.sleep(0.05)
    try:
        import PIL                            # noqa: F401
        # ⚠️ **그 방송의** 썸네일인지 본다. 앞 경기의 늦은 스레드가 섞여 들어올 수 있어서
        #    개수로 세면 테스트가 이유 없이 빨개진다(실제로 그랬다).
        mine = [t for t in FakeYT.thumbnails if t[0] == bid]
        check(len(mine) == 1, '썸네일이 그 방송에 올라간다', str(FakeYT.thumbnails))
        check(os.path.exists(mine[0][1]), '올린 썸네일 파일이 실제로 있다')
        check(bid in mine[0][1], '썸네일 파일 이름이 방송마다 다르다 (겹쳐 써서 얼굴이 바뀌지 않게)', mine[0][1])
    except ImportError:
        check(FakeYT.thumbnails == [], 'Pillow 가 없으면 썸네일만 조용히 건너뛴다')
    c.post('/stop-stream')

    # 구버전 태블릿 — roster 없이 이름만
    FakeYT.thumbnails.clear()
    r = c.post('/start-stream', json={'teamA': ['가', '나'], 'teamB': ['다', '라'], 'league': 'PS iLeague 26S3'})
    check(r.status_code == 200 and r.get_json()['success'], '이름만 보내는 구버전 태블릿도 그대로 시작된다')
    c.post('/stop-stream')

    # 10) 끝까지 못 친 경기 — 영상은 지우지 않고 [무효] (태블릿 '매치 취소')
    import time
    FakeYT.voided.clear()
    r = c.post('/start-stream', json={'teamA': ['a', 'b'], 'teamB': ['c', 'd'], 'league': 'PS iLeague 26S3'})
    bid = r.get_json()['broadcast_id']
    r = c.post('/stop-stream', json={'void': True})
    d = r.get_json()
    check(d['success'] and d.get('voided') == bid and not ss.stream_state['active'], '/stop-stream {void} — 끄고 voided 를 돌려준다')
    for _ in range(40):
        if bid in FakeYT.voided: break
        time.sleep(0.05)
    check(FakeYT.voided == [bid], '그 방송 영상에 [무효] 가 붙는다', str(FakeYT.voided))
    check(bid in ss.youtube.ended, '영상은 지우지 않고 방송만 끝낸다(end_broadcast)')

    # 평범한 종료엔 붙이지 않는다
    FakeYT.voided.clear()
    c.post('/start-stream', json={'teamA': ['a', 'b'], 'teamB': ['c', 'd']})
    r = c.post('/stop-stream')
    time.sleep(0.2)
    check('voided' not in r.get_json() and FakeYT.voided == [], '보통 종료(점수 저장)엔 [무효] 가 없다')

    # 이미 끝난 방송 — /void-video
    FakeYT.voided.clear()
    r = c.post('/void-video', json={'watchUrl': 'https://www.youtube.com/watch?v=abcDEF_12-x'})
    for _ in range(40):
        if FakeYT.voided: break
        time.sleep(0.05)
    check(r.get_json().get('voided') == 'abcDEF_12-x' and FakeYT.voided == ['abcDEF_12-x'], '/void-video — 주소에서 id 를 읽어 붙인다')
    r = c.post('/void-video', json={'watchUrl': 'https://evil.example/<x>'})
    check(r.status_code == 400, '알아볼 수 없는 주소는 400')
    check(ss._video_id_of('https://youtu.be/abcDEF123') == 'abcDEF123', 'youtu.be 짧은 주소도 읽는다')

    # 송출 중인 그 방송에 /void-video 가 오면 끄면서 붙인다
    FakeYT.voided.clear()
    r = c.post('/start-stream', json={'teamA': ['a', 'b'], 'teamB': ['c', 'd']})
    bid = r.get_json()['broadcast_id']
    c.post('/void-video', json={'videoId': bid})
    check(not ss.stream_state['active'], '송출 중인 영상이면 방송도 끈다')

    # 제목·설명 바꾸기 규칙(순수 함수 — 실물 youtube_api)
    import importlib.util
    spec = importlib.util.spec_from_file_location('yt_real', os.path.join(ROOT, 'youtube_api.py'))
    yt = importlib.util.module_from_spec(spec); spec.loader.exec_module(yt)
    sn = yt.void_snippet({'title': 'PSiL 26S3 [Gold+] Match 1 | A vs B', 'description': '설명', 'categoryId': '17',
                          'tags': ['파델'], 'thumbnails': {}, 'channelId': 'x', 'publishedAt': 'y'})
    check(sn['title'].startswith('[무효] PSiL 26S3'), '제목 앞에 [무효]')
    check(sn['description'].startswith('[무효]') and sn['description'].endswith('설명'), '설명 앞에 [무효] 안내 + 원래 설명')
    check(sn['tags'] == ['파델'] and 'thumbnails' not in sn and 'channelId' not in sn, '태그는 지키고 읽기 전용 칸은 뺀다')
    check(yt.void_snippet({'title': '[무효] 이미'}) is None, '이미 붙었으면 다시 안 붙인다')
    long = yt.void_snippet({'title': 'x' * 100, 'description': '가' * 3000})
    check(len(long['title']) == 100 and len(long['description'].encode('utf-8')) <= 5000, 'YouTube 한도(제목 100자 · 설명 5000바이트)')

    # 11) 다음 경기 — 지난 경기 방송을 끝내고 새로 연다 (2026-10-08 · 26S3 Match39 제목 방송에 Match 41)
    import threading, time
    for _ in range(3):
        c.post('/stop-stream')
    yt = ss.youtube
    L = 'PS iLeague 26S3'
    r = c.post('/start-stream', json={'teamA': ['구하경', '성유나'], 'teamB': ['문서영', '안리나'],
                                      'league': L, 'category': 'Bridge', 'matchNumber': 39, 'matchId': 'm39'})
    d39 = r.get_json()
    check(d39['success'] and not d39['reused'] and ss.stream_state['match_id'] == 'm39', 'Match39 방송 시작 — 매치 id 를 기억한다')
    check(ss.obs.key == f"key-{d39['broadcast_id']}", 'OBS 가 Match39 키로 송출')
    h = c.get('/health').get_json()
    check(h['match_id'] == 'm39' and h['match_number'] == 39, '/health 가 지금 방송의 매치(번호)를 준다')

    r = c.post('/start-stream', json={'teamA': ['임우재', '조준희'], 'teamB': ['이준우', '설정수'],
                                      'league': L, 'category': 'Gold+', 'matchNumber': 41, 'matchId': 'm41'})
    d41 = r.get_json()
    check(r.status_code == 200 and d41['success'] and d41['took_over'] and not d41['reused'],
          '다른 매치가 오면 409 가 아니라 넘겨받는다', str(d41))
    check(d41['broadcast_id'] != d39['broadcast_id'] and 'Match41' in d41['title'], '새 방송 — 제목이 Match41')
    check(d39['broadcast_id'] in yt.ended, '지난 경기(Match39) 방송은 끝낸다')
    check(ss.obs.key == f"key-{d41['broadcast_id']}", '영상이 새 방송 키로 나간다 — 지난 경기 제목 방송에 안 이어진다')
    check(ss.stream_state['match_id'] == 'm41' and ss.stream_state['match_number'] == 41, '상태가 새 매치를 가리킨다')

    # 12) 같은 매치 — 태블릿을 다시 열어 이어서 하기 · 켜기 두 번
    n_before, stops_before = yt.n, ss.obs.stops
    r = c.post('/start-stream', json={'teamA': ['임우재', '조준희'], 'teamB': ['이준우', '설정수'],
                                      'league': L, 'category': 'Gold+', 'matchNumber': 41, 'matchId': 'm41'})
    d = r.get_json()
    check(r.status_code == 200 and d['success'] and d['reused'] and d['broadcast_id'] == d41['broadcast_id'],
          '같은 매치는 그 방송을 그대로 쓴다(예전엔 409 오류 토스트)', str(d))
    check(yt.n == n_before and ss.obs.stops == stops_before, '새 방송도, 끄기도 없다')
    check(yt.retitled == [], '바뀐 게 없으면 제목·썸네일을 건드리지 않는다')

    # 같은 매치인데 팀을 다시 뽑았다 → 제목·설명·썸네일만 고친다
    FakeYT.thumbnails.clear()
    r = c.post('/start-stream', json={'teamA': ['임우재', '이준우'], 'teamB': ['조준희', '설정수'],
                                      'league': L, 'category': 'Gold+', 'matchNumber': 41, 'matchId': 'm41'})
    d = r.get_json()
    check(d['reused'] and yt.n == n_before, '팀을 다시 뽑아도 같은 매치면 새 방송을 안 연다(한 경기가 영상 둘로 안 쪼개진다)')
    for _ in range(60):
        if yt.retitled: break
        time.sleep(0.05)
    check(len(yt.retitled) == 1 and yt.retitled[0][0] == d41['broadcast_id'] and '임우재 / 이준우' in yt.retitled[0][2],
          '그 방송의 설명을 새 팀으로 고친다', str(yt.retitled))
    check(ss.stream_state['team_a'] == ['임우재', '이준우'], '상태의 팀도 새 팀')

    # 옛 태블릿(매치 id 없음) — 리그 + 번호로 가린다
    yt.retitled.clear()
    r = c.post('/start-stream', json={'teamA': ['임우재', '이준우'], 'teamB': ['조준희', '설정수'],
                                      'league': L, 'category': 'Gold+', 'matchNumber': 41})
    check(r.get_json()['reused'], '옛 태블릿 — 같은 번호면 이어 쓴다')
    r = c.post('/start-stream', json={'teamA': ['가', '나'], 'teamB': ['다', '라'],
                                      'league': L, 'category': 'Silver', 'matchNumber': 42})
    d42 = r.get_json()
    check(d42['success'] and d42['took_over'] and d41['broadcast_id'] in yt.ended, '옛 태블릿 — 번호가 다르면 넘겨받는다')

    # 13) 끄기 — 다른 매치의 끝내기 요청은 지금 방송을 안 건드린다
    r = c.post('/stop-stream', json={'matchId': 'whatever'})
    check(not r.get_json().get('skipped') and not ss.stream_state['active'],
          'id 를 모르는 방송(옛 태블릿이 켠 Match42)은 예전처럼 끈다')
    c.post('/start-stream', json={'teamA': ['가', '나'], 'teamB': ['다', '라'], 'league': L,
                                  'category': 'Silver', 'matchNumber': 43, 'matchId': 'm43'})
    r = c.post('/stop-stream', json={'matchId': 'm39', 'void': True})
    d = r.get_json()
    check(d['success'] and d.get('skipped') and 'voided' not in d and ss.stream_state['active'],
          '앞 매치(m39) [매치 취소] 가 지금 경기(m43) 방송을 끄거나 [무효] 를 붙이지 않는다', str(d))
    r = c.post('/stop-stream', json={'matchId': 'm43'})
    check(not r.get_json().get('skipped') and not ss.stream_state['active'], '그 매치의 끝내기는 끈다')
    c.post('/start-stream', json={'mode': 'free', 'durationMinutes': 90})
    r = c.post('/stop-stream', json={'matchId': 'm41'})
    check(r.get_json().get('skipped') and ss.stream_state['active'], '리그 매치 끝내기가 자유 라이브를 끄지 않는다')
    r = c.post('/stop-stream')
    check(not ss.stream_state['active'], 'id 없이 끄면 무엇이든 끈다(자유 라이브 종료 버튼)')

    # 14) 서버는 '방송 없음' 인데 OBS 가 지난 키로 송출 중 — 먼저 끄고 새 키로
    ss.obs.streaming = True; ss.obs.key = 'key-old'; ss.obs.calls.clear()
    r = c.post('/start-stream', json={'teamA': ['a', 'b'], 'teamB': ['c', 'd'], 'league': L,
                                      'matchNumber': 50, 'matchId': 'm50'})
    d = r.get_json()
    check(d['success'] and ss.obs.key == f"key-{d['broadcast_id']}", 'OBS 를 먼저 끄고 새 방송 키로 송출한다', str(ss.obs.calls))
    check(ss.obs.calls.index('stop') < ss.obs.calls.index('set'), '끄기가 새 키 넣기보다 먼저')
    c.post('/stop-stream')

    # 못 끄면 시작하지 않는다 — 빈 방송도 안 남긴다
    ss.obs.streaming = True; ss.obs.key = 'key-old'; ss.obs.stop_fail = True
    n_before = yt.n
    r = c.post('/start-stream', json={'teamA': ['a', 'b'], 'teamB': ['c', 'd'], 'league': L,
                                      'matchNumber': 51, 'matchId': 'm51'})
    d = r.get_json()
    check(r.status_code == 500 and 'OBS' in d['error'] and '방송 중단' in d['error'], '못 끄면 이유를 말하고 멈춘다', str(d))
    check(yt.n == n_before and not ss.stream_state['active'] and ss.stream_state['match_id'] == '',
          '유튜브 방송을 만들지 않고 상태도 비운다(지난 키에 이어 붙지 않는다)')
    ss.obs.stop_fail = False; ss.obs.streaming = False; ss.obs.key = None

    # 15) 끄기와 켜기가 겹친다 — 경기 저장(끄기)이 끝나기 전에 '다른 페어로 다시하기'(켜기)
    r = c.post('/start-stream', json={'teamA': ['a', 'b'], 'teamB': ['c', 'd'], 'league': L,
                                      'matchNumber': 60, 'matchId': 'm60'})
    b60 = r.get_json()['broadcast_id']
    ss.obs.stop_delay = 0.4
    res = {}
    def stopper():
        res['stop'] = app.test_client().post('/stop-stream', json={'matchId': 'm60'}).get_json()
    def starter():
        time.sleep(0.1)
        res['start'] = app.test_client().post('/start-stream', json={
            'teamA': ['a', 'c'], 'teamB': ['b', 'd'], 'league': L, 'matchNumber': 61, 'matchId': 'm61'}).get_json()
    t1 = threading.Thread(target=stopper); t2 = threading.Thread(target=starter)
    t1.start(); t2.start(); t1.join(5); t2.join(5)
    ss.obs.stop_delay = 0.0
    st = res.get('start') or {}
    check(st.get('success') and not st.get('reused'), '끄는 중에 온 켜기는 기다렸다가 새 방송을 연다(예전엔 409 또는 상태 덮어쓰기)', str(res))
    check(ss.stream_state['active'] and ss.stream_state['match_id'] == 'm61' and ss.stream_state['broadcast_id'] == st.get('broadcast_id'),
          '끄기의 정리가 새 방송 상태를 지우지 않는다', str(ss.stream_state))
    check(ss.obs.key == f"key-{st.get('broadcast_id')}" and b60 in yt.ended, '영상은 새 방송으로 · 지난 방송은 끝')
    c.post('/stop-stream')
    check(c.get('/health').get_json()['match_id'] == '', '끄면 매치 id 도 빈다')

    # 16) 같은 매치 판정(순수 함수)
    sm = ss._same_match
    cur = {'match_id': 'x', 'league': L, 'match_number': 3, 'team_a': ['a', 'b'], 'team_b': ['c', 'd']}
    check(sm(cur, 'x', L, 9, [], []) and not sm(cur, 'y', L, 3, ['a', 'b'], ['c', 'd']), 'id 가 둘 다 있으면 id 만 본다')
    cur_old = dict(cur, match_id='')
    check(sm(cur_old, 'y', L, 3, [], []) and not sm(cur_old, 'y', L, 4, [], []), '한쪽 id 가 없으면 리그 + 번호')
    check(not sm(cur_old, '', 'PS iLeague 26S2', 3, [], []), '리그가 다르면 다른 매치')
    cur_nono = dict(cur_old, match_number=0)
    check(sm(cur_nono, '', L, 0, ['d', 'c'], ['b', 'a']) and not sm(cur_nono, '', L, 0, ['a', 'b'], ['c', 'e']),
          '번호도 없으면 네 사람으로(팀을 다시 뽑아도 같은 매치)')

    # 17) 제목 바꾸기 규칙(순수 함수 — 실물 youtube_api)
    sn = yt_mod_retitle = None
    import importlib.util as _iu
    spec2 = _iu.spec_from_file_location('yt_real2', os.path.join(ROOT, 'youtube_api.py'))
    yt2 = _iu.module_from_spec(spec2); spec2.loader.exec_module(yt2)
    sn = yt2.retitle_snippet({'title': 'PSiL 26S3 [Gold+] Match41', 'description': 'A', 'categoryId': '17', 'tags': ['파델']},
                             'PSiL 26S3 [Gold+] Match41', 'B')
    check(sn['description'] == 'B' and sn['tags'] == ['파델'] and sn['categoryId'] == '17', '설명만 바뀌어도 태그·분류를 지킨다')
    check(yt2.retitle_snippet({'title': 'T', 'description': 'D'}, 'T', 'D') is None, '같으면 안 고친다(API 호출 없음)')
    check(yt2.retitle_snippet({'title': '[무효] T', 'description': 'D'}, 'T2', 'D')['title'] == '[무효] T2', '[무효] 표시는 지킨다')

    print(f'\nALL OK — {ok} checks')


if __name__ == '__main__':
    main()
