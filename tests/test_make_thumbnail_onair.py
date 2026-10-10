"""
썸네일 다시 만들기(make_thumbnail.py)도 **방송 이름**을 따른다 (2026-10-10 · 태블릿 c15 와 같은 규칙).
네트워크·YouTube 없이 돈다(서버 응답과 썸네일 합성을 가짜로 바꿔 끼운다).

    python tests/test_make_thumbnail_onair.py

여기서 못 박는 것:
  · 이름은 서버가 붙인 `onAir.name` 이 먼저 — 없으면(옛 서버) 예전 실명 규칙 그대로.
  · 닉네임 모드인데 닉네임이 비어 서버가 '선수' 를 주면 '선수' — **실명으로 되돌아가지 않는다**.
  · `onAir.showPhoto` 가 False 면 사진 없음 → 썸네일이 닉네임 첫 글자 이니셜을 그린다.
  · 실명 모드·옛 서버는 사진 그대로(파일 이름만 와도 URL 로).
"""
import io
import os
import sys
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import make_thumbnail as mt  # noqa: E402
import thumbnail  # noqa: E402

OUT_DIR = os.path.join(HERE, '_out')

fails = []


def check(name, cond, detail=''):
    if cond:
        print(f'  ok   {name}')
    else:
        fails.append(name)
        print(f'  FAIL {name}' + (f'\n       {detail}' if detail else ''))


def have_pillow():
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


# 서버(GET /league/:id/matches) 응답 모양 그대로의 사람들
NICK = {'_id': 'u1', 'lastNameKorean': '임', 'firstNameKorean': '우재', 'profile': 'https://cdn/p1.jpg',
        'par': {'score': 4.2, 'tier': 'gold'},
        'onAir': {'name': '우재짱', 'showPhoto': False, 'mode': 'nickname'}}
REAL = {'_id': 'u2', 'lastNameKorean': '조', 'firstNameKorean': '준희', 'profile': 'https://cdn/p2.jpg',
        'onAir': {'name': '조준희', 'showPhoto': True, 'mode': 'real'}}
OLD = {'_id': 'u3', 'lastNameKorean': '이', 'firstNameKorean': '준우', 'profile': 'p3.jpg'}   # 옛 서버 — onAir 없음
EMPTY_NICK = {'_id': 'u4', 'lastNameKorean': '설', 'firstNameKorean': '정수', 'profile': 'https://cdn/p4.jpg',
              'onAir': {'name': '선수', 'showPhoto': False, 'mode': 'nickname'}}

print('\n[1] name_of — onAir 이름이 먼저, 없으면 실명')
check('닉네임 모드 → 닉네임', mt.name_of(NICK) == '우재짱', mt.name_of(NICK))
check('실명 모드 → 실명', mt.name_of(REAL) == '조준희', mt.name_of(REAL))
check('onAir 없음(옛 서버) → 예전 실명 규칙', mt.name_of(OLD) == '이준우', mt.name_of(OLD))
check("닉네임이 비어 '선수' → 실명으로 안 돌아간다", mt.name_of(EMPTY_NICK) == '선수', mt.name_of(EMPTY_NICK))
check('onAir 이름 공백은 턴다', mt.name_of({'onAir': {'name': '  공백닉  '}}) == '공백닉')
check('빈 onAir 이름 → 실명', mt.name_of({'lastNameKorean': '김', 'firstNameKorean': '하경', 'onAir': {'name': '  '}}) == '김하경')
check('onAir 가 dict 가 아니면 무시 → 실명',
      mt.name_of({'lastNameKorean': '김', 'firstNameKorean': '하경', 'onAir': '닉'}) == '김하경')
check('영문 이름(옛 서버)', mt.name_of({'firstName': 'Ana', 'lastName': 'Kim'}) == 'Ana Kim')
check('dict 가 아니면 빈 이름', mt.name_of(None) == '' and mt.name_of('u1') == '')

print('\n[2] photo_of — showPhoto False 면 사진 없음')
check('닉네임 모드 → 사진 없음', mt.photo_of(NICK) == '', mt.photo_of(NICK))
check("닉네임 모드('선수') → 사진 없음", mt.photo_of(EMPTY_NICK) == '', mt.photo_of(EMPTY_NICK))
check('실명 모드 → 사진 그대로', mt.photo_of(REAL) == 'https://cdn/p2.jpg', mt.photo_of(REAL))
check('옛 서버 · 파일 이름 → URL 로', mt.photo_of(OLD) == f"{mt.api_base()}/public/profile/p3.jpg", mt.photo_of(OLD))
check('showPhoto 가 없으면(None) 사진 그대로',
      mt.photo_of({'profile': 'https://cdn/x.jpg', 'onAir': {'name': 'x'}}) == 'https://cdn/x.jpg')
check('dict 가 아니면 빈 사진', mt.photo_of(None) == '' and mt.photo_of('u1') == '')

print('\n[3] roster — 썸네일에 넘기는 한 벌')
r = mt.roster([NICK, REAL])
check('이름·사진이 방송용', [(p['name'], p['photo']) for p in r] == [('우재짱', ''), ('조준희', 'https://cdn/p2.jpg')], str(r))
check('등급은 그대로(사진만 빠진다)', r[0]['par'] == 4.2 and r[0]['tier'] == 'gold', str(r[0]))

print('\n[4] main — 서버 응답에서 썸네일까지(가짜 서버 · 가짜 합성)')
LEAGUES = {'items': [{'_id': 'L1', 'name': 'PS iLeague 26S3'}]}
MATCHES = [{'_id': 'M39', 'matchNumber': 39, 'date': '2026-10-08', 'categories': ['Gold+'],
            'teamA': [NICK, REAL], 'teamB': [OLD, EMPTY_NICK]}]
mt.get = lambda p: LEAGUES if p == '/league/list' else (MATCHES if p == '/league/L1/matches' else None)
built = []
real_build = thumbnail.build
thumbnail.build = lambda a, b, **kw: (built.append({'a': a, 'b': b, **kw}) or kw.get('out_path'))
argv = sys.argv
sys.argv = ['make_thumbnail.py', '--league', '26S3', '--match', '39', '--out', os.path.join(OUT_DIR, 'onair_main.jpg')]
buf = io.StringIO()
try:
    with redirect_stdout(buf):
        mt.main()
finally:
    sys.argv = argv
    thumbnail.build = real_build
names = built and ([p['name'] for p in built[0]['a']], [p['name'] for p in built[0]['b']])
check('썸네일 이름이 방송 이름', names == (['우재짱', '조준희'], ['이준우', '선수']), str(names))
photos = built and ([p['photo'] for p in built[0]['a']], [p['photo'] for p in built[0]['b']])
check('닉네임 둘은 사진 없음', photos == (['', 'https://cdn/p2.jpg'], [f"{mt.api_base()}/public/profile/p3.jpg", '']), str(photos))
printed = buf.getvalue()
check('찍는 목록에도 실명이 없다(닉네임 고른 둘)', '임우재' not in printed and '설정수' not in printed, printed)

print('\n[5] 실제 합성 — 사진 없는 자리는 닉네임 첫 글자 이니셜')
if not have_pillow():
    print('  skip Pillow 없음 — 썸네일은 건너뛰고 방송은 나간다(그게 실제 동작)')
else:
    fetched, initials = [], []
    real_fetch, real_initial = thumbnail._fetch, thumbnail._initial_avatar
    thumbnail._fetch = lambda url, timeout=3.0: (fetched.append(url) if url else None) or None
    thumbnail._initial_avatar = lambda name, tier, size: (initials.append(name) or real_initial(name, tier, size))
    os.makedirs(OUT_DIR, exist_ok=True)
    try:
        path = thumbnail.build(mt.roster([NICK, REAL]), mt.roster([OLD, EMPTY_NICK]),
                               league='PSiL 26S3', category='Gold+', match_number=39, date_str='2026.10.08',
                               out_path=os.path.join(OUT_DIR, 'onair.jpg'))
    finally:
        thumbnail._fetch, thumbnail._initial_avatar = real_fetch, real_initial
    check('파일이 나온다', bool(path) and os.path.exists(path))
    check('닉네임 고른 사람의 사진은 받으러 가지도 않는다',
          'https://cdn/p1.jpg' not in fetched and 'https://cdn/p4.jpg' not in fetched, str(fetched))
    check('이니셜은 닉네임으로 그린다', '우재짱' in initials and '선수' in initials, str(initials))
    check('실명인 사람 이름으로 이니셜을 그리지 않는다', '임우재' not in initials and '설정수' not in initials, str(initials))

print(f'\n실패 {len(fails)}건\n' if fails else '\n전부 통과\n')
sys.exit(1 if fails else 0)
