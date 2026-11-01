"""
🎬 → 📱 인스타 스토리(2026-11-01) — 방송 PC 몫: 최근 클립 기억 · 세로판 만들기(9:16 · 60초 이하) · 서버에 올리기.

    python tests/test_highlight_story.py

여기서 못 박는 것:
  · 업로드가 받아 준 클립만 '최근 클립' 에 남는다(최신 먼저 · 10개).
  · 세로판 ffmpeg 명령 — 뒤쪽 N초(최대 60) · 720x1280 · 흐린 배경 위 원본 · H.264/AAC · faststart · 스레드 2.
  · 만들기는 한 번에 하나(다른 클립이 만드는 중이면 409) · 이미 만들었으면 다시 안 만든다 · 모르는 클립 404.
  · 세 주소 다 운영자 확인(_require_operator) 뒤에 있다.
  · ffmpeg 가 있으면 실제로 세로 영상을 만들어 본다(720x1280 · 60초).
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
check(items[0]['title'] == 'Match 11' and items[0]['seconds'] == 60 and items[0]['story']['state'] == '', '제목 · 길이 · 아직 세로판 없음')
ss._remember_highlight('h11', clip, 30, {'title': 'again'})
items = c.get('/highlights/recent').get_json()['items']
check(len([x for x in items if x['id'] == 'h11']) == 1 and items[0]['seconds'] == 30, '같은 클립을 다시 받으면 한 줄로')

# 2) 업로드 응답의 id 로 기억한다
src = open(os.path.join(os.path.dirname(HERE), 'stream_server.py'), encoding='utf-8').read()
check("_remember_highlight(hid, path, seconds, meta)" in src and "get('data') or {}).get('id')" in src, '업로드 응답 data.id 로 기억한다')

# 3) ffmpeg 명령
cmd = ss._story_cmd('in.mp4', 90, 'out.mp4')
check(cmd[cmd.index('-sseof') + 1] == '-60' and cmd[cmd.index('-t') + 1] == '60', '90초 클립 → 뒤쪽 60초')
cmd30 = ss._story_cmd('in.mp4', 30, 'out.mp4')
check(cmd30[cmd30.index('-sseof') + 1] == '-30', '30초 클립은 30초 그대로')
check(ss._story_cmd('in.mp4', 0, 'o.mp4')[3] == '-60', '길이를 모르면 60초')
f = ss._story_filter()
check('scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,boxblur' in f and 'overlay=(W-w)/2:(H-h)/2' in f, '흐린 배경 + 가운데 원본(720x1280)')
check(all(k in cmd for k in ['libx264', 'aac', '+faststart', '-threads']) and cmd[cmd.index('-threads') + 1] == '2', 'H.264 · AAC · faststart · 스레드 2')
check("BELOW_NORMAL_PRIORITY_CLASS" in src, '윈도우는 낮은 우선순위로 인코딩')

# 4) 만들기 — 한 번에 하나 · 이미 만들었으면 그대로 · 모르는 클립 404
started = []
ss._make_and_upload_story = lambda item: started.append(item['id'])
r = c.post('/highlight/nope/story')
check(r.status_code == 404, '모르는 클립 → 404')
r = c.post('/highlight/h10/story')
time.sleep(0.1)
check(r.status_code == 200 and r.get_json()['story']['state'] == 'making' and started == ['h10'], '만들기 시작 → making')
r = c.post('/highlight/h9/story')
check(r.status_code == 409 and '만드는 중' in r.get_json()['error'], '다른 클립이 만드는 중이면 409')
r = c.post('/highlight/h10/story')
check(r.status_code == 200 and started == ['h10'], '같은 클립을 또 누르면 그대로(두 번 안 만든다)')
ss._story_jobs['h10'] = {'state': 'ready', 'error': '', 'url': 'https://api.padelsociety.co.kr/uploads/highlights/a_story.mp4'}
r = c.post('/highlight/h10/story')
check(r.get_json()['story']['state'] == 'ready' and started == ['h10'], '이미 만들었으면 다시 안 만든다')
check(c.get('/highlight/h10/story').get_json()['story']['url'].endswith('_story.mp4'), '상태 주소가 세로판 주소를 준다')
os.remove(clip)
ss._story_jobs.pop('h9', None)
r = c.post('/highlight/h9/story')
check(r.status_code == 404 and '지워졌' in r.get_json()['error'], '파일이 지워졌으면 404')

# 5) 운영자 확인
for path, method in (('/highlights/recent', 'GET'), ('/highlight/<hid>/story', 'POST'), ('/highlight/<hid>/story', 'GET')):
    i = src.index(f"@app.route('{path}', methods=['{method}'])")
    check(src[i:i + 120].split('\n')[1].strip() == '@_require_operator', f'{method} {path} — 운영자 확인')

# 6) 실제 ffmpeg(있으면)
ff = shutil.which('ffmpeg')
if ff and shutil.which('ffprobe'):
    srcv = os.path.join(tmp, 'src.mp4')
    subprocess.run([ff, '-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=1280x720:rate=30', '-f', 'lavfi',
                    '-i', 'sine=frequency=440', '-t', '70', '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', srcv], check=True)
    ss._ffmpeg_exe = lambda: ff
    out = ss._story_clip(srcv, 70)
    probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height:format=duration',
                            '-of', 'default=nw=1', out], capture_output=True, text=True).stdout
    check('width=720' in probe and 'height=1280' in probe, '실제 세로판 720x1280')
    dur = float([l for l in probe.splitlines() if l.startswith('duration=')][0].split('=')[1])
    check(59 <= dur <= 61, f'실제 길이 60초 이하({dur:.1f})')
else:
    print('  (ffmpeg 없음 — 실제 인코딩 건너뜀)')

shutil.rmtree(tmp, ignore_errors=True)
print('FAIL' if fails else 'OK', len(fails))
sys.exit(1 if fails else 0)
