"""
라이브 썸네일 만들기 — 그 경기의 **선수 이름과 사진**이 들어간 1280×720 한 장.

왜 필요했나
━━━━━━━━━━
26S2 라이브는 썸네일을 아예 안 올렸다. 그래서 YouTube 가 방송 첫 프레임을 자동으로
집었고, 그건 대개 사람이 아직 안 들어온 빈 코트였다. 목록에 똑같이 생긴 빈 코트
섬네일이 스무 개 늘어서서, 누가 뛰는 경기인지 열어 보기 전엔 알 수 없었다.

어디서 도나
━━━━━━━━━━
OBS PC 안이다(`C:\\dev\\PS-rank\\.venv`, Python 3.12). 백엔드에서 만들어 받아오는 길도
있었지만 그러면 VPS 에 렌더러(크로미움)를 새로 세워야 하고, 방송 시작이 그 서버의
사정에 묶인다. 여기서 만들면 필요한 게 **Pillow 하나**고, 실패해도 그 자리에서 끝난다.

⚠️ 썸네일 실패가 방송을 막지 않는다. 이 모듈의 모든 함수는 예외를 던지지 않고
   `None` 을 돌려준다 — 그림 한 장 때문에 경기 중계를 못 켜는 일은 없어야 한다.

⚠️ 이 파일은 **별도 repo `padelociety/PS-rank`** 로 배포된다(모노repo 는 사본).
   고칠 땐 PS-rank 를 먼저 보고, 고친 뒤 모노repo 로 되복사한다.
"""

import io
import logging
import os
import tempfile

logger = logging.getLogger(__name__)

W, H = 1280, 720          # YouTube 썸네일 규격 (16:9, 2MB 이하)
JPEG_QUALITY = 88         # 이 크기·품질이면 200~350KB — 2MB 한도에서 한참 아래다

# ── 팔레트 — DESIGN_SYSTEM.md 럭셔리 잉크 (tv.html 과 같은 값) ──────────────
INK0 = (18, 58, 66)       # #123A42  그라데이션 위
INK1 = (14, 48, 56)       # #0E3038
INK2 = (7, 27, 32)        # #071B20  아래
CREAM = (242, 236, 220)   # #F2ECDC
GOLD = (198, 168, 103)    # #C6A867
GOLD_HI = (217, 188, 122)  # #D9BC7A
TERRA = (179, 84, 60)     # #B3543C  LIVE
MUTED = (138, 160, 160)   # #8AA0A0

# PAR 등급 색 — ps_court_playus.html 의 PAR_COLOR 와 같은 값.
PAR_COLOR = {
    'beginner': (156, 163, 175), 'bronze': (201, 123, 63), 'silver': (154, 161, 170),
    'gold': (212, 160, 23), 'platinum': (79, 177, 168), 'master': (107, 43, 181),
    'pro': (255, 61, 0),
}

# ⚠️ **등급 색을 그대로 깔면 사진 없는 선수가 더 눈에 띈다.** 골드·플래티넘·프로가
#    쨍해서, 진짜 얼굴 사진(어둡고 채도가 낮다)보다 시선을 먼저 뺏는다 — 사진을
#    넣은 사람이 손해를 보는 그림이다. 그래서 아바타 원은 배경 잉크 쪽으로 눌러 깐다.
#    등급은 여전히 읽히되 사진을 이기지 않을 만큼만.
#    ⚠️ 이 감쇠는 **아바타 원에만** 건다. 이름 아래 PAR 숫자까지 누르면 어두운 배경
#       위 작은 글씨라 그냥 안 읽힌다 — 거기선 채도가 곧 가독성이다.
AVATAR_MUTE = 0.42        # 0 = 등급 색 그대로, 1 = 배경과 구분 안 됨
AVATAR_INK = (13, 46, 54)  # 아바타가 놓이는 높이(cy)의 배경색 — 여기로 당긴다


def _muted(color):
    return tuple(int(color[i] + (AVATAR_INK[i] - color[i]) * AVATAR_MUTE) for i in range(3))

# ── 폰트 ────────────────────────────────────────────────────────────────────
# ⚠️ 한글이 나온다. 한글 글리프가 없는 폰트를 집으면 이름이 전부 □□□ 로 나가고,
#    그건 썸네일이 아예 없는 것보다 나쁘다(경기 정보가 틀린 것처럼 보인다).
#    맑은 고딕은 Windows 기본 폰트라 OBS PC 에 반드시 있다.
_FONT_BOLD = [
    r'C:\Windows\Fonts\malgunbd.ttf',      # 맑은 고딕 Bold
    r'C:\Windows\Fonts\malgun.ttf',
    '/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
    '/System/Library/Fonts/AppleSDGothicNeo.ttc',
    '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',   # 리눅스 최후 폴백 (한글 글리프 포함)
]
_FONT_REG = [
    r'C:\Windows\Fonts\malgun.ttf',
    '/usr/share/fonts/truetype/nanum/NanumGothic.ttf',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/System/Library/Fonts/AppleSDGothicNeo.ttc',
    '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
]
_font_cache = {}


def _font(size: int, bold: bool = False):
    """크기·굵기에 맞는 폰트. 하나도 못 찾으면 None (호출부가 그리기를 건너뛴다)."""
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]
    from PIL import ImageFont
    for path in (_FONT_BOLD if bold else _FONT_REG):
        if os.path.exists(path):
            try:
                f = ImageFont.truetype(path, size)
                _font_cache[key] = f
                return f
            except Exception:
                continue
    logger.warning("⚠️ 한글 폰트를 못 찾았어요 — 썸네일 이름이 깨질 수 있습니다")
    _font_cache[key] = None
    return None


def _text_w(draw, text, font) -> int:
    if not font:
        return 0
    try:
        box = draw.textbbox((0, 0), text, font=font)
        return box[2] - box[0]
    except Exception:
        return 0


def _fit(draw, text: str, size: int, max_w: int, bold=True):
    """`max_w` 안에 들어갈 때까지 글자를 줄인다. (font, text) 를 돌려준다.

    ⚠️ 이름은 자르지 않고 **줄인다**. '김하경' 을 '김하…' 로 자르면 누구인지 모르게
       되는데, 썸네일에 이름을 넣는 이유가 바로 그 하나다. 그래도 안 들어가는
       아주 긴 이름만 마지막에 자른다.
    """
    for s in range(size, max(10, size - 18), -2):
        f = _font(s, bold)
        if not f or _text_w(draw, text, f) <= max_w:
            return f, text
    f = _font(max(10, size - 18), bold)
    while text and _text_w(draw, text + '…', f) > max_w:
        text = text[:-1]
    return f, (text + '…' if text else '')


# ── 배경 ────────────────────────────────────────────────────────────────────
def _background():
    """세로 그라데이션 + 위쪽 은은한 하이라이트 (tv.html 의 --tv-bg 를 흉내낸다)."""
    from PIL import Image
    img = Image.new('RGB', (W, H), INK1)
    px = img.load()
    for y in range(H):
        t = y / (H - 1)
        if t < 0.42:                                  # INK0 → INK1
            u = t / 0.42
            c = tuple(int(INK0[i] + (INK1[i] - INK0[i]) * u) for i in range(3))
        else:                                         # INK1 → INK2
            u = (t - 0.42) / 0.58
            c = tuple(int(INK1[i] + (INK2[i] - INK1[i]) * u) for i in range(3))
        for x in range(W):
            px[x, y] = c
    # 위쪽 가운데 하이라이트 — 평평한 배경이면 인쇄물처럼 죽어 보인다.
    from PIL import ImageDraw
    glow = Image.new('L', (W, H), 0)
    gd = ImageDraw.Draw(glow)
    gd.ellipse([W * 0.5 - 620, -420, W * 0.5 + 620, 420], fill=42)
    from PIL import ImageFilter
    glow = glow.filter(ImageFilter.GaussianBlur(120))
    img.paste(Image.new('RGB', (W, H), (255, 255, 255)), (0, 0), glow)
    return img


# ── 선수 사진 ───────────────────────────────────────────────────────────────
def _fetch(url: str, timeout=3.0):
    """사진 한 장. 실패하면 None — 한 명이 안 받아져도 나머지는 그린다."""
    if not url or not str(url).startswith(('http://', 'https://')):
        return None
    try:
        import requests
        from PIL import Image
        r = requests.get(url, timeout=timeout)
        if r.status_code != 200 or not r.content:
            return None
        # ⚠️ 아주 큰 원본이 올 수 있다 — 먼저 줄이고 나서 자른다.
        im = Image.open(io.BytesIO(r.content))
        im.load()
        return im.convert('RGB')
    except Exception as e:
        logger.debug(f"선수 사진 못 받음 ({url}): {e}")
        return None


def _circle(img, size: int):
    """가운데를 정사각으로 잘라 원형으로. 넣은 사진의 얼굴이 가운데 있다고 본다."""
    from PIL import Image, ImageDraw
    w, h = img.size
    side = min(w, h)
    # 위쪽을 조금 더 남긴다 — 인물 사진은 얼굴이 가운데보다 위에 있다.
    top = max(0, int((h - side) * 0.35))
    left = (w - side) // 2
    img = img.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)
    mask = Image.new('L', (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size * 4 - 1, size * 4 - 1], fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)     # 4배로 그려 줄여 가장자리를 매끈하게
    img.putalpha(mask)
    return img


def _initial_avatar(name: str, tier: str, size: int):
    """사진이 없는 선수 — 등급 색 원에 이름 첫 글자.

    ⚠️ 빈 자리로 두지 않는다. 넷 중 하나만 비면 그 사람만 빠진 것처럼 보인다.
    """
    from PIL import Image, ImageDraw
    color = _muted(PAR_COLOR.get(str(tier or '').lower(), (60, 80, 86)))
    big = size * 4
    img = Image.new('RGBA', (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([0, 0, big - 1, big - 1], fill=color + (255,))
    ch = (name or '?').strip()[:1] or '?'
    f = _font(int(big * 0.46), True)
    if f:
        box = d.textbbox((0, 0), ch, font=f)
        d.text(((big - (box[2] - box[0])) / 2 - box[0], (big - (box[3] - box[1])) / 2 - box[1]),
               ch, font=f, fill=(255, 255, 255, 235))
    return img.resize((size, size), Image.LANCZOS)


def _paste_player(img, draw, cx: int, cy: int, size: int, player: dict, name_w: int = 0):
    """원형 사진 + 골드 링 + 이름 + 등급 점수 한 벌.

    ⚠️ `name_w` 는 **이름이 쓸 수 있는 폭**이고 호출부가 선수 간격에서 계산해 넘긴다.
       사진 지름(+여백)으로 잡으면 안 된다 — 사진보다 간격이 좁아서, 긴 이름 둘이
       나란히 서면 서로 붙어 한 단어처럼 읽힌다('크리스티안 알렉산드로').
    """
    from PIL import Image, ImageDraw
    name = (player.get('name') or '').strip() or '선수'
    photo = _fetch(player.get('photo'))
    av = _circle(photo, size) if photo else _initial_avatar(name, player.get('tier'), size)

    # 골드 링 — 4배로 그려 줄인다(직접 그리면 계단이 보인다).
    ring = Image.new('RGBA', ((size + 14) * 4, (size + 14) * 4), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse([0, 0, (size + 14) * 4 - 1, (size + 14) * 4 - 1],
                                 outline=GOLD + (215,), width=13)
    ring = ring.resize((size + 14, size + 14), Image.LANCZOS)
    img.paste(ring, (cx - (size + 14) // 2, cy - (size + 14) // 2), ring)
    img.paste(av, (cx - size // 2, cy - size // 2), av)

    f, txt = _fit(draw, name, 42, name_w or (size + 56), bold=True)
    if f:
        draw.text((cx - _text_w(draw, txt, f) / 2, cy + size // 2 + 20), txt, font=f, fill=CREAM)

    par = player.get('par')
    if par not in (None, ''):
        try:
            label = f"{float(par):.1f}"
        except (TypeError, ValueError):
            label = str(par)
        fp = _font(28, True)
        if fp:
            color = PAR_COLOR.get(str(player.get('tier') or '').lower(), MUTED)
            draw.text((cx - _text_w(draw, label, fp) / 2, cy + size // 2 + 70),
                      label, font=fp, fill=color)


# ── 본체 ────────────────────────────────────────────────────────────────────
def build(team_a, team_b, *, league='', category='', match_number=0, date_str='',
          out_path=None):
    """
    썸네일 한 장을 만들어 **파일 경로**를 돌려준다. 못 만들면 `None`.

    team_a / team_b: `[{'name':…, 'photo':URL|'', 'par':4.2|None, 'tier':'gold'}]`
      문자열 리스트(이름만)도 받는다 — 옛 호출부·자유 라이브가 그렇게 부른다.
    """
    try:
        from PIL import Image, ImageDraw        # noqa: F401  (없으면 여기서 걸린다)
    except ImportError:
        logger.warning("⚠️ Pillow 가 없어 썸네일을 건너뜁니다 — pip install -r requirements.txt")
        return None

    try:
        return _build(team_a, team_b, league, category, match_number, date_str, out_path)
    except Exception as e:
        # ⚠️ 절대 위로 던지지 않는다 — 썸네일 때문에 방송이 안 켜지면 안 된다.
        logger.warning(f"⚠️ 썸네일 생성 실패 (방송은 그대로 진행): {e}")
        return None


def normalize(team):
    """이름 문자열 · dict 를 섞어 받아 한 모양으로."""
    out = []
    for p in (team or []):
        if isinstance(p, dict):
            out.append({
                'name': str(p.get('name') or '').strip(),
                'photo': str(p.get('photo') or '').strip(),
                'par': p.get('par'),
                'tier': p.get('tier') or '',
            })
        else:
            out.append({'name': str(p or '').strip(), 'photo': '', 'par': None, 'tier': ''})
    return [p for p in out if p['name']]


def _build(team_a, team_b, league, category, match_number, date_str, out_path):
    from PIL import ImageDraw

    a = normalize(team_a)
    b = normalize(team_b)
    img = _background()
    draw = ImageDraw.Draw(img)

    # ── 상단 띠 ──
    head = []
    if league:
        head.append(league)
    if category:
        head.append(f"[{category}]")
    if match_number:
        head.append(f"MATCH {match_number}")
    fh = _font(34, True)
    if fh and head:
        draw.text((60, 52), '   ·   '.join(head), font=fh, fill=GOLD_HI)
    if date_str:
        fd = _font(28, False)
        if fd:
            draw.text((W - 60 - _text_w(draw, date_str, fd), 58), date_str, font=fd, fill=MUTED)
    draw.line([(60, 112), (W - 60, 112)], fill=GOLD, width=2)

    # ── 선수 ──
    # ⚠️ **팀이 한 덩어리로 보여야 한다.** 넷을 화면에 고르게 벌려 놓으면 2v2 가 아니라
    #    개인 넷이 서 있는 그림이 된다. 그래서 팀 안 간격(`gap`)을 팀 사이 간격보다
    #    확실히 좁게 잡고, 가운데에 세로 헤어라인을 넣어 편을 가른다.
    n = max(len(a), len(b), 1)
    size = 190 if n <= 2 else 145
    gap = size + 30
    cy = 336
    for team, tcx in ((a, int(W * 0.245)), (b, int(W * 0.755))):
        if not team:
            continue
        start = tcx - gap * (len(team) - 1) / 2
        # 옆 사람과 최소 16px 은 띄운다. 혼자면 사진보다 조금 넓게 써도 된다.
        name_w = (gap - 16) if len(team) > 1 else (size + 56)
        for i, p in enumerate(team):
            _paste_player(img, draw, int(start + gap * i), cy, size, p, name_w)

    # ── 가운데 — 세로 헤어라인 + VS ──
    # 선은 VS 위아래로 **끊어서** 긋는다. 통으로 그으면 글자를 관통하는데, 그걸 가리려고
    # 뒤에 사각형을 깔면 배경 그라데이션 위에 네모난 얼룩이 생긴다(한 번 그렇게 했다).
    fv = _font(76, True)
    vs_half = 52
    blend = tuple(int(INK2[i] + (GOLD[i] - INK2[i]) * 0.30) for i in range(3))
    draw.line([(W // 2, 170), (W // 2, cy - vs_half)], fill=blend, width=2)
    draw.line([(W // 2, cy + vs_half), (W // 2, H - 132)], fill=blend, width=2)
    if fv:
        vw = _text_w(draw, 'VS', fv)
        draw.text((W // 2 - vw / 2, cy - 48), 'VS', font=fv, fill=GOLD)

    # ── 하단 띠 ──
    draw.line([(60, H - 96), (W - 60, H - 96)], fill=GOLD, width=2)
    fb = _font(30, True)
    if fb:
        draw.text((60, H - 74), 'PADEL SOCIETY  i-LEAGUE', font=fb, fill=CREAM)
        live = 'LIVE'
        wl = _text_w(draw, live, fb)
        draw.rounded_rectangle([W - 60 - wl - 34, H - 80, W - 60, H - 34], radius=23, fill=TERRA)
        draw.text((W - 60 - wl - 17, H - 74), live, font=fb, fill=(255, 255, 255))

    path = out_path or os.path.join(tempfile.gettempdir(), 'ps_live_thumbnail.jpg')
    img.save(path, 'JPEG', quality=JPEG_QUALITY, optimize=True)
    kb = os.path.getsize(path) // 1024
    logger.info(f"🖼️ 썸네일 생성: {os.path.basename(path)} ({W}×{H}, {kb}KB)")
    return path
