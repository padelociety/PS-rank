"""
OBS WebSocket Controller
OBS Studio를 원격으로 제어합니다 (obs-websocket 5.x)

필요 조건:
  - OBS Studio 28 이상
  - OBS → 도구 → WebSocket 서버 설정 → 활성화
  - 포트: 4455 (기본값)
"""

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)


class OBSController:
    def __init__(self, config: dict):
        obs_cfg = config.get('obs', {})
        self.host = obs_cfg.get('host', 'localhost')
        self.port = obs_cfg.get('port', 4455)
        self.password = obs_cfg.get('password', '')
        # 소리 싱크 — OBS 가 소리를 화면보다 늦게 녹화·송출한다(2026-09 측정: 하이라이트·라이브
        # 둘 다 타구음이 0.64~0.65초 늦음). 사람이 OBS 고급 오디오 속성에 숫자를 넣는 대신,
        # 연결할 때마다 여기서 넣는다 — OBS 를 새로 깔거나 장치를 다시 추가해도 되돌아가지 않는다.
        #   audio_sync_offset_ms : 음수 = 소리를 앞당김. 없으면(None) 건드리지 않는다.
        #   audio_sync_inputs    : 이 이름의 입력에만. 비우면 소리가 있는 입력 전부.
        raw = obs_cfg.get('audio_sync_offset_ms', None)
        try:
            self.audio_sync_offset_ms = None if raw is None or raw == '' else int(raw)
        except (TypeError, ValueError):
            logger.warning(f"⚠️ audio_sync_offset_ms 값이 숫자가 아니라 무시합니다: {raw!r}")
            self.audio_sync_offset_ms = None
        self.audio_sync_inputs = [str(x) for x in (obs_cfg.get('audio_sync_inputs') or []) if str(x).strip()]
        # /health 가 보여 줄 마지막 적용 결과 — 원격에서 '맞춰졌나' 를 확인하는 유일한 창.
        self.audio_sync_status = {'target_ms': self.audio_sync_offset_ms, 'applied': [], 'error': ''}
        self.client = None
        # OBS WebSocket(ReqClient)은 스레드 안전하지 않음 — 키프알라이브 스레드와
        # 하이라이트 요청이 동시에 접근하면 응답이 꼬여 엉뚱한 예외가 남(예: 버퍼가
        # 켜져 있는데 "꺼짐"으로 오판). 모든 OBS 호출을 이 락으로 직렬화한다.
        self._lock = threading.RLock()
        # 하이라이트 저장은 한 번에 하나 — 두 번 연달아 누르면 둘 다 같은 '직전 경로' 를 기억해,
        # 먼저 끝난 파일을 둘이 같이 올린다(두 번째 클립이 사라진다).
        self._replay_save_lock = threading.Lock()

    # ── 연결 ─────────────────────────────────────────────────────
    def connect(self, retries: int = 3, delay: float = 2.0):
        """OBS WebSocket에 연결합니다. 실패 시 재시도합니다."""
        try:
            import obsws_python as obs_ws
        except ImportError:
            raise RuntimeError(
                "obsws-python 패키지가 없어요.\n"
                "터미널에서 실행: pip install obsws-python"
            )

        with self._lock:
            last_error = None
            for attempt in range(1, retries + 1):
                try:
                    self.client = obs_ws.ReqClient(
                        host=self.host,
                        port=self.port,
                        password=self.password,
                        timeout=10
                    )
                    version = self.client.get_version()
                    logger.info(f"✅ OBS 연결됨 (v{version.obs_version})")
                    self.apply_audio_sync()
                    return
                except Exception as e:
                    last_error = e
                    self.client = None
                    if attempt < retries:
                        logger.warning(f"⚠️ OBS 연결 시도 {attempt}/{retries} 실패, {delay}초 후 재시도...")
                        time.sleep(delay)

        raise RuntimeError(
            f"OBS 연결 실패 ({retries}회 시도): {last_error}\n"
            "OBS Studio가 실행 중인지, WebSocket 서버가 활성화됐는지 확인해주세요.\n"
            "OBS → 도구 → WebSocket 서버 설정"
        )

    # ── 소리 싱크 ─────────────────────────────────────────────────
    # OBS 가 허용하는 범위(obs-websocket SetInputAudioSyncOffset): -950 ~ 20000 ms.
    SYNC_MIN_MS, SYNC_MAX_MS = -950, 20000

    @staticmethod
    def _input_names(resp):
        """GetInputList 응답에서 입력 이름만 — obsws-python 버전에 따라 키 모양이 다르다."""
        out = []
        for it in (getattr(resp, 'inputs', None) or []):
            if isinstance(it, dict):
                name = it.get('inputName') or it.get('input_name')
            else:
                name = getattr(it, 'inputName', None) or getattr(it, 'input_name', None)
            if name:
                out.append(str(name))
        return out

    def apply_audio_sync(self):
        """
        설정된 소리 싱크 값을 OBS 입력에 넣는다. **연결될 때마다** 부른다(connect 안).

        ⚠️ 방송을 막지 않는다 — 여기서 나는 오류는 전부 로그와 audio_sync_status 로만 남긴다.
           싱크가 0.6초 어긋난 방송이 안 켜진 방송보다 낫다.
        ⚠️ 이미 같은 값이면 다시 쓰지 않는다(키프알라이브가 재연결할 때마다 로그가 쌓이지 않게).
        ⚠️ 소리가 없는 입력(이미지·텍스트·브라우저 소스 등)은 OBS 가 조회부터 거절한다 — 건너뛴다.
        """
        target = self.audio_sync_offset_ms
        status = {'target_ms': target, 'applied': [], 'error': ''}
        if target is None:
            self.audio_sync_status = status
            return status
        clamped = max(self.SYNC_MIN_MS, min(self.SYNC_MAX_MS, target))
        if clamped != target:
            logger.warning(f"⚠️ 소리 싱크 {target}ms 는 OBS 범위 밖이라 {clamped}ms 로 넣습니다")
        with self._lock:
            if not self.client:
                status['error'] = 'OBS 미연결'
                self.audio_sync_status = status
                return status
            try:
                names = self._input_names(self.client.get_input_list())
            except Exception as e:
                status['error'] = f'입력 목록을 못 읽음: {e}'
                logger.warning(f"⚠️ 소리 싱크 — {status['error']}")
                self.audio_sync_status = status
                return status
            wanted = set(self.audio_sync_inputs)
            if wanted:
                missing = sorted(wanted - set(names))
                if missing:
                    status['error'] = f"OBS 에 없는 입력: {', '.join(missing)}"
                    logger.warning(f"⚠️ 소리 싱크 — {status['error']} (config.json 의 audio_sync_inputs 이름 확인)")
                names = [n for n in names if n in wanted]
            for name in names:
                try:
                    before = int(getattr(self.client.get_input_audio_sync_offset(name),
                                         'input_audio_sync_offset', 0) or 0)
                except Exception:
                    continue  # 소리가 없는 입력
                try:
                    if before != clamped:
                        self.client.set_input_audio_sync_offset(name, clamped)
                        logger.info(f"🔈 소리 싱크 '{name}': {before}ms → {clamped}ms")
                    status['applied'].append({'input': name, 'before_ms': before, 'ms': clamped})
                except Exception as e:
                    status['error'] = f"'{name}' 적용 실패: {e}"
                    logger.warning(f"⚠️ 소리 싱크 — {status['error']}")
            if not status['applied'] and not status['error']:
                status['error'] = '소리가 있는 입력을 못 찾음'
                logger.warning("⚠️ 소리 싱크 — 소리가 있는 OBS 입력이 없습니다(카메라 소리 소스 확인)")
        self.audio_sync_status = status
        return status

    def disconnect(self):
        """연결을 끊습니다."""
        with self._lock:
            if self.client:
                try:
                    self.client.disconnect()
                except Exception:
                    pass
                self.client = None

    # ── 스트림 설정 ───────────────────────────────────────────────
    def set_stream_settings(self, rtmp_url: str, stream_key: str):
        """
        OBS 스트림 서버/키를 설정합니다.

        Args:
            rtmp_url:   예) rtmp://a.rtmp.youtube.com/live2
            stream_key: YouTube 스트림 키
        """
        with self._lock:
            if not self.client:
                self.connect()

            # obsws-python 버전에 따라 키워드 이름이 다름(stream_service_type ↔ ss_type).
            # 위치 인자로 넘기면 두 버전 모두에서 동작한다.
            self.client.set_stream_service_settings(
                'rtmp_custom',
                {
                    'server': rtmp_url,
                    'key': stream_key,
                    'use_auth': False,
                },
            )

            # 넣은 값을 되읽어 확인한다. 이 요청이 조용히 안 먹으면 OBS 의 방송 설정이
            # 빈 채로 남고, 다음 start_stream 이 '설정된 방송 없음' 대화상자를 띄운 채
            # 멈춘다. 그 대화상자는 OBS 를 붙잡아 이후 WebSocket 요청까지 막는다.
            # 여기서 미리 걸러야 그 상태로 들어가지 않는다.
            try:
                cur = self.client.get_stream_service_settings()
                got = getattr(cur, 'stream_service_settings', None) or {}
                got_type = getattr(cur, 'stream_service_type', '') or ''
            except Exception as e:
                logger.warning(f"스트림 설정 확인 실패(무시하고 진행): {e}")
                got, got_type = None, ''

            if got is not None and (not got.get('server') or not got.get('key')):
                raise RuntimeError(
                    "OBS 가 스트림 설정을 받지 않았어요 "
                    f"(type={got_type!r}, server={'있음' if got.get('server') else '없음'}, "
                    f"key={'있음' if got.get('key') else '없음'}). "
                    "OBS 설정 → 방송에서 서비스를 '사용자 지정', "
                    "서버 rtmp://a.rtmp.youtube.com/live2 로 한 번 저장한 뒤 다시 시도해주세요."
                )

            logger.info(f"✅ OBS 스트림 설정 완료 ({rtmp_url})")

    # ── 스트리밍 시작/종료 ────────────────────────────────────────
    def start_stream(self):
        """OBS 스트리밍을 시작합니다.

        ⚠️ 2026-10-08: **이미 송출 중이면 성공으로 치지 않는다.** 예전엔 '이미 스트리밍 중이에요'
           로 조용히 돌아갔는데, 그 송출은 **지난 방송의 키**로 나가는 중이다 — 방금 넣은 새 키는
           송출을 다시 시작해야 먹는다. 그래서 새 방송은 영상을 한 프레임도 못 받고, 지난 경기 제목·
           썸네일 방송에 다음 경기가 이어서 나갔다(26S3 Match39 방송에 Match 41). 새 방송을 열기
           전에 서버가 OBS 를 먼저 끈다(stream_server._ensure_obs_idle) — 여기 닿았는데 송출 중이면
           그 사이 누가 OBS 에서 직접 켠 것이라 멈춘다."""
        with self._lock:
            if not self.client:
                self.connect()

            status = self.client.get_stream_status()
            if status.output_active:
                raise RuntimeError(
                    "OBS 가 이미 다른 방송을 내보내는 중이에요 — 새 방송 키로 바꾸려면 먼저 꺼야 해요. "
                    "OBS 에서 [방송 중단] 을 누른 뒤 다시 시작해 주세요."
                )

            self.client.start_stream()
            logger.info("▶️  OBS 스트리밍 시작됨")

    def stop_stream(self, timeout: float = 10.0) -> bool:
        """OBS 송출을 끈다. **정말 꺼진 걸 확인했으면 True**, 못 껐거나 확인을 못 했으면 False.

        ⚠️ 2026-10-08: 예전엔 연결(client)이 없으면 **아무것도 안 하고** 돌아갔다. 키프알라이브가
           OBS 응답 오류 한 번에 client 를 비우고(ensure_replay_buffer) 20초 뒤에야 다시 붙는데, 그
           사이에 경기가 끝나면 OBS 는 지난 방송 키로 계속 내보내고 서버는 '껐다' 고 믿었다.
           그래서 연결이 없으면 붙여서 끄고, 송출이 실제로 멈출 때까지 기다린다(StopStream 은 요청만
           받고 돌아온다 — 바로 다음 순간엔 아직 송출 중이다)."""
        # ⚠️ 연결이 **있는데 죽어 있으면**(소켓 끊김·응답 꼬임) 요청이 던진다 — 그때는 연결을 버리고 새로 붙어
        #    한 번 더 보낸다(2026-10-08 검토). 비어 있는지만 보면 죽은 연결로 끄기를 못 보내고 OBS 는 계속 송출한다.
        sent = False
        with self._lock:
            for attempt in range(2):
                if not self.client:
                    try:
                        # 다시 붙을 때는 한 번만 — OBS 가 멈춰 있으면 붙기마다 10초라 끄기 전체가 태블릿 기다림을 넘긴다.
                        self.connect(retries=2 if attempt == 0 else 1, delay=1.0)
                    except Exception as e:
                        logger.error(f"❌ OBS 에 붙지 못해 송출을 끄지 못했어요: {e}")
                        return False
                try:
                    status = self.client.get_stream_status()
                    if not status.output_active:
                        logger.info("스트리밍이 이미 종료됐어요.")
                        return True
                    self.client.stop_stream()
                    logger.info("⏹️  OBS 스트리밍 종료 요청")
                    sent = True
                    break
                except Exception as e:
                    logger.warning(f"스트리밍 종료 중 오류 — 다시 붙어서 한 번 더: {e}")
                    self.disconnect()
        if not sent:
            logger.error("❌ OBS 에 끄기를 보내지 못했어요")
        stopped = self.wait_until_stopped(timeout)
        if stopped:
            logger.info("⏹️  OBS 스트리밍 종료됨")
        else:
            logger.error(f"❌ OBS 송출이 {timeout:.0f}초 안에 멈추지 않았어요")
        # 예전처럼 끝나면 연결을 놓는다(다음 시작은 새로 붙는다).
        self.disconnect()
        return stopped

    def wait_until_stopped(self, timeout: float = 10.0) -> bool:
        """송출이 '실제로' 멈췄는지 확인. 연결이 끊겨 확인할 수 없으면 False 다(꺼졌다고 단정하지 않는다)."""
        deadline = time.time() + timeout
        while True:
            with self._lock:
                if not self.client:
                    try:
                        self.connect(retries=1, delay=0)
                    except Exception:
                        pass
                try:
                    if self.client and not self.client.get_stream_status().output_active:
                        return True
                except Exception:
                    # 죽은 연결로 계속 물으면 시간만 간다 — 버리고 다음 바퀴에 새로 붙는다.
                    self.disconnect()
            if time.time() >= deadline:
                return False
            time.sleep(0.4)

    def is_streaming(self) -> bool:
        """현재 스트리밍 중인지 확인합니다. 연결이 없으면 한 번 붙여보고 판단.
        ⚠️ 연결이 죽어 있어 물음이 던지면 버리고 새로 붙어 **한 번 더** 묻는다 — 바로 '아니오' 라고 하면 송출 중인
           방송을 '주인 없는 상태' 로 보고 정리해 버린다(2026-10-08 검토)."""
        with self._lock:
            for attempt in range(2):
                if not self.client:
                    try:
                        self.connect(retries=1, delay=0)
                    except Exception:
                        return False
                try:
                    return bool(self.client.get_stream_status().output_active)
                except Exception:
                    self.disconnect()
            return False

    def wait_until_streaming(self, timeout: float = 8.0) -> bool:
        """OBS가 '실제로' 송출을 시작했는지 확인.

        start_stream()은 요청이 접수된 것만 성공으로 돌려준다. 방송 설정(서버·키)이
        비어 있으면 OBS는 요청을 받아놓고 GUI에 '설정된 방송 없음' 대화상자를 띄운 채
        송출을 시작하지 않는다. 그러면 서버만 '라이브'라고 믿는 유령 상태가 되고,
        대화상자가 OBS를 붙잡아 이후 WebSocket 요청까지 207로 막힌다(실제로 겪음).
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.is_streaming():
                return True
            time.sleep(0.4)
        return False

    # ── 리플레이 버퍼 (하이라이트 클립) ───────────────────────────
    # ⚠️ OBS 사전 설정 필요: 설정 → 출력 → 리플레이 버퍼 활성화 + 최대 길이 60~90초.
    #    (길이는 websocket으로 못 바꿈 — OBS에서 미리 설정해야 함.)
    def start_replay_buffer(self):
        """리플레이 버퍼 시작 — 최근 N초를 상시 메모리에 유지."""
        with self._lock:
            if not self.client:
                self.connect()
            try:
                st = self.client.get_replay_buffer_status()
                if st.output_active:
                    return
            except Exception:
                pass
            try:
                self.client.start_replay_buffer()
                logger.info("🎬 리플레이 버퍼 시작됨 (하이라이트 대기)")
            except Exception as e:
                logger.warning(f"리플레이 버퍼 시작 실패 (OBS 설정에서 활성화 필요): {e}")

    def stop_replay_buffer(self):
        """리플레이 버퍼 종료."""
        with self._lock:
            if not self.client:
                return
            try:
                st = self.client.get_replay_buffer_status()
                if st.output_active:
                    self.client.stop_replay_buffer()
                    logger.info("리플레이 버퍼 종료됨")
            except Exception as e:
                logger.warning(f"리플레이 버퍼 종료 중 오류: {e}")

    def is_replay_buffer_active(self) -> bool:
        """리플레이 버퍼가 켜져 있는지 — 빠른 확인(즉시 에러 피드백용)."""
        with self._lock:
            if not self.client:
                try:
                    self.connect()
                except Exception:
                    return False
            try:
                return bool(self.client.get_replay_buffer_status().output_active)
            except Exception:
                return False

    def ensure_replay_buffer(self) -> bool:
        """리플레이 버퍼가 '돌고 있게' 보장 — 꺼져 있으면 켠다.
        키프알라이브 루프에서 주기적으로 호출(라이브 여부와 무관하게 항상 대기 상태 유지).
        OBS가 안 떠 있으면 조용히 False(로그 스팸 방지 — 재시도 1회, 대기 없음)."""
        with self._lock:
            if not self.client:
                try:
                    self.connect(retries=1, delay=0)
                except Exception:
                    return False
            try:
                st = self.client.get_replay_buffer_status()
                if st.output_active:
                    return True
            except Exception:
                # 연결이 끊겼을 수 있음 — 클라이언트 리셋 후 다음 사이클에 재연결
                self.client = None
                return False
            # 버퍼가 꺼져 있으면 켜기 시도 (OBS 설정에서 리플레이 버퍼 활성화돼 있어야 성공)
            try:
                self.client.start_replay_buffer()
            except Exception:
                return False
        # start 직후엔 output_active가 곧바로 안 잡힐 수 있어 짧게 확인
        for _ in range(6):
            time.sleep(0.3)
            with self._lock:
                try:
                    if self.client and self.client.get_replay_buffer_status().output_active:
                        return True
                except Exception:
                    break
        return False

    def _last_replay_path(self) -> str:
        """OBS 가 기억하는 '마지막으로 저장이 끝난' 리플레이 경로. 없거나 실패하면 빈 문자열."""
        try:
            resp = self.client.get_last_replay_buffer_replay()
            return getattr(resp, "saved_replay_path", "") or ""
        except Exception:
            return ""

    def save_replay_buffer(self, timeout_s: float = 15.0) -> str:
        """버퍼를 파일로 저장하고 **이번에 저장된** 파일 경로를 반환 (실패 시 빈 문자열).
        폴링 sleep 동안엔 락을 놓아 키프알라이브 등 다른 OBS 호출을 막지 않는다.

        ⚠️ GetLastReplayBufferReplay 는 '마지막으로 저장이 **끝난**' 파일이다. 방금 요청한
           저장이 아직 디스크에 쓰이는 중이면 **직전 하이라이트의 경로**를 돌려준다.
           예전엔 0.5초 뒤 받은 첫 경로를 그대로 썼다 — 90초 버퍼는 쓰는 데 그보다 오래 걸려서
           Match25 제목·선수에 **앞서 저장한 Match21 클립**이 붙어 올라갔다(2026-10-05 신고:
           "하이라이트 저장한건데 동영상은 해당 동영상이 아니네?").
           그래서 저장 전 경로를 기억해 두고 **그와 다른 경로**(또는 같은 이름을 덮어쓴 새 파일)가
           나올 때까지만 기다린다. 끝내 안 바뀌면 빈 문자열 — 엉뚱한 클립을 올리느니 안 올린다."""
        with self._replay_save_lock:
            return self._save_replay_buffer(timeout_s)

    def _save_replay_buffer(self, timeout_s: float) -> str:
        with self._lock:
            if not self.client:
                self.connect()
            before = self._last_replay_path()
            started = time.time()
            try:
                self.client.save_replay_buffer()
            except Exception as e:
                logger.error(f"리플레이 저장 실패: {e}")
                return ""
        # 저장은 비동기 — 새 파일 경로가 잡힐 때까지 폴링
        polls = max(1, int(timeout_s / 0.5))
        for _ in range(polls):
            time.sleep(0.5)
            with self._lock:
                path = self._last_replay_path()
            if not path:
                continue
            if path != before or self._written_since(path, started):
                logger.info(f"🎬 하이라이트 저장됨: {path}")
                return path
        logger.error(
            f"새 리플레이 파일이 {timeout_s:.0f}초 안에 안 잡혔어요 — 직전 클립을 잘못 올리지 않도록 건너뜁니다"
            f" (마지막 파일: {before or '없음'})"
        )
        return ""

    @staticmethod
    def _written_since(path: str, started: float) -> bool:
        """같은 이름으로 덮어쓴 경우 — 파일이 저장 요청 뒤에 쓰였으면 새 클립이다."""
        try:
            return os.path.getmtime(path) >= started - 1
        except OSError:
            return False

    # ── 씬 전환 (선택 사항) ───────────────────────────────────────
    def switch_scene(self, scene_name: str):
        """특정 씬으로 전환합니다."""
        with self._lock:
            if not self.client:
                self.connect()
            try:
                self.client.set_current_program_scene(scene_name)
                logger.info(f"씬 전환: {scene_name}")
            except Exception as e:
                logger.warning(f"씬 전환 실패: {e}")
