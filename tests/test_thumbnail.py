"""
라이브 썸네일 — 네트워크·YouTube 없이 돈다(사진 내려받기를 가짜로 바꿔 끼운다).

    python tests/test_thumbnail.py

여기서 못 박는 것:
  · **무슨 일이 있어도 방송을 막지 않는다** — 어떤 입력에도 예외를 던지지 않는다.
  · 사진이 없는 선수, 사진을 못 받은 선수, 이름만 있는 구버전 태블릿 입력 전부 그려진다.
  · YouTube 한도 안이다 (1280×720 · 2MB 이하).
  · 이름 배열(구버전)과 선수 상세(신버전)를 섞어 받아도 같은 모양으로 정규화된다.

⚠️ Pillow 가 없는 환경에서는 '건너뛴다'만 확인하고 끝낸다 — 그게 실제 동작이라
   (썸네일이 없을 뿐 방송은 나간다) 테스트가 실패하면 안 된다.
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

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


def fake_photo(w=600, h=800, color=(200, 90, 70)):
    """세로로 긴 인물 사진을 흉내낸다 — 가운데 정사각 자르기가 실제로 도는지 보려고."""
    from PIL import Image, ImageDraw
    im = Image.new('RGB', (w, h), color)
    d = ImageDraw.Draw(im)
    d.ellipse([w * 0.25, h * 0.12, w * 0.75, h * 0.52], fill=(250, 225, 205))   # 얼굴 자리
    return im


def install_fake_fetch(mapping):
    """`_fetch` 를 갈아 끼운다 — 테스트가 네트워크를 타면 CI 에서 붙었다 떨어졌다 한다."""
    thumbnail._fetch = lambda url, timeout=3.0: mapping.get(url)


def out(name):
    os.makedirs(OUT_DIR, exist_ok=True)
    return os.path.join(OUT_DIR, name)


# ─────────────────────────────────────────────────────────────────────────────
print('\n[1] normalize — 이름만(구버전) · 상세(신버전) 둘 다 받는다')
{
}
n1 = thumbnail.normalize(['김하경', '이준우'])
check('이름 배열 → 상세 모양', n1 == [
    {'name': '김하경', 'photo': '', 'par': None, 'tier': ''},
    {'name': '이준우', 'photo': '', 'par': None, 'tier': ''},
], str(n1))

n2 = thumbnail.normalize([{'name': ' 박서연 ', 'photo': 'http://x/a.jpg', 'par': 4.2, 'tier': 'Gold'}])
check('상세는 공백을 털고 그대로', n2 == [
    {'name': '박서연', 'photo': 'http://x/a.jpg', 'par': 4.2, 'tier': 'Gold'},
], str(n2))

check('빈 이름은 버린다 (자리만 차지한다)', thumbnail.normalize(['', None, '  ']) == [])
check('None 을 줘도 빈 목록', thumbnail.normalize(None) == [])


# ─────────────────────────────────────────────────────────────────────────────
if not have_pillow():
    print('\n[2] Pillow 없음 — 썸네일을 건너뛰는지만 확인')
    check('Pillow 가 없으면 None (방송은 그대로 진행)',
          thumbnail.build(['김하경'], ['박서연']) is None)
    print('\n실패 %d건\n' % len(fails) if fails else '\n전부 통과\n')
    sys.exit(1 if fails else 0)


from PIL import Image  # noqa: E402

A = [{'name': '김하경', 'photo': 'http://x/a.jpg', 'par': 4.2, 'tier': 'gold'},
     {'name': '이준우', 'photo': 'http://x/b.jpg', 'par': 3.4, 'tier': 'silver'}]
B = [{'name': '박서연', 'photo': '', 'par': 5.6, 'tier': 'platinum'},
     {'name': '최나래', 'photo': 'http://x/dead.jpg', 'par': 2.8, 'tier': 'bronze'}]

print('\n[2] 사진 있음 · 없음 · 못 받음 — 넷 다 그려진다')
install_fake_fetch({
    'http://x/a.jpg': fake_photo(),
    'http://x/b.jpg': fake_photo(900, 900, (70, 110, 180)),
    # 'http://x/dead.jpg' 는 일부러 없다 → None → 이니셜 폴백
})
p = thumbnail.build(A, B, league='PSiL 26S3', category='B&S', match_number=12,
                    date_str='2026.10.05', out_path=out('mixed.jpg'))
check('파일이 만들어진다', bool(p) and os.path.exists(p), str(p))
if p:
    im = Image.open(p)
    check('1280×720', im.size == (1280, 720), str(im.size))
    kb = os.path.getsize(p) / 1024
    check(f'2MB 한도 안 ({kb:.0f}KB)', os.path.getsize(p) < 2 * 1024 * 1024)
    check('JPEG', im.format == 'JPEG', str(im.format))
    # 사진이 들어갔는지 — 왼쪽 첫 선수 자리의 색이 배경(잉크)과 다르다.
    px = im.convert('RGB').load()
    check('첫 선수 자리에 사진이 들어갔다', px[313, 336] != px[20, 400],
          f'{px[313, 336]} vs 배경 {px[20, 400]}')

print('\n[3] 어떤 입력에도 예외를 던지지 않는다 (방송이 멈추면 안 된다)')
cases = [
    ('빈 팀 둘', ([], [])),
    ('한쪽만 있음', (['김하경'], [])),
    ('단식 1v1', (['김하경'], ['박서연'])),
    ('세 명 이상', (['가', '나', '다'], ['라', '마', '바'])),
    ('아주 긴 이름', (['김하경이준우박서연최나래장진호'], ['최'])),
    ('이름이 숫자·기호', (['★☆♪', '123456'], ['abc', 'A B C'])),
    ('par 가 문자열', ([{'name': '김', 'par': 'N/A'}], [{'name': '박', 'par': None}])),
    ('tier 가 모르는 값', ([{'name': '김', 'tier': '없는등급'}], [{'name': '박', 'tier': None}])),
    ('photo 가 http 가 아님', ([{'name': '김', 'photo': 'file:///etc/passwd'}], [{'name': '박', 'photo': 'abc.jpg'}])),
    ('None 투성이', (None, None)),
]
for label, (ta, tb) in cases:
    try:
        r = thumbnail.build(ta, tb, out_path=out('edge.jpg'))
        check(f'{label} — 예외 없음', True)
        if r:
            check(f'{label} — 규격 유지', Image.open(r).size == (1280, 720))
    except Exception as e:                                   # noqa: BLE001
        check(f'{label} — 예외 없음', False, f'{type(e).__name__}: {e}')

print('\n[4] 사진 내려받기가 통째로 죽어도 그린다')


def boom(url, timeout=3.0):
    raise RuntimeError('network down')


_saved = thumbnail._fetch
thumbnail._fetch = boom
try:
    # ⚠️ _fetch 자체가 던지는 건 실제로는 일어나지 않지만(내부에서 삼킨다), 누군가
    #    나중에 그 try 를 지울 수 있다. 그때 방송이 멈추면 안 된다.
    r = thumbnail.build(A, B, out_path=out('netdown.jpg'))
    check('네트워크가 죽어도 None 또는 정상 파일', r is None or os.path.exists(r), str(r))
except Exception as e:                                       # noqa: BLE001
    check('네트워크가 죽어도 예외 없음', False, f'{type(e).__name__}: {e}')
finally:
    thumbnail._fetch = _saved

print('\n[5] 긴 이름 둘이 나란히 서도 서로 침범하지 않는다')
# ⚠️ '크리스티안 알렉산드로' 가 한 단어처럼 붙어 읽힌 적이 있다 — 이름 폭 상한을
#    사진 지름(+여백)으로 잡았는데 선수 **간격**이 그보다 좁았다. 픽셀로 재면
#    폰트마다 답이 달라지므로(OBS PC 는 맑은 고딕, 리눅스는 대체 폰트) 운영 코드가
#    실제로 넘긴 값을 받아 **간격 안에 들어가는지**만 본다.
seen = []
_orig_paste = thumbnail._paste_player


def _spy(img, draw, cx, cy, size, player, name_w=0):
    seen.append((cx, name_w or (size + 56)))
    return _orig_paste(img, draw, cx, cy, size, player, name_w)


thumbnail._paste_player = _spy
try:
    install_fake_fetch({})
    thumbnail.build([{'name': '크리스티안'}, {'name': '알렉산드로'}],
                    [{'name': '구하경'}, {'name': '서원기'}], out_path=out('longname.jpg'))
finally:
    thumbnail._paste_player = _orig_paste

check('선수 넷을 다 그렸다', len(seen) == 4, str(seen))
pairs = list(zip(seen, seen[1:]))
same_team = [(a, b) for a, b in pairs if abs(b[0] - a[0]) < 400]     # 팀 안 이웃만
check('팀 안에 이웃이 잡힌다', len(same_team) == 2, str(same_team))
for (cx1, w1), (cx2, w2) in same_team:
    space = abs(cx2 - cx1)
    check(f'이름 폭({w1}) 이 선수 간격({space}) 보다 좁다 — 최소 8px 여백',
          w1 <= space - 8 and w2 <= space - 8, f'cx {cx1}->{cx2}, w {w1}/{w2}')

print('\n[6] 사진이 주인공이다 — 등급 색 아바타가 사진을 이기지 않는다')
# ⚠️ 등급 색을 그대로 깔면 **사진을 올린 사람이 손해를 본다**: 골드·플래티넘이 쨍해서
#    어둡고 채도 낮은 얼굴 사진보다 시선을 먼저 뺏는다. 그래서 아바타 원만 배경 잉크
#    쪽으로 누른다(AVATAR_MUTE). 여기서 못 박는 건 '누르되 너무 누르지 않는다' 다.
raw = thumbnail.PAR_COLOR['gold']
mut = thumbnail._muted(raw)
check('아바타 원은 등급 색 그대로가 아니다', mut != raw, f'{raw} -> {mut}')
check('배경 잉크 쪽으로 당겨진다 (어두워진다)', sum(mut) < sum(raw), f'{sum(raw)} -> {sum(mut)}')

# 잉크 한 점으로 당기는 선형 혼합이라 **모든 등급 쌍의 색 차이가 똑같이 (1-MUTE) 배**로
# 줄어든다. 그래서 등급 구분이 얼마나 남는지는 MUTE 하나로 정해진다 — 절반은 남겨야
# 브론즈와 골드가, 실버와 플래티넘이 아직 다른 색으로 보인다.
check('등급 구분이 절반 이상 남는다 (0 < MUTE <= 0.5)',
      0 < thumbnail.AVATAR_MUTE <= 0.5, f'AVATAR_MUTE={thumbnail.AVATAR_MUTE}')

# ⚠️ 이름 아래 PAR 숫자까지 누르면 안 된다 — 어두운 배경 위 28px 글씨라 채도가 곧
#    가독성이다. 감쇠는 아바타 **한 곳**에만 걸려 있어야 한다.
src = io.open(os.path.join(ROOT, 'thumbnail.py'), encoding='utf-8').read()
check('감쇠를 거는 자리는 아바타 한 곳뿐이다',
      src.count('_muted(PAR_COLOR') == 1, f"{src.count('_muted(PAR_COLOR')}곳")
par_line = [ln for ln in src.splitlines() if 'PAR_COLOR.get' in ln and '_muted' not in ln]
check('PAR 숫자는 등급 색 그대로 쓴다', len(par_line) == 1, str(par_line))

print('\n[7] 헤더 정보가 없어도 그린다 (자유 라이브)')
install_fake_fetch({})
r = thumbnail.build(['김하경', '이준우'], ['박서연', '최나래'], out_path=out('free.jpg'))
check('리그·카테고리·매치번호 없이도 파일이 나온다', bool(r) and os.path.exists(r))

print(f'\n실패 {len(fails)}건\n' if fails else '\n전부 통과\n')
sys.exit(1 if fails else 0)
