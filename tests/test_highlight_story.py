"""
🎬 → 📱 인스타 스토리(2026-11-01) — 방송 PC 몫: 최근 클립 기억 · 스토리용 파일(가로 그대로 · 60초 이하 · 재인코딩 없음) · 서버에 올리기.

    python tests/test_highlight_story.py

여기서 못 박는 것:
  · 업로드가 받아 준 클립만 '최근 클립' 에 남는다(최신 먼저 · 10개).
  · ffmpeg 명령 — 스트림 복사(재인코딩 없음 · 사용자 "세로로 안바꾸고 그냥 사진처럼") · 57초보다 길면 뒤쪽 57초부터 · 늘 60초 이하 · faststart.
  · 만들기는 한 번에 하나(다른 클립이 만드는 중이면 409) · 이미 만들었으면 다시 안 만든다 · 모르는 클립 404.
  · 세 주소 다 운영자 확인(_require_operator) 뒤에 있다.
  · ffmpeg 가 있으면 실제로 90초 클립을 잘라 본다(해상도 그대로 · 60초 이하).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_stream_server import load_server  # noqa: E402

fails = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        fails.append(msg)


ss = load_server()
c = ss.app.test_client()
tmp = tempfile.mkdtemp(prefix='story_')

# 1) 업로드가 받아 준 클립을 기억한다
clip = os.path.join(tmp, 'Replay_1_60s.mp4')
open(clip, 'wb').write(b'x')
for i in range(12):
    ss._remember_highlight(f'h{i}', clip, 60, {'title': f'Match {i}', 'recordedAt': '2026-11-01T10:00:00+00:00'})
items = c.get('/highlights/recent').get_json()['items']
check([x['id'] for x in items][:2] == ['h11', 'h10'] and len(items) == 10, '최근 클립 — 최신 먼저 · 10개')
check(items[0]['title'] == 'Match 11' and items[0]['seconds'] == 60 and items[0]['story']['state'] == '', '제목 · 길이 · 아직 스토리용 파일 없음')
ss._remember_highlight('h11', clip, 30, {'title': 'again'})
items = c.get('/highlights/recent').get_json()['items']
check(len([x for x in items if x['id'] == 'h11']) == 1 and items[0]['seconds'] == 30, '같은 클립을 다시 받으면 한 줄로')

# 2) 업로드 응답의 id 로 기억한다
src = open(os.path.join(os.path.dirname(HERE), 'stream_server.py'), encoding='utf-8').read()
check("_remember_highlight(hid, path, seconds, meta)" in src and "get('data') or {}).get('id')" in src, '업로드 응답 data.id 로 기억한다')

# 3) ffmpeg 명령 — 스트림 복사 · 60초 이하 · 정한 시작부터
cmd = ss._story_cmd('in.mp4', 26.3, 'out.mp4')
check(cmd[cmd.index('-ss') + 1] == '26.30' and cmd.index('-ss') < cmd.index('-i'), '정한 시작(26.3초)부터 — 입력 앞 -ss(빠른 자르기)')
check(cmd[cmd.index('-t') + 1] == '59.9', '60초를 안 넘게')
check(cmd[cmd.index('-c') + 1] == 'copy' and '-filter_complex' not in cmd and 'libx264' not in cmd, '재인코딩 없음(스트림 복사)')
check('-ss' not in ss._story_cmd('in.mp4', 0, 'o.mp4'), '처음부터면 -ss 없음')
check('+faststart' in cmd, 'faststart')
check('BELOW_NORMAL_PRIORITY_CLASS' not in src and '_story_filter' not in src, '세로 변환·우선순위 낮추기 코드는 없다')
check("threading.Thread(target=_make_and_upload_story, args=(item,), daemon=True).start()" in src
      and "HIGHLIGHT_CFG.get('auto_story', True) is not False" in src, '업로드 뒤 저절로 스토리용 파일(설정 auto_story: false 면 끔)')
check('with _story_make_lock:' in src, '한 번에 하나씩 차례로 만든다')

# 3-1) 마지막 랠리 찾기(rally_cut — 순수)
import rally_cut as rc
H = lambda ts, s=20: [(t, s) for t in ts]
# 앞 랠리 · 쉬는 시간(서브 준비 툭툭 약한 소리) · 마지막 랠리(29.3초 서브) · 다이빙 소리(센 소리 3번)
hits = H([10, 11.5, 13, 14.6, 16]) + [(26.3, 8), (27.3, 6)] + H([29.3, 30.9, 32.2, 34.2, 35.0, 36.6, 38.4, 39.8, 40.8]) + H([48.5, 51.0, 52.3])
check(rc.last_rally_serve(sorted(hits)) == 29.3, '마지막 랠리 서브 = 29.3초(다이빙 소리 3번은 랠리가 아님)')
# 점수 난 뒤 공 건네주기 — 짧고 촘촘(0.5초 안은 하나로 · 2.5초 미만)
pass_ = H([23.48, 23.94, 24.18, 26.01])
check(rc.last_rally_serve(sorted(H([7.7, 9, 10.5, 12, 13.5]) + pass_)) == 7.7, '공 건네주기 툭툭은 랠리가 아니다 → 앞 랠리')
check(rc.last_rally_serve(H([1, 2])) is None and rc.last_rally_serve([]) is None, '랠리가 없으면 None')
check(rc.story_window(60.0, 29.3) == (26.3, 'rally'), '서브 3초 전부터 끝까지')
check(rc.story_window(90.0, 10.0) == (33.0, 'rally_cut60'), '길면 마지막 57초(스토리 60초 한도 · 키프레임 여유)')
check(rc.story_window(30.0, 1.0) == (0.0, 'rally'), '서브가 3초 안이면 처음부터')
check(rc.story_window(30.0, None) == (0.0, 'no_rally') and rc.story_window(90.0, None) == (33.0, 'no_rally'), '못 찾으면 마지막 57초(짧으면 전체)')

# 4) 만들기 — 한 번에 하나 · 이미 만들었으면 그대로 · 모르는 클립 404
started = []
ss._make_and_upload_story = lambda item: started.append(item['id'])
r = c.post('/highlight/nope/story')
check(r.status_code == 404, '모르는 클립 → 404')
r = c.post('/highlight/h10/story')
time.sleep(0.1)
check(r.status_code == 200 and r.get_json()['story']['state'] == 'making' and started == ['h10'], '만들기 시작 → making')
r = c.post('/highlight/h9/story')
time.sleep(0.1)
check(r.status_code == 200 and started == ['h10', 'h9'], '다른 클립을 만드는 중이어도 받는다(차례로 만든다)')
r = c.post('/highlight/h10/story')
check(r.status_code == 200 and started == ['h10', 'h9'], '같은 클립을 또 누르면 그대로(두 번 안 만든다)')
ss._story_jobs['h10'] = {'state': 'ready', 'error': '', 'url': 'https://api.padelsociety.co.kr/uploads/highlights/a_story.mp4'}
r = c.post('/highlight/h10/story')
check(r.get_json()['story']['state'] == 'ready' and started == ['h10', 'h9'], '이미 만들었으면 다시 안 만든다')
check(c.get('/highlight/h10/story').get_json()['story']['url'].endswith('_story.mp4'), '상태 주소가 스토리용 파일 주소를 준다')
os.remove(clip)
ss._story_jobs.pop('h9', None)
r = c.post('/highlight/h9/story')
check(r.status_code == 404 and '지워졌' in r.get_json()['error'], '파일이 지워졌으면 404')

# 5) 운영자 확인
for path, method in (('/highlights/recent', 'GET'), ('/highlight/<hid>/story', 'POST'), ('/highlight/<hid>/story', 'GET')):
    i = src.index(f"@app.route('{path}', methods=['{method}'])")
    check(src[i:i + 120].split('\n')[1].strip() == '@_require_operator', f'{method} {path} — 운영자 확인')

# 6) 실제 ffmpeg(있으면) — OBS 처럼 키프레임 2초 간격 90초 클립
ff = shutil.which('ffmpeg')
if ff and shutil.which('ffprobe'):
    srcv = os.path.join(tmp, 'src.mp4')
    subprocess.run([ff, '-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=1280x720:rate=30', '-f', 'lavfi',
                    '-i', 'sine=frequency=440', '-t', '90', '-c:v', 'libx264', '-preset', 'ultrafast', '-g', '60', '-c:a', 'aac', srcv], check=True)
    ss._ffmpeg_exe = lambda: ff
    t0 = time.time()
    out, win = ss._story_clip(srcv, 90)
    took = time.time() - t0
    check(win['why'] in ('no_rally', 'no_numpy') and win['start'] == 33.0, f"소리 없는 90초 → 마지막 57초({win})")
    probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height:format=duration',
                            '-of', 'default=nw=1', out], capture_output=True, text=True).stdout
    check('width=1280' in probe and 'height=720' in probe, '해상도 그대로(가로)')
    dur = float([l for l in probe.splitlines() if l.startswith('duration=')][0].split('=')[1])
    check(55 <= dur <= 60, f'60초 이하({dur:.1f})')
    # 랠리 소리를 합성해 실제로 찾는지(numpy 가 있을 때) — 앞 랠리 · 쉬는 시간 · 15.0초 서브 마지막 랠리
    try:
        import numpy as np
        sr = 16000; dur_s = 40.0
        x = (np.random.RandomState(1).randn(int(sr * dur_s)) * 0.003).astype(np.float32)
        def click(t, amp=0.9):
            i = int(t * sr); n = 160
            x[i:i + n] += (amp * np.exp(-np.arange(n) / 25.0) * np.sign(np.sin(np.arange(n) * 2.3))).astype(np.float32)
        for t in [2, 3.4, 4.9, 6.3, 7.8]: click(t)
        for t in [15.0, 16.4, 17.7, 19.3, 20.6, 22.1, 23.5, 25.0]: click(t)
        raw = os.path.join(tmp, 'a.raw'); (np.clip(x, -1, 1) * 32767).astype(np.int16).tofile(raw)
        av = os.path.join(tmp, 'rally.mp4')
        subprocess.run([ff, '-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=30', '-f', 's16le', '-ar', str(sr), '-ac', '1',
                        '-i', raw, '-t', str(dur_s), '-c:v', 'libx264', '-preset', 'ultrafast', '-g', '60', '-c:a', 'aac', '-shortest', av], check=True)
        out2, win2 = ss._story_clip(av, 40)
        check(win2['why'] == 'rally' and abs(win2['serve'] - 15.0) < 0.3 and abs(win2['start'] - 12.0) < 0.3,
              f"합성 소리에서 마지막 랠리 서브 15초 → 12초부터({win2})")
    except ImportError:
        print('  (numpy 없음 — 랠리 합성 시험 건너뜀)')
    check(took < 10, f'몇 초면 끝난다({took:.1f}초)')
else:
    print('  (ffmpeg 없음 — 실제 자르기 건너뜀)')

shutil.rmtree(tmp, ignore_errors=True)
print('FAIL' if fails else 'OK', len(fails))
sys.exit(1 if fails else 0)
