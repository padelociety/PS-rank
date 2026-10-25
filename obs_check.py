"""
방송 PC 인코딩 점검 (2026-10-24 — *"라이브 인코딩 문제 아직도 계속된다"*).

OBS 가 켜져 있는 이 PC 에서:

    C:\\dev\\PS-rank\\.venv\\Scripts\\python.exe C:\\dev\\PS-rank\\obs_check.py
    ... obs_check.py --seconds 30     # 송출 숫자를 30초 동안 잰다(기본 10초)
    ... obs_check.py --no-sample      # 설정만
    ... obs_check.py --obs-log        # + OBS 로그(마지막으로 방송한 파일 · 그 방송부터)의 송출·출력·인코더 줄 · 비밀번호는 가림
                                       #   OBS 를 다시 띄운 뒤에 돌려도 멈췄던 앞 파일을 읽는다

무엇을 하나:
  ① OBS 인코딩 설정(출력 모드 · 방송 인코더 · 프리셋 · 녹화 인코더 · FPS · 해상도 · 카메라 디코딩)을 읽어 한 줄로
  ② 그중 PC 를 잡아먹는 칸을 큰 것부터 — stream_server 가 방송 상태 문장 끝에 붙이는 '고칠 것' 과 같은 판정
     (stream_health.encoding_advice 한 벌)
  ③ N초 동안 OBS 숫자(인코딩 밀림 · 화면 밀림 · 업로드 드롭 · 속도 · CPU)를 재서 지금 상태 한 줄

⚠️ **읽기만 한다** — OBS 설정을 바꾸지 않는다(소리 싱크·자동 재연결도 안 건드린다 — stream_server 가 하는 일).
방송 중에 돌려도 된다: stream_server 와 같은 OBS 에 웹소켓으로 한 번 더 붙을 뿐이다.
"""
import argparse
import json
import os
import sys
import time

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)

import stream_health  # noqa: E402


def report_lines(status: dict, health: dict = None, seconds: float = 0) -> list:
    """점검 결과 → 사람이 읽는 줄. status = OBSController.encoding_status · health = stream_health.evaluate 결과(없으면 None)."""
    st = status if isinstance(status, dict) else {}
    out = []
    if st.get('error'):
        out.append(f"⚠️ {st['error']}")
    else:
        out.append(f"설정: {st.get('summary') or '?'}")
        advice = st.get('advice')
        if advice is None:
            out.append('고칠 것: (설정을 못 읽었어요)')
        elif not advice:
            out.append('고칠 것: 없음 — 인코딩 설정은 권장값이에요')
        else:
            out.append('고칠 것(큰 것부터):')
            for i, a in enumerate(advice, 1):
                out.append(f"  {i}. {a.get('text')}")
        cfg = st.get('config') or {}
        if cfg.get('mode', '').strip().lower() == 'advanced' and not cfg.get('enc'):
            out.append('  (방송 인코더 세부 설정(프리셋)은 못 읽었어요 — OBS 설정 폴더를 못 찾음. '
                       "config.json 의 obs.config_dir 에 '%APPDATA%\\obs-studio' 경로를 적으면 읽어요)")
    if health:
        out.append(f"송출 숫자({seconds:g}초): 인코딩 밀림 {health.get('encode_skip_pct', 0):g}% · "
                   f"화면 밀림 {health.get('render_skip_pct', 0):g}% · 업로드 드롭 {health.get('net_drop_pct', 0):g}% · "
                   f"{health.get('kbps', 0)}kbps · CPU {health.get('cpu', 0):g}% · {health.get('fps', 0):g}fps")
        out.append(f"  → {health.get('text') or '정상 — 이 동안 밀린 곳이 없어요'}")
    return out


def stream_state_line(ctl) -> str:
    """지금 OBS 송출 상태 한 줄 — 꺼짐 / 송출 중 / 재연결 중. 못 물으면 그렇게 말한다."""
    try:
        st = ctl.client.get_stream_status()
    except Exception as e:
        return f'OBS 송출: (상태를 못 읽음 — {e})'
    if not getattr(st, 'output_active', False):
        return 'OBS 송출: 꺼짐'
    if getattr(st, 'output_reconnecting', False):
        return "OBS 송출: 재연결 중 — 서버에 방송이 없는데 이러면 OBS 에서 [방송 중단] 을 한 번 더(강제 종료)"
    return 'OBS 송출: 송출 중'


def _load_config(path):
    with open(path, encoding='utf-8-sig') as f:
        return json.load(f)


def main(argv=None):
    ap = argparse.ArgumentParser(description='OBS 인코딩 점검(읽기만)')
    ap.add_argument('--seconds', type=float, default=10.0, help='송출 숫자를 재는 시간(초)')
    ap.add_argument('--no-sample', action='store_true', help='설정만 본다')
    ap.add_argument('--obs-log', action='store_true', help="최근 OBS 로그의 송출·출력·인코더 줄을 같이 보인다")
    ap.add_argument('--config', default=os.path.join(_DIR, 'config.json'))
    args = ap.parse_args(argv)

    try:
        import obsws_python as obs_ws
    except ImportError:
        print('obsws-python 이 없어요 — .venv 의 python 으로 돌려 주세요')
        return 2
    from obs_controller import OBSController

    cfg = _load_config(args.config) if os.path.exists(args.config) else {}
    ctl = OBSController(cfg)
    try:
        # connect() 는 소리 싱크·자동 재연결을 맞춘다(쓰기) — 점검은 붙기만 한다.
        ctl.client = obs_ws.ReqClient(host=ctl.host, port=ctl.port, password=ctl.password, timeout=10)
    except Exception as e:
        print(f'OBS 에 못 붙었어요: {e}\nOBS 가 켜져 있는지 · 도구 → WebSocket 서버 설정 · config.json 의 obs.password 를 확인해 주세요')
        return 1

    print(f"OBS 인코딩 점검 — {time.strftime('%Y-%m-%d %H:%M')}")
    print(stream_state_line(ctl))
    status = ctl.refresh_encoding(force=True)
    health = None
    if not args.no_sample:
        first = ctl.stream_stats()
        t0 = time.time()
        time.sleep(max(1.0, args.seconds))
        second = ctl.stream_stats()
        if first and second:
            health = stream_health.evaluate(first, second, elapsed_s=time.time() - t0, camera_bad_ticks={},
                                            advice=status.get('advice'))
            if not second.get('active'):
                print('(지금 방송 중이 아니에요 — 송출 숫자는 0 이 정상이에요. 인코딩 밀림은 방송 중에 다시 재 주세요)')
    for line in report_lines(status, health, args.seconds):
        print(line)
    if args.obs_log:
        path = ctl.obs_log_file()
        lines = ctl.obs_log_lines(80, path=path) if path else []
        if lines:
            print(f"\nOBS 로그 {os.path.basename(path)} — 마지막 방송부터 송출·출력·인코더 줄 {len(lines)}개(비밀번호는 가림):")
        else:
            print('\nOBS 로그를 못 찾았어요(obs.config_dir 확인)')
        for ln in lines:
            print('  ' + ln)
    ctl.disconnect()
    return 0


if __name__ == '__main__':
    sys.exit(main())
