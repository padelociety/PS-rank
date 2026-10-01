"""
지난(또는 지금) 리그 경기 썸네일을 **다시** 만든다 — 사진이 있는 선수는 사진으로.

    cd C:\\dev\\PS-rank
    .venv\\Scripts\\python make_thumbnail.py --league 26S3 --match 1
    .venv\\Scripts\\python make_thumbnail.py --league 26S3 --match 1 --video https://youtube.com/live/XXXX   # 올리기까지

왜 (2026-10-01): 서버가 리그 매치 응답의 선수 사진을 파일 이름으로만 보내서(`attachParToMatch` 가 toJSON 을 건너뛰었다)
태블릿이 '사진 없음' 으로 보고 썸네일에 **이니셜**을 그렸다. 서버를 고쳐도 이미 올라간 썸네일은 그대로라, 여기서 다시 만든다.
- 사진·이름·등급은 서버(`/api/league/:id/matches`, 로그인 불필요)에서 읽는다. 사진이 파일 이름으로 와도 URL 로 만든다.
- 카테고리는 리그 부문 이름 그대로(Gold+ …) — 여러 부문이면 Bridge. 태블릿 c7 과 같은 규칙.
- `--video` 를 주면 그 방송/영상에 바로 올린다(stream_server 와 같은 YouTube 인증 · config.json).
  안 주면 JPG 파일만 만들고 경로를 찍는다 — YouTube Studio 에서 손으로 올려도 된다.
⚠️ 이 파일은 **별도 repo `padelociety/PS-rank`** 가 원본이다(모노repo `ps-rank/` 는 사본).
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime

import requests

import thumbnail

DIR = os.path.dirname(os.path.abspath(__file__))


def api_base():
    try:
        with open(os.path.join(DIR, 'config.json'), encoding='utf-8-sig') as f:
            cfg = json.load(f)
        return ((cfg.get('highlight') or {}).get('api_base') or 'https://api.padelsociety.co.kr').rstrip('/')
    except Exception:
        return 'https://api.padelsociety.co.kr'


def get(path):
    r = requests.get(api_base() + '/api' + path, timeout=15)
    r.raise_for_status()
    return r.json().get('data')


def name_of(u):
    if not isinstance(u, dict):
        return ''
    ko = f"{u.get('lastNameKorean') or ''}{u.get('firstNameKorean') or ''}".strip()
    if ko:
        return ko
    return f"{u.get('firstName') or ''} {u.get('lastName') or ''}".strip()


def photo_of(u):
    p = str((u or {}).get('profile') or '').strip() if isinstance(u, dict) else ''
    if not p:
        return ''
    if re.match(r'^https?://', p):
        return p
    return f"{api_base()}/public/profile/{p}"


def roster(arr):
    out = []
    for u in arr or []:
        par = (u or {}).get('par') or {} if isinstance(u, dict) else {}
        out.append({'name': name_of(u), 'photo': photo_of(u),
                    'par': par.get('score'), 'tier': par.get('tier') or ''})
    return thumbnail.normalize(out)


def cat_label(c):
    v = str(c or '').strip()
    if '&' not in v:
        return v
    if 'Bronze' in v or 'Silver' in v:
        return 'B&S'
    if 'Gold' in v or 'Platinum' in v:
        return 'G&P'
    return v


def video_id(s):
    s = (s or '').strip()
    m = re.search(r'(?:v=|/live/|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})', s)
    return m.group(1) if m else s


def main():
    ap = argparse.ArgumentParser(description='리그 경기 썸네일 다시 만들기')
    ap.add_argument('--league', required=True, help="리그 이름 일부 (예: 26S3)")
    ap.add_argument('--match', type=int, required=True, help='매치 번호 (썸네일의 MATCH n)')
    ap.add_argument('--date', default='', help='썸네일 날짜(YYYY.MM.DD) — 비우면 경기 날짜')
    ap.add_argument('--video', default='', help='올릴 YouTube 방송 URL 또는 id (선택)')
    ap.add_argument('--out', default='', help='JPG 저장 경로 (기본: 바탕화면 아래 ps_thumb_<리그>_<번호>.jpg)')
    a = ap.parse_args()

    leagues = (get('/league/list') or {}).get('items') or []
    lg = next((l for l in leagues if a.league.lower() in str(l.get('name', '')).lower()), None)
    if not lg:
        sys.exit(f"리그를 못 찾았어요: {a.league} (있는 것: {', '.join(str(l.get('name')) for l in leagues)})")
    matches = get(f"/league/{lg['_id']}/matches") or []
    m = next((x for x in matches if int(x.get('matchNumber') or 0) == a.match), None)
    if not m:
        sys.exit(f"{lg.get('name')} 에 Match {a.match} 가 없어요.")

    team_a, team_b = roster(m.get('teamA')), roster(m.get('teamB'))
    if not team_a or not team_b:
        sys.exit('팀이 아직 안 짜인 매치예요.')
    cats = m.get('categories') or []
    category = cat_label(cats[0]) if len(cats) == 1 else ('Bridge' if cats else cat_label(m.get('division')))
    date_str = a.date or (str(m.get('date') or '')[:10].replace('-', '.')) or datetime.now().strftime('%Y.%m.%d')

    league_short = shorten_league(str(lg.get('name') or ''))

    out = a.out or os.path.join(os.path.expanduser('~'), 'Desktop',
                                f"ps_thumb_{re.sub(r'[^A-Za-z0-9]+', '', league_short)}_{a.match}.jpg")
    os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
    path = thumbnail.build(team_a, team_b, league=league_short, category=category,
                           match_number=a.match, date_str=date_str, out_path=out)
    if not path:
        sys.exit('썸네일을 만들지 못했어요(위 로그 확인 — Pillow 설치 여부).')
    for side, team in (('A', team_a), ('B', team_b)):
        for p in team:
            print(f"  팀{side} {p['name']:<10} {'사진 ✅' if p['photo'] else '사진 없음 → 이니셜'}")
    print(f"\n✅ 썸네일: {path}  ({league_short} [{category}] Match {a.match} · {date_str})")

    if a.video:
        from youtube_api import YouTubeAPI
        with open(os.path.join(DIR, 'config.json'), encoding='utf-8-sig') as f:
            cfg = json.load(f)
        ok = YouTubeAPI(cfg).set_thumbnail(video_id(a.video), path)
        print('✅ YouTube 에 올렸어요.' if ok else '⚠️ YouTube 업로드 실패 — 위 로그를 보고, 파일을 Studio 에서 직접 올려 주세요.')


def shorten_league(league: str) -> str:
    """PS iLeague 26S3 → PSiL 26S3 — stream_server.shorten_league 와 같은 규칙(그 파일은 import 하면 서버가 뜬다)."""
    m = re.match(r'PS\s*i[\-\s]?League\s*(.*)', league, re.IGNORECASE)
    return f"PSiL {m.group(1).strip()}" if m else league


if __name__ == '__main__':
    main()
