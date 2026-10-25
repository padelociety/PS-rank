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
# 태블릿은 운영자 로그인 토큰(psToken)을 Authorization: Bearer 로 보낸다. 그 토큰을 Playus 백엔드
# myProfile 에 물어 역할을 본다. 이 PC 에서 직접 부른 요청(터널·LAN 이 아닌 127.0.0.1)만 토큰 없이 통과 —
# OBS PC 브라우저의 /reload-html 과 make_thumbnail.py 가 그 길이다.
_AUTH_CFG = config.get('control_auth') or {}
CONTROL_AUTH_ENABLED = _AUTH_CFG.get('enabled', True) is not False
CONTROL_ROLES = frozenset(_AUTH_CFG.get('roles') or ['league_operator', 'super_admin', 'center_admin'])
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
AUTH_DOWN_MSG = ('운영자 확인 서버(Playus)에 연결하지 못했어요 — 방송 PC 의 인터넷 연결을 확인한 뒤 '
                 '다시 시도해 주세요')

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


def _check_operator_token(token: str):
    """토큰을 확인한다 → None(통과) 또는 (상태코드, 응답 dict). 결과는 토큰 해시로 잠깐 기억한다."""
    key = hashlib.sha256(token.encode('utf-8')).hexdigest()
    now = time.time()
    with _auth_lock:
        ent = dict(_auth_cache.get(key) or {})
    if not ent or ent.get('until', 0) <= now:
        state, info = _fetch_profile_role(token)   # info = 역할('ok') 또는 못 물어본 이유('down')
        now = time.time()
        if state == 'ok':
            if info in CONTROL_ROLES:
                ent = {'verdict': 'ok', 'until': now + _AUTH_OK_TTL, 'last_ok': now}
            else:
                ent = {'verdict': 'forbidden', 'until': now + _AUTH_DENY_TTL, 'last_ok': 0}
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
        return 403, {'success': False, 'error': AUTH_FORBIDDEN_MSG, 'auth': 'forbidden'}
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
_health_mem = {'prev': None, 'prev_t': 0.0, 'cams': {}, 'yt': None, 'tick': 0, 'issue': '', 'reconnecting': False}


def _health_tick(now_t: float = None):
    """감시 한 바퀴 — 테스트가 직접 부른다. 방송 중이 아니면 기억을 비운다."""
    now_t = time.time() if now_t is None else now_t
    with state_lock:
        active = stream_state['active']
        bid = stream_state.get('broadcast_id')
    if not active or not bid:
        with state_lock:
            health_state.update({'broadcast_id': None, 'health': None, 'reconnects': 0, 'since': None})
        _health_mem.update({'prev': None, 'prev_t': 0.0, 'cams': {}, 'yt': None, 'tick': 0, 'issue': '', 'reconnecting': False})
        return None
    if health_state.get('broadcast_id') != bid:
        with state_lock:
            health_state.update({'broadcast_id': bid, 'health': None, 'reconnects': 0, 'since': None})
        _health_mem.update({'prev': None, 'prev_t': 0.0, 'cams': {}, 'yt': None, 'tick': 0, 'issue': '', 'reconnecting': False})
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
    h = stream_health.evaluate(prev, snap, elapsed_s=elapsed, camera_bad_ticks=_health_mem['cams'],
                               youtube=_health_mem['yt'], advice=advice)
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
    })


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

        # 2. OBS 설정 + 스트리밍 시작
        obs.connect()
        obs.set_stream_settings(rtmp_url, stream_key)
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


def _save_and_upload_highlight(meta, seconds=0):
    """리플레이 저장(느림, ~수 초) + 업로드를 백그라운드로 처리.
    save_replay_buffer가 파일 경로 폴링에 최대 ~5초 걸려서 요청 스레드를 막으면
    안 됨(헬스체크 타임아웃 → 라이브 표시 깜빡임). 그래서 통째로 스레드에서 실행."""
    path = obs.save_replay_buffer()
    if not path:
        logger.error("하이라이트 저장 실패 — 리플레이 경로 없음")
        return
    _upload_highlight(path, meta, seconds)


def _upload_highlight(path, meta, seconds=0):
    try:
        import requests
        # 요청 길이가 있으면 뒤쪽 N초만 잘라서 업로드 (버퍼=90초, 60초 버튼 대응)
        # 트림 안 하는 경우도 faststart 리먹스 — 폰 스트리밍 재생 끊김 방지.
        if seconds:
            path = _trim_clip(path, seconds)
        else:
            path = _faststart_clip(path)
        api = (HIGHLIGHT_CFG.get('api_base') or 'https://api.padelsociety.co.kr').rstrip('/')
        key = HIGHLIGHT_CFG.get('upload_key') or ''
        if not key:
            logger.warning("하이라이트 업로드 생략 — config.highlight.upload_key 없음 (OBS PC에 파일만 저장됨)")
            return
        with open(path, 'rb') as f:
            r = requests.post(
                f"{api}/api/highlight/upload",
                headers={'x-upload-key': key},
                data=meta,
                files={'file': (os.path.basename(path), f, 'video/mp4')},
                timeout=180,
            )
        if r.ok:
            logger.info(f"☁️ 하이라이트 업로드 완료: {os.path.basename(path)}")
        else:
            logger.error(f"하이라이트 업로드 실패 {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.error(f"하이라이트 업로드 오류: {e}")


@app.route('/highlight', methods=['POST'])
@_require_operator
def save_highlight():
    """리플레이 버퍼를 클립으로 저장(즉시 응답) + 백엔드 업로드(백그라운드).
    Body(선택): { "seconds": 60 } — 버퍼(권장 90초)에서 뒤쪽 N초만 잘라 업로드."""
    data = request.get_json(silent=True) or {}
    seconds = int(data.get('seconds') or 0)
    if seconds and not (15 <= seconds <= 300):
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
    threading.Thread(target=_save_and_upload_highlight, args=(meta, seconds), daemon=True).start()
    return jsonify({'success': True, 'seconds': seconds or None})


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
    # 지난번에 끝내지 못한 방송 정리·이어받기 — OBS 가 늦게 뜰 수 있어 조금 기다렸다가(2026-10-21)
    _rec = threading.Timer(20.0, _recover_leftover)
    _rec.daemon = True
    _rec.start()

    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True,
            ssl_context=ssl_ctx if ssl_ctx else None)
