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
import sys
import threading
import time
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from flask_cors import CORS

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
CORS(app, origins=config.get('cors_origins', ['https://jiwon.github.io']))  # 허용 도메인 제한

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


# ── 제목/설명 자동 생성 ────────────────────────────────────────
def build_title_and_desc(team_a: list, team_b: list, league: str) -> tuple:
    league_name = 'Gold+' if league == 'gold' else 'B&S'
    a_str = ' / '.join(team_a)
    b_str = ' / '.join(team_b)
    date_str = datetime.now().strftime('%Y.%m.%d')

    title = f"🎾 PS i-League {league_name} | {a_str} vs {b_str} | {date_str}"

    description = (
        f"🏆 PS Padel Society i-League 경기 생중계\n\n"
        f"📅 {date_str}\n"
        f"🏅 리그: {league_name}\n"
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

    if not team_a or not team_b:
        with state_lock:
            stream_state['active'] = False  # 롤백
        return jsonify({'success': False, 'error': '팀 정보가 없어요'}), 400

    try:
        title, description = build_title_and_desc(team_a, team_b, league)
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
    print("=" * 55)
    print("  🎾 PS Court 자동 스트리밍 서버")
    print("=" * 55)
    print(f"  주소:  http://localhost:5000")
    print(f"  상태:  http://localhost:5000/health")
    print()
    print("  ps_court.html에서 '스코어 입력' 버튼을 누르면")
    print("  YouTube 라이브가 자동으로 시작됩니다!")
    print()
    print("  종료: Ctrl+C")
    print("=" * 55)

    app.run(host='localhost', port=5000, debug=False, threaded=True)
