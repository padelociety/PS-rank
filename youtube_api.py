"""
YouTube Live Streaming API Helper
YouTube 라이브 방송을 자동으로 생성하고 관리합니다.

필요 조건:
  1. Google Cloud Console → 프로젝트 생성
  2. YouTube Data API v3 활성화
  3. OAuth 2.0 클라이언트 ID 생성 (데스크톱 앱)
  4. client_secrets.json 이 파일과 같은 폴더에 저장
"""

import os
import pickle
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# OAuth 토큰 저장 경로
_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(_DIR, 'youtube_token.pickle')
CLIENT_SECRETS_PATH = os.path.join(_DIR, 'client_secrets.json')

SCOPES = ['https://www.googleapis.com/auth/youtube']


class YouTubeAPI:
    def __init__(self, config: dict):
        self.config = config.get('youtube', {})
        self.youtube = None  # 지연 초기화 (처음 호출 시 인증)

    # ── 인증 ─────────────────────────────────────────────────────
    def _ensure_auth(self):
        """OAuth 2.0 인증을 확인하고 YouTube 클라이언트를 초기화합니다."""
        if self.youtube:
            return

        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError:
            raise RuntimeError(
                "Google API 패키지가 없어요.\n"
                "터미널에서 실행: pip install google-api-python-client google-auth-oauthlib"
            )

        if not os.path.exists(CLIENT_SECRETS_PATH):
            raise RuntimeError(
                f"client_secrets.json 파일이 없어요.\n"
                f"Google Cloud Console → OAuth 2.0 클라이언트 ID → JSON 다운로드\n"
                f"저장 위치: {CLIENT_SECRETS_PATH}"
            )

        creds = None
        if os.path.exists(TOKEN_PATH):
            with open(TOKEN_PATH, 'rb') as f:
                creds = pickle.load(f)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                logger.info("✅ YouTube 토큰 갱신됨")
            else:
                logger.info("🌐 브라우저에서 YouTube 인증이 필요해요...")
                flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_PATH, SCOPES)
                creds = flow.run_local_server(port=0)
                logger.info("✅ YouTube 인증 완료")

            with open(TOKEN_PATH, 'wb') as f:
                pickle.dump(creds, f)

        self.youtube = build('youtube', 'v3', credentials=creds)
        logger.info("✅ YouTube API 클라이언트 초기화 완료")

    # ── 방송 생성 ─────────────────────────────────────────────────
    def create_broadcast_and_stream(self, title: str, description: str) -> tuple:
        """
        YouTube 라이브 방송과 스트림을 생성하고 바인딩합니다.

        Returns:
            (broadcast_id, rtmp_url, stream_key)
            예) ('abc123', 'rtmp://a.rtmp.youtube.com/live2', 'xxxx-xxxx-xxxx-xxxx')
        """
        self._ensure_auth()
        now = datetime.now(timezone.utc).isoformat()
        privacy = self.config.get('privacy', 'public')  # public / unlisted / private

        # 1. 방송 객체 생성
        logger.info(f"📡 YouTube 방송 생성 중: {title}")
        broadcast = self.youtube.liveBroadcasts().insert(
            part='snippet,status,contentDetails',
            body={
                'snippet': {
                    'title': title,
                    'description': description,
                    'scheduledStartTime': now,
                },
                'status': {
                    'privacyStatus': privacy,
                    'selfDeclaredMadeForKids': False,
                },
                'contentDetails': {
                    'enableAutoStart': True,   # OBS 스트림 감지 시 자동 라이브 전환
                    'enableAutoStop': True,    # OBS 종료 시 자동 방송 종료
                    'latencyPreference': self.config.get('latency', 'ultraLow'),
                    'enableDvr': True,
                },
            }
        ).execute()
        broadcast_id = broadcast['id']
        logger.info(f"✅ 방송 ID: {broadcast_id}")

        # 2. 스트림(RTMP 엔드포인트) 생성
        stream = self.youtube.liveStreams().insert(
            part='snippet,cdn',
            body={
                'snippet': {'title': title},
                'cdn': {
                    'frameRate': 'variable',
                    'ingestionType': 'rtmp',
                    'resolution': 'variable',
                },
            }
        ).execute()
        stream_id = stream['id']
        rtmp_url = stream['cdn']['ingestionInfo']['ingestionAddress']
        stream_key = stream['cdn']['ingestionInfo']['streamName']
        logger.info(f"✅ 스트림 키 발급됨 ({rtmp_url})")

        # 3. 방송에 스트림 바인딩
        self.youtube.liveBroadcasts().bind(
            part='id,contentDetails',
            id=broadcast_id,
            streamId=stream_id
        ).execute()
        logger.info("✅ 방송-스트림 바인딩 완료")

        return broadcast_id, rtmp_url, stream_key

    # ── 방송 종료 ─────────────────────────────────────────────────
    def end_broadcast(self, broadcast_id: str):
        """방송을 명시적으로 종료합니다 (enableAutoStop이 있으면 자동으로 되지만 보험용)."""
        if not self.youtube or not broadcast_id:
            return
        try:
            self.youtube.liveBroadcasts().transition(
                broadcastStatus='complete',
                id=broadcast_id,
                part='id,status'
            ).execute()
            logger.info(f"⏹️  YouTube 방송 종료됨 (ID: {broadcast_id})")
        except Exception as e:
            logger.warning(f"방송 종료 중 오류 (무시): {e}")

    # ── 방송 URL ──────────────────────────────────────────────────
    @staticmethod
    def get_watch_url(broadcast_id: str) -> str:
        return f"https://www.youtube.com/watch?v={broadcast_id}"
