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
    'error': None,
}
state_lock = threading.Lock()


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
        'error': None,
    })

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


# ── 자유 라이브 (리그 매치가 아닌 방송) ─────────────────────────
# 리그가 없는 달에도 코트에 온 사람들이 태블릿에서 바로 방송을 켜고 🎬 하이라이트를
# 남길 수 있게 한다. 리그 방송과 다른 점 하나: **끝나는 시각을 시작할 때 정한다.**
# 리그 방송은 점수를 저장하면 끝나지만 자유 라이브엔 그런 신호가 없어서, 시간을 안
# 정해 두면 사람들이 집에 간 뒤에도 빈 코트가 몇 시간씩 방송된다(유튜브에 그대로 남는다).
FREE_LIVE_MINUTES = (90, 120, 180)


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
        + "\n#빠델 #빠델소사이어티 #빠소 #PSL"
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
    })


@app.route('/start-stream', methods=['POST'])
def start_stream():
    """
    스트리밍 시작
    Body(리그):     { "teamA": [...], "teamB": [...], "league": "...", "category": "...", "matchNumber": n }
    Body(자유 라이브): { "mode": "free", "durationMinutes": 90|120|180, "teamA": [...](선택), "teamB": [...](선택) }
    """
    data = request.get_json(silent=True) or {}
    mode = 'free' if (data.get('mode') == 'free') else 'league'
    team_a = [str(x).strip() for x in (data.get('teamA') or []) if str(x).strip()]
    team_b = [str(x).strip() for x in (data.get('teamB') or []) if str(x).strip()]
    league = data.get('league', '')
    category = data.get('category', '')
    match_number = int(data.get('matchNumber', 0) or 0)
    minutes = int(data.get('durationMinutes', 0) or 0)

    # 입력 검증은 **잠그기 전에** — 잘못된 요청이 자리를 차지했다 롤백하는 창을 없앤다.
    if mode == 'league' and (not team_a or not team_b):
        return jsonify({'success': False, 'error': '팀 정보가 없어요'}), 400
    if mode == 'free' and minutes not in FREE_LIVE_MINUTES:
        return jsonify({'success': False, 'error': f'방송 시간은 {"/".join(str(m) for m in FREE_LIVE_MINUTES)}분 중에서 골라주세요'}), 400

    takeover = False
    with state_lock:
        if stream_state['active']:
            # 정말 송출 중인지 OBS에 물어본다. OBS가 재시작되거나 송출이 끊겨도 이
            # 플래그는 True로 남고, 그 뒤 모든 방송 요청이 409로 막힌다 — 실제로
            # 3시간 동안 그랬고, 사람이 /stop-stream을 눌러야만 풀렸다.
            # 송출 중이 아니면 죽은 상태로 보고 정리한 뒤 새로 시작한다.
            if obs.is_streaming():
                cur_mode = stream_state.get('mode') or 'league'
                if cur_mode == 'free' and mode == 'league':
                    # 리그 경기가 우선이다 — 자유 라이브가 켜져 있으면 끄고 리그 방송을 연다.
                    # (안 그러면 코트에 온 리그 4명이 '이미 스트리밍 중' 에 막혀 점수판만 켠다.)
                    takeover = True
                elif cur_mode == 'free':
                    left = _minutes_left_locked()
                    return jsonify({'success': False,
                                    'error': f'자유 라이브가 진행 중이에요 ({left}분 남음) — 먼저 종료해주세요'}), 409
                else:
                    return jsonify({'success': False, 'error': '리그 경기 방송 중이에요'}), 409
            else:
                stale = stream_state.get('watch_url') or stream_state.get('broadcast_id') or ''
                logger.warning(f"⚠️ 이전 스트리밍 상태가 남아 있는데 OBS는 송출 중이 아님 — 정리하고 새로 시작 ({stale})")
                _clear_state_locked()
        if not takeover:
            stream_state['active'] = True  # 먼저 잠금 — 동시 요청 방지

    if takeover:
        logger.info("🔁 리그 경기 시작 — 진행 중이던 자유 라이브를 먼저 끕니다")
        _stop_stream_impl('리그 경기로 전환')
        with state_lock:
            if stream_state['active']:
                return jsonify({'success': False, 'error': '이미 스트리밍 중이에요'}), 409
            stream_state['active'] = True

    broadcast_id = None   # 실패 시 정리해야 하므로 try 밖에서 잡아둔다
    try:
        if mode == 'free':
            title, description = build_free_title_and_desc(team_a, team_b, minutes)
        else:
            title, description = build_title_and_desc(team_a, team_b, league, category, match_number)
        logger.info(f"🎬 스트리밍 시작({mode}): {title}")
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
                'error': None,
            })
        if ends:
            logger.info(f"⏱ 자유 라이브 {minutes}분 — {ends.astimezone().strftime('%H:%M')} 에 자동 종료")

        logger.info(f"✅ 스트리밍 시작됨! 시청: {watch_url}")
        with state_lock:
            ends_at = stream_state.get('ends_at')
        return jsonify({
            'success': True,
            'mode': mode,
            'title': title,
            'watch_url': watch_url,
            'broadcast_id': broadcast_id,
            'ends_at': ends_at,
        })

    except Exception as e:
        logger.error(f"❌ 스트리밍 시작 실패: {e}")

        # 방송을 만든 뒤에 실패했으면 그 방송을 닫는다. 안 그러면 YouTube에 아무것도
        # 안 나가는 빈 방송이 열린 채 남는다(실제로 3시간 떠 있었다).
        if broadcast_id:
            try:
                youtube.end_broadcast(broadcast_id)
                logger.info(f"↩️ 실패한 방송 정리됨 ({broadcast_id})")
            except Exception:
                pass
        try:
            obs.stop_stream()
        except Exception:
            pass

        with state_lock:
            stream_state['active'] = False  # 실패 시 롤백
            stream_state['error'] = str(e)
        return jsonify({'success': False, 'error': str(e)}), 500


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
    돌려주는 값은 일부 실패 목록(빈 리스트면 깨끗이 끝남). 스트리밍 중이 아니면 아무것도 안 한다."""
    with state_lock:
        if not stream_state['active']:
            return []
        broadcast_id = stream_state.get('broadcast_id')
        title = stream_state.get('title') or ''

    errors = []

    # OBS와 YouTube를 독립적으로 종료 (하나 실패해도 다른 쪽은 시도)
    # ⚠️ 리플레이 버퍼는 일부러 안 끈다 — 방송 종료 후에도 하이라이트 저장 가능하게
    #    (키프알라이브 스레드가 계속 켜둠). 버퍼는 OBS 종료 시에만 멈춤.
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
        _clear_state_locked()

    why = f" ({reason})" if reason else ''
    if errors:
        logger.warning(f"⚠️ 스트리밍 종료 중 일부 오류{why}: {errors}")
    else:
        logger.info(f"⏹️  스트리밍 종료됨{why} — {title}")
    return errors


@app.route('/stop-stream', methods=['POST'])
def stop_stream():
    """스트리밍 종료"""
    with state_lock:
        if not stream_state['active']:
            return jsonify({'success': True, 'message': '스트리밍 중이 아니에요'})
    errors = _stop_stream_impl('태블릿에서 종료')
    if errors:
        return jsonify({'success': True, 'warnings': errors})
    return jsonify({'success': True})


def _auto_stop_once(now=None) -> bool:
    """자유 라이브가 만료됐으면 끈다. 껐으면 True. (워치독 한 바퀴 — 테스트가 직접 부른다)"""
    now = now or datetime.now(timezone.utc)
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

    # 리플레이 버퍼 상시 유지 스레드 시작 — 라이브 아니어도 하이라이트 가능
    threading.Thread(target=_buffer_keepalive, daemon=True).start()
    # 자유 라이브 시간 만료 워치독 — 태블릿이 꺼져도 서버가 끈다
    threading.Thread(target=_auto_stop_loop, daemon=True).start()

    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True,
            ssl_context=ssl_ctx if ssl_ctx else None)
