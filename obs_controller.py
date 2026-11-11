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
import re
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
        # 마지막 송출 끄기가 어떻게 끝났나(2026-10-25 — '종료 중' 에서 멈춤). /health 의 obs_stop.
        #   stopped · forced(두 번째 끄기 = OBS 강제 종료를 보냈나) · reconnecting(끌 때 재연결 중이었나) · seconds · log(멈췄을 때 OBS 로그 끝)
        self.stop_report = None
        # OBS 설정 폴더 — 프로필의 streamEncoder.json(프리셋 등 웹소켓으로 못 읽는 칸)을 여기서 읽는다.
        appdata = os.environ.get('APPDATA') or ''
        self.obs_config_dir = obs_cfg.get('config_dir') or (os.path.join(appdata, 'obs-studio') if appdata else '')
        # 카메라(네트워크 미디어 소스) 판정 캐시 — {입력 이름: True/False}. 설정을 매번 읽지 않게(5분마다 다시).
        self._net_inputs = {}
        self._net_inputs_at = 0.0
        # 방송을 켤 때마다 카메라(네트워크 미디어 소스)를 다시 시작한다(2026-11-07 — refresh_cameras).
        #   camera_refresh_on_start : 기본 켬. false 면 건드리지 않는다.
        self.camera_refresh_on_start = obs_cfg.get('camera_refresh_on_start', True) is not False
        # /health 가 보여 줄 마지막 카메라 새로 고침 결과.
        self.camera_refresh_status = None
        # OBS 를 껐다 켜는 길(2026-11-11 — 인코더가 초록으로 굳으면 OBS 를 다시 켜야 풀렸다 · restart_obs_process).
        #   obs_task : OBS 를 띄우는 작업 스케줄러 이름(SETUP 3절 — 리플레이 버퍼까지 켜서 뜬다).
        self.obs_task = str(obs_cfg.get('obs_task') or 'PS-OBS-Studio')
        self.obs_restart_status = None
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

    # ── 카메라 새로 고침 (방송 켜기 직전) ─────────────────────────
    # 2026-11-07 "라이브 소리 또 느려짐" — RTSP 카메라(ffmpeg_source)는 오래 켜 두면 소리가 화면보다 조금씩 더 밀린다.
    # 고정 싱크 값(apply_audio_sync)은 처음 어긋남만 맞추고 밀리는 것은 못 막는다. 그래서 방송을 켤 때마다 카메라 소스를
    # 다시 시작해 매 경기를 처음 상태로 시작한다(OBS 에서 소스를 껐다 켜는 것과 같다 · 싱크 오프셋은 그대로 남는다).
    CAMERA_RESTART_ACTION = 'OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART'
    CAMERA_READY_WAIT_S = 8.0

    def refresh_cameras(self, wait_s: float = None):
        """네트워크 카메라 소스를 다시 시작하고 영상이 다시 들어올 때까지(최대 wait_s) 기다린다.
        **예외를 던지지 않는다** — 새로 고침을 못 해도 방송은 켠다(소리가 밀린 방송이 안 켜진 방송보다 낫다)."""
        status = {'restarted': [], 'ready': [], 'error': '', 'seconds': 0.0, 'frame': []}
        if not self.camera_refresh_on_start:
            status['error'] = '꺼 둠(camera_refresh_on_start: false)'
            self.camera_refresh_status = status
            return status
        wait_s = self.CAMERA_READY_WAIT_S if wait_s is None else wait_s
        t0 = time.time()
        with self._lock:
            if not self.client:
                status['error'] = 'OBS 미연결'
                self.camera_refresh_status = status
                return status
            self._net_inputs_at = 0.0   # 소스를 바꿨을 수 있다 — 지금 다시 본다
            cams = self._network_inputs()
            if not cams:
                status['error'] = '네트워크 카메라 소스를 못 찾음'
            for name in cams:
                err = self._restart_media(name)
                if err:
                    status['error'] = f"'{name}' 다시 시작 실패: {err}"
                else:
                    status['restarted'].append(name)
        # 영상이 다시 들어오길 기다린다 — 잠금을 놓고 짧게 묻는다(키프알라이브·하이라이트를 막지 않게).
        pending = list(status['restarted'])
        deadline = t0 + max(0.0, wait_s)
        while pending and time.time() < deadline:
            time.sleep(0.5)
            for name in list(pending):
                try:
                    with self._lock:
                        st = self.client.get_media_input_status(name) if self.client else None
                    state = str(getattr(st, 'media_state', '') or '')
                except Exception:
                    state = ''
                if state.endswith('PLAYING'):
                    pending.remove(name)
                    status['ready'].append(name)
        if pending and not status['error']:
            status['error'] = f"{wait_s:.0f}초 안에 영상이 다시 안 들어옴: {', '.join(pending)}"
        # 다시 연 카메라가 한 가지 색(초록·검정)이면 방송을 켜기 전에 고친다(2026-11-11 "또 유투브 초록화면이야").
        status['frame'] = self._fix_uniform_cameras(status['ready'])
        status['seconds'] = round(time.time() - t0, 1)
        if status['restarted']:
            logger.info(f"📷 카메라 새로 고침: {', '.join(status['restarted'])} ({status['seconds']}초)")
        for f in status['frame']:
            (logger.info if f['after'] == '' else logger.warning)(
                f"📷 켜기 전 화면 검사: {f['name']} — {f['label']} → "
                + ('하드웨어 디코딩 끔' if f['action'] == 'hw_off' else '다시 열기' if f['action'] == 'restart' else '고치지 못함')
                + (f" → {f['after'] or '정상'}" if f['checked'] else ''))
        if status['error']:
            logger.warning(f"⚠️ 카메라 새로 고침 — {status['error']}")
        self.camera_refresh_status = status
        return status

    # 켜기 전 화면 검사 — 다시 연 직후 첫 화면이 잠깐 비는 것은 넘기려고 한 번 쉬고, 두 번 연속 단색일 때만 손댄다.
    CAMERA_FRAME_SETTLE_S = 1.5
    CAMERA_FRAME_RECHECK_S = 1.0
    CAMERA_FIX_SETTLE_S = 4.0

    def _uniform_verdicts(self, names):
        import stream_health
        out = {}
        for name in names:
            v = stream_health.frame_verdict(self.frame_pixels(name))
            if v.get('ok') and v.get('uniform'):
                out[name] = v
        return out

    def _fix_uniform_cameras(self, names):
        """다시 연 카메라를 작게 찍어 한 가지 색이면 고친다 — 하드웨어 디코딩이 켜져 있으면 끄고(소스가 다시 열린다),
        아니면 한 번 더 다시 연다. 고친 뒤 한 번 더 찍어 결과를 적는다. [{name, label, action, checked, after}] ·
        **예외를 던지지 않는다**(찍지 못하면 아무것도 안 한다 — PIL 이 없거나 OBS 가 스크린샷을 못 주면 [])."""
        import stream_health
        fixed = []
        if not names:
            return fixed
        try:
            time.sleep(self.CAMERA_FRAME_SETTLE_S)
            bad = self._uniform_verdicts(names)
            if bad:
                time.sleep(self.CAMERA_FRAME_RECHECK_S)
                bad = self._uniform_verdicts(list(bad))
            if not bad:
                return fixed
            hw = dict(self.camera_sources())
            for name, v in bad.items():
                if hw.get(name) is True and self.set_hw_decode(name, False):
                    action = 'hw_off'
                elif self.restart_camera(name):
                    action = 'restart'
                else:
                    action = ''
                fixed.append({'name': name, 'label': stream_health.frame_label(v), 'action': action,
                              'checked': False, 'after': ''})
            if any(f['action'] for f in fixed):
                time.sleep(self.CAMERA_FIX_SETTLE_S)
                for f in fixed:
                    if not f['action']:
                        continue
                    a = stream_health.frame_verdict(self.frame_pixels(f['name']))
                    f['checked'] = bool(a.get('ok'))
                    f['after'] = stream_health.frame_label(a) if a.get('ok') and a.get('uniform') else ''
        except Exception as e:
            logger.warning(f"켜기 전 화면 검사 오류(무시): {e}")
        return fixed

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

    # ── 방송 화면 검사 · 카메라 다시 열기 (2026-11-01 — 초록 화면) ─────────────
    # 송출 숫자·카메라 '재생 중' 은 다 정상인데 유튜브엔 초록 화면만 나갔다. 화면을 직접 작게 찍어 본다.
    def camera_sources(self):
        """카메라 소스 [(이름, 하드웨어 디코딩 켜짐 True/False)] — 네트워크 미디어 소스(RTSP 등). 없으면 미디어 소스 전부.
        **예외를 던지지 않는다**(못 읽으면 [])."""
        import stream_health
        with self._lock:
            if not self.client:
                try:
                    self.connect(retries=1, delay=0)
                except Exception:
                    return []
            try:
                names = self._input_names(self.client.get_input_list('ffmpeg_source'))
            except Exception:
                return []
            out, rest = [], []
            for name in names:
                try:
                    st = getattr(self.client.get_input_settings(name), 'input_settings', None) or {}
                except Exception:
                    continue
                hw = st.get('hw_decode') is True   # 칸이 없으면 OBS 기본값(꺼짐)
                (out if stream_health.is_network_input(st) else rest).append((name, hw))
            return out or rest

    def program_scene(self) -> str:
        """지금 방송에 나가는 장면 이름 · 못 읽으면 ''."""
        with self._lock:
            if not self.client:
                return ''
            try:
                r = self.client.get_current_program_scene()
            except Exception:
                return ''
            return str(getattr(r, 'current_program_scene_name', '') or getattr(r, 'scene_name', '') or '')

    def frame_pixels(self, source: str, width: int = 32, height: int = 18):
        """소스(또는 장면) 화면을 작게 찍어 RGB 점 목록으로 · 못 찍으면 None. **예외를 던지지 않는다.**"""
        import base64
        import io
        if not source:
            return None
        try:
            from PIL import Image
        except Exception:
            return None
        with self._lock:
            if not self.client:
                return None
            try:
                r = self.client.get_source_screenshot(source, 'png', int(width), int(height), -1)
            except Exception:
                return None
        data = str(getattr(r, 'image_data', '') or '')
        if ',' in data[:64]:
            data = data.split(',', 1)[1]
        try:
            img = Image.open(io.BytesIO(base64.b64decode(data))).convert('RGB')
            return list(img.getdata())
        except Exception:
            return None

    def _restart_media(self, name: str) -> str:
        """미디어 소스 RESTART 한 번 — 카메라를 다시 여는 곳(켜기 전 새로 고침 · 화면 검사 · [📷 카메라 다시 연결])은 전부
        여기를 지난다. 성공이면 '' · 실패면 이유."""
        with self._lock:
            if not self.client:
                return 'OBS 미연결'
            try:
                self.client.trigger_media_input_action(name, self.CAMERA_RESTART_ACTION)
                return ''
            except Exception as e:
                return str(e) or e.__class__.__name__

    def restart_camera(self, name: str) -> bool:
        """미디어 소스를 다시 연다(OBS 소스 속성 [확인] 과 같은 효과 — 디코더를 새로 만든다)."""
        err = self._restart_media(name)
        if err:
            logger.warning(f"⚠️ 카메라 다시 열기 실패({name}): {err}")
            return False
        logger.info(f"📷 카메라 다시 열기: {name}")
        return True

    def set_hw_decode(self, name: str, on: bool) -> bool:
        """소스 속성 '가능한 경우 하드웨어 디코딩 사용' 을 바꾼다(바꾸면 OBS 가 소스를 다시 연다 · OBS 장면 파일에 남는다)."""
        with self._lock:
            if not self.client:
                return False
            try:
                self.client.set_input_settings(name, {'hw_decode': bool(on)}, True)
                logger.info(f"📷 하드웨어 디코딩 {'켬' if on else '끔'}: {name}")
                self._net_inputs_at = 0.0   # 인코딩 점검의 카메라 목록도 새로
                return True
            except Exception as e:
                logger.warning(f"⚠️ 하드웨어 디코딩 바꾸기 실패({name}): {e}")
                return False

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

    # 끄기 기다림(2026-10-25). OBS 의 '방송 중단' 은 남은 영상을 다 보내고 멈춘다 — 업로드가 밀려 있으면 그게 길어지고
    # (OBS 기본 상한 30초), 그동안 OBS 버튼은 '종료 중' 이다. 예전엔 10초만 기다리고 YouTube 방송을 끝냈는데, 그러면 아직
    # 보내던 연결이 끊겨 OBS 가 **'종료 중' 인 채로 다시 붙으려 하다 멈췄고**, 다음 경기 켜기가 '이전 방송을 계속 내보낸다' 로
    # 막혔다("라이브가 하나 끝나고 두번째 리그할때 연결이 안되네? Obs보면 종료중이라고 뜨고 종료가 안되더라고").
    # 그래서: 조금 기다려 보고(경기는 이미 끝났다 — 마지막 몇 초는 버려도 된다) 안 멈추면 **한 번 더 끈다** — '종료 중' 에
    # 다시 누르는 끄기는 OBS 가 **강제 종료**로 받는다(남은 영상을 버리고 바로 멈춘다 · OBS 화면에서 [방송 중단] 을 한 번 더
    # 누르는 것과 같다). YouTube 방송은 그 **뒤에** 끝낸다(stream_server — 이 함수가 돌아온 다음).
    STOP_GRACE_S = 5.0
    STOP_FORCE_WAIT_S = 15.0

    def stop_stream(self, timeout: float = None, grace: float = None) -> bool:
        """OBS 송출을 끈다. **정말 꺼진 걸 확인했으면 True**, 못 껐거나 확인을 못 했으면 False.
        grace 초 안에 안 멈추면 한 번 더 끈다(OBS 강제 종료) · 그 뒤 timeout 초를 더 기다린다.

        ⚠️ 2026-10-08: 예전엔 연결(client)이 없으면 **아무것도 안 하고** 돌아갔다. 키프알라이브가
           OBS 응답 오류 한 번에 client 를 비우고(ensure_replay_buffer) 20초 뒤에야 다시 붙는데, 그
           사이에 경기가 끝나면 OBS 는 지난 방송 키로 계속 내보내고 서버는 '껐다' 고 믿었다.
           그래서 연결이 없으면 붙여서 끄고, 송출이 실제로 멈출 때까지 기다린다(StopStream 은 요청만
           받고 돌아온다 — 바로 다음 순간엔 아직 송출 중이다)."""
        timeout = self.STOP_FORCE_WAIT_S if timeout is None else float(timeout)
        grace = self.STOP_GRACE_S if grace is None else float(grace)
        t0 = time.time()
        report = {'at': t0, 'reached': True, 'stopped': False, 'forced': False, 'reconnecting': None, 'seconds': 0.0,
                  'log': []}
        sent = self._send_stop(report)
        if sent is None:  # 이미 꺼져 있었다
            report.update(stopped=True)
            self.stop_report = report
            return True
        if not sent:
            # OBS 에 닿지 않는다 — 끄기도 강제 종료도 못 보낸다. 예전처럼 바로 False(기다리면 태블릿 기다림을 넘긴다).
            report.update(reached=False, seconds=round(time.time() - t0, 1))
            self.stop_report = report
            return False
        stopped = self.wait_until_stopped(min(grace, timeout) if grace > 0 else 0)
        if not stopped:
            # '종료 중' 에서 안 끝난다 — 한 번 더 끈다(OBS 는 이걸 강제 종료로 받는다).
            logger.warning(f"⚠️ OBS 가 {grace:.0f}초 안에 '종료 중' 을 못 끝냈어요"
                           + (" (재연결 중이었어요)" if report.get('reconnecting') else '')
                           + " — 한 번 더 끕니다(강제 종료)")
            again = self._send_stop(report)
            if again is None:
                stopped = True
            else:
                report['forced'] = bool(again)
                stopped = self.wait_until_stopped(timeout)
        report['reached'] = True
        report['stopped'] = bool(stopped)
        report['seconds'] = round(time.time() - t0, 1)
        if stopped:
            logger.info("⏹️  OBS 스트리밍 종료됨" + (" (강제 종료)" if report['forced'] else ''))
        else:
            logger.error(f"❌ OBS 송출이 강제 종료 뒤에도 멈추지 않았어요({report['seconds']:.0f}초) — OBS 가 '종료 중' 에서 "
                         "멈춘 것 같아요. OBS 를 껐다 켜 주세요")
            report['log'] = self.obs_log_lines(60)
            for line in report['log']:
                logger.error(f"   OBS 로그 │ {line}")
        self.stop_report = report
        # 예전처럼 끝나면 연결을 놓는다(다음 시작은 새로 붙는다).
        self.disconnect()
        return stopped

    def _send_stop(self, report=None):
        """StopStream 을 한 번 보낸다. True = 보냄 · False = 못 보냄 · None = 이미 꺼져 있음.
        ⚠️ 연결이 **있는데 죽어 있으면**(소켓 끊김·응답 꼬임) 요청이 던진다 — 그때는 연결을 버리고 새로 붙어
           한 번 더 보낸다(2026-10-08 검토). 비어 있는지만 보면 죽은 연결로 끄기를 못 보내고 OBS 는 계속 송출한다."""
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
                        return None
                    if report is not None:
                        rec = getattr(status, 'output_reconnecting', None)
                        if isinstance(rec, bool):
                            report['reconnecting'] = bool(report.get('reconnecting')) or rec
                    self.client.stop_stream()
                    logger.info("⏹️  OBS 스트리밍 종료 요청")
                    return True
                except Exception as e:
                    logger.warning(f"스트리밍 종료 중 오류 — 다시 붙어서 한 번 더: {e}")
                    self.disconnect()
        logger.error("❌ OBS 에 끄기를 보내지 못했어요")
        return False

    # OBS 로그 읽기(2026-10-25). ⚠️ OBS 는 RTSP 카메라 주소를 **비밀번호째** 로그에 적는다(`rtsp://admin:…@192.168.1.7`) —
    # 서버 로그·점검 화면으로 옮길 때는 반드시 가린다(`mask_secrets`). 사람이 그 화면을 대화·채팅에 붙여 넣는다.
    _SECRET_URL = re.compile(r'([a-z][a-z0-9+.-]*://[^:/@\s]+):[^@\s]+@', re.IGNORECASE)
    _LOG_KEYS = ('rtmp', 'stream', 'output', 'reconnect', 'stopping', 'stopped', 'encoder', 'qsv', 'nvenc', 'amf',
                 'replay', 'error', 'warning', 'disconnect', 'socket', 'shutdown', 'timeout', 'failed')
    # 시작할 때 늘 찍히는 줄(모듈·장치 목록) — 멈춘 이유와 상관없다.
    _LOG_NOISE = ('failed to initialize module', 'failed to load', 'loadlibrary failed', 'version info size',
                  '[obs_module_', 'not supported', '.dll', 'available encoders', 'video encoders', 'audio encoders',
                  'output resolution', 'output 0:', 'coreaudio')
    LOG_LOOKBACK_FILES = 6

    @classmethod
    def mask_secrets(cls, text: str) -> str:
        """주소 안의 '아이디:비밀번호@' 를 '아이디:***@' 로."""
        return cls._SECRET_URL.sub(r'\1:***@', str(text or ''))

    def obs_log_file(self) -> str:
        """읽을 OBS 로그 파일 — 최근 파일들 중 **방송을 한 번이라도 켠**('==== Streaming Start') 가장 새 것.
        OBS 를 다시 띄우면 새 파일이 생기는데, 멈췄던 장면은 그 앞 파일에 있다(2026-10-25 — 다시 띄운 뒤 점검하니
        새 파일만 읽어 멈춘 장면을 놓쳤다). 방송한 파일이 없으면 가장 새 파일 · 못 읽으면 ''."""
        root = self.obs_config_dir
        logs = os.path.join(root, 'logs') if root else ''
        try:
            files = [os.path.join(logs, n) for n in os.listdir(logs) if n.lower().endswith('.txt')]
        except OSError:
            return ''
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for path in files[:self.LOG_LOOKBACK_FILES]:
            try:
                with open(path, encoding='utf-8', errors='replace') as f:
                    if '==== Streaming Start' in f.read():
                        return path
            except OSError:
                continue
        return files[0] if files else ''

    def obs_log_lines(self, limit: int = 60, path: str = None) -> list:
        """OBS 로그(obs_log_file)의 **마지막 방송부터** — 송출·출력·인코더·재연결 줄만 · 비밀번호는 가린다. 못 읽으면 [].
        '종료 중' 에서 멈췄을 때 그 순간 OBS 가 무엇을 하고 있었는지 남기려고 쓴다(읽기만)."""
        path = self.obs_log_file() if path is None else path
        if not path:
            return []
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                lines = f.read().splitlines()
        except OSError:
            return []
        starts = [i for i, ln in enumerate(lines) if '==== Streaming Start' in ln]
        if starts:
            lines = lines[starts[-1]:]
        picked = [ln for ln in lines[-600:]
                  if any(k in ln.lower() for k in self._LOG_KEYS) and not any(n in ln.lower() for n in self._LOG_NOISE)]
        return [self.mask_secrets(ln.rstrip()) for ln in picked[-max(1, int(limit)):]]

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

    def save_replay_buffer_nowait(self, timeout_s: float = 15.0) -> str:
        """송출 영상 검사용 저장 — 하이라이트가 저장 중이면 기다리지 않고 '' (검사는 다음에 · 하이라이트가 늦어지면 안 된다)."""
        if not self._replay_save_lock.acquire(blocking=False):
            return ''
        try:
            return self._save_replay_buffer(timeout_s)
        finally:
            self._replay_save_lock.release()

    # ── OBS 다시 켜기 (2026-11-11 — 유튜브 초록 화면) ─────────────────
    # OBS 화면은 멀쩡한데 인코더(QuickSync)가 빈 버퍼를 내며 굳으면 새 방송도 계속 초록이었고, OBS 를 껐다 켜야 풀렸다
    # (사용자가 Stop-Process obs64 → Start-ScheduledTask PS-OBS-Studio 로 확인). 그걸 서버가 한다.
    OBS_RESTART_WAIT_S = 60.0

    def _run(self, cmd, timeout=20):
        import subprocess
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
        return subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=flags)

    def restart_obs_process(self, wait_s: float = None) -> bool:
        """OBS 를 끄고(taskkill) 작업 스케줄러로 다시 띄운 뒤 웹소켓이 붙을 때까지(최대 wait_s) 기다린다.
        송출 중이었다면 송출은 끊긴다 — 부르는 쪽이 다시 켠다. 결과는 obs_restart_status. **예외를 던지지 않는다.**"""
        wait_s = self.OBS_RESTART_WAIT_S if wait_s is None else float(wait_s)
        t0 = time.time()
        st = {'at': t0, 'ok': False, 'error': '', 'seconds': 0.0}
        self.disconnect()
        try:
            self._run(['taskkill', '/IM', 'obs64.exe', '/F'])
        except Exception as e:
            st['error'] = f'OBS 끄기 실패: {e}'
        time.sleep(2.0)
        try:
            r = self._run(['schtasks', '/Run', '/TN', self.obs_task])
            if getattr(r, 'returncode', 1) != 0:
                st['error'] = (f"작업 스케줄러 '{self.obs_task}' 를 못 켰어요: "
                               + (r.stderr or r.stdout or b'').decode('utf-8', 'ignore').strip()[:160])
        except Exception as e:
            st['error'] = f"작업 스케줄러 '{self.obs_task}' 를 못 켰어요: {e}"
        deadline = t0 + max(5.0, wait_s)
        while time.time() < deadline:
            time.sleep(3.0)
            try:
                self.connect(retries=1, delay=0)
                st['ok'] = True
                st['error'] = ''
                break
            except Exception:
                continue
        if not st['ok'] and not st['error']:
            st['error'] = f'{wait_s:.0f}초 안에 OBS 가 다시 뜨지 않았어요'
        st['seconds'] = round(time.time() - t0, 1)
        (logger.info if st['ok'] else logger.error)(
            f"🔄 OBS 다시 켜기: {'됨' if st['ok'] else '실패 — ' + st['error']} ({st['seconds']}초)")
        self.obs_restart_status = st
        return st['ok']

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
