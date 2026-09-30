"""
소리 싱크 자동 적용 — OBS 없이 돈다(가짜 클라이언트).

    python tests/test_audio_sync.py

2026-09 측정: 하이라이트·라이브 모두 타구음이 화면보다 0.64~0.65초 늦었다(OBS 녹화 단계).
stream_server 가 OBS 에 연결할 때마다 config 의 obs.audio_sync_offset_ms 를 넣는다.
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from obs_controller import OBSController  # noqa: E402

fails = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        fails.append(msg)


class R:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeClient:
    def __init__(self, inputs):
        self.offsets = dict(inputs)   # name -> offset, None = 소리 없는 입력
        self.sets = []

    def get_input_list(self):
        return R(inputs=[{'inputName': n, 'inputKind': 'x'} for n in self.offsets])

    def get_input_audio_sync_offset(self, name):
        if self.offsets[name] is None:
            raise RuntimeError('604 InvalidInputKind')
        return R(input_audio_sync_offset=self.offsets[name])

    def set_input_audio_sync_offset(self, name, offset):
        self.sets.append((name, offset))
        self.offsets[name] = offset


def ctl(**obs):
    c = OBSController({'obs': obs})
    return c


print('\n[1] 값이 없으면 아무것도 안 건드린다')
c = ctl()
c.client = FakeClient({'카메라': 0})
st = c.apply_audio_sync()
check(st['target_ms'] is None and c.client.sets == [], 'audio_sync_offset_ms 없음 → OBS 그대로')

print('\n[2] 소리가 있는 입력 전부에 · 소리 없는 입력은 건너뛴다')
c = ctl(audio_sync_offset_ms=-640)
c.client = FakeClient({'카메라': 0, '데스크톱 오디오': 0, '점수판': None})
st = c.apply_audio_sync()
check(sorted(c.client.sets) == [('데스크톱 오디오', -640), ('카메라', -640)], '두 입력에 -640')
check([a['input'] for a in st['applied']] == ['카메라', '데스크톱 오디오'] and st['error'] == '', 'status 에 적용 결과')

print('\n[3] 같은 값이면 다시 쓰지 않는다(재연결마다 로그가 쌓이지 않게)')
c.client.sets.clear()
c.apply_audio_sync()
check(c.client.sets == [], '이미 -640 이면 set 안 함')

print('\n[4] 입력 이름을 정하면 그 입력에만 · 없는 이름은 알려 준다')
c = ctl(audio_sync_offset_ms=-640, audio_sync_inputs=['카메라', '없는마이크'])
c.client = FakeClient({'카메라': 0, '데스크톱 오디오': 0})
st = c.apply_audio_sync()
check(c.client.sets == [('카메라', -640)], '카메라에만')
check('없는마이크' in st['error'], '없는 입력 이름을 status 에 적는다')

print('\n[5] OBS 범위 밖은 잘라 넣는다 · 숫자가 아니면 무시')
c = ctl(audio_sync_offset_ms=-2000)
c.client = FakeClient({'카메라': 0})
c.apply_audio_sync()
check(c.client.sets == [('카메라', -950)], '-2000 → -950')
c = ctl(audio_sync_offset_ms='abc')
check(c.audio_sync_offset_ms is None, "'abc' → 무시")

print('\n[6] 오류가 나도 예외를 던지지 않는다(방송을 막지 않는다)')
c = ctl(audio_sync_offset_ms=-640)
class Broken(FakeClient):
    def get_input_list(self):
        raise RuntimeError('ws closed')
c.client = Broken({})
try:
    st = c.apply_audio_sync()
    check('입력 목록' in st['error'], '목록 실패 → status.error')
except Exception as e:  # pragma: no cover
    check(False, f'예외가 새어 나옴: {e}')
c.client = None
check(c.apply_audio_sync()['error'] == 'OBS 미연결', '미연결 → status.error')

print('\n[7] connect 가 연결 직후 apply_audio_sync 를 부른다')
src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'obs_controller.py'), encoding='utf-8').read()
i = src.index('def connect(')
check('self.apply_audio_sync()' in src[i:src.index('def disconnect(')], 'connect() 안에서 부른다')

print('\n[8] set_audio_sync.py — config.json 을 고친다(BOM · 범위 · 끄기)')
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with tempfile.TemporaryDirectory() as d:
    os.makedirs(os.path.join(d, 'setup'))
    script = os.path.join(d, 'setup', 'set_audio_sync.py')
    with open(os.path.join(root, 'setup', 'set_audio_sync.py'), encoding='utf-8') as f:
        open(script, 'w', encoding='utf-8').write(f.read())
    cfgp = os.path.join(d, 'config.json')
    with open(cfgp, 'w', encoding='utf-8-sig') as f:   # 메모장이 붙인 BOM
        json.dump({'obs': {'password': 'x'}, 'highlight': {'upload_key': 'k'}}, f)
    run = lambda *a: subprocess.run([sys.executable, script, *a], capture_output=True, text=True, encoding='utf-8')
    r = run('-640')
    cfg = json.load(open(cfgp, encoding='utf-8'))    # BOM 없이 다시 써졌다
    check(r.returncode == 0 and cfg['obs']['audio_sync_offset_ms'] == -640 and cfg['obs']['password'] == 'x'
          and cfg['highlight']['upload_key'] == 'k', '-640 을 넣고 나머지 값은 그대로')
    r = run('-5000')
    check(r.returncode == 2 and json.load(open(cfgp, encoding='utf-8'))['obs']['audio_sync_offset_ms'] == -640, '범위 밖은 거절하고 파일을 안 건드린다')
    run('off')
    check('audio_sync_offset_ms' not in json.load(open(cfgp, encoding='utf-8'))['obs'], "'off' 는 키를 뺀다")

print('\n' + ('전부 통과' if not fails else f'실패 {len(fails)}건'))
sys.exit(1 if fails else 0)
