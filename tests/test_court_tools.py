"""
자유 라이브 썸네일은 리그 경기처럼 보이면 안 된다 (2026-10-05).

    python tests/test_court_tools.py

  · 리그가 비어도 'PS i-League' 머리를 붙이지 않는다 · 꼬리는 'PADEL SOCIETY'(i-LEAGUE 아님).
  · 이름을 안 적은 자유 라이브는 썸네일을 올리지 않는다(금색 VS 한 줄뿐인 빈 카드였다).
  · 리그 경기는 예전 그대로(머리 'PS i-League' · 꼬리 i-LEAGUE).
  · /start-stream 이 mode 를 썸네일 스레드에 넘긴다.
⚠️ 썸네일 합성·YouTube 는 가짜로 바꿔 끼운다 — 무엇을 넘겼는지만 본다.
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_stream_server as tss  # noqa: E402  (OBS·YouTube 가짜를 그대로 쓴다)

fails = []


def check(name, cond, detail=''):
    if cond:
        print(f'  ok   {name}')
    else:
        fails.append(name)
        print(f'  FAIL {name}' + (f'\n       {detail}' if detail else ''))


ss = tss.load_server()
calls = []
ss.thumbnail.build = lambda a, b, **kw: (calls.append({'a': a, 'b': b, **kw}) or '/tmp/x.jpg')
ups = []
ss.youtube.set_thumbnail = lambda bid, path: ups.append(bid)

print('\n[1] 자유 라이브 · 이름 없음 — 올리지 않는다')
ss.push_thumbnail('b1', [], [], league='', free=True)
check('썸네일을 만들지도 올리지도 않는다', calls == [] and ups == [], f'{calls} {ups}')

print('\n[2] 자유 라이브 · 이름 있음 — 리그 표시 없이')
calls.clear(); ups.clear()
ss.push_thumbnail('b2', [{'name': '김하경'}], [{'name': '박서연'}], league='', free=True)
check('만들어 올린다', len(calls) == 1 and ups == ['b2'], f'{calls} {ups}')
check("머리에 'PS i-League' 가 없다", calls and calls[0]['league'] == '', calls and calls[0]['league'])
check("꼬리가 'PADEL SOCIETY'", calls and calls[0]['footer'] == ss.thumbnail.FOOTER_FREE == 'PADEL SOCIETY')

print('\n[3] 리그 경기 — 예전 그대로')
calls.clear(); ups.clear()
ss.push_thumbnail('b3', [{'name': '김하경'}], [{'name': '박서연'}], league='')
check("리그 이름이 비면 'PS i-League'", calls and calls[0]['league'] == 'PS i-League')
check("꼬리는 i-LEAGUE", calls and 'i-LEAGUE' in calls[0]['footer'])

print('\n[4] /start-stream 이 mode 를 썸네일 스레드에 넘긴다')
src = io.open(os.path.join(tss.ROOT, 'stream_server.py'), encoding='utf-8').read()
check("'free': mode == 'free' 를 넘긴다", "'free': mode == 'free'" in src)
th = io.open(os.path.join(tss.ROOT, 'thumbnail.py'), encoding='utf-8').read()
check("thumbnail 하단 띠가 박혀 있지 않다", "draw.text((60, H - 74), 'PADEL SOCIETY" not in th)

print(f'\n실패 {len(fails)}건\n' if fails else '\n전부 통과\n')
sys.exit(1 if fails else 0)
