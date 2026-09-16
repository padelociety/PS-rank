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

    # ── 썸네일 ────────────────────────────────────────────────────
    def set_thumbnail(self, broadcast_id: str, image_path: str) -> bool:
        """
        방송 썸네일을 올린다. 성공 여부를 bool 로 돌려주고 **예외를 던지지 않는다.**

        ⚠️ 썸네일은 방송의 부속이지 조건이 아니다 — 실패해도 중계는 그대로 간다.
           그래서 호출부가 try 로 감쌀 필요가 없게 여기서 전부 삼킨다.

        ⚠️ 채널에 **썸네일 업로드 권한이 있어야** 한다(전화번호 인증). 없으면 YouTube 가
           403 `forbidden` 을 준다 — 코드 문제가 아니라 채널 설정이므로 메시지를 그대로 남긴다.

        쓰는 스코프는 기존 `.../auth/youtube` 그대로라 **재인증이 필요 없다**
        (`youtube_token.pickle` 을 다시 만들지 않아도 된다).
        """
        if not broadcast_id or not image_path or not os.path.exists(image_path):
            return False
        size = os.path.getsize(image_path)
        if size > 2 * 1024 * 1024:                       # YouTube 한도 2MB
            logger.warning(f"⚠️ 썸네일이 2MB를 넘어 건너뜁니다 ({size // 1024}KB)")
            return False
        try:
            self._ensure_auth()
            self.youtube.thumbnails().set(
                videoId=broadcast_id,
                media_body=image_path,
            ).execute()
            logger.info(f"🖼️ 썸네일 업로드 완료 ({size // 1024}KB)")
            return True
        except Exception as e:
            logger.warning(f"⚠️ 썸네일 업로드 실패 (방송은 그대로): {e}")
            return False

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
