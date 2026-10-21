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
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# OAuth 토큰 저장 경로
_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(_DIR, 'youtube_token.pickle')
CLIENT_SECRETS_PATH = os.path.join(_DIR, 'client_secrets.json')

SCOPES = ['https://www.googleapis.com/auth/youtube']


# ── 지연(latency) ───────────────────────────────────────────────
# ⚠️ 2026-10-21 신고 *"요즘 유투브가 자주 끊긴대"*: 이 서버는 처음부터 ultraLow(초저지연)로 방송을 만들었다
#    (config.json 예시·설치 스크립트도 ultraLow). 초저지연은 시청자 버퍼가 2~3초뿐이라 폰·LTE 에서 조금만
#    흔들려도 멈춰서 다시 받는다 — YouTube 도 '시청자가 버퍼링을 더 자주 겪는다' 고 적는다. 채팅으로 주고받을
#    일이 없는 경기 중계라 지연이 짧을 이유가 없다. 그래서 기본은 normal(일반 · 15~30초 늦게 보인다)이고,
#    config 에 남아 있는 ultraLow 는 **받지 않는다**(normal 로 바꾸고 로그에 남긴다 — 설치된 PC 마다 config 를
#    고치러 가지 않아도 고쳐진다). low(저지연 · 몇 초 늦게)는 그대로 받는다.
LATENCY_DEFAULT = 'normal'
LATENCIES = ('normal', 'low')


def latency_of(youtube_cfg: dict) -> str:
    """config 의 youtube.latency → liveBroadcasts 의 latencyPreference. normal 이 기본 · ultraLow 는 normal 로."""
    raw = str((youtube_cfg or {}).get('latency') or '').strip()
    if raw in LATENCIES:
        return raw
    if raw.lower() == 'ultralow':
        logger.warning("⚠️ config 의 latency 'ultraLow'(초저지연)는 시청자 화면이 자주 멈춰 더 쓰지 않아요 — "
                       "normal(일반)로 방송합니다. 몇 초라도 빨라야 하면 config.json 에 \"low\" 를 적으세요.")
    elif raw:
        logger.warning(f"⚠️ config 의 latency {raw!r} 을(를) 몰라 normal(일반)로 방송합니다")
    return LATENCY_DEFAULT


def _err_has(e: Exception, word: str) -> bool:
    """googleapiclient HttpError 의 이유(reason)에 그 낱말이 있나 — 본문(content)과 메시지를 같이 본다."""
    try:
        body = getattr(e, 'content', b'') or b''
        if isinstance(body, bytes):
            body = body.decode('utf-8', errors='ignore')
    except Exception:
        body = ''
    return word in str(body) or word in str(e)


class YouTubeAPI:
    def __init__(self, config: dict):
        self.config = config.get('youtube', {})
        self.latency = latency_of(self.config)
        self.youtube = None  # 지연 초기화 (처음 호출 시 인증)
        # 방송 → 그 방송에 묶인 스트림(RTMP) id — 스트림 건강(stream_health)을 묻는 데 쓴다.
        # 서버를 다시 켜 기억이 없으면 방송에서 다시 읽는다(_stream_id_of).
        self._stream_of = {}
        # ⚠️ 유튜브 클라이언트(googleapiclient → httplib2.Http 하나)는 **스레드에 안전하지 않다**. 방송 만들기(요청
        #    스레드)와 썸네일·[무효]·제목 고치기(뒤 스레드)가 같은 연결을 동시에 쓰면 서로의 응답을 읽거나 끊는다
        #    (2026-10-08 검토 — 실제 httplib2 로 재현: 방송 만들기가 실패하거나 60초 매달렸다). 그래서 이 객체의
        #    API 호출은 전부 이 잠금으로 한 줄씩 한다. 호출이 몇 백 ms 라 줄 서도 티가 안 난다.
        self._lock = threading.RLock()

    # ── 인증 ─────────────────────────────────────────────────────
    def _ensure_auth(self, interactive: bool = True):
        """OAuth 2.0 인증을 확인하고 YouTube 클라이언트를 초기화합니다.
        interactive=False 면 브라우저 인증이 필요할 때 기다리지 않고 던진다(서버를 켤 때 남은 방송 정리 등 —
        무인 PC 에서 브라우저를 띄우고 멈추면 안 된다)."""
        with self._lock:
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
                    if not interactive:
                        raise RuntimeError("YouTube 인증이 필요해요(youtube_token.pickle) — 사람이 있을 때 다시 해 주세요")
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
        with self._lock:
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
                        # ⚠️ 2026-10-21: 자동 종료를 끈다. 켜 두면 업로드가 1분 남짓 끊겼을 때(경기장 인터넷 ·
                        #    공유기) YouTube 가 방송을 **끝내 버리고**, OBS 가 다시 붙어 보내는 영상은 끝난 방송으로
                        #    가서 아무에게도 안 보인다 — 태블릿은 계속 LIVE 인데 시청자 화면은 '라이브 종료'.
                        #    끄면 다시 붙는 순간 **같은 방송으로 이어진다.** 끝내기는 서버가 한다(경기 저장·취소 →
                        #    end_broadcast · 실패하면 다시 해 본다 · 서버가 죽었으면 다음에 켤 때 남은 방송을 닫는다).
                        'enableAutoStop': False,
                        'latencyPreference': self.latency,
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
            self._stream_of[broadcast_id] = stream_id

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
        with self._lock:
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
    def end_broadcast(self, broadcast_id: str) -> bool:
        """방송을 끝낸다. **더 할 일이 없으면 True**(끝냈다 · 이미 끝나 있었다 · 한 번도 라이브가 안 돼 지웠다),
        다시 해 봐야 하면 False. **예외를 던지지 않는다.**

        ⚠️ 2026-10-21 부터 방송을 자동 종료 없이 만든다(create_broadcast_and_stream) — 이 함수가 유일한 끝내기다.
           False 면 부르는 쪽이 나중에 다시 부른다(stream_server._end_later)."""
        with self._lock:
            if not broadcast_id:
                return False
            try:
                self._ensure_auth(interactive=False)
            except Exception as e:
                logger.warning(f"방송 종료 — YouTube 연결 실패 (나중에 다시): {e}")
                return False
            try:
                self.youtube.liveBroadcasts().transition(
                    broadcastStatus='complete',
                    id=broadcast_id,
                    part='id,status'
                ).execute()
                logger.info(f"⏹️  YouTube 방송 종료됨 (ID: {broadcast_id})")
                self._stream_of.pop(broadcast_id, None)
                return True
            except Exception as e:
                if _err_has(e, 'redundantTransition'):
                    logger.info(f"YouTube 방송이 이미 끝나 있었어요 (ID: {broadcast_id})")
                    self._stream_of.pop(broadcast_id, None)
                    return True
                if _err_has(e, 'invalidTransition'):
                    return self._close_not_live(broadcast_id)
                logger.warning(f"⚠️ 방송 종료 실패 — 나중에 다시 해 봐요 ({broadcast_id}): {e}")
                return False

    def _close_not_live(self, broadcast_id: str) -> bool:
        """'끝내기' 가 안 되는 방송(아직 라이브가 아님) — 상태를 보고 정한다. 잠금 안에서 부른다.
        · 한 번도 영상을 못 받은 방송(created·ready)은 **지운다** — 채널에 빈 '예정된 라이브' 로 남지 않게.
        · 라이브로 넘어가는 중(testing·liveStarting)이면 False — 조금 뒤에 다시 끝낸다.
        · 이미 끝났거나(complete) 막혔으면(revoked) True."""
        try:
            res = self.youtube.liveBroadcasts().list(part='status', id=broadcast_id).execute()
            items = res.get('items') or []
            if not items:
                logger.info(f"YouTube 방송이 이미 없어요 (ID: {broadcast_id})")
                return True
            life = str(((items[0].get('status') or {}).get('lifeCycleStatus')) or '')
            if life in ('complete', 'revoked'):
                return True
            if life in ('created', 'ready'):
                self.youtube.liveBroadcasts().delete(id=broadcast_id).execute()
                logger.info(f"🗑️ 영상을 한 번도 못 받은 빈 방송을 지웠어요 (ID: {broadcast_id})")
                self._stream_of.pop(broadcast_id, None)
                return True
            logger.info(f"YouTube 방송이 아직 '{life}' — 조금 뒤에 다시 끝내요 (ID: {broadcast_id})")
            return False
        except Exception as e:
            logger.warning(f"⚠️ 방송 상태 확인 실패 — 나중에 다시 ({broadcast_id}): {e}")
            return False

    # ── 스트림 건강 ───────────────────────────────────────────────
    def _stream_id_of(self, broadcast_id: str) -> str:
        """방송에 묶인 스트림 id. 기억이 없으면(서버를 다시 켬) 방송에서 읽는다. 모르면 ''. 잠금 안에서 부른다."""
        sid = self._stream_of.get(broadcast_id)
        if sid:
            return sid
        res = self.youtube.liveBroadcasts().list(part='contentDetails', id=broadcast_id).execute()
        items = res.get('items') or []
        sid = str(((items[0].get('contentDetails') or {}).get('boundStreamId')) or '') if items else ''
        if sid:
            self._stream_of[broadcast_id] = sid
        return sid

    def stream_health(self, broadcast_id: str):
        """YouTube 가 보는 이 방송 스트림의 건강 — {'status': 'good'|'ok'|'bad'|'noData'|…,
        'issues': [{'type','severity','reason'}]} · 모르면 None. **예외를 던지지 않는다.**
        (liveStreams.list 1 단위 — 방송 중 1분에 한 번 부른다)"""
        with self._lock:
            if not broadcast_id:
                return None
            try:
                self._ensure_auth(interactive=False)
                sid = self._stream_id_of(broadcast_id)
                if not sid:
                    return None
                res = self.youtube.liveStreams().list(part='status', id=sid).execute()
                items = res.get('items') or []
                if not items:
                    return None
                hs = ((items[0].get('status') or {}).get('healthStatus')) or {}
                issues = []
                for it in hs.get('configurationIssues') or []:
                    if isinstance(it, dict):
                        issues.append({'type': str(it.get('type') or ''),
                                       'severity': str(it.get('severity') or ''),
                                       'reason': str(it.get('reason') or '')})
                return {'status': str(hs.get('status') or ''), 'issues': issues}
            except Exception as e:
                logger.warning(f"YouTube 스트림 건강 확인 실패(무시): {e}")
                return None

    # ── 제목·설명 바꾸기 ──────────────────────────────────────────
    def update_snippet(self, video_id: str, title: str, description: str) -> bool:
        """진행 중인 방송의 제목·설명을 바꾼다. **예외를 던지지 않는다.**

        같은 매치를 이어서 방송하는데(태블릿을 새로 열어 [이어서 하기]) 팀을 다시 뽑았거나 번호가
        바뀌었을 때만 쓴다 — 방송을 새로 열면 그 경기가 영상 두 개로 쪼개진다(2026-10-08).
        라이브 방송의 영상 id 는 broadcast id 와 같다."""
        with self._lock:
            if not video_id:
                return False
            try:
                self._ensure_auth()
                res = self.youtube.videos().list(part='snippet', id=video_id).execute()
                items = res.get('items') or []
                if not items:
                    logger.warning(f"⚠️ 제목 바꾸기: 영상을 못 찾음 ({video_id})")
                    return False
                snippet = retitle_snippet(items[0].get('snippet') or {}, title, description)
                if snippet is None:
                    return True
                self.youtube.videos().update(
                    part='snippet', body={'id': video_id, 'snippet': snippet},
                ).execute()
                logger.info(f"✏️ 방송 제목·설명 갱신 ({video_id}) — {snippet['title']}")
                return True
            except Exception as e:
                logger.warning(f"⚠️ 방송 제목·설명 갱신 실패 (방송은 그대로): {e}")
                return False

    # ── 무효 표시 ─────────────────────────────────────────────────
    def mark_void(self, video_id: str) -> bool:
        """끝까지 못 친 경기의 영상 제목·설명 앞에 [무효] 를 붙인다. **예외를 던지지 않는다.**

        매치는 태블릿에서 지워지고(순위·PAR·전적에서 빠진다) 영상만 채널에 남는다 —
        그 영상이 정식 경기처럼 보이면 안 된다(사용자 지시 2026-10-01).
        라이브 방송의 영상 id 는 broadcast id 와 같다. 쓰는 스코프(`.../auth/youtube`)는
        그대로라 재인증이 필요 없다."""
        with self._lock:
            if not video_id:
                return False
            try:
                self._ensure_auth()
                res = self.youtube.videos().list(part='snippet', id=video_id).execute()
                items = res.get('items') or []
                if not items:
                    logger.warning(f"⚠️ [무효] 표시: 영상을 못 찾음 ({video_id})")
                    return False
                snippet = void_snippet(items[0].get('snippet') or {})
                if snippet is None:
                    logger.info(f"[무효] 이미 붙어 있음 ({video_id})")
                    return True
                self.youtube.videos().update(
                    part='snippet', body={'id': video_id, 'snippet': snippet},
                ).execute()
                logger.info(f"🚫 영상에 [무효] 표시 ({video_id}) — {snippet['title']}")
                return True
            except Exception as e:
                logger.warning(f"⚠️ [무효] 표시 실패 ({video_id}): {e}")
                return False

    # ── 방송 URL ──────────────────────────────────────────────────
    @staticmethod
    def get_watch_url(broadcast_id: str) -> str:
        return f"https://www.youtube.com/watch?v={broadcast_id}"


VOID_TAG = '[무효]'
VOID_NOTE = ('[무효] 시간이 모자라 끝까지 치르지 못한 경기예요. '
             '순위·PAR·전적에 반영되지 않았어요.')


def void_snippet(snippet: dict):
    """videos.update 에 보낼 snippet — 제목·설명 앞에 [무효]. 이미 붙어 있으면 None.

    ⚠️ update 는 **보낸 칸으로 통째로 갈아끼운다** — categoryId 는 필수고, 태그·언어를
    빼먹으면 지워진다. 그래서 읽은 값을 그대로 싣고, 읽기 전용 칸(thumbnails·channelId·
    publishedAt·localized …)만 뺀다.
    ⚠️ YouTube 한도: 제목 100자 · 설명 5000바이트. 넘으면 400 이라 잘라서 보낸다."""
    title = str(snippet.get('title') or '')
    desc = str(snippet.get('description') or '')
    if title.startswith(VOID_TAG):
        return None
    out = {
        'title': (VOID_TAG + ' ' + title)[:100],
        'description': _cut_bytes(VOID_NOTE + ('\n\n' + desc if desc else ''), 5000),
        'categoryId': snippet.get('categoryId') or '17',   # 읽은 값이 없을 때만 — 17 = Sports
    }
    for k in ('tags', 'defaultLanguage', 'defaultAudioLanguage'):
        if snippet.get(k):
            out[k] = snippet[k]
    return out


def retitle_snippet(snippet: dict, title: str, description: str):
    """videos.update 에 보낼 snippet — 제목·설명만 바꾼다. 이미 같으면 None.
    ⚠️ void_snippet 과 같은 이유로 categoryId·태그·언어를 그대로 싣는다(빼면 지워진다).
    ⚠️ [무효] 가 붙은 영상이면 그 표시를 지키고 제목 앞에 다시 붙인다."""
    cur_title = str(snippet.get('title') or '')
    cur_desc = str(snippet.get('description') or '')
    title = str(title or '').strip() or cur_title
    description = str(description or '')
    if cur_title.startswith(VOID_TAG) and not title.startswith(VOID_TAG):
        title = VOID_TAG + ' ' + title
    title = title[:100]
    description = _cut_bytes(description, 5000)
    if title == cur_title and description == cur_desc:
        return None
    out = {
        'title': title,
        'description': description,
        'categoryId': snippet.get('categoryId') or '17',
    }
    for k in ('tags', 'defaultLanguage', 'defaultAudioLanguage'):
        if snippet.get(k):
            out[k] = snippet[k]
    return out


def _cut_bytes(s: str, limit: int) -> str:
    b = s.encode('utf-8')
    if len(b) <= limit:
        return s
    return b[:limit].decode('utf-8', errors='ignore')
