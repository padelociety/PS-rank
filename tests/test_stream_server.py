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
    def __init__(self, cfg):
        self.streaming = False
        self.stops = 0

    def connect(self, *a, **k): pass
    def set_stream_settings(self, *a, **k): pass
    def start_stream(self): self.streaming = True
    def stop_stream(self): self.streaming = False; self.stops += 1
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

    def create_broadcast_and_stream(self, title, desc):
        self.n += 1
        return (f'bcast{self.n}', 'rtmp://x/live2', 'key')

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

    print(f'\nALL OK — {ok} checks')


if __name__ == '__main__':
    main()
