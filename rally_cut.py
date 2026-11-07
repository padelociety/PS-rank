"""마지막 랠리 시작 찾기 — 인스타 스토리용 구간(2026-11-01).

사용자: *"마지막 랠리 시작을 잡아주는건 좋을거같아"* · *"랠리 시작 3초전부터 버튼 누를때까지"*.
라켓 타구음(1.5~6kHz 의 짧고 센 소리)을 찾아, '센 타구' 가 0.5초보다 떨어져 2.8초 안에 이어진 묶음 중
**4번 이상 · 2.5초 이상 이어진 마지막 묶음의 첫 타구 = 서브**로 본다. 점수 난 뒤 공을 건네주며 툭툭 친 소리는
짧고 촘촘해서 빠진다. 실제 하이라이트 9개로 맞췄다(Match 180: 서브 29.3초 · 화면으로 본 서브 28.9초) —
틀리면 몇 초 일찍 잡히는 쪽이다(서브를 자르지 않는다).

⚠️ numpy 가 필요하다(requirements.txt). 없으면 랠리를 못 찾은 것으로 치고 마지막 57초를 쓴다(멈추지 않는다).
⚠️ 소리만 본다 — 옆 코트 소리가 크게 들어오는 곳에선 틀릴 수 있다. 태블릿 미리보기에서 보고 올린다.
"""
import subprocess

LEAD_S = 3.0     # 서브 3초 전부터
SR = 16000


def audio_hits(ffmpeg, path, timeout=90):
    """[(시각초, 점수)] — 1.5~6kHz 스펙트럼 오름(flux)이 주변 1초 중앙값보다 크게 튄 곳. numpy 가 없거나 실패하면 None."""
    try:
        import numpy as np
        from numpy.lib.stride_tricks import sliding_window_view as sw
    except Exception:
        return None
    try:
        raw = subprocess.run([ffmpeg, '-v', 'error', '-i', path, '-vn', '-ac', '1', '-ar', str(SR), '-f', 's16le', '-'],
                             capture_output=True, timeout=timeout).stdout
    except Exception:
        return None
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    win, hop = 320, 80
    n = (len(x) - win) // hop
    if n < 50:
        return []
    fr = sw(x, win)[::hop][:n] * np.hanning(win)
    S = np.abs(np.fft.rfft(fr, axis=1))
    f = np.fft.rfftfreq(win, 1 / SR)
    S = S[:, (f >= 1500) & (f <= 6000)]
    L = np.log1p(S * 50)
    flux = np.concatenate([[0], np.maximum(L[1:] - L[:-1], 0).sum(1)])
    k = int(1.0 / (hop / SR))
    pad = np.pad(flux, (k // 2, k - k // 2 - 1), mode='edge')
    Wn = sw(pad, k)
    med = np.median(Wn, 1)
    mad = np.median(np.abs(Wn - med[:, None]), 1) + 1e-6
    sc = (flux - med) / mad
    t = np.arange(n) * hop / SR
    out = []
    for i in range(1, n - 1):
        if sc[i] > 5 and flux[i] >= flux[i - 1] and flux[i] >= flux[i + 1]:
            if out and t[i] - out[-1][0] < 0.18:
                if sc[i] > out[-1][1]:
                    out[-1] = (float(t[i]), float(sc[i]))
                continue
            out.append((float(t[i]), float(sc[i])))
    return out


def last_rally_serve(hits):
    """마지막 랠리의 서브 시각(초) 또는 None. 순수 — 테스트가 부른다."""
    if not hits:
        return None
    sc = sorted(s for _, s in hits)
    p90 = sc[min(len(sc) - 1, int(round(0.9 * (len(sc) - 1))))]
    th = max(12.0, 0.4 * p90)
    strong = []
    for t, s in hits:
        if s < th:
            continue
        if strong and t - strong[-1] < 0.5:      # 0.5초 안에 또 = 같은 타구
            continue
        strong.append(t)
    groups = []
    for t in strong:
        if groups and t - groups[-1][-1] <= 2.8:
            groups[-1].append(t)
        else:
            groups.append([t])
    groups = [g for g in groups if len(g) >= 4 and g[-1] - g[0] >= 2.5]
    return groups[-1][0] if groups else None


def story_window(duration, serve, max_len=57.0):
    """올릴 구간 → (시작초, 이유). 끝은 늘 클립 끝(버튼을 누른 순간). 순수 — 테스트가 부른다.
    max_len 57: 스트림 복사는 시작이 앞 키프레임으로 1~2초 당겨지므로 60초(스토리 한도)보다 3초 여유."""
    if serve is None:
        return round(max(0.0, duration - max_len), 2), 'no_rally'
    start = max(0.0, serve - LEAD_S)
    if duration - start > max_len:
        return round(duration - max_len, 2), 'rally_cut60'
    return round(start, 2), 'rally'


def find_window(ffmpeg, path, duration):
    """(시작초, 서브초|None, 이유) — 이유: rally · rally_cut60 · no_rally · no_numpy."""
    hits = audio_hits(ffmpeg, path)
    if hits is None:
        s, _ = story_window(duration, None)
        return s, None, 'no_numpy'
    serve = last_rally_serve(hits)
    s, why = story_window(duration, serve)
    return s, (round(serve, 2) if serve is not None else None), why
