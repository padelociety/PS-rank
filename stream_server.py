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
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone

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

with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
    config = json.load(f)

# ── 컨트롤러 초기화 ────────────────────────────────────────────
obs = OBSController(config)
youtube = YouTubeAPI(config)

# ── Flask 앱 ───────────────────────────────────────────────────
app = Flask(__name__)
CORS(app, origins='*')  # LAN 내부 + 테블릿 접속 허용 (로컬 서버라 외부 노출 없음)

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


@app.route('/reload-html')
def _reload_html():
    """페이지 재다운로드 (최신 버전으로 갱신)."""
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
    'broadcast_id': None,
    'watch_url': None,
    'started_at': None,
    'team_a': [],
    'team_b': [],
    'league': '',
    'title': '',
    'error': None,
}
state_lock = threading.Lock()


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
    # 카테고리: B&S | G&P | Bridge (ps_court에서 넘어옴, 없으면 폴백)
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
        f"#빠델 #빠델소사이어티 #빠소 #빠델리그 #빠소리그 #PSL"
    )
    return title, description


# ════════════════════════════════════════════════════════════════
# API 엔드포인트
# ════════════════════════════════════════════════════════════════

@app.route('/health', methods=['GET'])
def health():
    """서버 상태 확인 — ps_court.html이 서버를 감지하는 데 사용"""
    with state_lock:
        return jsonify({
            'ok': True,
            'streaming': stream_state['active'],
            'watch_url': stream_state.get('watch_url'),
        })


@app.route('/start-stream', methods=['POST'])
def start_stream():
    """
    스트리밍 시작
    Body: { "teamA": ["이름1","이름2"], "teamB": ["이름3","이름4"], "league": "gold"|"bs" }
    """
    with state_lock:
        if stream_state['active']:
            return jsonify({'success': False, 'error': '이미 스트리밍 중이에요'}), 409
        stream_state['active'] = True  # 먼저 잠금 — 동시 요청 방지

    data = request.get_json(silent=True) or {}
    team_a = data.get('teamA', [])
    team_b = data.get('teamB', [])
    league = data.get('league', '')
    category = data.get('category', '')
    match_number = int(data.get('matchNumber', 0) or 0)

    if not team_a or not team_b:
        with state_lock:
            stream_state['active'] = False  # 롤백
        return jsonify({'success': False, 'error': '팀 정보가 없어요'}), 400

    try:
        title, description = build_title_and_desc(team_a, team_b, league, category, match_number)
        logger.info(f"🎬 스트리밍 시작: {title}")

        # 1. YouTube 방송 생성
        broadcast_id, rtmp_url, stream_key = youtube.create_broadcast_and_stream(title, description)
        watch_url = YouTubeAPI.get_watch_url(broadcast_id)

        # 2. OBS 설정 + 스트리밍 시작
        obs.connect()
        obs.set_stream_settings(rtmp_url, stream_key)
        obs.start_stream()

        with state_lock:
            stream_state.update({
                'broadcast_id': broadcast_id,
                'watch_url': watch_url,
                'started_at': datetime.now(timezone.utc).isoformat(),
                'team_a': team_a,
                'team_b': team_b,
                'league': league,
                'title': title,
                'error': None,
            })

        logger.info(f"✅ 스트리밍 시작됨! 시청: {watch_url}")
        return jsonify({
            'success': True,
            'title': title,
            'watch_url': watch_url,
            'broadcast_id': broadcast_id,
        })

    except Exception as e:
        logger.error(f"❌ 스트리밍 시작 실패: {e}")
        with state_lock:
            stream_state['active'] = False  # 실패 시 롤백
            stream_state['error'] = str(e)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/stop-stream', methods=['POST'])
def stop_stream():
    """스트리밍 종료"""
    with state_lock:
        if not stream_state['active']:
            return jsonify({'success': True, 'message': '스트리밍 중이 아니에요'})
        broadcast_id = stream_state.get('broadcast_id')

    errors = []

    # OBS와 YouTube를 독립적으로 종료 (하나 실패해도 다른 쪽은 시도)
    try:
        obs.stop_stream()
    except Exception as e:
        logger.error(f"❌ OBS 종료 실패: {e}")
        errors.append(f"OBS: {e}")

    try:
        youtube.end_broadcast(broadcast_id)
    except Exception as e:
        logger.error(f"❌ YouTube 종료 실패: {e}")
        errors.append(f"YouTube: {e}")

    # 상태 전체 초기화
    with state_lock:
        stream_state.update({
            'active': False,
            'broadcast_id': None,
            'watch_url': None,
            'started_at': None,
            'team_a': [],
            'team_b': [],
            'league': '',
            'title': '',
            'error': None,
        })

    if errors:
        logger.warning(f"⚠️ 스트리밍 종료 중 일부 오류: {errors}")
        return jsonify({'success': True, 'warnings': errors})

    logger.info("⏹️  스트리밍 종료됨")
    return jsonify({'success': True})


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
        youtube.end_broadcast(bid)
    except Exception:
        pass
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

    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True,
            ssl_context=ssl_ctx if ssl_ctx else None)
