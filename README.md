# PS-rank

Padel Society 코트 장비 코드.

| 경로 | 무엇 | 어디서 도나 |
|---|---|---|
| `ps_court/ps_court_playus.html` | **리그앱**(코트 태블릿) — 매치 선택 → 팀 배정 → 점수 → 라이브 · 🎬 하이라이트 · **자유 라이브** | OBS PC 의 `stream_server.py` 가 시작할 때 이 repo `main` 의 raw 를 받아 서빙 (`https://obs.padelsociety.co.kr`) |
| `stream_server.py` · `obs_controller.py` · `youtube_api.py` | OBS PC 자동 스트리밍 서버 — 유튜브 방송 생성 · OBS 송출 · 리플레이 버퍼 → 하이라이트 업로드 | OBS PC `C:\dev\PS-rank` (예약 작업 `PS-StreamServer`, `setup/run_stream_server.ps1`) |
| `setup/` | OBS PC 설치·점검 스크립트 | OBS PC |
| `padel_app/` · `padel_live_ranking.html` | 공개 순위 표시 | GitHub Pages |

⚠️ 이 repo 는 모노repo `padelociety/playus` 의 `ps-rank/` 에 **거울**이 있다. 배포 원본은 **여기**다 —
OBS PC 가 여기서 pull 한다. 고치면 양쪽을 같게 맞출 것(2026-09 에 양방향으로 갈라진 적이 있다).

## 자유 라이브 (2026-09)

리그 매치가 없어도 태블릿에서 방송을 켜고 🎬 하이라이트를 남긴다.
매치 목록 맨 위 **🔴 자유 라이브** → 시간 **90분 / 2시간 / 3시간** 고르고(이름은 선택) → 시작.

- 끄는 건 **서버**(`stream_server.py` 워치독) — 태블릿이 꺼져도 제시간에 끝난다.
- 리그 경기가 시작되면 자유 라이브를 끄고 리그 방송을 연다(리그 우선).
- `/start-stream` body: `{ "mode": "free", "durationMinutes": 90|120|180, "teamA": [..], "teamB": [..] }`
  `/health` 가 `mode` · `ends_at` 을 준다(태블릿이 다시 켜져도 카드가 살아난다).

## OBS PC 에 새 버전 올리기

```powershell
cd C:\dev\PS-rank
git pull --ff-only
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup\check_livestream_pc.ps1 -Restart
```

python 파일은 재시작해야 반영된다. 태블릿 페이지는 재시작 때 새로 받고, 태블릿 헤더의 `앱 c3` 같은
빌드 표시가 바뀌면 2분 안에 스스로 새로고침한다. 페이지만 갱신: `https://obs.padelsociety.co.kr/reload-html`.

## 검증

```
python tests\test_stream_server.py     # OBS·YouTube 없이 스트리밍 서버 규칙 20개
```
