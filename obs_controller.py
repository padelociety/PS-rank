"""
OBS WebSocket Controller
OBS Studio를 원격으로 제어합니다 (obs-websocket 5.x)

필요 조건:
  - OBS Studio 28 이상
  - OBS → 도구 → WebSocket 서버 설정 → 활성화
  - 포트: 4455 (기본값)
"""

import logging
import time

logger = logging.getLogger(__name__)


class OBSController:
    def __init__(self, config: dict):
        obs_cfg = config.get('obs', {})
        self.host = obs_cfg.get('host', 'localhost')
        self.port = obs_cfg.get('port', 4455)
        self.password = obs_cfg.get('password', '')
        self.client = None

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

    def disconnect(self):
        """연결을 끊습니다."""
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
        if not self.client:
            self.connect()

        self.client.set_stream_service_settings(
            stream_service_type='rtmp_custom',
            stream_service_settings={
                'server': rtmp_url,
                'key': stream_key,
                'use_auth': False,
            }
        )
        logger.info(f"✅ OBS 스트림 설정 완료 ({rtmp_url})")

    # ── 스트리밍 시작/종료 ────────────────────────────────────────
    def start_stream(self):
        """OBS 스트리밍을 시작합니다."""
        if not self.client:
            self.connect()

        status = self.client.get_stream_status()
        if status.output_active:
            logger.info("이미 스트리밍 중이에요.")
            return

        self.client.start_stream()
        logger.info("▶️  OBS 스트리밍 시작됨")

    def stop_stream(self):
        """OBS 스트리밍을 종료합니다."""
        if not self.client:
            return

        try:
            status = self.client.get_stream_status()
            if not status.output_active:
                logger.info("스트리밍이 이미 종료됐어요.")
                return
            self.client.stop_stream()
            logger.info("⏹️  OBS 스트리밍 종료됨")
        except Exception as e:
            logger.warning(f"스트리밍 종료 중 오류: {e}")
        finally:
            self.disconnect()

    def is_streaming(self) -> bool:
        """현재 스트리밍 중인지 확인합니다."""
        if not self.client:
            return False
        try:
            return self.client.get_stream_status().output_active
        except Exception:
            return False

    # ── 리플레이 버퍼 (하이라이트 클립) ───────────────────────────
    # ⚠️ OBS 사전 설정 필요: 설정 → 출력 → 리플레이 버퍼 활성화 + 최대 길이 60~90초.
    #    (길이는 websocket으로 못 바꿈 — OBS에서 미리 설정해야 함.)
    def start_replay_buffer(self):
        """리플레이 버퍼 시작 — 최근 N초를 상시 메모리에 유지."""
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
        if not self.client:
            try:
                self.connect()
            except Exception:
                return False
        try:
            return bool(self.client.get_replay_buffer_status().output_active)
        except Exception:
            return False

    def save_replay_buffer(self) -> str:
        """버퍼를 파일로 저장하고 저장된 파일 경로를 반환 (실패 시 빈 문자열)."""
        if not self.client:
            self.connect()
        try:
            self.client.save_replay_buffer()
        except Exception as e:
            logger.error(f"리플레이 저장 실패: {e}")
            return ""
        # 저장은 비동기 — 파일 경로가 잡힐 때까지 짧게 폴링 (최대 ~5초)
        for _ in range(10):
            time.sleep(0.5)
            try:
                resp = self.client.get_last_replay_buffer_replay()
                path = getattr(resp, "saved_replay_path", "") or ""
                if path:
                    logger.info(f"🎬 하이라이트 저장됨: {path}")
                    return path
            except Exception:
                continue
        logger.error("리플레이 저장 경로를 못 받았어요 (OBS 버전 확인)")
        return ""

    # ── 씬 전환 (선택 사항) ───────────────────────────────────────
    def switch_scene(self, scene_name: str):
        """특정 씬으로 전환합니다."""
        if not self.client:
            self.connect()
        try:
            self.client.set_current_program_scene(scene_name)
            logger.info(f"씬 전환: {scene_name}")
        except Exception as e:
            logger.warning(f"씬 전환 실패: {e}")
