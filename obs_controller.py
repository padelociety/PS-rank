"""
OBS WebSocket Controller
OBS Studio를 원격으로 제어합니다 (obs-websocket 5.x)

필요 조건:
  - OBS Studio 28 이상
  - OBS → 도구 → WebSocket 서버 설정 → 활성화
  - 포트: 4455 (기본값)
"""

import json
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
        # /health 가 보여 줄 OBS 자동 재연결 설정(연결할 때마다 맞춘다 — ensure_reconnect).
        self.reconnect_status = {'reconnect': None, 'retry_delay': None, 'max_retries': None, 'changed': [], 'error': ''}
        # OBS 인코딩 설정과 고칠 칸(2026-10-24 — refresh_encoding). /health 의 obs_encoding · 방송 상태 문장의 '고칠 것'.
        #   advice: None = 아직 못 읽음 · [] = 권장값 그대로
        self.encoding_status = {'config': {}, 'summary': '', 'advice': None, 'error': '', 'at': 0.0}
        # OBS 설정 폴더 — 프로필의 streamEncoder.json(프리셋 등 웹소켓으로 못 읽는 칸)을 여기서 읽는다.
        appdata = os.environ.get('APPDATA') or ''
        self.obs_config_dir = obs_cfg.get('config_dir') or (os.path.join(appdata, 'obs-studio') if appdata else '')
        # 카메라(네트워크 미디어 소스) 판정 캐시 — {입력 이름: True/False}. 설정을 매번 읽지 않게(5분마다 다시).
        self._net_inputs = {}
        self._net_inputs_at = 0.0
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
                    self.ensure_reconnect()
                    self.refresh_encoding(force=True)
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

    # ── 자동 재연결 ───────────────────────────────────────────────
    # OBS 설정 → 고급 → '자동으로 다시 연결'. 꺼져 있으면 업로드가 한 번만 흔들려도 송출이 **멈추고 그대로** 끝난다
    # (2026-10-21 *"유투브가 자주 끊긴대"*). OBS 는 송출을 시작할 때마다 이 값을 읽으므로, 연결할 때마다 맞춰 두면
    # 다음 송출부터 먹는다. 재시도 사이 간격은 OBS 가 늘려 가므로(1.5배씩) 25번이면 몇 분을 버틴다 — 그동안
    # 유튜브 방송은 자동 종료를 꺼 두어 다시 붙으면 같은 방송으로 이어진다(youtube_api).
    RECONNECT_WANT = {'Reconnect': 'true'}
    RETRY_DELAY_MAX = 10     # 이보다 길게 잡혀 있으면 3초로 — 첫 재시도까지 화면이 너무 오래 멈춘다
    RETRY_DELAY_SET = '3'
    MAX_RETRIES_MIN = 20     # 이보다 적으면 25로
    MAX_RETRIES_SET = '25'

    def _profile_param(self, name):
        resp = self.client.get_profile_parameter('Output', name)
        val = getattr(resp, 'parameter_value', None)
        if val in (None, ''):
            val = getattr(resp, 'default_parameter_value', None)
        return '' if val is None else str(val)

    def ensure_reconnect(self):
        """OBS '자동으로 다시 연결' 을 켜 둔다(필요하면 재시도 간격·횟수도). **연결될 때마다** 부른다(connect 안).
        ⚠️ 방송을 막지 않는다 — 오류는 로그와 reconnect_status 로만 남긴다."""
        status = {'reconnect': None, 'retry_delay': None, 'max_retries': None, 'changed': [], 'error': ''}
        with self._lock:
            if not self.client:
                status['error'] = 'OBS 미연결'
                self.reconnect_status = status
                return status
            try:
                cur = {n: self._profile_param(n) for n in ('Reconnect', 'RetryDelay', 'MaxRetries')}
                want = {}
                if cur['Reconnect'].strip().lower() not in ('true', '1'):
                    want['Reconnect'] = 'true'
                try:
                    if int(cur['RetryDelay'] or 0) > self.RETRY_DELAY_MAX:
                        want['RetryDelay'] = self.RETRY_DELAY_SET
                except ValueError:
                    want['RetryDelay'] = self.RETRY_DELAY_SET
                try:
                    if int(cur['MaxRetries'] or 0) < self.MAX_RETRIES_MIN:
                        want['MaxRetries'] = self.MAX_RETRIES_SET
                except ValueError:
                    want['MaxRetries'] = self.MAX_RETRIES_SET
                for name, val in want.items():
                    self.client.set_profile_parameter('Output', name, val)
                    status['changed'].append(f"{name}: {cur[name] or '(없음)'} → {val}")
                    cur[name] = val
                if status['changed']:
                    logger.info(f"🔁 OBS 자동 재연결 맞춤 — {', '.join(status['changed'])}")
                status['reconnect'] = cur['Reconnect'].strip().lower() in ('true', '1')
                status['retry_delay'] = int(cur['RetryDelay'] or 0) if str(cur['RetryDelay']).isdigit() else None
                status['max_retries'] = int(cur['MaxRetries'] or 0) if str(cur['MaxRetries']).isdigit() else None
            except Exception as e:
                status['error'] = f'자동 재연결 설정을 못 맞춤: {e}'
                logger.warning(f"⚠️ {status['error']} (OBS 설정 → 고급 → '자동으로 다시 연결' 을 직접 켜 주세요)")
        self.reconnect_status = status
        return status

    # ── 인코딩 설정 점검 (2026-10-24 — "라이브 인코딩 문제 아직도 계속된다") ───────────────
    # '인코딩이 밀린다' 는 알려 줬지만 OBS 의 어느 설정 때문인지는 아무도 안 봤다. 연결할 때와 방송 중 5분마다 읽어
    # stream_health.encoding_advice 로 고칠 칸을 고른다. ⚠️ 읽기만 한다 — 인코더·해상도는 바꾸지 않는다(바꾸려면 OBS 를
    # 다시 띄워야 하고, 이 PC 에 어떤 하드웨어 인코더가 있는지 서버는 모른다).
    ENCODING_REFRESH_S = 300
    ENC_KEYS = ('preset', 'preset2', 'multipass', 'lookahead', 'target_usage', 'rate_control', 'bitrate', 'keyint_sec')

    def _param(self, category, name):
        resp = self.client.get_profile_parameter(category, name)
        val = getattr(resp, 'parameter_value', None)
        if val in (None, ''):
            val = getattr(resp, 'default_parameter_value', None)
        return '' if val is None else str(val)

    @staticmethod
    def _ini_value(path, section, key):
        """OBS ini 한 칸(BOM·대소문자 무시) · 없으면 ''."""
        try:
            with open(path, encoding='utf-8-sig') as f:
                lines = f.read().splitlines()
        except OSError:
            return ''
        inside = False
        for line in lines:
            t = line.strip()
            if t.startswith('[') and t.endswith(']'):
                inside = t[1:-1].strip().lower() == section.lower()
                continue
            if inside and '=' in t:
                k, v = t.split('=', 1)
                if k.strip().lower() == key.lower():
                    return v.strip()
        return ''

    def _profile_dir(self, profile_name=''):
        """지금 프로필 폴더 — user.ini(OBS 31+)·global.ini 의 [Basic] ProfileDir → 이름이 같은 프로필 → 프로필이 하나뿐이면 그것.
        못 정하면 ''(여럿 중에 짐작하지 않는다)."""
        root = self.obs_config_dir
        profiles = os.path.join(root, 'basic', 'profiles') if root else ''
        if not profiles or not os.path.isdir(profiles):
            return ''
        for ini in ('user.ini', 'global.ini'):
            d = self._ini_value(os.path.join(root, ini), 'Basic', 'ProfileDir')
            if d and os.path.isdir(os.path.join(profiles, d)):
                return os.path.join(profiles, d)
        names = sorted(n for n in os.listdir(profiles) if os.path.isdir(os.path.join(profiles, n)))
        if profile_name:
            for n in names:
                if self._ini_value(os.path.join(profiles, n, 'basic.ini'), 'General', 'Name') == profile_name:
                    return os.path.join(profiles, n)
        return os.path.join(profiles, names[0]) if len(names) == 1 else ''

    def read_encoding(self):
        """OBS 인코딩 설정 → stream_health.encoding_advice 가 읽는 cfg. 잠금 안에서 부른다. 못 읽은 칸은 넣지 않는다."""
        import stream_health
        cfg = {'mode': self._param('Output', 'Mode') or 'Simple'}
        adv = cfg['mode'].strip().lower() == 'advanced'
        if adv:
            cfg['encoder'] = self._param('AdvOut', 'Encoder')
            cfg['rec_encoder'] = self._param('AdvOut', 'RecEncoder')
        else:
            cfg['encoder'] = self._param('SimpleOutput', 'StreamEncoder')
            cfg['rec_quality'] = self._param('SimpleOutput', 'RecQuality')
        try:
            v = self.client.get_video_settings()
            num = float(getattr(v, 'fps_numerator', 0) or 0)
            den = float(getattr(v, 'fps_denominator', 0) or 0)
            if num and den:
                cfg['fps'] = round(num / den, 2)
            cfg['base'] = [int(getattr(v, 'base_width', 0) or 0), int(getattr(v, 'base_height', 0) or 0)]
            cfg['output'] = [int(getattr(v, 'output_width', 0) or 0), int(getattr(v, 'output_height', 0) or 0)]
        except Exception:
            pass
        cfg['scale'] = self._param('Video', 'ScaleType')
        enc = {}
        kind = stream_health.encoder_kind(cfg.get('encoder'))
        if adv:
            # 고급 모드의 방송 인코더 세부(프리셋·2패스·룩어헤드)는 웹소켓으로 못 읽는다 — 프로필의 streamEncoder.json.
            name = ''
            try:
                name = str(getattr(self.client.get_profile_list(), 'current_profile_name', '') or '')
            except Exception:
                pass
            prof = self._profile_dir(name)
            path = os.path.join(prof, 'streamEncoder.json') if prof else ''
            if path and os.path.isfile(path):
                try:
                    with open(path, encoding='utf-8-sig') as f:
                        enc = json.load(f) or {}
                except (OSError, ValueError):
                    enc = {}
        else:
            simple_key = {'x264': ('preset', 'Preset'), 'nvenc': ('preset2', 'NVENCPreset2'),
                          'qsv': ('target_usage', 'QSVPreset'), 'amf': ('preset', 'AMDPreset')}.get(kind)
            if simple_key:
                enc[simple_key[0]] = self._param('SimpleOutput', simple_key[1])
        cfg['enc'] = {k: enc[k] for k in self.ENC_KEYS if isinstance(enc, dict) and k in enc and enc[k] not in (None, '')}
        cams = []
        for nm in self._network_inputs():
            try:
                st = getattr(self.client.get_input_settings(nm), 'input_settings', None) or {}
            except Exception:
                continue  # 그사이 지운 소스(목록은 5분 캐시) — 판정하지 않는다
            if stream_health.is_network_input(st) and st.get('hw_decode') is not True:
                cams.append(nm)
        cfg['sw_decode_cameras'] = cams
        return cfg

    def refresh_encoding(self, force=False):
        """인코딩 설정을 다시 읽어 encoding_status 를 고친다(5분에 한 번 · force 면 바로). **예외를 던지지 않는다.**
        못 읽으면 지난 값을 둔다(연결이 없을 때) 또는 error 를 적는다."""
        import stream_health
        cur = self.encoding_status or {}
        if not force and cur.get('at') and time.time() - cur['at'] < self.ENCODING_REFRESH_S:
            return cur
        with self._lock:
            if not self.client:
                return cur
            try:
                cfg = self.read_encoding()
                advice = stream_health.encoding_advice(cfg)
                new = {'config': cfg, 'summary': stream_health.encoding_summary(cfg), 'advice': advice,
                       'error': '', 'at': time.time()}
            except Exception as e:
                new = {'config': {}, 'summary': '', 'advice': None, 'error': f'인코딩 설정을 못 읽음: {e}', 'at': time.time()}
        before = (cur.get('summary'), [a.get('key') for a in (cur.get('advice') or [])], cur.get('error'))
        after = (new['summary'], [a.get('key') for a in (new['advice'] or [])], new['error'])
        self.encoding_status = new
        if before != after:
            if new['error']:
                logger.warning(f"⚠️ {new['error']}")
            else:
                logger.info(f"🎛️ OBS 인코딩: {new['summary'] or '?'}")
                for a in new['advice'] or []:
                    logger.warning(f"   ⚠️ 고칠 것: {a.get('text')}")
                if new['advice'] == []:
                    logger.info('   ✅ 인코딩 설정은 권장값이에요')
        return new

    # ── 송출 숫자 (방송 상태 감시) ─────────────────────────────────
    NET_INPUT_RECHECK_S = 300

    def _network_inputs(self):
        """네트워크 카메라(RTSP 등)인 미디어 소스 이름들. 5분마다 다시 본다. 잠금 안에서 부른다."""
        import stream_health
        now = time.time()
        if self._net_inputs and now - self._net_inputs_at < self.NET_INPUT_RECHECK_S:
            return [n for n, net in self._net_inputs.items() if net]
        found = {}
        try:
            names = self._input_names(self.client.get_input_list('ffmpeg_source'))
        except Exception:
            names = []
        for name in names:
            try:
                st = getattr(self.client.get_input_settings(name), 'input_settings', None) or {}
            except Exception:
                st = {}
            found[name] = stream_health.is_network_input(st)
        self._net_inputs = found
        self._net_inputs_at = now
        return [n for n, net in found.items() if net]

    def stream_stats(self):
        """방송 상태 감시용 숫자 한 벌(stream_health.evaluate 가 읽는 모양) · 못 읽으면 None. **예외를 던지지 않는다.**
        누적 숫자(bytes·프레임)는 부르는 쪽이 10초 차이로 본다."""
        with self._lock:
            if not self.client:
                try:
                    self.connect(retries=1, delay=0)
                except Exception:
                    return None
            try:
                st = self.client.get_stream_status()
            except Exception:
                self.disconnect()
                return None
            out = {
                'active': bool(getattr(st, 'output_active', False)),
                'reconnecting': bool(getattr(st, 'output_reconnecting', False)),
                'congestion': float(getattr(st, 'output_congestion', 0) or 0),
                'bytes': int(getattr(st, 'output_bytes', 0) or 0),
                'net_skipped': int(getattr(st, 'output_skipped_frames', 0) or 0),
                'net_total': int(getattr(st, 'output_total_frames', 0) or 0),
                'media': {},
            }
            try:
                g = self.client.get_stats()
                out.update({
                    'cpu': float(getattr(g, 'cpu_usage', 0) or 0),
                    'fps': float(getattr(g, 'active_fps', 0) or 0),
                    'enc_skipped': int(getattr(g, 'output_skipped_frames', 0) or 0),
                    'enc_total': int(getattr(g, 'output_total_frames', 0) or 0),
                    'render_skipped': int(getattr(g, 'render_skipped_frames', 0) or 0),
                    'render_total': int(getattr(g, 'render_total_frames', 0) or 0),
                })
            except Exception:
                pass
            for name in self._network_inputs():
                try:
                    out['media'][name] = str(getattr(self.client.get_media_input_status(name), 'media_state', '') or '')
                except Exception:
                    out['media'][name] = 'OBS_MEDIA_STATE_ERROR'
            return out

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
