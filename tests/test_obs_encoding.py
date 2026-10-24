"""
OBS 인코딩 점검 (2026-10-24 — *"라이브 인코딩 문제 아직도 계속된다"*) — OBS 없이 돈다.

    python tests/test_obs_encoding.py

'PC 과부하(인코딩 밀림)' 는 c18 부터 알려 줬지만 **왜** 밀리는지는 아무도 안 봤다. 여기서 못 박는 것:
  ① stream_health.encoding_advice — OBS 설정 → 고칠 칸(큰 것부터): x264 · 녹화 인코더 따로(인코딩 두 번) · FPS 60 ·
     해상도 · 란초스 · 무거운 NVENC/QSV/AMD 프리셋 · 카메라 소프트웨어 디코딩. 권장값이면 [] · 못 읽은 칸은 판정 안 함.
  ② encoding_summary 한 줄.
  ③ evaluate(advice=) — '인코딩 밀림'·'화면 밀림' 문장 끝에 가장 큰 칸 하나('고칠 것: …') · 권장값이면 'PC 성능 한계' ·
     설정을 못 읽었으면(None) 아무것도 안 붙인다 · 다른 문제(업로드)엔 안 붙인다.
  ④ obs_controller.read_encoding — 웹소켓(출력 모드·인코더·FPS·해상도) + 프로필 폴더의 streamEncoder.json(프리셋) +
     카메라 hw_decode · 프로필 폴더 찾기(user.ini → global.ini → 이름 → 하나뿐).
  ⑤ refresh_encoding — 5분에 한 번 · force · 던지지 않는다 · 연결이 없으면 지난 값.
  ⑥ stream_server — 방송 상태 문장에 '고칠 것' · /health obs_encoding.
  ⑦ obs_check.py — 읽기만 한다(connect() 를 안 부른다) · 결과 줄.
"""
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import stream_health as sh  # noqa: E402
import obs_controller  # noqa: E402  — 실물(⑥ 의 load_server 가 sys.modules 를 가짜로 바꾸기 전에)
from obs_controller import OBSController  # noqa: E402

fails = []
n_ok = [0]


def check(cond, msg, detail=''):
    if cond:
        n_ok[0] += 1
        print('  ok   ' + msg)
    else:
        print('  FAIL ' + msg + (('\n        ' + str(detail)) if detail else ''))
        fails.append(msg)


class R:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def keys(advice):
    return [a['key'] for a in advice]


GOOD = {'mode': 'Advanced', 'encoder': 'jim_nvenc', 'rec_encoder': 'none', 'fps': 30.0,
        'base': [1920, 1080], 'output': [1920, 1080], 'scale': 'bicubic',
        'enc': {'preset2': 'p5', 'multipass': 'qres', 'lookahead': False}, 'sw_decode_cameras': []}


def cfg(**kw):
    d = json.loads(json.dumps(GOOD))
    d.update(kw)
    return d


# ═══════════════════════════════════════════════════════════════
print('\n[①] 고칠 칸 — OBS 설정 → 큰 것부터')
check(sh.encoder_kind('obs_x264') == 'x264' and sh.encoder_kind('x264') == 'x264' and sh.encoder_kind('x264_lowcpu') == 'x264',
      'x264 인코더 id 셋(고급 obs_x264 · 단순 x264 · x264_lowcpu)')
check(all(sh.encoder_kind(e) == 'nvenc' for e in ('jim_nvenc', 'ffmpeg_nvenc', 'jim_hevc_nvenc', 'obs_nvenc_h264_tex', 'nvenc')),
      'NVENC id 여럿(옛 jim_nvenc · OBS 31 obs_nvenc_* · 단순 nvenc)')
check(all(sh.encoder_kind(e) == 'qsv' for e in ('obs_qsv11', 'obs_qsv11_v2', 'qsv')), 'QuickSync id')
check(all(sh.encoder_kind(e) == 'amf' for e in ('h264_texture_amf', 'amd_amf_h264', 'amd')), 'AMD id')
check(sh.encoder_kind('') == '' and sh.encoder_kind(None) == '' and sh.encoder_kind('apple_h264') == 'other',
      "모름은 '' · 모르는 인코더는 other")

check(sh.encoding_advice(GOOD) == [], '권장값(고급 · NVENC P5 · 녹화=방송 인코더 · 1080p30)이면 고칠 것 없음',
      sh.encoding_advice(GOOD))
check(sh.encoding_advice({}) == [] and sh.encoding_advice(None) == [], '못 읽은 칸은 판정하지 않는다(빈 설정 → [])')

a = sh.encoding_advice(cfg(encoder='obs_x264', enc={'preset': 'medium'}))
check(keys(a)[:2] == ['x264', 'x264_preset'], 'x264(CPU) → 하드웨어 인코더가 첫째 · 무거운 프리셋이 둘째', a)
check('NVENC' in a[0]['text'] and '설정 → 출력 → 방송' in a[0]['text'], '문장이 어디를 바꾸는지 말한다', a[0]['text'])
check('medium' in a[1]['text'] and 'veryfast' in a[1]['text'], "프리셋 'medium → veryfast'", a[1]['text'])
check(keys(sh.encoding_advice(cfg(encoder='obs_x264', enc={'preset': 'veryfast'}))) == ['x264'],
      'x264 veryfast 이하면 프리셋은 안 말한다(인코더만)')

a = sh.encoding_advice(cfg(rec_encoder='jim_nvenc'))
check(keys(a) == ['double_encode'] and "(방송 인코더 사용)" in a[0]['text'],
      "고급 모드 녹화 인코더가 따로면 → '(방송 인코더 사용)'(리플레이 버퍼가 늘 켜져 방송 중 인코딩 두 번)", a)
check(sh.encoding_advice(cfg(rec_encoder='')) == [] and sh.encoding_advice(cfg(rec_encoder='none')) == [],
      "녹화 인코더 'none'(방송 인코더 사용)·빈 값이면 괜찮다")
check(keys(sh.encoding_advice({'mode': 'Simple', 'encoder': 'nvenc', 'rec_quality': 'Small'})) == ['double_encode'],
      "단순 모드 녹화 품질이 '방송과 같음'(Stream)이 아니면 → 인코딩 두 번")
check(sh.encoding_advice({'mode': 'Simple', 'encoder': 'nvenc', 'rec_quality': 'Stream'}) == [],
      "단순 모드 '방송과 같음' 이면 괜찮다")

a = sh.encoding_advice(cfg(fps=60.0))
check(keys(a) == ['fps'] and a[0]['render'] is True and '60 → 30' in a[0]['text'], 'FPS 60 → 30(그리기에도 듣는다)', a)
check(sh.encoding_advice(cfg(fps=29.97)) == [] and sh.encoding_advice(cfg(fps=30.0)) == [], '29.97·30 은 괜찮다')

a = sh.encoding_advice(cfg(base=[2560, 1440], output=[2560, 1440]))
check(keys(a) == ['resolution'] and '2560x1440' in a[0]['text'], '출력 1440p → 1080p', a)
a = sh.encoding_advice(cfg(base=[2560, 1440], output=[1920, 1080], scale='lanczos'))
check(keys(a) == ['resolution', 'scale'], '캔버스가 크고 란초스로 줄이면 → 캔버스 1080p · 바이큐빅', a)
check(sh.encoding_advice(cfg(scale='lanczos')) == [], '줄이지 않으면(캔버스 = 출력) 란초스는 상관없다')

a = sh.encoding_advice(cfg(enc={'preset2': 'p7', 'multipass': 'fullres', 'lookahead': True}))
check(keys(a) == ['preset'] and all(s in a[0]['text'] for s in ('P7 → P4', '1/4 해상도', '룩어헤드')),
      'NVENC P7 · 2패스 전체 해상도 · 룩어헤드 → 한 줄로', a)
check(sh.encoding_advice(cfg(enc={'preset2': 'p6'})) and sh.encoding_advice(cfg(enc={'preset2': 'p4'})) == [],
      'NVENC P6 는 무겁고 P4 는 괜찮다')
check(keys(sh.encoding_advice(cfg(encoder='obs_qsv11', enc={'target_usage': 'TU1'}))) == ['preset'],
      'QuickSync TU1 → balanced')
check(keys(sh.encoding_advice(cfg(encoder='h264_texture_amf', enc={'preset': 'quality'}))) == ['preset'],
      "AMD '품질' → '균형'")

a = sh.encoding_advice(cfg(sw_decode_cameras=['코트 카메라']))
check(keys(a) == ['camera_decode'] and '코트 카메라' in a[0]['text'] and '하드웨어' in a[0]['text'],
      "카메라 소스가 소프트웨어 디코딩이면 → '가능한 경우 하드웨어 디코딩 사용'", a)

a = sh.encoding_advice({'mode': 'Advanced', 'encoder': 'obs_x264', 'rec_encoder': 'obs_x264', 'fps': 60,
                        'base': [1920, 1080], 'output': [1920, 1080], 'enc': {'preset': 'fast'},
                        'sw_decode_cameras': ['캠']})
check(keys(a) == ['x264', 'x264_preset', 'double_encode', 'fps', 'camera_decode'], '여럿이면 효과가 큰 순서', keys(a))

# ═══════════════════════════════════════════════════════════════
print('\n[②] 한 줄 요약')
check(sh.encoding_summary(GOOD) == '고급 · NVENC(P5) · 1920x1080@30 · 녹화=방송 인코더', '고급 NVENC', sh.encoding_summary(GOOD))
s = sh.encoding_summary(cfg(encoder='obs_x264', rec_encoder='obs_x264', enc={'preset': 'veryfast'}, fps=60))
check(s == '고급 · x264(CPU)(veryfast) · 1920x1080@60 · 녹화=따로(obs_x264)', 'x264 · 녹화 따로', s)
s = sh.encoding_summary({'mode': 'Simple', 'encoder': 'qsv', 'enc': {'target_usage': 'balanced'}})
check(s == '단순 · QuickSync(balanced)', '단순 모드는 녹화 칸을 안 적는다 · 못 읽은 해상도는 뺀다', s)
check(sh.encoding_summary({}) == '' and sh.encoding_summary(None) == '', '빈 설정은 빈 줄')

# ═══════════════════════════════════════════════════════════════
print("\n[③] 방송 상태 문장 끝에 '고칠 것'")
base = {'active': True, 'reconnecting': False, 'congestion': 0, 'bytes': 0, 'net_skipped': 0, 'net_total': 0,
        'enc_skipped': 0, 'enc_total': 0, 'render_skipped': 0, 'render_total': 0, 'cpu': 20, 'fps': 30, 'media': {}}


def snap(**kw):
    d = dict(base)
    d.update(kw)
    return d


ENC = snap(enc_skipped=15, enc_total=300, net_total=300)
REN = snap(render_skipped=30, render_total=300, net_total=300)
adv = sh.encoding_advice(cfg(rec_encoder='jim_nvenc', fps=60))
h = sh.evaluate(snap(), ENC, elapsed_s=10, advice=adv)
check(h['issue'] == 'encoder' and h['fix'] == adv[0]['text'] and h['text'].endswith('고칠 것: ' + adv[0]['text']),
      '인코딩 밀림 → 가장 큰 칸(녹화 인코더) 하나를 붙인다', h['text'])
check(h['short'] == 'PC 과부하', '짧은 말(태블릿 상태 줄)은 그대로', h['short'])
h = sh.evaluate(snap(), REN, elapsed_s=10, advice=adv)
check(h['issue'] == 'render' and h['fix'] == adv[1]['text'] and 'FPS' in h['fix'],
      '화면 밀림 → 그리기에 듣는 칸(FPS)만 고른다 — 녹화 인코더는 그리기와 상관없다', h)
h = sh.evaluate(snap(), ENC, elapsed_s=10, advice=[])
check('PC 성능 한계' in h['fix'] and h['text'].endswith(h['fix']), "설정이 권장값이면 'PC 성능 한계'", h['text'])
h = sh.evaluate(snap(), REN, elapsed_s=10, advice=[])
check(h['fix'] == '' and '고칠 것' not in h['text'], '화면 밀림에 권장값이면 덧붙이지 않는다(GPU·소스 문제일 수 있다)', h)
h = sh.evaluate(snap(), ENC, elapsed_s=10, advice=None)
check(h['fix'] == '' and '고칠 것' not in h['text'], '설정을 못 읽었으면(None) 아무것도 안 붙인다', h)
h = sh.evaluate(snap(), ENC, elapsed_s=10)
check(h['fix'] == '', 'advice 를 안 넘기면(옛 호출) 예전 문장 그대로')
h = sh.evaluate(snap(), snap(net_skipped=12, net_total=300, enc_skipped=15, enc_total=300), elapsed_s=10, advice=adv)
check(h['issue'] == 'network' and h['fix'] == '', '업로드 불안정이 먼저면 인코딩 칸을 붙이지 않는다', h)
h = sh.evaluate(snap(), snap(net_total=300, enc_total=300), elapsed_s=10, advice=adv)
check(h['issue'] == '' and h['text'] == '' and h['fix'] == '', '정상이면 아무것도 안 붙인다(설정에 고칠 칸이 있어도)', h)

# ═══════════════════════════════════════════════════════════════
print('\n[④] OBS 설정 읽기 — 웹소켓 + 프로필 폴더')
tmp = tempfile.mkdtemp(prefix='obsenc_')


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, 'w', encoding='utf-8-sig') as f:
        f.write(text)


root = os.path.join(tmp, 'obs-studio')
prof = os.path.join(root, 'basic', 'profiles')
write(os.path.join(prof, 'Court', 'basic.ini'), '[General]\nName=코트 방송\n')
write(os.path.join(prof, 'Court', 'streamEncoder.json'),
      json.dumps({'preset2': 'p7', 'multipass': 'fullres', 'lookahead': True, 'bitrate': 6000, 'tune': 'hq'}))
write(os.path.join(prof, 'Test', 'basic.ini'), '[General]\nName=시험\n')
write(os.path.join(root, 'user.ini'), '[General]\nX=1\n\n[Basic]\nProfile=코트 방송\nProfileDir=Court\n')


class FakeClient:
    def __init__(self, params, video=None, profile='코트 방송', inputs=None, fail=False):
        self.params = dict(params)
        self.video = video or R(fps_numerator=60, fps_denominator=1, base_width=1920, base_height=1080,
                                output_width=1920, output_height=1080)
        self.profile = profile
        self.inputs = inputs or {}
        self.fail = fail
        self.param_calls = 0
        self.sets = []

    def get_profile_parameter(self, cat, name):
        self.param_calls += 1
        if self.fail:
            raise RuntimeError('웹소켓 끊김')
        v = self.params.get(f'{cat}.{name}')
        return R(parameter_value=v, default_parameter_value=None)

    def set_profile_parameter(self, *a):
        self.sets.append(a)

    def get_video_settings(self):
        return self.video

    def get_profile_list(self):
        return R(current_profile_name=self.profile, profiles=['코트 방송', '시험'])

    def get_input_list(self, kind=None):
        return R(inputs=[{'inputName': n} for n in self.inputs])

    def get_input_settings(self, name):
        return R(input_settings=self.inputs.get(name, {}))

    def disconnect(self):
        pass


ADV = {'Output.Mode': 'Advanced', 'AdvOut.Encoder': 'jim_nvenc', 'AdvOut.RecEncoder': 'jim_nvenc', 'Video.ScaleType': 'bicubic'}
CAMS = {'코트 카메라': {'is_local_file': False, 'input': 'rtsp://u:p@10.0.0.64/101', 'hw_decode': False},
        '옆 카메라': {'is_local_file': False, 'input': 'rtsp://u:p@10.0.0.65/101', 'hw_decode': True},
        '인트로': {'is_local_file': True, 'local_file': 'C:/intro.mp4'}}

c = OBSController({'obs': {'config_dir': root}})
check(c.obs_config_dir == root, 'config.json obs.config_dir 이 있으면 그것', c.obs_config_dir)
c.client = FakeClient(ADV, inputs=CAMS)
r = c.read_encoding()
check(r['mode'] == 'Advanced' and r['encoder'] == 'jim_nvenc' and r['rec_encoder'] == 'jim_nvenc', '고급 모드 인코더 둘', r)
check(r['fps'] == 60.0 and r['base'] == [1920, 1080] and r['output'] == [1920, 1080], 'FPS·해상도(웹소켓 비디오 설정)', r)
check(r['enc'] == {'preset2': 'p7', 'multipass': 'fullres', 'lookahead': True, 'bitrate': 6000},
      '프리셋 등은 프로필 폴더 streamEncoder.json(ProfileDir) — 필요한 칸만', r['enc'])
check(r['sw_decode_cameras'] == ['코트 카메라'], '카메라 중 하드웨어 디코딩을 안 켠 것만(로컬 파일은 안 본다)', r['sw_decode_cameras'])
check(c.client.sets == [], '읽기만 한다(설정을 바꾸지 않는다)', c.client.sets)
adv = sh.encoding_advice(r)
check(keys(adv) == ['double_encode', 'fps', 'preset', 'camera_decode'], '읽은 설정 → 고칠 칸 넷', keys(adv))

# 프로필 폴더 찾기
os.remove(os.path.join(root, 'user.ini'))
write(os.path.join(root, 'global.ini'), '[Basic]\nProfileDir=Court\n')
check(c._profile_dir('') == os.path.join(prof, 'Court'), '옛 OBS(30 이하)는 global.ini 의 ProfileDir')
os.remove(os.path.join(root, 'global.ini'))
check(c._profile_dir('코트 방송') == os.path.join(prof, 'Court'), 'ini 가 없으면 지금 프로필 이름이 같은 폴더(basic.ini Name)')
check(c._profile_dir('없는 프로필') == '', '프로필이 여럿이고 맞는 게 없으면 짐작하지 않는다')
shutil.rmtree(os.path.join(prof, 'Test'))
check(c._profile_dir('없는 프로필') == os.path.join(prof, 'Court'), '프로필이 하나뿐이면 그것')
c2 = OBSController({'obs': {'config_dir': os.path.join(tmp, 'none')}})
c2.client = FakeClient(ADV)
r2 = c2.read_encoding()
check(r2['enc'] == {} and r2['encoder'] == 'jim_nvenc', '설정 폴더가 없으면 프리셋 칸만 비고 나머지는 읽는다', r2)
check('preset' not in keys(sh.encoding_advice(r2)), '못 읽은 프리셋은 판정하지 않는다')
saved = os.environ.get('APPDATA')
os.environ['APPDATA'] = tmp
check(OBSController({'obs': {}}).obs_config_dir == root, '기본은 %APPDATA%\\obs-studio')
if saved is None:
    del os.environ['APPDATA']
else:
    os.environ['APPDATA'] = saved

# 단순 모드
SIMPLE = {'Output.Mode': 'Simple', 'SimpleOutput.StreamEncoder': 'x264', 'SimpleOutput.Preset': 'faster',
          'SimpleOutput.RecQuality': 'Small'}
c.client = FakeClient(SIMPLE, video=R(fps_numerator=30000, fps_denominator=1001, base_width=1920, base_height=1080,
                                      output_width=1280, output_height=720))
r = c.read_encoding()
check(r['mode'] == 'Simple' and r['encoder'] == 'x264' and r['rec_quality'] == 'Small' and r['enc'] == {'preset': 'faster'},
      '단순 모드 — 인코더·녹화 품질·프리셋(SimpleOutput)', r)
check(r['fps'] == 29.97 and r['output'] == [1280, 720], '29.97 · 720p', r)
check(r['sw_decode_cameras'] == [], '카메라 목록 캐시(5분)에 남은 소스가 이제 카메라가 아니면 판정하지 않는다', r['sw_decode_cameras'])
check(keys(sh.encoding_advice(r)) == ['x264', 'x264_preset', 'double_encode'], '단순 x264 faster · 녹화 따로', keys(sh.encoding_advice(r)))
c.client = FakeClient({'SimpleOutput.StreamEncoder': 'nvenc', 'SimpleOutput.NVENCPreset2': 'p6'})
r = c.read_encoding()
check(r['mode'] == 'Simple' and r['enc'] == {'preset2': 'p6'}, "모드가 비면 단순 · 단순 NVENC 는 NVENCPreset2", r)

# ═══════════════════════════════════════════════════════════════
print('\n[⑤] 다시 읽기 — 5분에 한 번 · 던지지 않는다')
write(os.path.join(root, 'user.ini'), '[Basic]\nProfileDir=Court\n')
c = OBSController({'obs': {'config_dir': root}})
check(c.encoding_status['advice'] is None, '처음엔 못 읽음(None)')
fc = FakeClient(ADV, inputs=CAMS)
c.client = fc
st = c.refresh_encoding()
check(st['advice'] and st['summary'].startswith('고급 · NVENC(P7)') and st['error'] == '', '읽어서 요약·고칠 칸을 둔다', st['summary'])
n = fc.param_calls
c.refresh_encoding()
check(fc.param_calls == n, '5분 안에는 다시 안 읽는다(방송 상태 감시가 10초마다 부른다)', fc.param_calls - n)
c.refresh_encoding(force=True)
check(fc.param_calls > n, 'force 면 바로 다시 읽는다')
c.encoding_status['at'] -= 301
fc.params['AdvOut.RecEncoder'] = 'none'
st = c.refresh_encoding()
check('double_encode' not in keys(st['advice']), '5분이 지나면 다시 읽는다 — OBS 에서 고치면 반영', keys(st['advice']))
c.client = FakeClient(ADV, fail=True)
st = c.refresh_encoding(force=True)
check(st['error'].startswith('인코딩 설정을 못 읽음') and st['advice'] is None, '못 읽으면 던지지 않고 오류만', st)
c.client = None
check(c.refresh_encoding(force=True) is st, '연결이 없으면 지난 값 그대로(붙지 않는다)')
check('self.refresh_encoding(force=True)' in io.open(os.path.join(ROOT, 'obs_controller.py'), encoding='utf-8').read(),
      'OBS 에 붙을 때마다 한 번 읽는다(connect)')

# ═══════════════════════════════════════════════════════════════
print("\n[⑥] 방송 서버 — 상태 문장의 '고칠 것' · /health obs_encoding")
import test_stream_server as tss  # noqa: E402

ss = tss.load_server()
cl = ss.app.test_client()
hv = cl.get('/health').get_json()
check('obs_encoding' in hv and hv['obs_encoding'] is None, '가짜 OBS(설정 없음)면 obs_encoding 은 null', hv.get('obs_encoding'))
status = {'config': {'mode': 'Advanced'}, 'summary': '고급 · NVENC(P7) · 1920x1080@60 · 녹화=따로(jim_nvenc)',
          'advice': adv, 'error': '', 'at': 1.0}
ss.obs.encoding_status = status
refreshes = []
ss.obs.refresh_encoding = lambda force=False: refreshes.append(force) or status
hv = cl.get('/health').get_json()['obs_encoding']
check(hv['summary'] == status['summary'] and keys(hv['advice']) == keys(adv) and set(hv['advice'][0]) == {'key', 'text'},
      '/health obs_encoding — 요약 · 고칠 칸(key·text) · 설정', hv)
ss.obs.encoding_status = dict(status, advice=None, error='인코딩 설정을 못 읽음: x')
hv = cl.get('/health').get_json()['obs_encoding']
check(hv['advice'] is None and hv['error'], '못 읽었으면 advice null + error', hv)
ss.obs.encoding_status = status

r = cl.post('/start-stream', json={'teamA': ['가', '나'], 'teamB': ['다', '라'], 'league': 'PSiL 26S3',
                                   'matchNumber': 41, 'matchId': 'm41'})
check(r.status_code == 200, '방송 시작', r.get_json())
seq = [snap(), ENC]
ss.obs.stream_stats = lambda: seq.pop(0) if seq else None
ss.youtube.stream_health = lambda b: {'status': 'good', 'issues': []}
ss._health_tick(1000.0)
h = ss._health_tick(1010.0)
check(h and h['issue'] == 'encoder' and h['fix'] == adv[0]['text'], "방송 상태 감시가 '고칠 것' 을 붙인다", h)
check(refreshes and all(f is False for f in refreshes), '감시는 force 없이 부른다(5분 캐시)', refreshes)
sh_ = cl.get('/health').get_json().get('stream_health') or {}
check(sh_.get('short') == 'PC 과부하' and '고칠 것' in (sh_.get('text') or ''), '/health stream_health 문장에도', sh_)
src = io.open(os.path.join(ROOT, 'stream_server.py'), encoding='utf-8').read()
check("getattr(obs, 'refresh_encoding', None)" in src, '옛 OBS 컨트롤러(refresh_encoding 없음)에서도 죽지 않는다')
cl.post('/stop-stream', json={'matchId': 'm41'})

# ═══════════════════════════════════════════════════════════════
print('\n[⑦] obs_check.py — 읽기만 한다')
sys.path.insert(0, ROOT)
import obs_check  # noqa: E402

lines = obs_check.report_lines(status, None)
check(lines[0] == '설정: ' + status['summary'] and lines[1] == '고칠 것(큰 것부터):' and lines[2].startswith('  1. '),
      '설정 한 줄 · 고칠 칸 번호', lines)
lines = obs_check.report_lines(dict(status, advice=[]), None)
check(any('권장값' in x for x in lines), '고칠 것이 없으면 그렇게 말한다', lines)
lines = obs_check.report_lines({'error': '인코딩 설정을 못 읽음: x'}, None)
check(lines == ['⚠️ 인코딩 설정을 못 읽음: x'], '못 읽으면 오류만', lines)
h = sh.evaluate(snap(), ENC, elapsed_s=10, advice=adv)
lines = obs_check.report_lines(status, h, 10)
check(any(x.startswith('송출 숫자(10초): 인코딩 밀림 5%') for x in lines) and lines[-1].startswith('  → 방송 PC 인코딩이 밀려요'),
      '송출 숫자 · 지금 상태 한 줄', lines)
lines = obs_check.report_lines(dict(status, config={'mode': 'Advanced', 'enc': {}}), None)
check(any('obs.config_dir' in x for x in lines), '고급 모드인데 프리셋을 못 읽었으면 config_dir 을 안내', lines)
csrc = io.open(os.path.join(ROOT, 'obs_check.py'), encoding='utf-8').read()
check('.connect(' not in csrc and 'ReqClient(' in csrc, 'connect()(소리 싱크·자동 재연결 쓰기)를 안 부르고 붙기만 한다')
check('set_profile_parameter' not in csrc and 'set_input_settings' not in csrc, '설정을 쓰는 호출이 없다')

shutil.rmtree(tmp, ignore_errors=True)
print()
if fails:
    print(f'FAIL {len(fails)}건')
    sys.exit(1)
print(f'ALL OK — {n_ok[0]} checks')
