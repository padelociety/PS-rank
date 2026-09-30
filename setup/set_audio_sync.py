"""
소리 싱크 값을 config.json 에 넣는다 — 손으로 JSON 을 고치다 쉼표 하나 빠지면 서버가 안 뜬다.

    .\\.venv\\Scripts\\python.exe setup\\set_audio_sync.py -640          # 소리를 0.64초 앞당김
    .\\.venv\\Scripts\\python.exe setup\\set_audio_sync.py -640 "카메라"  # 이 입력에만
    .\\.venv\\Scripts\\python.exe setup\\set_audio_sync.py off           # 끄기(OBS 값을 건드리지 않음)

넣은 뒤 stream_server 를 재시작해야 반영된다(config 는 시작할 때 한 번 읽는다):
    powershell -NoProfile -ExecutionPolicy Bypass -File .\\setup\\check_livestream_pc.ps1 -Restart

왜 있나: 2026-09 측정에서 하이라이트·라이브 모두 타구음이 화면보다 0.64~0.65초 늦었다.
OBS '고급 오디오 속성' 에 사람이 숫자를 넣어도 되지만, OBS 를 다시 깔거나 장치를 다시
추가하면 사라진다. stream_server 가 OBS 에 연결할 때마다 이 값을 넣는다(obs_controller.apply_audio_sync).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(os.path.dirname(HERE), 'config.json')
MIN_MS, MAX_MS = -950, 20000


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    if not os.path.exists(CONFIG):
        print(f'config.json 이 없습니다: {CONFIG}')
        return 1
    with open(CONFIG, 'r', encoding='utf-8-sig') as f:
        cfg = json.load(f)
    obs = cfg.setdefault('obs', {})
    if argv[0].lower() in ('off', 'none', 'null'):
        obs.pop('audio_sync_offset_ms', None)
        obs.pop('audio_sync_inputs', None)
        msg = '소리 싱크 끔 — OBS 값을 건드리지 않습니다'
    else:
        try:
            ms = int(argv[0])
        except ValueError:
            print(f'숫자가 아닙니다: {argv[0]!r} (예: -640)')
            return 2
        if not (MIN_MS <= ms <= MAX_MS):
            print(f'범위 밖입니다: {ms} (OBS 는 {MIN_MS} ~ {MAX_MS} ms 만 받습니다)')
            return 2
        obs['audio_sync_offset_ms'] = ms
        obs['audio_sync_inputs'] = [a for a in argv[1:] if a.strip()]
        where = ', '.join(obs['audio_sync_inputs']) or '소리가 있는 입력 전부'
        msg = f'소리 싱크 {ms}ms ({where})'
    tmp = CONFIG + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:     # BOM 없이
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG)
    print(f'✅ {msg} — config.json 에 넣었습니다. stream_server 를 재시작하세요.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
