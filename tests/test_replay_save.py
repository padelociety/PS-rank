"""
하이라이트 저장 — **이번에 저장한 파일**만 올린다. OBS 없이 돈다(가짜 클라이언트).

    python tests/test_replay_save.py

2026-10-05 신고: Match25 하이라이트를 저장했는데 영상은 앞서 저장한 Match21 클립이었다.
OBS 의 GetLastReplayBufferReplay 는 '마지막으로 저장이 끝난' 파일이라, 새 파일을 쓰는 동안엔
직전 경로를 돌려준다 — 예전 코드는 0.5초 뒤 받은 첫 경로를 그대로 올렸다.
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import obs_controller  # noqa: E402
from obs_controller import OBSController  # noqa: E402

obs_controller.time.sleep = lambda s: None   # 폴링 대기를 건너뛴다

fails = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        fails.append(msg)


class R:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeClient:
    """save 뒤 `lag` 번 물을 때까지는 직전 경로(before)를, 그 뒤엔 새 경로(after)를 준다."""
    def __init__(self, before, after, lag, raise_before=False):
        self.before, self.after, self.lag = before, after, lag
        self.saved = False
        self.asks_after_save = 0
        self.raise_before = raise_before

    def save_replay_buffer(self):
        self.saved = True

    def get_last_replay_buffer_replay(self):
        if not self.saved:
            if self.raise_before:
                raise RuntimeError('604 — 아직 저장한 리플레이 없음')
            return R(saved_replay_path=self.before)
        self.asks_after_save += 1
        return R(saved_replay_path=self.after if self.asks_after_save > self.lag else self.before)


def ctl(client):
    c = OBSController({'obs': {}})
    c.client = client
    return c


print('\n[1] 새 파일이 늦게 잡혀도 직전 클립을 올리지 않는다 (신고 재현)')
c = ctl(FakeClient('C:/rec/Replay 18-31-02.mp4', 'C:/rec/Replay 18-43-10.mp4', lag=4))
path = c.save_replay_buffer()
check(path == 'C:/rec/Replay 18-43-10.mp4', f'이번 파일을 돌려준다 → {path}')

print('\n[2] 끝내 안 바뀌면 빈 문자열 — 엉뚱한 클립을 올리느니 안 올린다')
c = ctl(FakeClient('C:/rec/Replay 18-31-02.mp4', 'C:/rec/Replay 18-31-02.mp4', lag=0))
path = c.save_replay_buffer(timeout_s=2)
check(path == '', f'직전 경로 그대로면 실패 처리 → {path!r}')

print('\n[3] 그 세션의 첫 저장(직전 파일 없음 — OBS 가 오류를 던짐)도 된다')
c = ctl(FakeClient('', 'C:/rec/Replay 18-43-10.mp4', lag=1, raise_before=True))
path = c.save_replay_buffer()
check(path == 'C:/rec/Replay 18-43-10.mp4', f'첫 저장 → {path}')

print('\n[4] 같은 이름을 덮어쓴 새 파일(1초 안에 두 번)은 수정 시각으로 알아본다')
with tempfile.TemporaryDirectory() as d:
    same = os.path.join(d, 'Replay.mp4')
    open(same, 'wb').close()
    old = time.time() - 600
    os.utime(same, (old, old))

    class Overwrite(FakeClient):
        def save_replay_buffer(self):
            super().save_replay_buffer()
            os.utime(same, None)   # 지금 시각으로 다시 쓰였다

    c = ctl(Overwrite(same, same, lag=0))
    check(c.save_replay_buffer(timeout_s=2) == same, '덮어쓴 파일은 새 클립으로 본다')
    c = ctl(FakeClient(same, same, lag=0))
    os.utime(same, (old, old))
    check(c.save_replay_buffer(timeout_s=2) == '', '안 덮어썼으면(옛 수정 시각) 직전 클립 — 실패 처리')

print('\n[5] 저장 요청 자체가 실패하면 빈 문자열')


class Boom(FakeClient):
    def save_replay_buffer(self):
        raise RuntimeError('501 replay buffer not active')


c = ctl(Boom('a', 'b', lag=0))
check(c.save_replay_buffer() == '', '저장 실패 → 빈 문자열')

print('\n[6] 두 번 연달아 눌러도 각자 자기 파일을 받는다(저장은 한 번에 하나)')
import threading  # noqa: E402


class Sequential:
    """OBS 흉내 — 저장할 때마다 새 파일 번호. 쓰는 데 두 번 묻는 시간이 걸린다."""
    def __init__(self):
        self.n = 0
        self.last = 'C:/rec/old.mp4'
        self.pending = None
        self.asks = 0
        self.lock = threading.Lock()

    def save_replay_buffer(self):
        with self.lock:
            self.n += 1
            self.pending = f'C:/rec/clip{self.n}.mp4'
            self.asks = 0

    def get_last_replay_buffer_replay(self):
        with self.lock:
            if self.pending:
                self.asks += 1
                if self.asks > 2:
                    self.last, self.pending = self.pending, None
            return R(saved_replay_path=self.last)


c = ctl(Sequential())
got = []
ts = [threading.Thread(target=lambda: got.append(c.save_replay_buffer())) for _ in range(2)]
[t.start() for t in ts]
[t.join() for t in ts]
check(sorted(got) == ['C:/rec/clip1.mp4', 'C:/rec/clip2.mp4'], f'두 클립이 각각 → {sorted(got)}')

print()
if fails:
    print(f'FAIL {len(fails)}')
    sys.exit(1)
print('all ok')
