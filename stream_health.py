"""
방송 상태 판정 — OBS 송출 숫자 · 카메라 소스 · YouTube 스트림 건강을 '지금 무엇이 문제인가' 한 줄로 바꾼다.

2026-10-21 신고: *"요즘 유투브가 자주 끊긴대"*. 끊김은 원인이 여럿이고(인터넷 업로드 · PC 인코딩 · 카메라 · YouTube
쪽 설정) 그동안은 **어디서 끊겼는지 알 방법이 없었다** — 서버 로그엔 켜고 끈 것만 남았고 태블릿은 'LIVE' 만 그렸다.
stream_server 의 감시 루프가 방송 중 10초마다 OBS 숫자를 읽어 여기에 넘기고, 결과를 /health(`stream_health`)·로그·
태블릿 상태 줄에 싣는다.

순수 모듈 — OBS·YouTube·Flask 를 import 하지 않는다(테스트가 숫자만 넣어 돌린다).
"""

# ── 문턱 ────────────────────────────────────────────────────────
# 10초 동안 보낸 프레임 중 버린 비율(%) — OBS 상태표시줄 '프레임 손실' 과 같은 셈.
NET_DROP_PCT = 2.0      # 인터넷(업로드)이 못 따라가 버린 프레임
ENCODE_SKIP_PCT = 2.0   # 인코더가 밀려 건너뛴 프레임(PC 과부하)
RENDER_SKIP_PCT = 5.0   # 화면 그리기가 밀려 건너뛴 프레임(GPU 과부하)
CONGESTION = 0.5        # OBS 가 재는 출력 혼잡(0~1)
CAMERA_GRACE_TICKS = 2  # 카메라가 이만큼 연속으로 안 돌아야 경고(장면 전환·잠깐 버퍼링은 넘긴다)

# 문제 종류 → (태블릿 상태 줄 짧은 말, 로그·토스트 문장). 위에 있을수록 먼저 고친다(가장 큰 원인 하나만 싣는다).
ISSUES = [
    ('reconnecting', '재연결 중', '방송 재연결 중 — 인터넷(업로드)이 끊겼어요. 다시 붙으면 같은 방송으로 이어져요'),
    ('camera', '카메라 끊김', '카메라 영상이 끊겼어요 — OBS 가 다시 연결하는 중이에요(카메라 전원·네트워크 확인)'),
    ('network', '업로드 불안정', '업로드가 불안정해요 — 인터넷이 영상을 다 못 보내 끊겨 보일 수 있어요'),
    ('encoder', 'PC 과부하', '방송 PC 인코딩이 밀려요 — 영상이 끊겨 보일 수 있어요(다른 프로그램 끄기)'),
    ('render', '화면 밀림', 'OBS 화면 그리기가 밀려요 — 영상이 끊겨 보일 수 있어요'),
    ('youtube', '유튜브 경고', 'YouTube 가 받는 영상에 문제가 있다고 해요'),
]
_ISSUE_ORDER = [k for k, _s, _t in ISSUES]
_SHORT = {k: s for k, s, _t in ISSUES}
_TEXT = {k: t for k, _s, t in ISSUES}

# OBS 미디어 소스가 영상을 받는 중인 상태(그 밖은 끊김으로 본다).
MEDIA_OK = {'OBS_MEDIA_STATE_PLAYING'}
# 네트워크로 받는 미디어 소스(카메라)인가 — '입력' 칸이 이 주소로 시작하면.
NET_INPUT_PREFIXES = ('rtsp://', 'rtsps://', 'rtmp://', 'rtmps://', 'srt://', 'udp://', 'http://', 'https://')

# YouTube liveStreams.status.healthStatus.configurationIssues 의 type → 사람 말. 모르는 type 은 reason 그대로.
YT_ISSUE_TEXT = {
    'videoIngestionStarved': '영상 데이터가 끊겨 들어와요(업로드 부족)',
    'audioIngestionStarved': '소리 데이터가 끊겨 들어와요(업로드 부족)',
    'bitrateLow': '비트레이트가 낮아요',
    'bitrateHigh': '비트레이트가 너무 높아요',
    'videoBitrateMismatch': '비트레이트가 설정과 달라요',
    'gopSizeLong': '키프레임 간격이 너무 길어요(OBS 키프레임 간격 2초)',
    'gopSizeOver': '키프레임 간격이 너무 길어요(OBS 키프레임 간격 2초)',
    'gopMismatch': '키프레임 간격이 고르지 않아요(OBS 키프레임 간격 2초)',
    'frameRateHigh': '프레임이 너무 높아요',
    'frameRateMismatch': '프레임이 설정과 달라요',
    'resolutionMismatch': '해상도가 설정과 달라요',
    'noAudioStream': '소리가 없어요',
    'noVideoStream': '영상이 없어요',
    'videoCodec': '영상 코덱이 맞지 않아요',
    'audioCodec': '소리 코덱이 맞지 않아요',
}


def is_network_input(settings: dict) -> bool:
    """미디어 소스 설정이 네트워크 카메라(RTSP 등)인가. 로컬 파일(인트로 영상 등)은 끝나는 게 정상이라 안 본다."""
    if not isinstance(settings, dict):
        return False
    if settings.get('is_local_file') is True:
        return False
    url = str(settings.get('input') or '').strip().lower()
    return url.startswith(NET_INPUT_PREFIXES)


def _pct(part, whole) -> float:
    try:
        part = float(part)
        whole = float(whole)
    except (TypeError, ValueError):
        return 0.0
    if whole <= 0 or part <= 0:
        return 0.0
    return round(min(100.0, part * 100.0 / whole), 1)


def _delta(cur: dict, prev: dict, key: str) -> float:
    """누적 숫자의 10초 차이. 송출을 다시 시작해 숫자가 0 으로 돌아가면(차이가 음수) 지금 값을 쓴다."""
    try:
        c = float((cur or {}).get(key) or 0)
    except (TypeError, ValueError):
        return 0.0
    try:
        p = float((prev or {}).get(key) or 0)
    except (TypeError, ValueError):
        p = 0.0
    d = c - p
    return c if d < 0 else d


def youtube_issue_lines(yt: dict) -> list:
    """YouTube 스트림 건강의 문제 줄(사람 말). severity 가 error/warning 인 것만 · 같은 말은 한 번."""
    out = []
    for it in (yt or {}).get('issues') or []:
        if not isinstance(it, dict):
            continue
        if str(it.get('severity') or '').lower() not in ('error', 'warning'):
            continue
        t = str(it.get('type') or '')
        line = YT_ISSUE_TEXT.get(t) or str(it.get('reason') or t or '').strip()
        if line and line not in out:
            out.append(line)
    return out


def evaluate(prev: dict, cur: dict, *, elapsed_s: float = 10.0, camera_bad_ticks: dict = None,
             youtube: dict = None) -> dict:
    """OBS 스냅샷 둘(10초 간격) → 지금 방송 상태.

    cur/prev 모양(obs_controller.stream_stats 가 만든다 — 없는 칸은 0):
      active · reconnecting · congestion · bytes · net_skipped · net_total
      enc_skipped · enc_total · render_skipped · render_total · cpu · fps
      media: {입력 이름: 'OBS_MEDIA_STATE_…'} — 네트워크 카메라만
    camera_bad_ticks: {입력 이름: 연속으로 안 돈 횟수} — 부르는 쪽이 들고 다닌다(여기서 고쳐 돌려준다).
    youtube: {'status': 'good'|'ok'|'bad'|'noData'|..., 'issues': [...]} — 1분마다 바뀐다(없으면 None).

    돌려주는 것: {issue, short, text, issues:[...], reconnecting, congestion, net_drop_pct, encode_skip_pct,
                 render_skip_pct, kbps, cpu, fps, camera:[이름…], youtube:{status, lines}}"""
    cur = cur or {}
    prev = prev or {}
    ticks = camera_bad_ticks if camera_bad_ticks is not None else {}
    found = []

    reconnecting = bool(cur.get('reconnecting'))
    if reconnecting:
        found.append('reconnecting')

    # 카메라 — 연속 CAMERA_GRACE_TICKS 번 안 돌아야(장면 전환·잠깐 버퍼링은 넘긴다)
    media = cur.get('media') or {}
    bad_cams = []
    for name, state in media.items():
        if state in MEDIA_OK:
            ticks.pop(name, None)
            continue
        ticks[name] = ticks.get(name, 0) + 1
        if ticks[name] >= CAMERA_GRACE_TICKS:
            bad_cams.append(name)
    for name in list(ticks.keys()):
        if name not in media:
            ticks.pop(name, None)
    if bad_cams:
        found.append('camera')

    net_drop = _pct(_delta(cur, prev, 'net_skipped'), _delta(cur, prev, 'net_total'))
    enc_skip = _pct(_delta(cur, prev, 'enc_skipped'), _delta(cur, prev, 'enc_total'))
    render_skip = _pct(_delta(cur, prev, 'render_skipped'), _delta(cur, prev, 'render_total'))
    try:
        congestion = round(float(cur.get('congestion') or 0), 2)
    except (TypeError, ValueError):
        congestion = 0.0
    if not reconnecting and (net_drop >= NET_DROP_PCT or congestion >= CONGESTION):
        found.append('network')
    if enc_skip >= ENCODE_SKIP_PCT:
        found.append('encoder')
    if render_skip >= RENDER_SKIP_PCT:
        found.append('render')

    yt_lines = youtube_issue_lines(youtube)
    yt_status = str((youtube or {}).get('status') or '')
    if yt_status == 'bad' or any(
            str(it.get('severity') or '').lower() == 'error'
            for it in ((youtube or {}).get('issues') or []) if isinstance(it, dict)):
        found.append('youtube')

    kbps = 0
    if elapsed_s and elapsed_s > 0 and not reconnecting:
        kbps = int(_delta(cur, prev, 'bytes') * 8 / 1000.0 / float(elapsed_s))

    found.sort(key=_ISSUE_ORDER.index)
    issue = found[0] if found else ''
    text = _TEXT.get(issue, '')
    if issue == 'camera' and bad_cams:
        text = f"{text} ({', '.join(bad_cams)})"
    if issue == 'youtube' and yt_lines:
        text = f"{text} — {yt_lines[0]}"
    try:
        cpu = round(float(cur.get('cpu') or 0), 1)
    except (TypeError, ValueError):
        cpu = 0.0
    try:
        fps = round(float(cur.get('fps') or 0), 1)
    except (TypeError, ValueError):
        fps = 0.0
    return {
        'issue': issue,
        'short': _SHORT.get(issue, ''),
        'text': text,
        'issues': found,
        'reconnecting': reconnecting,
        'congestion': congestion,
        'net_drop_pct': net_drop,
        'encode_skip_pct': enc_skip,
        'render_skip_pct': render_skip,
        'kbps': kbps,
        'cpu': cpu,
        'fps': fps,
        'camera': bad_cams,
        'youtube': {'status': yt_status, 'lines': yt_lines},
    }


def transition_line(before: str, after: str, health: dict) -> str:
    """문제가 생기거나 풀렸을 때 남길 로그 한 줄. 바뀐 게 없으면 ''."""
    if before == after:
        return ''
    nums = (f"드롭 {health.get('net_drop_pct', 0)}% · 혼잡 {health.get('congestion', 0)} · "
            f"인코딩 밀림 {health.get('encode_skip_pct', 0)}% · {health.get('kbps', 0)}kbps · CPU {health.get('cpu', 0)}%")
    if after:
        return f"⚠️ 방송 상태: {health.get('text') or after} [{nums}]"
    return f"✅ 방송 상태 정상으로 돌아옴 (전: {_SHORT.get(before, before)}) [{nums}]"
