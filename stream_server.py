"""
PS Court 자동 스트리밍 서버
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ps_court.html에서 '스코어 입력' 버튼을 누르면:
  1. YouTube 라이브 방송 자동 생성 (제목/설명 자동 생성)
  2. OBS 스트리밍 자동 시작
  3. 경기 저장 or 초기화 시 자동 종료

실행 방법:
  python stream_server.py
  (또는 start_server.bat 더블클릭)

포트: 5000
"""

import atexit
import functools
import hashlib
import json
import re
import logging
import os
import signal
import socket
import sys
import tempfile
import itertools
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, request
from flask_cors import CORS


def get_lan_ip():
    """현재 PC의 LAN IP (테블릿이 같은 와이파이에서 접속할 주소)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return 'localhost'

import stream_health
import thumbnail
from obs_controller import OBSController
from youtube_api import YouTubeAPI

# ── 로깅 설정 ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-8s  %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)
# ⚠️ obsws_python 은 붙을 때마다 INFO 로 'Connecting with parameters: … password=…' 를 찍는다 — OBS 웹소켓 비밀번호가
#    로그 파일에 그대로 남았다(2026-11-12 발견). 그 로거는 경고부터만 받는다.
logging.getLogger('obsws_python').setLevel(logging.WARNING)

# ── 설정 로드 ──────────────────────────────────────────────────
_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_DIR, 'config.json')

if not os.path.exists(CONFIG_PATH):
    logger.error(f"❌ config.json 파일이 없어요: {CONFIG_PATH}")
    logger.error("config.json.example 파일을 복사해서 config.json으로 저장하세요.")
    exit(1)

with open(CONFIG_PATH, 'r', encoding='utf-8-sig') as f:  # 메모장·PowerShell 이 붙인 BOM 도 읽는다
    config = json.load(f)

# ── 컨트롤러 초기화 ────────────────────────────────────────────
obs = OBSController(config)
youtube = YouTubeAPI(config)

# ── Flask 앱 ───────────────────────────────────────────────────
app = Flask(__name__)

# ⚠️ 2026-10-19: 예전엔 CORS origins='*' 에 인증이 없었다 — '로컬 서버라 외부 노출 없음' 이라 믿었지만
#    obs.padelsociety.co.kr 이 역방향 ssh 터널로 이 서버를 **인터넷에 그대로** 내놓는다. 누구나 방송을 켜고
#    끄고 [무효] 를 붙일 수 있었다. 출처는 목록으로 좁히고, 바꾸는 요청은 운영자 로그인을 확인한다(아래).
#    이 서버 자기 주소(localhost:5000)는 config 의 목록과 상관없이 늘 넣는다 — 옛 config.json 예시엔
#    github.io · localhost:3000 둘뿐이라 그대로 쓰면 PC 브라우저에서 연 태블릿 페이지가 막힌다.
_SELF_ORIGINS = ['http://localhost:5000', 'http://127.0.0.1:5000',
                 'https://localhost:5000', 'https://127.0.0.1:5000']
CORS_ORIGINS = list(dict.fromkeys(
    [str(o).rstrip('/') for o in (config.get('cors_origins') or [
        'https://padelociety.github.io',
        'https://obs.padelsociety.co.kr',
    ]) if o] + _SELF_ORIGINS))
CORS(app, origins=CORS_ORIGINS, allow_headers=['Content-Type', 'Authorization'])

# ── 운영자 확인 (방송을 바꾸는 요청) ───────────────────────────────
# 태블릿은 운영자 로그인 토큰(psToken)을 Authorization: Bearer 로 보낸다. 그 토큰을 Playus 백엔드에 물어
# **이 방송 PC 의 센터를 조작해도 되나** 를 본다(2026-11-09 · 검토 S4-04·B1-06 — 예전엔 myProfile 의 역할만 봐서
# 어느 센터의 center_admin 이든 본점 방송을 켜고 끄고 [무효] 를 붙였다):
#   GET {api_base}/api/stream/operator-check[?centerId=<control_auth.center_id>] → data.ok
#   = 슈퍼어드민 ∨ 그 센터에 묶인 태블릿 계정(league_operator) ∨ 그 센터 관리자(메인·부관리자).
#   center_id 를 비우면 백엔드의 본점 설정(서버 .env HQ_CENTER_ID · 없으면 PUBLIC_BOOKING_CENTER_ID — 어스센터 isDefault 아님).
# 옛 백엔드(그 주소가 없어 404)면 예전처럼 myProfile 역할로 본다 — 그때 기본 역할엔 center_admin 이 없다(센터를 못 가린다).
# 이 PC 에서 직접 부른 요청(터널·LAN 이 아닌 127.0.0.1)만 토큰 없이 통과 — OBS PC 브라우저의 /reload-html 과
# make_thumbnail.py 가 그 길이다.
_AUTH_CFG = config.get('control_auth') or {}
CONTROL_AUTH_ENABLED = _AUTH_CFG.get('enabled', True) is not False
# 이 방송 PC 의 센터(Playus 센터 id) — 비우면 백엔드의 본점 설정(HQ_CENTER_ID). 백엔드가 이 센터로 판정한다.
# ⚠️ 적어 두는 게 맞다 — 비워 두면 백엔드 본점 설정이 바뀌는 순간 이 PC 의 운영자가 따라 바뀐다.
CONTROL_CENTER_ID = str(_AUTH_CFG.get('center_id') or '').strip()
# 옛 백엔드(확인 주소 없음)일 때만 쓰는 역할 목록 — center_admin 은 기본에서 뺐다(어느 센터 관리자든 들어왔다).
CONTROL_ROLES = frozenset(_AUTH_CFG.get('roles') or ['league_operator', 'super_admin'])
AUTH_API_BASE = (_AUTH_CFG.get('api_base') or (config.get('highlight') or {}).get('api_base')
                 or 'https://api.padelsociety.co.kr').rstrip('/')
_AUTH_OK_TTL = 600            # 통과한 토큰은 10분 동안 다시 안 묻는다
_AUTH_DENY_TTL = 30           # 거절(401·역할 없음)은 30초 — 권한을 바로 주면 곧 풀린다
_AUTH_GRACE = 12 * 3600       # 백엔드가 안 닿을 때: 12시간 안에 통과했던 토큰이면 들인다(경기장 인터넷 끊김)
_AUTH_GRACE_RECHECK = 60      # 그렇게 들인 뒤엔 1분 동안 다시 묻지 않는다(요청마다 5초씩 멈추지 않게)
_auth_lock = threading.Lock()
_auth_cache: dict = {}        # sha256(토큰) → {'verdict': 'ok'|'forbidden'|'invalid', 'until': t, 'last_ok': t}

AUTH_REQUIRED_MSG = '운영자 로그인이 필요해요 — 태블릿 ⚙ 설정에서 로그인해 주세요'
AUTH_FORBIDDEN_MSG = '이 계정엔 운영자 권한이 없어요 — 운영자 계정으로 ⚙ 설정에서 다시 로그인해 주세요'
AUTH_CENTER_CFG_MSG = ('방송 PC 의 센터 설정이 틀렸어요 — config.json 의 control_auth.center_id 를 '
                       'Playus 센터 id(24자리)로 고치거나 비워 주세요')
AUTH_DOWN_MSG = ('운영자 확인 서버(Playus)에 연결하지 못했어요 — 방송 PC 의 인터넷 연결을 확인한 뒤 '
                 '다시 시도해 주세요')

if CONTROL_CENTER_ID and not re.fullmatch(r'[0-9a-fA-F]{24}', CONTROL_CENTER_ID):
    logger.error(f"❌ control_auth.center_id 가 센터 id 모양이 아니에요({CONTROL_CENTER_ID!r}) — 운영자 확인이 전부 거절됩니다")
elif CONTROL_AUTH_ENABLED and not CONTROL_CENTER_ID:
    logger.warning("⚠️ control_auth.center_id 가 비어 있어요 — 백엔드 본점 설정(HQ_CENTER_ID)의 센터로 운영자를 확인합니다. "
                   "이 PC 의 센터 id 를 적어 두세요")

if not CONTROL_AUTH_ENABLED:
    logger.warning("!" * 60)
    logger.warning("⚠️⚠️ control_auth.enabled = false — 운영자 확인을 끈 상태입니다.")
    logger.warning("⚠️⚠️ obs.padelsociety.co.kr 터널로 **누구나** 방송을 켜고 끌 수 있어요. 점검 때만 끄세요.")
    logger.warning("!" * 60)

# 썸네일 사진은 우리 API 호스트에서만 받는다(thumbnail.PHOTO_HOSTS · SSRF 방지) — 다른 백엔드를 쓰는 PC 만 config 로 바꾼다.
_photo_hosts = (config.get('thumbnail') or {}).get('photo_hosts')
if _photo_hosts:
    thumbnail.PHOTO_HOSTS = frozenset(str(h).strip().lower() for h in _photo_hosts if str(h).strip())

_LOCAL_ADDRS = ('127.0.0.1', '::1')
# 프록시·터널이 붙이는 머리 — 하나라도 있으면 이 PC 에서 직접 온 요청이 아니다(ssh 터널도 127.0.0.1 로 들어온다).
_PROXY_HEADERS = ('X-Real-IP', 'X-Forwarded-For', 'Forwarded', 'CF-Connecting-IP')


def _is_local_direct(req) -> bool:
    """이 PC 에서 직접 부른 요청인가 — 127.0.0.1 · 프록시 머리 없음 · 출처가 없거나 허용 목록."""
    if req.remote_addr not in _LOCAL_ADDRS:
        return False
    if any(h in req.headers for h in _PROXY_HEADERS):
        return False
    origin = req.headers.get('Origin')
    if origin:
        return origin.rstrip('/') in CORS_ORIGINS
    # 출처가 없는 GET(<img src> 등)도 다른 사이트가 보낸 것이면 들이지 않는다.
    return req.headers.get('Sec-Fetch-Site', '') not in ('cross-site', 'same-site')


def _fetch_profile_role(token: str):
    """백엔드에 토큰 주인을 묻는다 → (상태, 역할). 테스트가 이 함수를 갈아 끼운다.
    상태: 'ok'(역할을 읽었다) · 'invalid'(401·403·433 — 틀린·만료·회수된 토큰) · 'down'(못 물어봤다)."""
    req = urllib.request.Request(
        f"{AUTH_API_BASE}/api/user/myProfile",
        headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read(2_000_000).decode('utf-8') or '{}')
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 433):   # 433 = 백엔드의 '토큰 만료'
            return 'invalid', ''
        return 'down', f'HTTP {e.code}'
    except Exception as e:
        return 'down', str(e)
    if not isinstance(body, dict) or body.get('isSuccess') is False:
        return 'invalid', ''
    data = body.get('data') if isinstance(body.get('data'), dict) else {}
    return 'ok', str(data.get('role') or 'user')


_old_backend_warned = [False]


def _fetch_operator_verdict(token: str):
    """백엔드에 '이 토큰이 이 방송 PC(센터)를 조작해도 되나' 를 묻는다 → (상태, 정보). 테스트가 이 함수를 갈아 끼운다.
    상태: 'ok'(정보 = 통과 이유) · 'forbidden'(로그인은 맞는데 이 센터 권한 없음 — 정보 = 서버 문장) ·
          'invalid'(401·403·433) · 'down'(못 물어봤다 — 정보 = 이유) · 'old'(옛 백엔드 — 주소가 없어 404)."""
    qs = f"?centerId={urllib.parse.quote(CONTROL_CENTER_ID)}" if CONTROL_CENTER_ID else ''
    req = urllib.request.Request(
        f"{AUTH_API_BASE}/api/stream/operator-check{qs}",
        headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read(2_000_000).decode('utf-8') or '{}')
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 433):   # 433 = 백엔드의 '토큰 만료'
            return 'invalid', ''
        if e.code == 404:
            return 'old', ''
        if e.code == 422:               # centerId 모양이 틀렸다 — 설정 오타
            return 'forbidden', AUTH_CENTER_CFG_MSG
        return 'down', f'HTTP {e.code}'
    except Exception as e:
        return 'down', str(e)
    if not isinstance(body, dict) or body.get('isSuccess') is False:
        return 'invalid', ''
    data = body.get('data') if isinstance(body.get('data'), dict) else {}
    if data.get('ok') is True:
        return 'ok', str(data.get('reason') or '')
    return 'forbidden', str(data.get('message') or '')


def _operator_state(token: str):
    """운영자 확인 한 번 → (상태, 정보). 새 주소가 먼저 — 옛 백엔드(404)면 myProfile 역할(CONTROL_ROLES)로 떨어진다."""
    state, info = _fetch_operator_verdict(token)
    if state != 'old':
        return state, info
    if not _old_backend_warned[0]:
        _old_backend_warned[0] = True
        logger.warning("⚠️ Playus 백엔드에 방송 운영자 확인 주소(/api/stream/operator-check)가 없어요 — "
                       f"옛 규칙(역할 {sorted(CONTROL_ROLES)} · 센터 구분 없음)으로 확인합니다. 백엔드를 배포해 주세요")
    state, role = _fetch_profile_role(token)
    if state == 'ok':
        return ('ok', role) if role in CONTROL_ROLES else ('forbidden', '')
    return state, role


def _check_operator_token(token: str):
    """토큰을 확인한다 → None(통과) 또는 (상태코드, 응답 dict). 결과는 토큰 해시로 잠깐 기억한다."""
    key = hashlib.sha256(token.encode('utf-8')).hexdigest()
    now = time.time()
    with _auth_lock:
        ent = dict(_auth_cache.get(key) or {})
    if not ent or ent.get('until', 0) <= now:
        state, info = _operator_state(token)   # info = 통과 이유·역할('ok') · 서버 문장('forbidden') · 못 물어본 이유('down')
        now = time.time()
        if state == 'ok':
            ent = {'verdict': 'ok', 'until': now + _AUTH_OK_TTL, 'last_ok': now}
        elif state == 'forbidden':
            ent = {'verdict': 'forbidden', 'until': now + _AUTH_DENY_TTL, 'last_ok': 0, 'msg': info}
        elif state == 'invalid':
            ent = {'verdict': 'invalid', 'until': now + _AUTH_DENY_TTL, 'last_ok': 0}
        elif ent.get('last_ok') and now - ent['last_ok'] <= _AUTH_GRACE:
            logger.warning(f"⚠️ 운영자 확인 서버에 못 닿음({info}) — 12시간 안에 확인된 토큰이라 들입니다")
            ent = dict(ent, verdict='ok', until=now + _AUTH_GRACE_RECHECK)
        else:
            logger.error(f"❌ 운영자 확인 서버에 못 닿음({info}) — 확인된 적 없는 토큰이라 막습니다")
            return 503, {'success': False, 'error': AUTH_DOWN_MSG, 'auth': 'unavailable'}
        with _auth_lock:
            if len(_auth_cache) > 256:   # 오래된 칸 정리 — 통과 기록(last_ok)이 살아 있는 칸은 남긴다
                for k in [k for k, v in _auth_cache.items()
                          if v.get('until', 0) <= now and now - (v.get('last_ok') or 0) > _AUTH_GRACE]:
                    _auth_cache.pop(k, None)
            _auth_cache[key] = ent
    if ent['verdict'] == 'ok':
        return None
    if ent['verdict'] == 'forbidden':
        return 403, {'success': False, 'error': ent.get('msg') or AUTH_FORBIDDEN_MSG, 'auth': 'forbidden'}
    return 401, {'success': False, 'error': AUTH_REQUIRED_MSG, 'auth': 'required'}


def _require_operator(fn):
    """방송을 바꾸는 라우트 앞에 붙인다 — 이 PC 직접 요청이거나 운영자 토큰이어야 통과."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if CONTROL_AUTH_ENABLED and not _is_local_direct(request):
            auth = request.headers.get('Authorization', '')
            token = auth[7:].strip() if auth[:7].lower() == 'bearer ' else ''
            if not token or len(token) > 4096:
                fail = (401, {'success': False, 'error': AUTH_REQUIRED_MSG, 'auth': 'required'})
            else:
                fail = _check_operator_token(token)
            if fail:
                who = request.headers.get('X-Real-IP') or request.remote_addr
                logger.warning(f"🔒 {request.path} 거절({fail[0]} · {fail[1]['auth']}) — {who}")
                return jsonify(fail[1]), fail[0]
        return fn(*args, **kwargs)
    return wrapper

# ── ps_court_playus.html 서빙 ─────────────────────────────────
# GitHub에서 최신 파일을 시작 시 다운로드해 메모리에 캐시. OBS PC에 파일 없어도 OK.
# 테블릿이 http://(PC-IP):5000/ 으로 열면 페이지·API 가 같은 출처(HTTP)라
# localhost 한계와 HTTPS 혼합콘텐츠 차단이 모두 사라진다.
COURT_HTML_URL = 'https://raw.githubusercontent.com/padelociety/PS-rank/main/ps_court/ps_court_playus.html'
_court_html_cache = None


def _load_court_html():
    global _court_html_cache
    try:
        logger.info("📥 ps_court_playus.html 다운로드 중...")
        with urllib.request.urlopen(COURT_HTML_URL, timeout=15) as resp:
            _court_html_cache = resp.read().decode('utf-8')
        logger.info("✅ ps_court_playus.html 로드 완료")
    except Exception as e:
        logger.warning(f"⚠️ 다운로드 실패 ({e}) — 로컬 파일 시도")
        for candidate in [
            os.path.join(_DIR, 'ps_court_playus.html'),
            os.path.join(_DIR, 'ps_court', 'ps_court_playus.html'),
            os.path.join(os.path.dirname(_DIR), 'PS-rank', 'ps_court', 'ps_court_playus.html'),
        ]:
            if os.path.exists(candidate):
                with open(candidate, 'r', encoding='utf-8') as f:
                    _court_html_cache = f.read()
                logger.info(f"로컬 파일 사용: {candidate}")
                return
        logger.error("❌ ps_court_playus.html 을 찾을 수 없어요")


_load_court_html()  # 서버 시작 시 즉시 로드


@app.route('/')
def _index():
    if _court_html_cache:
        return _court_html_cache, 200, {'Content-Type': 'text/html; charset=utf-8'}
    return '❌ ps_court_playus.html 로드 실패. 서버를 재시작해주세요.', 503


_RELOAD_MIN_GAP = 20          # 초 — 페이지 다시 받기는 이 간격에 한 번만
_reload_lock = threading.Lock()
_last_reload = [0.0]


@app.route('/reload-html')
def _reload_html():
    """페이지 재다운로드 (최신 버전으로 갱신).
    ⚠️ 운영자 확인을 걸지 않는다 — 배포 절차가 폰 브라우저로 https://obs.padelsociety.co.kr/reload-html 을 여는
       것이고(backend/nginx/deploy_obs_court.md), 하는 일은 GitHub PS-rank main 의 파일을 다시 받는 것뿐이라 남이 불러도
       페이지 내용을 바꿀 수 없다. 대신 20초에 한 번만 받는다 — 터널로 두드려 GitHub 요청을 쏟아내지 못하게."""
    with _reload_lock:
        now = time.time()
        if now - _last_reload[0] < _RELOAD_MIN_GAP:
            return jsonify({'ok': True, 'loaded': _court_html_cache is not None, 'throttled': True})
        _last_reload[0] = now
    _load_court_html()
    return jsonify({'ok': True, 'loaded': _court_html_cache is not None})


@app.route('/ps_court_playus_manifest.json')
def _manifest():
    """홈 화면 추가(웹클립)용 매니페스트. start_url을 stream_server 루트로."""
    return jsonify({
        "name": "PS Court — 리그 운영",
        "short_name": "PS Court",
        "start_url": "/",
        "scope": "/",
        "id": "/ps-court",
        "display": "standalone",
        "background_color": "#080C10",
        "theme_color": "#080C10",
        "orientation": "any",
        "icons": [
            {"src": "https://padelociety.github.io/PS-rank/icon-court-192.png",
             "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "https://padelociety.github.io/PS-rank/icon-court-512.png",
             "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    })

# ── 스트림 상태 ────────────────────────────────────────────────
stream_state: dict = {
    'active': False,
    'mode': '',            # 'league'(리그 매치 — 점수 저장하면 끝) | 'free'(자유 라이브 — 시간이 다 되면 끝)
    'broadcast_id': None,
    'watch_url': None,
    'started_at': None,
    'ends_at': None,       # 자유 라이브만. 이 시각이 지나면 워치독이 끈다(태블릿이 꺼져도).
    'duration_minutes': 0,
    'team_a': [],
    'team_b': [],
    'league': '',
    'title': '',
    # 어느 리그 매치의 방송인가 — 다음 매치가 오면 넘겨받고, 같은 매치면 이어 쓴다(2026-10-08).
    'match_id': '',
    'match_number': 0,
    'category': '',
    'error': None,
}
state_lock = threading.Lock()

# ⚠️ 방송을 켜고 끄는 일(/start-stream · /stop-stream · /void-video · 자유 라이브 워치독)은 **한 번에 하나**.
#    2026-10-08: 경기 저장(끄기)이 OBS·유튜브를 정리하는 몇 초 사이에 '다른 페어로 다시하기' 의 켜기가
#    끼어들면, 켜기가 새로 세운 상태를 끄기의 마지막 정리가 지웠다 — 서버는 '방송 없음' 이라 믿고 OBS 는
#    그 방송으로 계속 내보냈다. 그 뒤로는 끄기가 아무 일도 안 해서 지난 경기 제목 방송에 다음 경기가
#    이어 나갔다. RLock — 켜기가 넘겨받으며 끄기를 부른다. 상태 읽기(/health · /highlight)는 이 잠금을
#    안 쓴다(state_lock 만) — 켜는 동안에도 태블릿 상태 표시는 그대로 돈다.
stream_op_lock = threading.RLock()


def _clear_state_locked():
    """스트림 상태를 초기값으로. 호출자가 state_lock을 잡고 있어야 한다."""
    stream_state.update({
        'active': False,
        'mode': '',
        'broadcast_id': None,
        'watch_url': None,
        'started_at': None,
        'ends_at': None,
        'duration_minutes': 0,
        'team_a': [],
        'team_b': [],
        'league': '',
        'title': '',
        'match_id': '',
        'match_number': 0,
        'category': '',
        'error': None,
    })

# ── 진행 중인 방송을 디스크에 적어 둔다 (2026-10-21) ─────────────────
# 유튜브 방송을 자동 종료 없이 만든다(youtube_api — 업로드가 잠깐 끊겨도 같은 방송으로 이어지게). 대신 끝내기는
# 이 서버 몫이라, 서버가 죽거나 PC 가 꺼지면 그 방송이 '라이브' 로 남는다. 그래서 켤 때 적고 끌 때 지우고,
# 다음에 서버가 뜰 때 남아 있으면 정리한다(_recover_leftover). 끝내기가 실패한 방송도 여기 남겨 다시 해 본다.
LIVE_FILE = os.path.join(_DIR, 'live_broadcast.json')
_live_file_lock = threading.Lock()


def _live_file_read() -> dict:
    """{'current': {방송 상태}|None, 'pending_end': [방송 id…]} — 없거나 못 읽으면 빈 모양."""
    try:
        with open(LIVE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        data = {}
    cur = data.get('current') if isinstance(data.get('current'), dict) else None
    pend = [str(x) for x in (data.get('pending_end') or []) if str(x)]
    return {'current': cur, 'pending_end': pend}


def _live_file_write(data: dict):
    try:
        if not data.get('current') and not data.get('pending_end'):
            if os.path.exists(LIVE_FILE):
                os.remove(LIVE_FILE)
            return
        tmp = LIVE_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, LIVE_FILE)
    except Exception as e:
        logger.warning(f"방송 기록 파일을 못 썼어요(무시): {e}")


def _live_file_set_current(state: dict):
    with _live_file_lock:
        data = _live_file_read()
        keys = ('mode', 'broadcast_id', 'watch_url', 'started_at', 'ends_at', 'duration_minutes',
                'team_a', 'team_b', 'league', 'title', 'match_id', 'match_number', 'category')
        data['current'] = {k: state.get(k) for k in keys}
        _live_file_write(data)


def _live_file_done(broadcast_id: str, ended: bool):
    """방송을 끝냈으면(ended) 기록에서 지우고, 못 끝냈으면 '다시 끝낼 것' 에 남긴다."""
    if not broadcast_id:
        return
    with _live_file_lock:
        data = _live_file_read()
        if data['current'] and data['current'].get('broadcast_id') == broadcast_id:
            data['current'] = None
        pend = [b for b in data['pending_end'] if b != broadcast_id]
        if not ended:
            pend.append(broadcast_id)
        data['pending_end'] = pend[-20:]
        _live_file_write(data)


END_RETRY_DELAYS = (15, 45, 120, 300, 600)   # 끝내기 실패 → 이만큼 쉬고 다시(초). 다 실패하면 다음 서버 시작 때.
_end_later_ids = set()
_end_later_lock = threading.Lock()


def _end_broadcast_now(broadcast_id: str) -> bool:
    """유튜브 방송을 끝내고 기록을 맞춘다. 못 끝냈으면 뒤에서 다시 해 본다(_end_later). 끝냈으면 True."""
    if not broadcast_id:
        return True
    try:
        ended = bool(youtube.end_broadcast(broadcast_id))
    except Exception as e:
        logger.warning(f"⚠️ 방송 종료 오류 ({broadcast_id}): {e}")
        ended = False
    _live_file_done(broadcast_id, ended)
    if not ended:
        _end_later(broadcast_id)
    return ended


def _end_later(broadcast_id: str, delays=None):
    """끝내기가 실패한 방송을 뒤에서 몇 번 더 끝내 본다(같은 방송에 둘이 돌지 않게). 끝내 실패하면 기록에 남아
    다음 서버 시작 때 다시 정리한다. 자동 종료를 꺼서(youtube_api) 안 끝내면 '라이브' 로 계속 남는다."""
    delays = END_RETRY_DELAYS if delays is None else delays
    with _end_later_lock:
        if broadcast_id in _end_later_ids:
            return
        _end_later_ids.add(broadcast_id)

    def run():
        try:
            for wait in delays:
                time.sleep(wait)
                with state_lock:
                    live_again = stream_state.get('active') and stream_state.get('broadcast_id') == broadcast_id
                if live_again:
                    return   # 그 사이 같은 방송을 다시 쓰고 있다(되살림) — 끝내지 않는다
                try:
                    ended = bool(youtube.end_broadcast(broadcast_id))
                except Exception:
                    ended = False
                if ended:
                    _live_file_done(broadcast_id, True)
                    logger.info(f"⏹️ 미뤄 둔 방송 종료 성공 ({broadcast_id})")
                    return
            logger.error(f"❌ 방송을 끝내지 못했어요 — 유튜브 스튜디오에서 직접 종료해 주세요 ({broadcast_id})")
        finally:
            with _end_later_lock:
                _end_later_ids.discard(broadcast_id)

    threading.Thread(target=run, daemon=True, name=f'end-{broadcast_id}').start()


def _recover_leftover():
    """서버가 뜰 때 — 지난번에 끝내지 못한 방송을 정리한다.
    · 기록된 방송이 있고 OBS 가 **아직 송출 중**이면(서버만 죽었다 다시 뜸) 그 방송을 이어받는다 — 태블릿이 다시
      LIVE 로 보고, 경기 저장이 평소처럼 끈다.
    · OBS 가 송출 중이 아니면 그 방송을 끝낸다(정전·PC 재시작 — 경기는 이미 끝났다).
    · '다시 끝낼 것' 에 남은 방송도 끝낸다."""
    data = _live_file_read()
    cur = data.get('current')
    pend = list(data.get('pending_end') or [])
    if not cur and not pend:
        return
    adopted = False
    if cur and cur.get('broadcast_id'):
        try:
            streaming = obs.is_streaming()
        except Exception:
            streaming = False
        if streaming:
            with stream_op_lock:
                with state_lock:
                    if not stream_state['active']:
                        stream_state.update({k: v for k, v in cur.items() if k in stream_state})
                        stream_state['active'] = True
                        stream_state['error'] = None
                        adopted = True
            if adopted:
                logger.info(f"↪️ 서버가 다시 떴어요 — OBS 가 송출 중인 방송을 이어받아요: {cur.get('title') or cur.get('broadcast_id')}")
        if not adopted:
            logger.info(f"↩️ 지난번에 끝내지 못한 방송을 정리해요: {cur.get('title') or cur.get('broadcast_id')}")
            _end_broadcast_now(cur['broadcast_id'])
    for bid in pend:
        if adopted and cur and bid == cur.get('broadcast_id'):
            continue
        logger.info(f"↩️ 끝내기 실패로 남은 방송을 다시 끝내요: {bid}")
        _end_broadcast_now(bid)


# ── 방송 상태 감시 (2026-10-21 — "유투브가 자주 끊긴대") ───────────────
# 방송 중 10초마다 OBS 송출 숫자(재연결 · 프레임 손실 · 혼잡 · 인코딩 밀림 · 카메라)를, 1분마다 YouTube 가 보는
# 스트림 건강을 읽어 stream_health.evaluate 로 '지금 무엇이 문제인가' 를 정한다. /health 의 stream_health 로
# 태블릿 상태 줄이 그리고, 바뀔 때마다 로그 한 줄. 그동안은 끊겨도 **어디서** 끊겼는지 남는 게 없었다.
HEALTH_TICK_S = 10
YOUTUBE_HEALTH_EVERY = 6          # 10초 × 6 = 1분마다 YouTube 에 묻는다(liveStreams.list 1 단위)
health_state = {'broadcast_id': None, 'health': None, 'reconnects': 0, 'since': None}
_health_mem = {'prev': None, 'prev_t': 0.0, 'cams': {}, 'yt': None, 'tick': 0, 'issue': '', 'reconnecting': False,
               'frame': None}


# ── 방송 화면 검사 (2026-11-01 — "유투브 또 이러네ㅜㅜ 나 외부여서 가서 고치지도 못하는데") ─────
# 유튜브엔 초록 화면만 나가는데 송출 숫자·카메라 '재생 중' 은 다 정상이었다(카메라 영상을 푸는 디코더가 빈 프레임을
# 낸다). 방송 중 20초마다 카메라 소스를 32x18 로 찍어 한 가지 색이면 — 두 번 연속 → 그 카메라를 다시 열고, 그래도면
# 하드웨어 디코딩을 끈다(stream_health.camera_frame_step). 카메라가 멀쩡한데 방송 장면이 단색이면 알리기만 한다.
# 사람이 누르는 길도 있다: POST /camera-restart(운영자) — 태블릿 [📷 카메라 다시 연결] · 밖에서는 폰으로 obs 주소.
_frame_lock = threading.Lock()
_frame_mem = {'bad': {}, 'notes': {}, 'restarts': {}}
camera_frame_state = {'at': None, 'sources': [], 'program': None, 'last_action': '', 'last_action_at': None}
PROGRAM_KEY = '방송 화면'


def _frame_verdicts(sources):
    """[(이름, hw)] → [{name, hw, ok, uniform, green, color, share, label}] — 못 찍은 소스는 ok=False."""
    out = []
    for name, hw in sources:
        v = stream_health.frame_verdict(obs.frame_pixels(name))
        v.update({'name': name, 'hw': hw, 'label': stream_health.frame_label(v)})
        out.append(v)
    return out


def _note_action(text: str):
    logger.warning(f"📷 {text}")
    with state_lock:
        camera_frame_state['last_action'] = text
        camera_frame_state['last_action_at'] = datetime.now(timezone.utc).isoformat()


def _frame_check():
    """화면 검사 한 바퀴 → evaluate 의 frame 인자({'bad': [...], 'note': ...}). 테스트가 직접 부른다."""
    with _frame_lock:
        srcs = obs.camera_sources()
        cams = _frame_verdicts(srcs)
        bad = _frame_mem['bad']
        notes = _frame_mem['notes']
        names = {c['name'] for c in cams}
        for k in list(bad.keys()):
            if k not in names and k != PROGRAM_KEY:
                bad.pop(k, None)
                notes.pop(k, None)
        for c in cams:
            name = c['name']
            if not c['ok']:
                continue                      # 못 찍었으면 판정하지 않는다(세지도 풀지도 않는다)
            if not c['uniform']:
                if bad.get(name, 0) >= stream_health.FRAME_GRACE:
                    logger.info(f"✅ 카메라 화면 돌아옴: {name}")
                bad.pop(name, None)
                notes.pop(name, None)
                continue
            bad[name] = bad.get(name, 0) + 1
            step = stream_health.camera_frame_step(bad[name], c['hw'])
            if step == 'restart' and obs.restart_camera(name):
                n = _frame_mem['restarts'][name] = _frame_mem['restarts'].get(name, 0) + 1
                notes[name] = f"카메라를 다시 열었어요({n}번)"
                _note_action(f"{name} — {c['label']} → 카메라 다시 열기")
            elif step == 'hw_off' and obs.set_hw_decode(name, False):
                c['hw'] = False
                notes[name] = '하드웨어 디코딩을 끄고 다시 열었어요'
                _note_action(f"{name} — {c['label']} → 하드웨어 디코딩 끔")
        cam_bad = [c['name'] for c in cams if bad.get(c['name'], 0) >= stream_health.FRAME_GRACE]
        # 카메라가 멀쩡한데(또는 카메라 소스가 없는데) 방송 장면이 단색 — 장면·소스 배치 문제. 알리기만 한다.
        prog = None
        if not cam_bad:
            scene = obs.program_scene()
            if scene:
                prog = stream_health.frame_verdict(obs.frame_pixels(scene))
                prog.update({'name': scene, 'label': stream_health.frame_label(prog)})
                if prog['ok'] and prog['uniform']:
                    bad[PROGRAM_KEY] = bad.get(PROGRAM_KEY, 0) + 1
                    notes[PROGRAM_KEY] = (f"카메라는 정상 — OBS 장면 '{scene}' 을 확인해 주세요" if cams
                                          else f"카메라 소스를 못 찾았어요 — OBS 장면 '{scene}' 을 확인해 주세요")
                elif prog['ok']:
                    bad.pop(PROGRAM_KEY, None)
                    notes.pop(PROGRAM_KEY, None)
        else:
            bad.pop(PROGRAM_KEY, None)
            notes.pop(PROGRAM_KEY, None)
        bad_names = cam_bad + ([PROGRAM_KEY] if bad.get(PROGRAM_KEY, 0) >= stream_health.FRAME_GRACE else [])
        note = ''
        for n in bad_names:
            if notes.get(n):
                note = notes[n]
                break
        with state_lock:
            camera_frame_state.update({
                'at': datetime.now(timezone.utc).isoformat(),
                'sources': [{k: c.get(k) for k in ('name', 'ok', 'uniform', 'green', 'color', 'share', 'hw', 'label')}
                            for c in cams],
                'program': ({k: prog.get(k) for k in ('name', 'ok', 'uniform', 'color', 'share', 'label')}
                            if prog else None),
            })
        return {'bad': bad_names, 'note': note}


# ── 송출 영상 검사 · OBS 다시 켜기 (2026-11-11 — "또 유투브 초록화면이야") ─────────────
# OBS 화면(캔버스)은 정상인데 유튜브만 초록이었고, 한 번 그러면 새 방송도 계속 초록 — OBS 를 다시 켜야 풀렸다.
# OBS 스크린샷(_frame_check)은 인코더 **앞**이라 이걸 못 본다. 녹화 인코더가 '(스트리밍 인코더 사용)' 이면 리플레이
# 버퍼 파일 = 유튜브로 나가는 바로 그 영상이라, 방송 중 그 파일을 잠깐 저장해 끝쪽 키프레임을 32x18 로 풀어 본다
# (재인코딩 없음 · 키프레임만 · 1초 안팎). 두 번 연속 한 가지 색이고 OBS 화면은 정상이면 '유튜브 초록 화면' —
# 서버가 OBS 를 다시 켜고 같은 유튜브 방송으로 이어 보낸다(방송 하나에 한 번 · config output_check.auto_restart).
# 녹화 인코더가 따로면(검사가 유튜브 영상을 못 본다) 검사하지 않고 인코딩 권고(hw_double)만 남는다.
OUTPUT_CFG = config.get('output_check') or {}
OUTPUT_ON = OUTPUT_CFG.get('enabled', True) is not False
OUTPUT_AUTO_RESTART = OUTPUT_CFG.get('auto_restart', True) is not False
OUTPUT_FIRST_S = 45          # 방송 시작 뒤 첫 검사(초록은 보통 시작할 때 생긴다)
OUTPUT_EVERY_S = max(120, int(OUTPUT_CFG.get('every_s', 300) or 300))
OUTPUT_RECHECK_S = 30        # 한 번 걸리면 이만큼 뒤 다시(두 번 연속이어야 손댄다)
OUTPUT_HIGHLIGHT_GAP_S = 60  # 하이라이트를 막 저장했으면 그 사이엔 검사 저장을 안 한다
_output_mem = {'bid': None, 'next_at': 0.0, 'bad': 0, 'restarted': False}
output_state = {'at': None, 'checked': None, 'label': '', 'bad': False, 'note': '', 'skipped': '', 'frames': 0,
                'last_action': '', 'last_action_at': None}
_ingest = {'broadcast_id': None, 'rtmp': '', 'key': ''}   # 메모리에만(키는 비밀 — 파일·로그·/health 에 안 남긴다)
_last_highlight_at = [0.0]
SMALL_W, SMALL_H = 32, 18


def _small_frames(path, seconds: int = 12):
    """영상 파일 끝 seconds 초의 키프레임만 32x18 RGB 로 풀어 [[(r,g,b)…]…] — 못 풀면 []."""
    import subprocess
    cmd = [_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-sseof', f'-{int(seconds)}', '-skip_frame', 'nokey',
           '-i', path, '-vf', f'scale={SMALL_W}:{SMALL_H}:flags=area', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1']
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0   # 키프레임만이라 1초 안팎
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=60, creationflags=flags)
    except Exception as e:
        logger.warning(f"송출 영상 풀기 실패(무시): {e}")
        return []
    data = r.stdout or b''
    n = SMALL_W * SMALL_H * 3
    return [[tuple(data[i + j:i + j + 3]) for j in range(0, n, 3)] for i in range(0, len(data) - n + 1, n)]


def _output_covers_stream() -> bool:
    """리플레이 버퍼가 방송 인코더를 같이 쓰나(= 리플레이 파일이 유튜브로 나가는 그 영상인가)."""
    cfg = ((getattr(obs, 'encoding_status', None) or {}).get('config')) or {}
    mode = str(cfg.get('mode') or '').strip().lower()
    if mode == 'advanced':
        return str(cfg.get('rec_encoder') or '').strip().lower() in ('', 'none')
    if mode == 'simple':
        return str(cfg.get('rec_quality') or '').strip().lower() == 'stream'
    return False


def _output_check_once() -> dict:
    """리플레이 버퍼를 잠깐 저장해 인코더가 낸 영상을 판정 → stream_health.output_verdict + canvas(OBS 화면 판정).
    못 하면 {'skipped': 이유}. 저장한 검사 파일은 지운다."""
    if not _output_covers_stream():
        return {'skipped': "녹화 인코더가 따로라 유튜브 영상을 못 봐요 — 녹화 인코더 '(스트리밍 인코더 사용)' 이면 검사해요"}
    if time.time() - _last_highlight_at[0] < OUTPUT_HIGHLIGHT_GAP_S:
        return {'skipped': '하이라이트를 막 저장했어요'}
    if not stream_op_lock.acquire(blocking=False):   # RLock 엔 locked() 가 없다
        return {'skipped': '방송을 켜고 끄는 중'}
    stream_op_lock.release()
    if not obs.is_replay_buffer_active():
        return {'skipped': '리플레이 버퍼 꺼짐'}
    path = obs.save_replay_buffer_nowait()
    if not path:
        return {'skipped': '검사용 저장 실패(하이라이트 저장 중일 수 있어요)'}
    try:
        v = stream_health.output_verdict(_small_frames(path))
    finally:
        with _story_lock:
            used = any(x.get('path') == path for x in _recent_highlights)
        if not used:
            try:
                os.remove(path)
            except OSError as e:
                logger.info(f"검사용 리플레이 파일을 못 지움(무시): {e}")
    canvas = None
    if v.get('bad'):
        scene = obs.program_scene()
        canvas = stream_health.frame_verdict(obs.frame_pixels(scene)) if scene else None
    v['canvas_uniform'] = bool(canvas and canvas.get('ok') and canvas.get('uniform'))
    return v


def _set_output_state(**kw):
    with state_lock:
        output_state.update(kw)


def _note_output_action(text: str):
    logger.warning(f"📺 {text}")
    _set_output_state(last_action=text, last_action_at=datetime.now(timezone.utc).isoformat())


def _restart_obs_and_resume(reason: str) -> dict:
    """OBS 를 다시 켜고, 방송 중이었으면 같은 유튜브 방송(같은 키)으로 이어 보낸다. 유튜브 방송은 끝내지 않는다
    (enableAutoStop=False — 끊긴 몇십 초는 시청자에게 '버퍼링' 으로 보인다). {'ok', 'text'}."""
    with stream_op_lock:
        with state_lock:
            active = bool(stream_state['active'])
            bid = stream_state.get('broadcast_id')
        rtmp, key = ('', '')
        if active and bid:
            if _ingest.get('broadcast_id') == bid and _ingest.get('key'):
                rtmp, key = _ingest['rtmp'], _ingest['key']
            else:
                rtmp, key = youtube.ingest_of(bid)
            if not (rtmp and key):
                return {'ok': False, 'text': '유튜브 방송 주소를 못 읽어 OBS 를 다시 켜지 않았어요 — 방송을 끄고 다시 켜 주세요'}
            try:
                obs.stop_stream()
            except Exception:
                pass
        if not obs.restart_obs_process():
            err = ((getattr(obs, 'obs_restart_status', None) or {}).get('error')) or 'OBS 가 다시 뜨지 않았어요'
            return {'ok': False, 'text': f"OBS 를 다시 켜지 못했어요 — {err} · 방송 PC 에서 OBS 를 직접 켜 주세요"}
        if not active:
            obs.ensure_replay_buffer()
            return {'ok': True, 'text': 'OBS 를 다시 켰어요'}
        try:
            obs.set_stream_settings(rtmp, key)
            obs.start_stream()
            ok = obs.wait_until_streaming()
        except Exception as e:
            ok = False
            logger.error(f"OBS 다시 켠 뒤 송출 시작 실패: {e}")
        try:
            obs.start_replay_buffer()
        except Exception:
            pass
        if not ok:
            return {'ok': False, 'text': 'OBS 는 다시 켰는데 송출이 안 붙었어요 — 태블릿에서 라이브를 끄고 다시 켜 주세요'}
        logger.info(f"🔄 OBS 다시 켜고 같은 방송으로 이어 보냄({reason})")
        return {'ok': True, 'text': 'OBS 를 다시 켜고 같은 유튜브 방송으로 이어 보냈어요 — 20~30초 뒤 유튜브 화면을 봐 주세요'}


def _output_tick(now_t: float = None):
    """송출 영상 검사 한 바퀴 — 테스트가 직접 부른다. 방송 중일 때만 · 시작 45초 뒤 · 5분마다 · 걸리면 30초 뒤 다시."""
    now_t = time.time() if now_t is None else now_t
    with state_lock:
        active = stream_state['active']
        bid = stream_state.get('broadcast_id')
        started = stream_state.get('started_at')
    if not OUTPUT_ON or not active or not bid:
        if _output_mem['bid'] is not None:
            _output_mem.update({'bid': None, 'next_at': 0.0, 'bad': 0, 'restarted': False})
            _set_output_state(bad=False, note='', label='')
        return None
    if _output_mem['bid'] != bid:
        try:
            t0 = datetime.fromisoformat(started).timestamp() if started else now_t
        except (TypeError, ValueError):
            t0 = now_t
        _output_mem.update({'bid': bid, 'next_at': t0 + OUTPUT_FIRST_S, 'bad': 0, 'restarted': False})
        _set_output_state(bad=False, note='', label='', skipped='', checked=None)
    if now_t < _output_mem['next_at']:
        return None
    v = _output_check_once()
    stamp = datetime.now(timezone.utc).isoformat()
    if v.get('skipped'):
        _output_mem['next_at'] = now_t + 60
        _set_output_state(at=stamp, skipped=v['skipped'])
        return v
    if not v.get('ok'):
        _output_mem['next_at'] = now_t + 60
        _set_output_state(at=stamp, checked=False, skipped='영상을 풀지 못했어요')
        return v
    if not v.get('bad') or v.get('canvas_uniform'):
        # 정상 — 또는 OBS 화면부터 한 가지 색(인코더가 아니라 장면·카메라 — 화면 검사 몫)
        if _output_mem['bad']:
            logger.info('✅ 유튜브로 나가는 영상 돌아옴')
        _output_mem.update({'bad': 0, 'next_at': now_t + OUTPUT_EVERY_S})
        _set_output_state(at=stamp, checked=True, bad=False, label='', note='', skipped='', frames=v.get('frames', 0))
        return v
    _output_mem['bad'] += 1
    _output_mem['next_at'] = now_t + OUTPUT_RECHECK_S
    _set_output_state(at=stamp, checked=True, label=v.get('label') or '', skipped='', frames=v.get('frames', 0))
    if _output_mem['bad'] < 2:
        logger.warning(f"⚠️ 유튜브로 나가는 영상이 {v.get('label')} — {OUTPUT_RECHECK_S}초 뒤 다시 봐요(OBS 화면은 정상)")
        return v
    if OUTPUT_AUTO_RESTART and not _output_mem['restarted']:
        _output_mem['restarted'] = True
        _set_output_state(bad=True, note='서버가 OBS 를 다시 켜는 중')
        _note_output_action(f"유튜브로 나가는 영상이 {v.get('label')} → OBS 다시 켜기")
        res = _restart_obs_and_resume('송출 영상 ' + (v.get('label') or '깨짐'))
        _note_output_action(res['text'])
        _output_mem.update({'bad': 0, 'next_at': time.time() + OUTPUT_FIRST_S})
        _set_output_state(bad=not res['ok'], note='' if res['ok'] else res['text'])
        return v
    _set_output_state(bad=True, note=('서버가 이미 한 번 OBS 를 다시 켰어요 — 방송 PC 를 봐 주세요'
                                      if _output_mem['restarted'] else '방송 PC 에서 OBS 를 다시 켜 주세요'))
    return v


# ── 유튜브가 영상을 못 받는 방송 살리기 (2026-11-12 — '예정' 에서 안 넘어감) ──────────────────
# Match230: OBS 는 '송출 시작됨' 이라 답했는데 유튜브는 처음부터 noData 였고(방송이 '예정' 에 머문다), 53분 동안 아무도
# 몰랐다. 리플레이 버퍼 저장도 그동안 전부 빈손이었다 — 방송·리플레이가 같이 쓰는 인코더가 아무것도 안 내고 있었다.
# 방송을 켠 뒤 유튜브가 영상을 받을 때까지 지켜보고, 못 받으면 ① 송출만 껐다 다시 켜고 ② 그래도면 OBS 를 다시 켠다.
# 한 번이라도 영상을 받으면 그 방송은 더 안 본다(도중 끊김은 재연결·방송 상태 감시 몫).
NODATA_CFG = config.get('nodata_guard') or {}
NODATA_ON = NODATA_CFG.get('enabled', True) is not False
NODATA_FIRST_S = 30      # 방송 시작 뒤 첫 확인(유튜브가 받기 시작하는 데 10~20초)
NODATA_RETRY_S = 15      # 받는 중인지 다시 묻는 간격
NODATA_AFTER_FIX_S = 35  # 손댄 뒤 다시 묻기까지
_nodata_mem = {'bid': None, 'next_at': 0.0, 'step': 0, 'seen': 0}
nodata_state = {'at': None, 'status': '', 'step': 0, 'note': '', 'done': False}


def _restream_same(reason: str) -> dict:
    """OBS 송출만 껐다가 같은 유튜브 방송(같은 키)으로 다시 켠다. {'ok', 'text'}. 유튜브 방송은 끝내지 않는다."""
    with stream_op_lock:
        with state_lock:
            active = bool(stream_state['active'])
            bid = stream_state.get('broadcast_id')
        if not (active and bid):
            return {'ok': False, 'text': '방송 중이 아니에요'}
        if _ingest.get('broadcast_id') == bid and _ingest.get('key'):
            rtmp, key = _ingest['rtmp'], _ingest['key']
        else:
            rtmp, key = youtube.ingest_of(bid)
        if not (rtmp and key):
            return {'ok': False, 'text': '유튜브 방송 주소를 못 읽었어요'}
        try:
            obs.stop_stream()
            time.sleep(2.0)
            obs.connect()
            obs.set_stream_settings(rtmp, key)
            obs.start_stream()
            ok = obs.wait_until_streaming()
        except Exception as e:
            ok = False
            logger.error(f"송출 다시 켜기 실패: {e}")
        if ok:
            logger.info(f"🔁 OBS 송출을 껐다 다시 켬({reason})")
        return {'ok': bool(ok), 'text': '송출을 껐다 다시 켰어요' if ok else '송출을 다시 켜지 못했어요'}


def _set_nodata_state(**kw):
    with state_lock:
        nodata_state.update(kw)


def _nodata_tick(now_t: float = None):
    """한 바퀴 — 테스트가 직접 부른다. 방송을 켠 뒤 유튜브가 영상을 받을 때까지만 본다."""
    now_t = time.time() if now_t is None else now_t
    with state_lock:
        active = stream_state['active']
        bid = stream_state.get('broadcast_id')
        started = stream_state.get('started_at')
    if not NODATA_ON or not active or not bid:
        if _nodata_mem['bid'] is not None:
            _nodata_mem.update({'bid': None, 'next_at': 0.0, 'step': 0, 'seen': 0})
            _set_nodata_state(at=None, status='', step=0, note='', done=False)
        return None
    if _nodata_mem['bid'] != bid:
        try:
            t0 = datetime.fromisoformat(started).timestamp() if started else now_t
        except (TypeError, ValueError):
            t0 = now_t
        _nodata_mem.update({'bid': bid, 'next_at': t0 + NODATA_FIRST_S, 'step': 0, 'seen': 0})
        _set_nodata_state(at=None, status='', step=0, note='', done=False)
    if _nodata_mem['step'] >= 9 or now_t < _nodata_mem['next_at']:
        return None
    yt = youtube.stream_health(bid)
    status = str((yt or {}).get('status') or '')
    stamp = datetime.now(timezone.utc).isoformat()
    if yt is None or not status:
        _nodata_mem['next_at'] = now_t + NODATA_RETRY_S          # 유튜브에 못 물었다 — 손대지 않고 다시 묻는다
        return None
    if status != 'noData':
        if _nodata_mem['step']:
            _note_output_action('유튜브가 영상을 받기 시작했어요')
        _nodata_mem['step'] = 9                                   # 받았다 — 이 방송은 더 안 본다
        _set_nodata_state(at=stamp, status=status, note='', done=True)
        return status
    _nodata_mem['seen'] += 1
    _set_nodata_state(at=stamp, status=status)
    if _nodata_mem['seen'] < 2:                                   # 한 번은 넘긴다(받기 직전일 수 있다)
        _nodata_mem['next_at'] = now_t + NODATA_RETRY_S
        return status
    step = _nodata_mem['step']
    if step == 0:
        _nodata_mem['step'] = 1
        _set_nodata_state(step=1, note='유튜브가 영상을 못 받아 송출을 다시 켜는 중')
        _note_output_action("유튜브가 영상을 못 받고 있어요(noData · 방송이 '예정' 에 머문다) → 송출 껐다 다시 켜기")
        res = _restream_same('유튜브 noData')
        _note_output_action(res['text'])
    elif step == 1:
        _nodata_mem['step'] = 2
        _set_nodata_state(step=2, note='유튜브가 영상을 못 받아 OBS 를 다시 켜는 중')
        _note_output_action('송출을 다시 켜도 유튜브가 영상을 못 받아요 → OBS 다시 켜기')
        res = _restart_obs_and_resume('유튜브 noData')
        _note_output_action(res['text'])
    else:
        _nodata_mem['step'] = 9
        note = "유튜브가 영상을 못 받고 있어요 — 방송 PC 의 OBS 를 봐 주세요(방송이 '예정' 에 머물러요)"
        _set_nodata_state(step=3, note=note, done=True)
        _set_output_state(bad=True, note=note)
        logger.error(f"❌ {note}")
        return status
    _nodata_mem.update({'seen': 0, 'next_at': time.time() + NODATA_AFTER_FIX_S})
    return status


def _nodata_loop():
    while True:
        try:
            _nodata_tick()
        except Exception as e:
            logger.warning(f"유튜브 수신 확인 오류(무시): {e}")
        time.sleep(5)


def _output_loop():
    while True:
        try:
            _output_tick()
        except Exception as e:
            logger.warning(f"송출 영상 검사 오류(무시): {e}")
        time.sleep(15)


def _output_view():
    with state_lock:
        v = dict(output_state)
    v['covers_stream'] = _output_covers_stream()
    v['restart'] = getattr(obs, 'obs_restart_status', None)
    return v


def _health_tick(now_t: float = None):
    """감시 한 바퀴 — 테스트가 직접 부른다. 방송 중이 아니면 기억을 비운다."""
    now_t = time.time() if now_t is None else now_t
    with state_lock:
        active = stream_state['active']
        bid = stream_state.get('broadcast_id')
    if not active or not bid:
        with state_lock:
            health_state.update({'broadcast_id': None, 'health': None, 'reconnects': 0, 'since': None})
        _health_mem.update({'prev': None, 'prev_t': 0.0, 'cams': {}, 'yt': None, 'tick': 0, 'issue': '', 'reconnecting': False,
                            'frame': None})
        return None
    if health_state.get('broadcast_id') != bid:
        with state_lock:
            health_state.update({'broadcast_id': bid, 'health': None, 'reconnects': 0, 'since': None})
        _health_mem.update({'prev': None, 'prev_t': 0.0, 'cams': {}, 'yt': None, 'tick': 0, 'issue': '', 'reconnecting': False,
                            'frame': None})
    snap = obs.stream_stats()
    if snap is None:
        return None
    _health_mem['tick'] += 1
    if _health_mem['tick'] % YOUTUBE_HEALTH_EVERY == 1:
        yt = youtube.stream_health(bid)
        if yt is not None:
            before = stream_health.youtube_issue_lines(_health_mem['yt'])
            after = stream_health.youtube_issue_lines(yt)
            if after != before or (yt.get('status') != (_health_mem['yt'] or {}).get('status')):
                logger.info(f"📺 YouTube 스트림 건강: {yt.get('status') or '?'}"
                            + (f" — {' · '.join(after)}" if after else ''))
            _health_mem['yt'] = yt
    elapsed = (now_t - _health_mem['prev_t']) if _health_mem['prev_t'] else HEALTH_TICK_S
    prev = _health_mem['prev'] if _health_mem['prev'] is not None else snap
    # OBS 인코딩 설정에서 고칠 칸(2026-10-24) — '인코딩 밀림'·'화면 밀림' 문장 끝에 붙는다. 5분에 한 번 다시 읽는다.
    refresh = getattr(obs, 'refresh_encoding', None)
    advice = (refresh() or {}).get('advice') if callable(refresh) else None
    # 방송 화면 검사(2026-11-01 — 초록 화면) — 20초마다. 사이 바퀴는 지난 결과를 그대로 쓴다.
    if _health_mem['tick'] % stream_health.FRAME_CHECK_EVERY == 1 or _health_mem['frame'] is None:
        try:
            _health_mem['frame'] = _frame_check()
        except Exception as e:
            logger.warning(f"화면 검사 오류(무시): {e}")
    with state_lock:
        out = {'bad': bool(output_state.get('bad')), 'note': output_state.get('note') or ''}
    h = stream_health.evaluate(prev, snap, elapsed_s=elapsed, camera_bad_ticks=_health_mem['cams'],
                               youtube=_health_mem['yt'], advice=advice, frame=_health_mem['frame'], output=out)
    _health_mem['prev'] = snap
    _health_mem['prev_t'] = now_t
    if h['reconnecting'] and not _health_mem['reconnecting']:
        with state_lock:
            health_state['reconnects'] = int(health_state.get('reconnects') or 0) + 1
    _health_mem['reconnecting'] = h['reconnecting']
    line = stream_health.transition_line(_health_mem['issue'], h['issue'], h)
    if line:
        (logger.warning if h['issue'] else logger.info)(line)
    since = None
    if h['issue']:
        since = health_state.get('since') if _health_mem['issue'] == h['issue'] else datetime.now(timezone.utc).isoformat()
    _health_mem['issue'] = h['issue']
    with state_lock:
        if stream_state.get('broadcast_id') == bid:
            health_state.update({'broadcast_id': bid, 'health': h, 'since': since})
    return h


def _health_loop():
    while True:
        try:
            _health_tick()
        except Exception as e:
            logger.warning(f"방송 상태 감시 오류(무시): {e}")
        time.sleep(HEALTH_TICK_S)


# ── 리플레이 버퍼 상시 유지 (라이브 아니어도 하이라이트 가능) ──────────
# OBS가 켜져 있으면 리플레이 버퍼를 항상 돌려서, 방송 중이 아니어도 언제든
# 직전 60/90초를 클립으로 저장할 수 있게 한다. 20초마다 상태 확인·필요 시 재시작.
_buffer_ready = False


def _buffer_keepalive():
    global _buffer_ready
    while True:
        try:
            active = obs.ensure_replay_buffer()
        except Exception:
            active = False
        if active and not _buffer_ready:
            logger.info("🎬 리플레이 버퍼 준비됨 — 라이브 아니어도 하이라이트 저장 가능")
        elif not active and _buffer_ready:
            logger.info("리플레이 버퍼 꺼짐 (OBS 실행/리플레이 버퍼 활성화 확인 필요)")
        _buffer_ready = active
        time.sleep(20)


# ── 리그명 축약 ────────────────────────────────────────────────
def shorten_league(league: str) -> str:
    """PS iLeague 26S2 → PSiL 26S2, 기타는 그대로."""
    import re
    m = re.match(r'PS\s*i[\-\s]?League\s*(.*)', league, re.IGNORECASE)
    if m:
        return f"PSiL {m.group(1).strip()}"
    return league


# ── 제목/설명 자동 생성 ────────────────────────────────────────
def build_title_and_desc(team_a: list, team_b: list, league: str,
                          category: str = '', match_number: int = 0) -> tuple:
    # 리그 축약명: PS iLeague 26S2 → PSiL 26S2
    league_short = shorten_league(league) if league else 'PSiL'
    # 카테고리: 리그 부문 이름(Bronze·Silver·Gold+ …) | Bridge — ps_court 가 정해 넘긴다(옛 부문만 B&S·G&P), 없으면 폴백
    cat = category if category else 'Bridge'
    match_str = f"Match{match_number}" if match_number else 'Match'

    a_str = ' / '.join(team_a)
    b_str = ' / '.join(team_b)
    date_str = datetime.now().strftime('%Y.%m.%d')

    # 제목: PSiL 26S2 [B&S] Match3
    title = f"{league_short} [{cat}] {match_str}"

    description = (
        f"🏆 PS Padel Society i-League 경기 생중계\n\n"
        f"📅 {date_str}\n"
        f"🎯 카테고리: {cat}\n"
        f"🔢 {match_str}\n"
        f"🟢 Team A: {a_str}\n"
        f"🟡 Team B: {b_str}\n\n"
        f"#빠델 #파델 #빠델소사이어티 #빠소 #빠델리그 #빠소리그 #PSL"
    )
    return title, description


# ── 자유 라이브 (리그 매치가 아닌 방송) ─────────────────────────
# 리그가 없는 달에도 코트에 온 사람들이 태블릿에서 바로 방송을 켜고 🎬 하이라이트를
# 남길 수 있게 한다. 리그 방송과 다른 점 하나: **끝나는 시각을 시작할 때 정한다.**
# 리그 방송은 점수를 저장하면 끝나지만 자유 라이브엔 그런 신호가 없어서, 시간을 안
# 정해 두면 사람들이 집에 간 뒤에도 빈 코트가 몇 시간씩 방송된다(유튜브에 그대로 남는다).
FREE_LIVE_MINUTES = (90, 120, 180)

_thumb_seq = itertools.count(1)   # 썸네일 임시 파일 순번(push_thumbnail)


def push_thumbnail(broadcast_id: str, roster_a: list, roster_b: list, *,
                   league='', category='', match_number=0, free=False):
    """
    그 경기 선수 얼굴이 들어간 썸네일을 만들어 YouTube 에 올린다.

    ⚠️ **방송을 시작한 뒤 별도 스레드에서** 부른다. 사진 네 장을 받아 합성하는 데
       1~3초가 걸리는데, 그걸 /start-stream 응답 경로에 넣으면 태블릿의 '스트리밍
       시작' 버튼이 그만큼 더 멈춰 있는다. 썸네일은 나중에 붙어도 아무 문제가 없다
       (방송 목록에 뜰 때쯤이면 이미 올라가 있다).
    ⚠️ 여기서 나는 어떤 예외도 방송에 영향을 주면 안 된다 — thumbnail.build 와
       youtube.set_thumbnail 둘 다 스스로 삼키지만, 스레드 최상단이라 한 번 더 감싼다.
    """
    # ⚠️ 2026-10-05: 자유 라이브(free)는 리그 경기가 아니다 — 예전엔 리그가 비면 'PS i-League'
    #    머리·'i-LEAGUE' 꼬리를 붙여서 'Padel Society 라이브' 방송이 리그 경기처럼 보였고,
    #    이름을 안 적으면(선택이다) 금색 VS 한 줄뿐인 빈 카드가 올라갔다. 이름이 없으면
    #    올리지 않는다 — 유튜브가 방송 화면에서 알아서 뽑는다.
    if free and not roster_a and not roster_b:
        logger.info("🖼️ 자유 라이브 · 이름 없음 — 썸네일은 유튜브 자동 프레임으로 둔다")
        return
    try:
        # ⚠️ 파일 이름에 방송 id 를 넣는다. 고정 이름을 쓰면 방송이 이어서 시작될 때
        #    (자유 라이브 → 리그 경기 넘겨받기) 앞 스레드가 아직 쓰는 파일을 뒤 스레드가
        #    덮어써서, 엉뚱한 경기 얼굴이 올라간다.
        safe = ''.join(ch for ch in str(broadcast_id) if ch.isalnum() or ch in '-_')[:40]
        # 같은 방송의 썸네일을 두 스레드가 만들 수 있다(첫 썸네일이 아직 도는데 같은 경기를 이어 쓰며 팀이 바뀜) —
        # 파일 이름에 순번을 붙여 서로 덮어쓰지 않게 한다.
        safe = f"{safe or 'live'}_{next(_thumb_seq)}"
        path = thumbnail.build(
            roster_a, roster_b,
            league=shorten_league(league) if league else ('' if free else 'PS i-League'),
            category=category, match_number=match_number,
            footer=thumbnail.FOOTER_FREE if free else thumbnail.FOOTER_LEAGUE,
            date_str=datetime.now().strftime('%Y.%m.%d'),
            out_path=os.path.join(tempfile.gettempdir(), f'ps_thumb_{safe}.jpg'),
        )
        if path:
            youtube.set_thumbnail(broadcast_id, path)
    except Exception as e:
        logger.warning(f"⚠️ 썸네일 처리 중 오류 (방송에는 영향 없음): {e}")


def merge_roster(names: list, roster: list) -> list:
    """
    이름 배열과 (선택) 선수 상세를 합친다.

    태블릿은 예전부터 `teamA: ["김하경", "이준우"]` 만 보냈다. 썸네일에 사진을 넣으려면
    사진 URL 과 등급이 더 필요한데, **이름 배열을 없애지 않고 `rosterA` 를 옆에 추가**했다 —
    구버전 태블릿(그리고 자유 라이브)은 이름만 보내고, 그때는 이름만으로 그린다.
    """
    detail = thumbnail.normalize(roster)
    if detail:
        return detail
    return thumbnail.normalize(names)


def build_free_title_and_desc(team_a: list, team_b: list, minutes: int) -> tuple:
    date_str = datetime.now().strftime('%Y.%m.%d %H:%M')
    names = ''
    if team_a or team_b:
        a_str = ' / '.join(team_a) if team_a else '팀 A'
        b_str = ' / '.join(team_b) if team_b else '팀 B'
        names = f" · {a_str} vs {b_str}"
    title = f"Padel Society 라이브 {date_str}{names}"
    description = (
        f"🎾 Padel Society 코트 라이브\n\n"
        f"📅 {date_str}\n"
        f"⏱ {minutes}분\n"
        + (f"🟢 Team A: {' / '.join(team_a)}\n" if team_a else '')
        + (f"🟡 Team B: {' / '.join(team_b)}\n" if team_b else '')
        + "\n#빠델 #파델 #빠델소사이어티 #빠소 #PSL"
    )
    return title, description


# ════════════════════════════════════════════════════════════════
# API 엔드포인트
# ════════════════════════════════════════════════════════════════

@app.route('/health', methods=['GET'])
def health():
    """서버 상태 확인 — ps_court.html이 서버를 감지하는 데 사용"""
    with state_lock:
        st = dict(stream_state)
    return jsonify({
        'ok': True,
        'streaming': st['active'],
        'watch_url': st.get('watch_url'),
        # 리플레이 버퍼가 대기 중이면 라이브 아니어도 하이라이트 버튼 노출
        'buffer_ready': _buffer_ready,
        # 태블릿이 다시 켜져도 진행 중인 라이브(무슨 모드·언제 끝나나)를 그대로 그릴 수 있게
        'mode': st.get('mode') or '',
        'title': st.get('title') or '',
        'started_at': st.get('started_at'),
        'ends_at': st.get('ends_at'),
        'duration_minutes': st.get('duration_minutes') or 0,
        'team_a': st.get('team_a') or [],
        'team_b': st.get('team_b') or [],
        # 어느 리그 매치의 방송인가 — 태블릿 상태 줄이 'LIVE · Match 39' 로 그린다(엉뚱한 경기 제목이면 보인다).
        'match_id': st.get('match_id') or '',
        'match_number': st.get('match_number') or 0,
        # 소리 싱크(OBS 연결 때마다 넣는 값) — 원격에서 맞춰졌는지 본다. config 에 없으면 target_ms: null.
        'audio_sync': getattr(obs, 'audio_sync_status', None),
        'camera_refresh': getattr(obs, 'camera_refresh_status', None),
        # 방송 상태(2026-10-21) — 방송 중일 때만. issue/short/text 가 비면 정상. 태블릿 상태 줄이 short 를 그린다.
        'stream_health': _health_view() if st['active'] else None,
        # 끊김을 줄이는 설정이 실제로 먹었나 — 지연(normal 이 기본) · OBS 자동 재연결.
        'broadcast_settings': {
            'latency': getattr(youtube, 'latency', ''),
            'auto_stop': False,
            'reconnect': getattr(obs, 'reconnect_status', None),
        },
        # OBS 인코딩 설정과 고칠 칸(2026-10-24 — 인코딩 밀림의 원인). advice: [] = 권장값 · null = 아직 못 읽음.
        'obs_encoding': _encoding_view(),
        # 마지막 OBS 송출 끄기(2026-10-25) — stopped · forced(강제 종료를 보냈나) · reconnecting · seconds. 로그 줄은 서버 로그에만.
        'obs_stop': _stop_view(),
        # 방송 화면 검사(2026-11-01 — 초록 화면) — 카메라마다 마지막 판정(label 이 비면 정상) · 서버가 마지막으로 한 일.
        'camera_frame': _frame_view(),
        # 송출 영상 검사(2026-11-11 — 유튜브 초록 화면) — 리플레이 버퍼로 본 인코더 출력 · covers_stream false 면 검사 안 함.
        'output_check': _output_view(),
        # 유튜브가 영상을 받는지(2026-11-12 — '예정' 에서 안 넘어감) — step 1 송출 다시 · 2 OBS 다시 · 3 사람이 봐야 함.
        'nodata_guard': dict(nodata_state),
    })


def _frame_view():
    with state_lock:
        v = dict(camera_frame_state)
    v['sources'] = [dict(x) for x in (v.get('sources') or [])]
    return v


def _stop_view():
    rep = getattr(obs, 'stop_report', None)
    if not isinstance(rep, dict):
        return None
    return {k: rep.get(k) for k in ('at', 'reached', 'stopped', 'forced', 'reconnecting', 'seconds')}


def _encoding_view():
    st = getattr(obs, 'encoding_status', None) or {}
    if not st:
        return None
    return {
        'summary': st.get('summary') or '',
        'advice': [{'key': a.get('key'), 'text': a.get('text')} for a in (st.get('advice') or [])]
        if st.get('advice') is not None else None,
        'config': st.get('config') or {},
        'error': st.get('error') or '',
    }


def _health_view():
    with state_lock:
        h = dict(health_state.get('health') or {})
        reconnects = int(health_state.get('reconnects') or 0)
        since = health_state.get('since')
    if not h:
        return {'issue': '', 'short': '', 'text': '', 'reconnects': reconnects, 'since': None}
    h['reconnects'] = reconnects
    h['since'] = since
    return h


def _same_match(cur: dict, match_id: str, league: str, match_number: int,
                team_a: list, team_b: list) -> bool:
    """지금 방송(cur)이 이 리그 매치의 방송인가.

    태블릿(c14~)은 매치 id 를 보낸다 — 둘 다 있으면 그것만 본다. 옛 태블릿(캐시된 c13 이하)은 id 가
    없으니 리그 + 번호로, 번호도 없으면 리그 + 네 사람으로 본다(팀을 다시 뽑아도 같은 네 명이다)."""
    cur_id = str(cur.get('match_id') or '')
    if match_id and cur_id:
        return match_id == cur_id
    if (cur.get('league') or '') != (league or ''):
        return False
    cur_no = int(cur.get('match_number') or 0)
    if match_number > 0 and cur_no > 0:
        return match_number == cur_no
    people = sorted(list(cur.get('team_a') or []) + list(cur.get('team_b') or []))
    return bool(people) and people == sorted(list(team_a) + list(team_b))


def _ensure_obs_idle():
    """새 방송 키를 넣기 **전에** OBS 가 아무것도 내보내지 않게 한다. 내보내고 있으면 끄고, 못 끄면 던진다.

    ⚠️ 2026-10-08 (26S3 — Match39 제목·썸네일 방송에 Match 41 이 나갔다): 서버는 '방송 없음' 이라
       믿는데 OBS 가 지난 방송 키로 계속 내보내는 때가 있었다(끄기 직전 연결이 끊겨 OBS 를 못 껐거나,
       끄기와 켜기가 겹쳐 상태가 지워졌다). 그때 켜기는 새 키를 넣고 '이미 스트리밍 중' 에 붙어 성공이라
       답했다 — 새 키는 송출을 다시 시작해야 먹으므로 **영상은 계속 지난 방송으로** 갔다.
       유튜브 방송을 만들기 전에 부른다 — 여기서 실패하면 빈 방송이 남지 않는다."""
    if not obs.is_streaming():
        return
    logger.warning("⚠️ 서버엔 방송이 없는데 OBS 가 송출 중 — 지난 방송 키일 수 있어 먼저 끕니다")
    if not obs.stop_stream():
        raise RuntimeError(
            "OBS 가 이전 방송을 계속 내보내고 있어 새 방송을 열지 못했어요. " + _obs_stop_hint()
        )


def _obs_stop_hint() -> str:
    """OBS 를 못 껐을 때 사람이 할 일(2026-10-25 — '종료 중' 에서 멈춤). obs.stop_report 를 읽는다(옛 컨트롤러면 예전 문장)."""
    rep = getattr(obs, 'stop_report', None) or {}
    if rep.get('reached') is False:
        return 'OBS 가 켜져 있는지 확인해 주세요(방송 PC).'
    if rep.get('forced'):
        return "OBS 가 '종료 중' 에서 멈췄어요 — 방송 PC 에서 OBS 를 껐다 켜 주세요."
    return 'OBS 에서 [방송 중단] 을 누른 뒤 다시 시작해 주세요.'


def _refresh_live_meta(broadcast_id: str, title: str, description: str, *, retitle: bool,
                       rethumb: bool, roster_a: list, roster_b: list, league: str,
                       category: str, match_number: int):
    """같은 매치를 이어 쓰는데 제목(번호·부문)이나 팀이 바뀌었으면 그 방송의 제목·설명·썸네일을 고친다.
    뒤에서 돈다 — 태블릿은 기다리지 않는다. 실패해도 방송은 그대로다."""
    def run():
        try:
            if retitle:
                youtube.update_snippet(broadcast_id, title, description)
            if rethumb:
                push_thumbnail(broadcast_id, roster_a, roster_b, league=league,
                               category=category, match_number=match_number)
        except Exception as e:
            logger.warning(f"⚠️ 이어 쓰는 방송 정보 갱신 실패(방송은 그대로): {e}")
    threading.Thread(target=run, daemon=True, name='live-meta').start()


@app.route('/start-stream', methods=['POST'])
@_require_operator
def start_stream():
    """
    스트리밍 시작
    Body(리그):     { "teamA": [...], "teamB": [...], "league": "...", "category": "...", "matchNumber": n,
                      "matchId": "..."(c14~) }
    Body(자유 라이브): { "mode": "free", "durationMinutes": 90|120|180, "teamA": [...](선택), "teamB": [...](선택) }

    리그 방송 중에 리그 경기가 또 오면(2026-10-08):
      · **같은 매치**(태블릿을 다시 열어 이어서 하기 · 두 번 누름) → 그 방송을 그대로 쓴다(reused).
        팀·번호가 바뀌었으면 제목·설명·썸네일만 고친다.
      · **다른 매치** → 지난 방송을 끝내고 새 방송을 연다. 예전엔 409 로 거절해서, 지난 경기 방송이
        그대로 남아 다음 경기가 지난 경기 제목·썸네일로 나갔다.
    """
    data = request.get_json(silent=True) or {}
    mode = 'free' if (data.get('mode') == 'free') else 'league'
    team_a = [str(x).strip() for x in (data.get('teamA') or []) if str(x).strip()]
    team_b = [str(x).strip() for x in (data.get('teamB') or []) if str(x).strip()]
    league = data.get('league', '')
    category = data.get('category', '')
    match_number = int(data.get('matchNumber', 0) or 0)
    match_id = str(data.get('matchId') or '').strip()[:64] if mode == 'league' else ''
    minutes = int(data.get('durationMinutes', 0) or 0)
    # 썸네일용 선수 상세 — 있으면 사진·등급까지 그린다. 없으면 이름만 (구버전 태블릿).
    roster_a = merge_roster(team_a, data.get('rosterA'))
    roster_b = merge_roster(team_b, data.get('rosterB'))

    # 입력 검증은 **잠그기 전에** — 잘못된 요청이 자리를 차지했다 롤백하는 창을 없앤다.
    if mode == 'league' and (not team_a or not team_b):
        return jsonify({'success': False, 'error': '팀 정보가 없어요'}), 400
    if mode == 'free' and minutes not in FREE_LIVE_MINUTES:
        return jsonify({'success': False, 'error': f'방송 시간은 {"/".join(str(m) for m in FREE_LIVE_MINUTES)}분 중에서 골라주세요'}), 400

    with stream_op_lock:
        return _start_stream_locked(mode, team_a, team_b, league, category, match_number,
                                    match_id, minutes, roster_a, roster_b)


def _start_stream_locked(mode, team_a, team_b, league, category, match_number,
                         match_id, minutes, roster_a, roster_b):
    """/start-stream 의 본체 — stream_op_lock 을 쥔 채로 돈다(끄기·다른 켜기와 겹치지 않는다)."""
    takeover = ''
    with state_lock:
        cur = dict(stream_state)
    if cur['active']:
        # 정말 송출 중인지 OBS에 물어본다. OBS가 재시작되거나 송출이 끊겨도 이
        # 플래그는 True로 남고, 그 뒤 모든 방송 요청이 409로 막힌다 — 실제로
        # 3시간 동안 그랬고, 사람이 /stop-stream을 눌러야만 풀렸다.
        # 송출 중이 아니면 죽은 상태로 보고 정리한 뒤 새로 시작한다.
        # (OBS 질문은 state_lock 밖에서 — 켜기·끄기는 stream_op_lock 으로 이미 한 줄이다.)
        if obs.is_streaming():
            cur_mode = cur.get('mode') or 'league'
            if cur_mode == 'free' and mode == 'league':
                # 리그 경기가 우선이다 — 자유 라이브가 켜져 있으면 끄고 리그 방송을 연다.
                # (안 그러면 코트에 온 리그 4명이 '이미 스트리밍 중' 에 막혀 점수판만 켠다.)
                takeover = '리그 경기로 전환'
            elif cur_mode == 'free':
                with state_lock:
                    left = _minutes_left_locked()
                return jsonify({'success': False,
                                'error': f'자유 라이브가 진행 중이에요 ({left}분 남음) — 먼저 종료해주세요'}), 409
            elif mode == 'free':
                return jsonify({'success': False, 'error': '리그 경기 방송 중이에요'}), 409
            elif _same_match(cur, match_id, league, match_number, team_a, team_b):
                return _reuse_league_stream(cur, team_a, team_b, league, category, match_number,
                                            match_id, roster_a, roster_b)
            else:
                takeover = '다음 경기로 전환'
                logger.info(f"🔁 다른 리그 매치가 시작됨 — 진행 중이던 방송을 끝내고 새로 엽니다 "
                            f"({cur.get('title') or cur.get('broadcast_id')} → Match{match_number or '?'})")
        else:
            stale = cur.get('broadcast_id') or ''
            logger.warning(f"⚠️ 이전 스트리밍 상태가 남아 있는데 OBS는 송출 중이 아님 — 정리하고 새로 시작 ({cur.get('watch_url') or stale})")
            with state_lock:
                _clear_state_locked()
            # 주인 잃은 유튜브 방송은 **여기서(잠금 안에서) 바로** 닫는다 — 뒤 스레드로 돌리면 바로 다음 방송 만들기와
            # 같은 유튜브 연결을 동시에 써서 만들기가 실패했다(2026-10-08 검토). 실패하면 뒤에서 다시 해 본다
            # (2026-10-21 부터 자동 종료가 없다 — _end_later 가 보험).
            if stale and _end_broadcast_now(stale):
                logger.info(f"↩️ 남아 있던 방송 정리: {stale}")

    if takeover:
        if mode == 'league' and (cur.get('mode') or 'league') == 'free':
            logger.info("🔁 리그 경기 시작 — 진행 중이던 자유 라이브를 먼저 끕니다")
        _stop_stream_impl(takeover)

    with state_lock:
        stream_state['active'] = True  # 먼저 잠금 — /health 가 '켜는 중' 을 방송 중으로 본다

    broadcast_id = None   # 실패 시 정리해야 하므로 try 밖에서 잡아둔다
    try:
        if mode == 'free':
            title, description = build_free_title_and_desc(team_a, team_b, minutes)
        else:
            title, description = build_title_and_desc(team_a, team_b, league, category, match_number)
        logger.info(f"🎬 스트리밍 시작({mode}): {title}")
        # 0. OBS 가 지난 방송 키로 내보내는 중이면 먼저 끈다 — 못 끄면 여기서 멈춘다(빈 방송을 만들기 전).
        _ensure_obs_idle()

        # 1. YouTube 방송 생성
        broadcast_id, rtmp_url, stream_key = youtube.create_broadcast_and_stream(title, description)
        watch_url = YouTubeAPI.get_watch_url(broadcast_id)
        _ingest.update({'broadcast_id': broadcast_id, 'rtmp': rtmp_url, 'key': stream_key})

        # 2. OBS 설정 + 스트리밍 시작
        obs.connect()
        obs.set_stream_settings(rtmp_url, stream_key)
        # 카메라 소스를 다시 시작해 소리 밀림을 매 경기 처음으로 되돌린다(2026-11-07 · 실패해도 방송은 켠다).
        obs.refresh_cameras()
        obs.start_stream()

        # OBS가 진짜로 송출을 시작했는지 확인한다. 여기를 안 보면 OBS가 대화상자를
        # 띄운 채 가만히 있어도 '시작됨'으로 응답해, 태블릿엔 라이브라고 뜨는데
        # YouTube로는 아무것도 안 나가는 상태가 된다.
        if not obs.wait_until_streaming():
            raise RuntimeError(
                "OBS가 송출을 시작하지 않았어요. OBS 화면에 대화상자가 떠 있는지 확인해주세요. "
                "'설정된 방송 없음'이면 OBS 프로필에 YouTube 계정 연동이 남아 있는 겁니다 — "
                "관리자 PowerShell에서 setup\\fix_obs_broadcast.ps1 을 한 번 실행하면 해결됩니다. "
                "(설정 화면에서 서비스만 바꾸는 걸로는 로그인 정보가 안 지워져 재시작하면 되살아납니다.)"
            )

        obs.start_replay_buffer()  # 하이라이트 대기 — OBS 설정에서 리플레이 버퍼 활성화 필요(60~90초)

        started = datetime.now(timezone.utc)
        ends = (started + timedelta(minutes=minutes)) if mode == 'free' else None
        with state_lock:
            stream_state.update({
                'active': True,
                'mode': mode,
                'broadcast_id': broadcast_id,
                'watch_url': watch_url,
                'started_at': started.isoformat(),
                'ends_at': ends.isoformat() if ends else None,
                'duration_minutes': minutes if mode == 'free' else 0,
                'team_a': team_a,
                'team_b': team_b,
                'league': league,
                'title': title,
                'match_id': match_id,
                'match_number': match_number if mode == 'league' else 0,
                'category': category if mode == 'league' else '',
                'error': None,
            })
            recorded = dict(stream_state)
        _live_file_set_current(recorded)   # 서버가 죽어도 이 방송을 다음에 정리·이어받을 수 있게
        if ends:
            logger.info(f"⏱ 자유 라이브 {minutes}분 — {ends.astimezone().strftime('%H:%M')} 에 자동 종료")

        logger.info(f"✅ 스트리밍 시작됨! 시청: {watch_url}")

        # 썸네일은 **방송이 뜬 뒤에** 뒤따라 붙인다 — 응답을 붙잡지 않는다.
        threading.Thread(
            target=push_thumbnail,
            args=(broadcast_id, roster_a, roster_b),
            kwargs={'league': league, 'category': category, 'match_number': match_number,
                    'free': mode == 'free'},
            daemon=True,
            name='thumbnail',
        ).start()

        with state_lock:
            ends_at = stream_state.get('ends_at')
        return jsonify({
            'success': True,
            'mode': mode,
            'title': title,
            'watch_url': watch_url,
            'broadcast_id': broadcast_id,
            'ends_at': ends_at,
            'reused': False,
            'took_over': bool(takeover),
        })

    except Exception as e:
        logger.error(f"❌ 스트리밍 시작 실패: {e}")

        # 방송을 만든 뒤에 실패했으면 그 방송을 닫는다. 안 그러면 YouTube에 아무것도
        # 안 나가는 빈 방송이 열린 채 남는다(실제로 3시간 떠 있었다).
        # ⚠️ OBS 를 먼저 끄고 방송을 닫는다(2026-10-25 — 끄기 절과 같은 순서).
        try:
            obs.stop_stream()
        except Exception:
            pass
        if broadcast_id:
            if _end_broadcast_now(broadcast_id):
                logger.info(f"↩️ 실패한 방송 정리됨 ({broadcast_id})")

        with state_lock:
            _clear_state_locked()   # 실패 시 롤백 — 반쯤 채운 칸(제목·매치)을 남기지 않는다
            stream_state['error'] = str(e)
        return jsonify({'success': False, 'error': str(e)}), 500


def _reuse_league_stream(cur, team_a, team_b, league, category, match_number, match_id,
                         roster_a, roster_b):
    """같은 리그 매치의 방송이 이미 나가는 중 — 새로 열지 않고 그대로 쓴다(stream_op_lock 안에서).

    태블릿을 다시 열어 [이어서 하기] 를 누르거나 켜기를 두 번 누르면 여기로 온다. 예전엔 409
    '리그 경기 방송 중이에요' 로 오류 토스트가 떴다(방송은 멀쩡한데). 팀을 다시 뽑았거나 번호·부문이
    바뀌었으면 제목·설명·썸네일만 고친다 — 새 방송을 열면 한 경기가 영상 둘로 쪼개진다."""
    bid = cur.get('broadcast_id')
    title, description = build_title_and_desc(team_a, team_b, league, category, match_number)
    teams_changed = (list(cur.get('team_a') or []) != team_a) or (list(cur.get('team_b') or []) != team_b)
    title_changed = title != (cur.get('title') or '')
    with state_lock:
        stream_state.update({
            'team_a': team_a,
            'team_b': team_b,
            'league': league,
            'title': title,
            'match_id': match_id or cur.get('match_id') or '',
            'match_number': match_number or int(cur.get('match_number') or 0),
            'category': category,
        })
    if teams_changed or title_changed:
        logger.info(f"✏️ 같은 매치 방송을 이어 씀 — {'팀 ' if teams_changed else ''}{'제목 ' if title_changed else ''}갱신: {title}")
        _refresh_live_meta(bid, title, description, retitle=True,
                           rethumb=teams_changed or title_changed,
                           roster_a=roster_a, roster_b=roster_b, league=league,
                           category=category, match_number=match_number)
    else:
        logger.info(f"▶️ 같은 매치 방송을 이어 씀: {title}")
    return jsonify({
        'success': True,
        'mode': 'league',
        'title': title,
        'watch_url': cur.get('watch_url'),
        'broadcast_id': bid,
        'ends_at': None,
        'reused': True,
        'took_over': False,
    })


def _minutes_left_locked() -> int:
    """자유 라이브 남은 분. 호출자가 state_lock을 잡고 있어야 한다. 없으면 0."""
    ends = stream_state.get('ends_at')
    if not ends:
        return 0
    try:
        left = datetime.fromisoformat(ends) - datetime.now(timezone.utc)
        return max(0, int(left.total_seconds() // 60))
    except Exception:
        return 0


def _stop_stream_impl(reason: str = '') -> list:
    """스트리밍 종료 — 사람이 누른 /stop-stream 과 시간 만료 워치독이 **같은 길**을 탄다.
    돌려주는 값은 일부 실패 목록(빈 리스트면 깨끗이 끝남). 스트리밍 중이 아니면 아무것도 안 한다.
    ⚠️ stream_op_lock 안에서 돈다(RLock — 켜기가 넘겨받으며 부를 때는 이미 쥐고 있다)."""
    with stream_op_lock:
        return _stop_stream_locked(reason)


def _stop_stream_locked(reason: str = '') -> list:
    with state_lock:
        if not stream_state['active']:
            return []
        broadcast_id = stream_state.get('broadcast_id')
        title = stream_state.get('title') or ''

    errors = []

    # OBS와 YouTube를 독립적으로 종료 (하나 실패해도 다른 쪽은 시도)
    # ⚠️ 리플레이 버퍼는 일부러 안 끈다 — 방송 종료 후에도 하이라이트 저장 가능하게
    #    (키프알라이브 스레드가 계속 켜둠). 버퍼는 OBS 종료 시에만 멈춤.
    # ⚠️ 못 껐으면(연결 실패 · 시간 안에 안 멈춤) 오류로 남긴다 — 다음 켜기가 _ensure_obs_idle 로
    #    다시 끄고, 그래도 안 되면 켜기를 멈춘다(지난 방송 키에 이어 붙지 않게).
    try:
        if obs.stop_stream() is False:
            logger.error("❌ OBS 송출을 끄지 못했어요 — 다음 방송을 열 때 다시 끕니다")
            errors.append(f"OBS: 송출을 끄지 못했어요 — {_obs_stop_hint()}")
    except Exception as e:
        logger.error(f"❌ OBS 종료 실패: {e}")
        errors.append(f"OBS: {e}")

    # 유튜브 방송 끝내기 — **OBS 를 끈 뒤에**(2026-10-25). 아직 보내는 중에 끝내면 YouTube 가 연결을 끊고 OBS 가
    # '종료 중' 인 채로 다시 붙으려 하다 멈춘다(다음 경기가 안 열렸다). obs.stop_stream 이 강제 종료까지 기다리고 돌아온다.
    # 못 끝냈으면 뒤에서 다시 해 본다(자동 종료를 껐다 · 2026-10-21). 태블릿엔 경고만.
    if not _end_broadcast_now(broadcast_id):
        errors.append("YouTube: 방송 종료가 늦어지고 있어요(서버가 다시 해 봐요)")

    # 상태 전체 초기화
    with state_lock:
        _clear_state_locked()

    why = f" ({reason})" if reason else ''
    if errors:
        logger.warning(f"⚠️ 스트리밍 종료 중 일부 오류{why}: {errors}")
    else:
        logger.info(f"⏹️  스트리밍 종료됨{why} — {title}")
    return errors


@app.route('/stop-stream', methods=['POST'])
@_require_operator
def stop_stream():
    """스트리밍 종료. Body(선택): { "void": true } — 끝까지 못 친 경기(태블릿 '매치 취소').
    영상은 지우지 않고 제목·설명에 [무효] 를 붙인다(매치는 태블릿이 지워 순위·PAR·전적에서 빠진다)."""
    data = request.get_json(silent=True) or {}
    void = data.get('void') is True
    # 태블릿(c14~)이 끝낸 매치의 id — 지금 방송이 **다른** 매치면 건드리지 않는다(아래).
    match_id = str(data.get('matchId') or '').strip()[:64]
    with stream_op_lock:
        with state_lock:
            if not stream_state['active']:
                # idle — 태블릿이 '방송이 없었다' 를 안다(켜기가 실패했는데 그 사이 /health 를 '방송 중' 으로 읽은
                # 태블릿이 [무효] 를 '직접 붙이라' 고 하지 않게 · '종료됨' 이라 하지 않게).
                return jsonify({'success': True, 'idle': True, 'message': '스트리밍 중이 아니에요'})
            bid = stream_state.get('broadcast_id')
            cur_mode = stream_state.get('mode') or 'league'
            cur_id = stream_state.get('match_id') or ''
        # ⚠️ 2026-10-08: 다음 매치가 방송을 넘겨받은 뒤 앞 매치를 정리하는 태블릿 동작(앞 매치 [매치 취소] ·
        #    뒤로 가기)이 **지금 경기 방송을 끄고 [무효] 까지** 붙이면 안 된다. 매치 id 를 함께 보냈고 지금
        #    방송이 그 매치가 아니면(다른 리그 매치 · 자유 라이브) 그대로 둔다. id 를 모르는 방송(옛 태블릿이
        #    켠 것)이면 예전처럼 끈다.
        if match_id and (cur_mode != 'league' or (cur_id and cur_id != match_id)):
            logger.info(f"⏭ 다른 매치({match_id}) 끝내기 요청 — 지금 방송({cur_id or cur_mode})은 그대로 둡니다")
            return jsonify({'success': True, 'skipped': True,
                            'message': '지금 방송은 다른 경기예요 — 그대로 둬요'})
        errors = _stop_stream_impl('태블릿에서 종료' + (' · 무효' if void else ''))
    out = {'success': True}
    if void and bid:
        _void_later(bid)
        out['voided'] = bid     # 태블릿이 이걸로 '새 서버인지' 안다 — 옛 서버는 void 를 모른다
    if errors:
        out['warnings'] = errors
    return jsonify(out)


_VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{6,20}$')


def _video_id_of(s: str) -> str:
    """'https://www.youtube.com/watch?v=ID' · 'https://youtu.be/ID' · 'ID' → ID. 모르면 ''."""
    s = str(s or '').strip()
    m = re.search(r'[?&]v=([A-Za-z0-9_-]+)', s) or re.search(r'youtu\.be/([A-Za-z0-9_-]+)', s)
    vid = m.group(1) if m else s
    return vid if _VIDEO_ID.match(vid) else ''


def _void_later(video_id: str):
    """[무효] 표시는 백그라운드 — 방금 끝낸 영상은 YouTube 가 잠깐 처리 중일 수 있어 몇 번 다시 해 본다.
    태블릿은 기다리지 않는다(매치 삭제가 이것 때문에 늦어지면 안 된다)."""
    def run():
        for i in range(3):
            if youtube.mark_void(video_id):
                return
            time.sleep(20 * (i + 1))
        logger.warning(f"⚠️ [무효] 표시 끝내 실패 — 유튜브 스튜디오에서 직접 ({video_id})")
    threading.Thread(target=run, daemon=True).start()


@app.route('/void-video', methods=['POST'])
@_require_operator
def void_video():
    """이미 끝난 방송의 영상에 [무효] — 방송을 먼저 끄고 나중에 '매치 취소' 를 누른 경우.
    Body: { "watchUrl": "https://www.youtube.com/watch?v=..." } (또는 "videoId")"""
    data = request.get_json(silent=True) or {}
    vid = _video_id_of(data.get('videoId') or data.get('watchUrl') or '')
    if not vid:
        return jsonify({'success': False, 'error': '영상 주소를 알아볼 수 없어요'}), 400
    with stream_op_lock:
        with state_lock:
            live_now = stream_state['active'] and stream_state.get('broadcast_id') == vid
        if live_now:
            # 아직 송출 중인 그 방송이면 끄면서 붙인다
            _stop_stream_impl('태블릿에서 종료 · 무효')
    _void_later(vid)
    return jsonify({'success': True, 'voided': vid})


def _auto_stop_once(now=None) -> bool:
    """자유 라이브가 만료됐으면 끈다. 껐으면 True. (워치독 한 바퀴 — 테스트가 직접 부른다)"""
    now = now or datetime.now(timezone.utc)
    with stream_op_lock:
        with state_lock:
            active = stream_state['active']
            ends = stream_state.get('ends_at')
        if not (active and ends):
            return False
        if datetime.fromisoformat(ends) > now:
            return False
        logger.info("⏱ 자유 라이브 시간 만료 — 자동 종료")
        _stop_stream_impl('시간 만료')
        return True


def _auto_stop_loop():
    """자유 라이브 시간 만료 워치독. 태블릿이 꺼지거나 페이지가 닫혀도 서버가 스스로 끈다.
    ⚠️ 이게 없으면 '3시간' 을 고른 방송이 3시간에 끝날 방법이 태블릿 타이머 하나뿐이다 —
       태블릿은 화면이 꺼지면 타이머도 멈춘다."""
    while True:
        try:
            _auto_stop_once()
        except Exception as e:
            logger.warning(f"자동 종료 워치독 오류(무시): {e}")
        time.sleep(10)


# ── 하이라이트 (리플레이 버퍼 클립) ───────────────────────────
# 테블릿의 🎬 버튼 → 버튼 누른 시점 직전 N초(OBS 리플레이 버퍼 길이, 권장 60~90초)를
# 파일로 저장하고 백그라운드로 Playus 백엔드에 업로드 → 회원들이 다운로드(인스타 등).
HIGHLIGHT_CFG = config.get('highlight', {})


def _ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return 'ffmpeg'  # imageio-ffmpeg 미설치면 PATH의 ffmpeg 시도


def _trim_clip(path, seconds):
    """클립의 '뒤쪽 seconds초'만 남긴 파일 생성 — ffmpeg 스트림카피(재인코딩 없음, 빠름).
    키프레임 경계에서 잘려 ±수 초 오차 있음. 실패하면 원본 경로 반환(원본 길이로 업로드).
    +faststart: 재생정보(moov)를 파일 앞으로 → 폰에서 받으면서 바로 재생(끊김 방지)."""
    try:
        import subprocess
        base, ext = os.path.splitext(path)
        out = f"{base}_{seconds}s{ext}"
        r = subprocess.run(
            [_ffmpeg_exe(), '-y', '-sseof', f'-{seconds}', '-i', path,
             '-c', 'copy', '-movflags', '+faststart', out],
            capture_output=True, timeout=90,
        )
        if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0:
            logger.info(f"✂️ {seconds}초 클립 생성: {os.path.basename(out)}")
            return out
        logger.warning(f"클립 트림 실패(코드 {r.returncode}) — 원본 길이로 업로드")
    except Exception as e:
        logger.warning(f"클립 트림 오류({e}) — 원본 길이로 업로드")
    return path


def _faststart_clip(path):
    """트림 없이 업로드할 때도 faststart 리먹스(스트림카피, 수 초).
    OBS 원본은 moov가 뒤/하이브리드라 폰에서 스트리밍 재생이 끊김 → 앞으로 옮겨준다.
    실패하면 원본 경로 반환(재생은 되지만 로딩이 느릴 수 있음)."""
    try:
        import subprocess
        base, ext = os.path.splitext(path)
        out = f"{base}_fs{ext}"
        r = subprocess.run(
            [_ffmpeg_exe(), '-y', '-i', path, '-c', 'copy', '-movflags', '+faststart', out],
            capture_output=True, timeout=90,
        )
        if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0:
            logger.info(f"⚡ faststart 처리됨: {os.path.basename(out)}")
            return out
        logger.warning(f"faststart 실패(코드 {r.returncode}) — 원본으로 업로드")
    except Exception as e:
        logger.warning(f"faststart 오류({e}) — 원본으로 업로드")
    return path


def _save_and_upload_highlight(meta, seconds=0, mode='seconds'):
    """리플레이 저장(느림, ~수 초) + 업로드를 백그라운드로 처리.
    save_replay_buffer가 파일 경로 폴링에 최대 ~5초 걸려서 요청 스레드를 막으면
    안 됨(헬스체크 타임아웃 → 라이브 표시 깜빡임). 그래서 통째로 스레드에서 실행.
    mode 'rally' = [직전 랠리](앱 하이라이트 + 인스타 자동) · 그 밖 = [60초](앱 하이라이트만)."""
    path = obs.save_replay_buffer()
    if not path:
        logger.error("하이라이트 저장 실패 — 리플레이 경로 없음")
        return
    if mode == 'rally':
        _upload_rally_highlight(path, meta)
    else:
        _upload_highlight(path, meta, seconds)


def _post_highlight(path, meta) -> str:
    """파일을 백엔드 하이라이트로 올린다 → 하이라이트 id(못 올리면 '')."""
    import requests
    api = (HIGHLIGHT_CFG.get('api_base') or 'https://api.padelsociety.co.kr').rstrip('/')
    key = HIGHLIGHT_CFG.get('upload_key') or ''
    if not key:
        logger.warning("하이라이트 업로드 생략 — config.highlight.upload_key 없음 (OBS PC에 파일만 저장됨)")
        return ''
    with open(path, 'rb') as f:
        r = requests.post(
            f"{api}/api/highlight/upload",
            headers={'x-upload-key': key},
            data=meta,
            files={'file': (os.path.basename(path), f, 'video/mp4')},
            timeout=180,
        )
    if not r.ok:
        logger.error(f"하이라이트 업로드 실패 {r.status_code}: {r.text[:200]}")
        return ''
    logger.info(f"☁️ 하이라이트 업로드 완료: {os.path.basename(path)}")
    try:
        return str(((r.json() or {}).get('data') or {}).get('id') or '')
    except Exception:
        return ''


def _upload_highlight(path, meta, seconds=0):
    """[60초](또는 옛 태블릿의 30·90초) — 뒤쪽 N초를 앱 하이라이트에만 올린다. 인스타 스토리는 만들지 않는다
    (2026-11-11 사용자 *"60초는 하이라이트에만 올라가는거고"* — 예전엔 모든 클립의 스토리 파일을 저절로 만들었다).
    태블릿 '📱 인스타 스토리' 로 고르면 그때 만든다(/highlight/<id>/story)."""
    try:
        # 요청 길이가 있으면 뒤쪽 N초만 잘라서 업로드 (버퍼=90초, 60초 버튼 대응)
        # 트림 안 하는 경우도 faststart 리먹스 — 폰 스트리밍 재생 끊김 방지.
        if seconds:
            path = _trim_clip(path, seconds)
        else:
            path = _faststart_clip(path)
        hid = _post_highlight(path, meta)
        if hid:
            _remember_highlight(hid, path, seconds, meta)
    except Exception as e:
        logger.error(f"하이라이트 업로드 오류: {e}")


def _upload_rally_highlight(path, meta):
    """[직전 랠리](2026-11-11 · 사용자 *"직전랠리는 인스타에 자동으로 올라가는거랑 앱 하이라이트에서 볼 수 있는거 둘다"*) —
    마지막 랠리 서브 3초 전 ~ 버튼 누른 순간(rally_cut · 60초 이하 · 재인코딩 없음)을 **앱 하이라이트로 올리고 같은 파일을
    인스타 스토리로 자동 게시**한다(백엔드 story-file autoPost — 인스타가 연결돼 있을 때만, 실패하면 관리자 알림). 자르기가 실패하면
    뒤쪽 60초를 하이라이트에만 올린다(인스타는 안 올린다 — 무엇이 올라가는지 모르는 클립을 자동으로 내보내지 않는다).
    config.highlight.auto_story: false 면 하이라이트만."""
    try:
        with _story_make_lock:
            out, win = _story_clip(path, 0)
    except Exception as e:
        logger.warning(f"직전 랠리 자르기 실패 — 뒤쪽 60초를 하이라이트에만 올려요: {e}")
        _upload_highlight(path, meta, 60)
        return
    try:
        hid = _post_highlight(out, meta)
        if not hid:
            return
        secs = int(max(0, (win.get('duration') or 0) - (win.get('start') or 0)))
        item = _remember_highlight(hid, out, secs, meta)
        if HIGHLIGHT_CFG.get('auto_story', True) is not False:
            with _story_lock:
                _story_jobs[hid] = {'state': 'making', 'error': '', 'url': ''}
            _upload_story_file(item['id'], out, win, auto_post=True)
    except Exception as e:
        logger.error(f"직전 랠리 하이라이트 오류: {e}")


@app.route('/highlight', methods=['POST'])
@_require_operator
def save_highlight():
    """리플레이 버퍼를 클립으로 저장(즉시 응답) + 백엔드 업로드(백그라운드).
    Body: { "mode": "rally" } — [직전 랠리](앱 하이라이트 + 인스타 스토리 자동) ·
          { "seconds": 60 } — [60초](뒤쪽 N초 · 앱 하이라이트만 · 옛 태블릿의 30·90초도 같은 길)."""
    data = request.get_json(silent=True) or {}
    _last_highlight_at[0] = time.time()   # 송출 영상 검사가 이 사이엔 리플레이를 저장하지 않는다
    mode = 'rally' if str(data.get('mode') or '') == 'rally' else 'seconds'
    try:
        seconds = int(data.get('seconds') or 0)
    except (TypeError, ValueError):
        seconds = 0
    if mode == 'rally':
        seconds = 0
    elif seconds and not (15 <= seconds <= 300):
        seconds = 0  # 이상값은 무시 — 버퍼 전체 길이로
    # 사전 확인 — 버퍼가 꺼져 있으면 켜기까지 시도(ensure). 그래도 안 되면 명확히 안내.
    # (버퍼가 이미 돌고 있으면 즉시 통과 — 오판으로 "꺼짐" 뜨는 걸 방지)
    if not obs.ensure_replay_buffer():
        return jsonify({
            'success': False,
            'error': 'OBS 리플레이 버퍼를 켤 수 없어요 — OBS가 실행 중인지, 설정 → 출력 → 리플레이 버퍼 활성화됐는지 확인해주세요',
        }), 400
    # ⚠️ **녹화 시각을 지금 찍는다.** 아래 저장(리플레이 버퍼 폴링 ~수 초) →
    #    ffmpeg 트림/리먹스(수십 초) → 업로드까지 시간이 걸려서, 서버가 받는 시각은
    #    실제 녹화보다 한참 뒤다. 23:59 에 누른 클립이 00:00 에 도착하면 목록에서
    #    **다음 날 클립**이 된다. 버튼을 누른 이 순간이 맞는 시각이다.
    recorded_at = datetime.now(timezone.utc).isoformat()
    with state_lock:
        meta = {
            'title': stream_state.get('title') or '하이라이트',
            'league': stream_state.get('league') or '',
            'teamA': ','.join(stream_state.get('team_a') or []),
            'teamB': ','.join(stream_state.get('team_b') or []),
            'watchUrl': stream_state.get('watch_url') or '',
            'recordedAt': recorded_at,
        }
    # 저장(폴링 ~수 초)+업로드는 통째로 백그라운드 — 요청은 즉시 반환(헬스체크 안 막힘)
    threading.Thread(target=_save_and_upload_highlight, args=(meta, seconds, mode), daemon=True).start()
    return jsonify({'success': True, 'seconds': seconds or None, 'mode': mode,
                    'instagram': mode == 'rally' and HIGHLIGHT_CFG.get('auto_story', True) is not False})


# ════════════════════════════════════════════════════════════════
# 🎬 → 인스타 스토리 (2026-11-01 · 미리보기 후 올리기)
# ════════════════════════════════════════════════════════════════
# 사용자: "하이라이트 저장 버튼 누르면 바로 인스타에 올리는 기능 … 인스타 스토리" → 센터 공식 계정 · 미리보기 후 올리기.
# 이 PC 가 하는 일은 **스토리용 파일 만들기**뿐이다: 클립이 서버에 올라가면 **바로 저절로**(태블릿을 기다리지 않는다) 마지막 랠리의
# 서브 3초 전부터 클립 끝(버튼을 누른 순간)까지 잘라(가로 그대로 — 인스타가 위아래를 검게 채운다 · 60초 이하) 백엔드
# (POST /api/highlight/:id/story-file)에 올린다. 서버가 슈퍼어드민에게 예약앱 알림을 보내고, **관리자가** 미리 보고 [올리기]를 한 번 누른다
# (사용자 *"관리자인 내가 한번 누르기를 해야할거같은데?"* — 태블릿은 준비·상태만)
# (2026-11-01 사용자 *"마지막 랠리 시작을 잡아주는건 좋을거같아 … 인스타 자동업데이트"* → '자동 준비 + 한 번 누르기').
# 랠리 찾기는 rally_cut.py(라켓 소리 · numpy — 없으면 마지막 57초). 세로 변환·공 따라가기는 시험해 보고 접었다(사용자 *"공 따라가는건 안되겠다"*).
# ⚠️ 재인코딩 없음(스트림 복사) — 몇 초 · 방송과 겹쳐도 부담이 없다. 한 번에 하나씩(_story_make_lock) 차례로 만든다.
# ⚠️ 스토리 영상은 **60초까지**. 복사로 자르면 시작이 키프레임에 맞춰 조금 앞당겨지므로 rally_cut 이 57초 안으로 잡고 -t 로 60초를 넘지 않게 막는다.
import rally_cut
STORY_MAX_S = 60          # 인스타 스토리 영상 한 장 최대 길이
RECENT_MAX = 10
_recent_highlights = []   # 최신 먼저 — {id, path, seconds, title, at}
_story_jobs = {}          # 하이라이트 id → {state: making|ready|failed, error, url, start, serve, why}
_story_lock = threading.Lock()
_story_make_lock = threading.Lock()


def _remember_highlight(hid, path, seconds, meta):
    """백엔드가 받아 준 클립을 기억한다(이 PC 의 파일 경로 · 태블릿 '최근 클립' 목록)."""
    item = {
        'id': hid, 'path': path, 'seconds': int(seconds or 0),
        'title': (meta or {}).get('title') or '하이라이트',
        'at': (meta or {}).get('recordedAt') or datetime.now(timezone.utc).isoformat(),
    }
    with _story_lock:
        _recent_highlights[:] = [x for x in _recent_highlights if x['id'] != hid]
        _recent_highlights.insert(0, item)
        del _recent_highlights[RECENT_MAX:]
    return item


def _clip_duration(path):
    """클립 길이(초) — ffmpeg -i 의 Duration 줄. 모르면 0."""
    import subprocess
    try:
        r = subprocess.run([_ffmpeg_exe(), '-hide_banner', '-i', path], capture_output=True, timeout=20)
        m = re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', (r.stderr or b'').decode('utf-8', 'ignore'))
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception:
        pass
    return 0.0


def _story_cmd(path, start, out):
    """스트림 복사(재인코딩 없음) — start 초부터 끝까지 · 늘 60초 이하 · faststart."""
    head = ['-ss', f'{float(start):.2f}'] if start and start > 0 else []
    return [_ffmpeg_exe(), '-y', *head, '-i', path, '-t', str(STORY_MAX_S - 0.1),
            '-c', 'copy', '-movflags', '+faststart', out]


def _story_clip(path, seconds):
    """스토리용 파일 → (경로, {start, serve, why}). 실패하면 RuntimeError(사람이 읽을 말)."""
    import subprocess
    dur = _clip_duration(path) or float(seconds or 0) or 60.0
    start, serve, why = rally_cut.find_window(_ffmpeg_exe(), path, dur)
    base, _ = os.path.splitext(path)
    out = f"{base}_story.mp4"
    r = subprocess.run(_story_cmd(path, start, out), capture_output=True, timeout=120)
    if r.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
        tail = (r.stderr or b'').decode('utf-8', 'ignore').strip().splitlines()[-1:] or ['']
        raise RuntimeError(f"스토리용 영상을 만들지 못했어요(ffmpeg {r.returncode}) {tail[0][:120]}")
    logger.info(f"📱 스토리용 영상: {os.path.basename(out)} · {why} · 서브 {serve} · {start}초부터 {dur:.1f}초까지")
    return out, {'start': start, 'serve': serve, 'why': why, 'duration': round(dur, 1)}


def _make_and_upload_story(item):
    """태블릿 '📱 인스타 스토리' 로 고른 클립 — 스토리용 파일을 만들어 올린다(게시는 관리자 [올리기])."""
    hid = item['id']
    with _story_lock:
        _story_jobs[hid] = {'state': 'making', 'error': '', 'url': ''}
    try:
        with _story_make_lock:
            out, win = _story_clip(item['path'], item['seconds'])
    except Exception as e:
        logger.error(f"스토리용 영상 실패({hid}): {e}")
        with _story_lock:
            _story_jobs[hid] = {'state': 'failed', 'error': str(e)[:300], 'url': ''}
        return
    _upload_story_file(hid, out, win)


def _upload_story_file(hid, out, win, auto_post=False):
    """스토리용 파일을 백엔드에 올린다(POST /api/highlight/:id/story-file) — auto_post 면 백엔드가 받자마자 인스타에 올린다
    (직전 랠리). 결과는 _story_jobs[hid](state ready · post = 백엔드 게시 상태 posting/posted/failed/'')."""
    try:
        import requests
        api = (HIGHLIGHT_CFG.get('api_base') or 'https://api.padelsociety.co.kr').rstrip('/')
        key = HIGHLIGHT_CFG.get('upload_key') or ''
        if not key:
            raise RuntimeError('이 PC 설정에 하이라이트 업로드 키가 없어요(config.highlight.upload_key)')
        with open(out, 'rb') as f:
            r = requests.post(
                f"{api}/api/highlight/{hid}/story-file",
                headers={'x-upload-key': key},
                files={'file': (os.path.basename(out), f, 'video/mp4')},
                # 어디를 잘랐나 — 관리자 예약앱 미리보기의 '✂️' 한 줄(서버 storyCut)
                data={'cutStart': str(win.get('start') if win.get('start') is not None else ''),
                      'cutServe': str(win.get('serve') if win.get('serve') is not None else ''),
                      'cutDuration': str(win.get('duration') if win.get('duration') is not None else ''),
                      'cutWhy': str(win.get('why') or ''),
                      **({'autoPost': '1'} if auto_post else {})},
                timeout=180,
            )
        body = {}
        try:
            body = r.json() or {}
        except Exception:
            pass
        if not r.ok:
            raise RuntimeError(body.get('message') or f'서버가 스토리용 영상을 받지 않았어요(HTTP {r.status_code})')
        url = ((body.get('data') or {}).get('storyUrl')) or ''
        post = str((body.get('data') or {}).get('status') or '')
        with _story_lock:
            _story_jobs[hid] = {'state': 'ready', 'error': '', 'url': url, 'post': post, 'auto': bool(auto_post), **win}
        logger.info(f"☁️ 스토리용 영상 업로드 완료: {hid}"
                    + (' · 인스타 자동 게시 ' + ('시작' if post == 'posting' else f'안 됨({post or "인스타 연결 전"})') if auto_post else ''))
    except Exception as e:
        logger.error(f"스토리용 영상 실패({hid}): {e}")
        with _story_lock:
            _story_jobs[hid] = {'state': 'failed', 'error': str(e)[:300], 'url': ''}


def _story_view(hid):
    with _story_lock:
        job = dict(_story_jobs.get(hid) or {'state': '', 'error': '', 'url': ''})
    return job


@app.route('/highlights/recent', methods=['GET'])
@_require_operator
def recent_highlights():
    """태블릿 '최근 클립' — 이 PC 가 올린 클립(재시작하면 비어 있다)."""
    with _story_lock:
        items = [dict(x) for x in _recent_highlights]
    return jsonify({'success': True, 'items': [
        {'id': x['id'], 'title': x['title'], 'seconds': x['seconds'], 'at': x['at'], 'story': _story_view(x['id'])}
        for x in items
    ]})


@app.route('/highlight/<hid>/story', methods=['POST'])
@_require_operator
def make_story(hid):
    """스토리용 파일 만들기 시작(뒤에서) — 이미 만들었거나 만드는 중이면 그대로. 보통은 업로드 뒤 저절로 만들어져 있다."""
    hid = str(hid)[:64]
    data = request.get_json(silent=True) or {}
    with _story_lock:
        item = next((dict(x) for x in _recent_highlights if x['id'] == hid), None)
        if not item:
            return jsonify({'success': False, 'error': '이 방송 PC 에 그 클립이 없어요 — 방송 서버를 다시 켠 뒤라면 새로 저장해 주세요.'}), 404
        if not os.path.exists(item['path']):
            return jsonify({'success': False, 'error': '클립 파일이 이 PC 에서 지워졌어요 — 새로 저장해 주세요.'}), 404
        job = _story_jobs.get(hid) or {}
        if job.get('state') == 'making':
            return jsonify({'success': True, 'story': dict(job)})
        if job.get('state') == 'ready' and not data.get('remake'):
            return jsonify({'success': True, 'story': dict(job)})
        _story_jobs[hid] = {'state': 'making', 'error': '', 'url': ''}   # 다른 클립을 만드는 중이면 그 뒤에 차례로(_story_make_lock)
    threading.Thread(target=_make_and_upload_story, args=(item,), daemon=True).start()
    return jsonify({'success': True, 'story': _story_view(hid)})


@app.route('/highlight/<hid>/story', methods=['GET'])
@_require_operator
def story_state(hid):
    return jsonify({'success': True, 'story': _story_view(str(hid)[:64])})


CAMERA_SETTLE_S = 4.0   # 다시 연 카메라가 첫 화면을 낼 때까지


@app.route('/camera-restart', methods=['POST'])
@_require_operator
def camera_restart():
    """카메라 다시 연결(2026-11-01) — 카메라 소스를 다시 열고 몇 초 뒤 화면을 다시 본다. 그래도 한 가지 색이고 하드웨어
    디코딩이 켜져 있으면 끄고(다시 열린다) 한 번 더 본다. 방송 중이 아니어도 된다.
    Body(선택): { "hwDecodeOff": true } — 바로 하드웨어 디코딩부터 끈다."""
    data = request.get_json(silent=True) or {}
    force_off = bool(data.get('hwDecodeOff'))
    with _frame_lock:
        srcs = obs.camera_sources()
        if not srcs:
            return jsonify({'success': False,
                            'error': 'OBS 에서 카메라(미디어 소스)를 못 찾았어요 — OBS 가 켜져 있는지 확인해 주세요'}), 400
        before = {c['name']: c for c in _frame_verdicts(srcs)}
        hw_off = []
        for name, hw in srcs:
            if force_off and hw and obs.set_hw_decode(name, False):
                hw_off.append(name)
            else:
                obs.restart_camera(name)
        time.sleep(CAMERA_SETTLE_S)
        srcs = obs.camera_sources() or srcs
        after = {c['name']: c for c in _frame_verdicts(srcs)}
        retry = [n for n, hw in srcs if hw and after.get(n, {}).get('uniform') and n not in hw_off]
        for name in retry:
            if obs.set_hw_decode(name, False):
                hw_off.append(name)
        if retry:
            time.sleep(CAMERA_SETTLE_S)
            after.update({c['name']: c for c in _frame_verdicts([(n, False) for n in retry])})
        for name in after:
            _frame_mem['bad'].pop(name, None)     # 감시는 처음부터 다시 센다
            _frame_mem['notes'].pop(name, None)
        rows = []
        for name, _hw in srcs:
            a = after.get(name) or {}
            rows.append({'name': name, 'before': (before.get(name) or {}).get('label') or '',
                         'after': a.get('label') or '', 'checked': bool(a.get('ok')), 'hw_off': name in hw_off})
    still = [r['name'] for r in rows if r['after']]
    if still:
        label = next(r['after'] for r in rows if r['after'])
        text = (f"다시 열었는데 아직 {label}이에요({', '.join(still)}) — "
                "카메라 전원·랜선을 확인하거나 OBS 를 다시 켜 주세요")
    elif not any(r['checked'] for r in rows):
        text = '카메라를 다시 열었어요 — 화면은 확인하지 못했어요(유튜브에서 봐 주세요)'
    else:
        text = '카메라를 다시 열었어요 — 화면 정상'
    if hw_off:
        text += f" · 하드웨어 디코딩을 껐어요({', '.join(hw_off)})"
    _note_action(f"[카메라 다시 연결] {text}")
    return jsonify({'success': True, 'ok': not still, 'text': text, 'sources': rows})


@app.route('/obs-restart', methods=['POST'])
@_require_operator
def obs_restart():
    """OBS 다시 켜기(2026-11-11) — 유튜브가 초록인데 OBS 화면은 정상일 때. 방송 중이면 같은 유튜브 방송으로 이어 보낸다."""
    _note_output_action('[OBS 다시 켜기] 요청')
    res = _restart_obs_and_resume('운영자 요청')
    _note_output_action(f"[OBS 다시 켜기] {res['text']}")
    if res['ok']:
        _output_mem.update({'bad': 0, 'next_at': time.time() + OUTPUT_FIRST_S})
        _set_output_state(bad=False, note='')
    return jsonify({'success': True, 'ok': res['ok'], 'text': res['text']})


@app.route('/status', methods=['GET'])
def status():
    """현재 스트림 상태 반환"""
    with state_lock:
        return jsonify(dict(stream_state))


# ════════════════════════════════════════════════════════════════
# 서버 실행
# ════════════════════════════════════════════════════════════════

# ── Graceful Shutdown ─────────────────────────────────────────
def _cleanup():
    """서버 종료 시 스트리밍을 정리합니다."""
    with state_lock:
        if not stream_state['active']:
            return
        bid = stream_state.get('broadcast_id')
    logger.info("🛑 서버 종료 감지 — 스트리밍 정리 중...")
    try:
        obs.stop_stream()
    except Exception:
        pass
    try:
        ended = bool(youtube.end_broadcast(bid))
    except Exception:
        ended = False
    _live_file_done(bid, ended)   # 못 끝냈으면 기록에 남아 다음 시작 때 정리된다
    logger.info("✅ 정리 완료")

atexit.register(_cleanup)

def _signal_handler(sig, frame):
    logger.info("🛑 종료 신호 수신")
    _cleanup()
    sys.exit(0)

signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


if __name__ == '__main__':
    lan_ip = get_lan_ip()

    # ── SSL 인증서 자동 감지 (mkcert 생성 파일) ────────────────
    # ssl/ 폴더에 cert.pem + key.pem 있으면 HTTPS로 자동 전환.
    # 생성 방법:
    #   1) mkcert 설치: https://github.com/FiloSottile/mkcert/releases
    #   2) mkcert -install          (PC에 로컬 CA 등록 — 최초 1회)
    #   3) mkdir ssl && mkcert -cert-file ssl/cert.pem -key-file ssl/key.pem 192.168.1.5 localhost 127.0.0.1
    #   4) 태블릿에 rootCA 설치:  mkcert -CAROOT  로 경로 확인 후 rootCA.pem 을 태블릿에 전송 → 설정>인증서 설치
    SSL_CERT = os.path.join(_DIR, 'ssl', 'cert.pem')
    SSL_KEY  = os.path.join(_DIR, 'ssl', 'key.pem')
    ssl_ctx = None
    if os.path.exists(SSL_CERT) and os.path.exists(SSL_KEY):
        import ssl as _ssl
        ssl_ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(SSL_CERT, SSL_KEY)

    scheme = 'https' if ssl_ctx else 'http'

    print("=" * 60)
    print("  🎾 PS Court 자동 스트리밍 서버")
    print("=" * 60)
    if ssl_ctx:
        print(f"  ✅ HTTPS 모드 (mkcert 인증서 감지)")
        print(f"  이 PC에서:   {scheme}://localhost:5000")
        print(f"  테블릿에서:  {scheme}://{lan_ip}:5000   ← 이 주소로 열어주세요")
        print(f"  PWA 설치:    주소창 옆 설치 아이콘 또는 공유>홈화면 추가")
    else:
        print(f"  ⚠️  HTTP 모드 (PWA 미지원 — HTTPS 사용 권장)")
        print(f"  이 PC에서:   http://localhost:5000")
        print(f"  테블릿에서:  http://{lan_ip}:5000")
        print()
        print("  HTTPS(PWA) 설정 방법:")
        print("    1) mkcert 설치: https://github.com/FiloSottile/mkcert/releases")
        print("    2) mkcert -install")
        print(f"   3) mkdir ssl && mkcert -cert-file ssl/cert.pem -key-file ssl/key.pem {lan_ip} localhost")
        print("    4) 태블릿에 rootCA 설치 (mkcert -CAROOT 로 경로 확인)")
        print("    5) 이 서버 재시작 → 자동으로 HTTPS 전환")
    print()
    print("  종료: Ctrl+C")
    print("=" * 60)

    # 리플레이 버퍼 상시 유지 스레드 시작 — 라이브 아니어도 하이라이트 가능
    threading.Thread(target=_buffer_keepalive, daemon=True).start()
    # 자유 라이브 시간 만료 워치독 — 태블릿이 꺼져도 서버가 끈다
    threading.Thread(target=_auto_stop_loop, daemon=True).start()
    # 방송 상태 감시 — 재연결·업로드·카메라·YouTube 경고를 /health 와 로그에(2026-10-21)
    threading.Thread(target=_health_loop, daemon=True, name='health').start()
    threading.Thread(target=_output_loop, daemon=True, name='output-check').start()
    threading.Thread(target=_nodata_loop, daemon=True, name='nodata-guard').start()
    # 지난번에 끝내지 못한 방송 정리·이어받기 — OBS 가 늦게 뜰 수 있어 조금 기다렸다가(2026-10-21)
    _rec = threading.Timer(20.0, _recover_leftover)
    _rec.daemon = True
    _rec.start()

    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True,
            ssl_context=ssl_ctx if ssl_ctx else None)
