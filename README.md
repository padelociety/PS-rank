# PS-rank

Padel Society 코트 장비 코드.

| 경로 | 무엇 | 어디서 도나 |
|---|---|---|
| `ps_court/ps_court_playus.html` | **리그앱**(코트 태블릿) — 매치 선택 → 등급(PAR) 반영 → 팀 배정 → 점수 → 라이브 · 🎬 하이라이트 · **자유 라이브** | OBS PC 의 `stream_server.py` 가 시작할 때 이 repo `main` 의 raw 를 받아 서빙 (`https://obs.padelsociety.co.kr`) |
| `stream_server.py` · `obs_controller.py` · `youtube_api.py` | OBS PC 자동 스트리밍 서버 — 유튜브 방송 생성 · OBS 송출 · 리플레이 버퍼 → 하이라이트 업로드 | OBS PC `C:\dev\PS-rank` (예약 작업 `PS-StreamServer`, `setup/run_stream_server.ps1`) |
| `setup/` | OBS PC 설치·점검 스크립트 | OBS PC |
| `padel_app/` · `padel_live_ranking.html` | **내렸다**(2026-11-10) — 구글 시트 시절 공개 순위. 지금은 '앱으로 옮겼어요' 안내 한 장(스크립트 없음) · 옛 PWA 서비스워커는 스스로 등록을 푼다 | GitHub Pages |

⚠️ 이 repo 는 모노repo `padelociety/playus` 의 `ps-rank/` 에 **거울**이 있다. 배포 원본은 **여기**다 —
OBS PC 가 여기서 pull 한다. 고치면 양쪽을 같게 맞출 것(2026-09 에 양방향으로 갈라진 적이 있다).

## 자유 라이브 (2026-09)

리그 매치가 없어도 태블릿에서 방송을 켜고 🎬 하이라이트를 남긴다.
매치 목록 맨 위 **🔴 자유 라이브** → 시간 **90분 / 2시간 / 3시간** 고르고(이름은 선택) → 시작.

- 끄는 건 **서버**(`stream_server.py` 워치독) — 태블릿이 꺼져도 제시간에 끝난다.
- 리그 경기가 시작되면 자유 라이브를 끄고 리그 방송을 연다(리그 우선).
- `/start-stream` body: `{ "mode": "free", "durationMinutes": 90|120|180, "teamA": [..], "teamB": [..] }`
  `/health` 가 `mode` · `ends_at` 을 준다(태블릿이 다시 켜져도 카드가 살아난다).

## 방송 하나 = 경기 하나 (2026-10-08)

신고: 유튜브 라이브 제목·썸네일은 `PSiL 26S3 [Bridge] Match39` 인데 화면 점수판은 `Match 41` —
**한 방송에 다음 경기들이 이어서 나갔다.** 원인이 셋이었고 전부 같은 결과(OBS 가 지난 방송 키로 계속 송출)를 냈다:

1. **OBS 끄기가 조용히 실패했다** — 연결(client)이 없으면 아무것도 안 하고 돌아갔다. 키프알라이브가 OBS 응답 오류
   한 번에 연결을 비우고 20초 뒤에야 다시 붙는데, 그 사이 경기가 끝나면 OBS 는 계속 내보냈다.
   → `obs.stop_stream()` 은 붙어서 끄고 **실제로 멈춘 걸 확인해야** True.
2. **켜기가 '이미 스트리밍 중' 에 붙었다** — 새 키를 넣어도 송출을 다시 시작해야 먹는데 성공이라 답했다.
   → `obs.start_stream()` 은 송출 중이면 던지고, 서버는 새 방송을 열기 전에 OBS 를 먼저 끈다(`_ensure_obs_idle` —
   못 끄면 유튜브 방송을 만들지 않고 멈춘다).
3. **다른 경기가 오면 409, 끄기·켜기가 겹치면 상태가 꼬였다** — 경기 저장(끄기)이 끝나기 전에 '다른 페어로 다시하기'
   (켜기)가 오면 409(새 경기 방송 없음)거나, 끄기의 마지막 정리가 켜기가 세운 상태를 지워 서버가 '방송 없음' 으로 믿었다.
   → 켜고 끄는 일은 **`stream_op_lock` 으로 한 줄**. 리그 방송 중에 리그 경기가 또 오면
   **다른 매치 = 지난 방송을 끝내고 새로**(`took_over`) · **같은 매치 = 그 방송 그대로**(`reused` — 팀을 다시 뽑았으면
   제목·설명·썸네일만 고친다). 매치는 태블릿 c14 가 보내는 `matchId` 로, 옛 태블릿은 리그 + 번호(없으면 네 사람)로 가린다.

- `/stop-stream {matchId}` — 지금 방송이 **다른** 경기(다음 경기가 넘겨받았다 · 자유 라이브)면 끄지 않고
  `skipped` 로 답한다(앞 경기 [매치 취소] 가 지금 경기 방송을 끄고 [무효] 까지 붙이지 않게). id 없이 부르면 예전처럼 무엇이든 끈다.
- `/health` 가 `match_id` · `match_number` 를 준다 — 태블릿 상태 줄이 `🔴 LIVE · Match 39` 로 그린다.
- 태블릿 c14: 켜기 전에 진행 중인 끄기를 기다린다 · 목록으로 나가면(팀·PAR 화면에서 뒤로) 그 매치 방송을 끈다 ·
  매치별 영상 주소(최근 20개, `ps_stream_of`)로 [무효] 를 붙인다.
- 검토(같은 날)에서 더 고친 것: **유튜브 클라이언트는 스레드에 안전하지 않다** — `YouTubeAPI` 의 호출은 전부 잠금으로 한 줄
  (방송 만들기와 썸네일·[무효]·제목 고치기·남은 방송 닫기가 같은 연결을 동시에 쓰면 서로의 응답을 읽었다). 상태만 남은 방송은
  새 방송을 만들기 **전에** 같은 줄에서 닫는다 · OBS 연결이 **있는데 죽어 있으면** 끄기·확인이 새로 붙어 한 번 더 한다 ·
  썸네일 임시 파일은 부를 때마다 다른 이름 · 태블릿은 **켜는 중(10~20초)** 에 온 끄기를 버리지 않고 다 켜진 뒤 끈다(켜는 사이
  매치를 떠났으면 스스로 끈다) · 기다리는 시간 켜기 60초·끄기 45초, 끄기가 시간 초과여도 앱 LIVE 배너는 끈다.
- 검토 2차: 켜는 중인 켜기를 **전부** 기억한다(`_starts`) — 아직 `/start-stream` 을 안 보낸 켜기(앞 끄기를 기다리는 중)는
  같은 경기의 끄기가 오면 **거둔다**(만들자마자 끄는 몇 초짜리 영상이 안 남게) · 보낸 켜기는 끝날 때까지 기다리고, 그 사이
  **실제로 다시 보낸** 켜기가 있으면 그 끄기는 버린다(`_startSeq` — 서버 꺼짐으로 보내지 않고 끝난 켜기는 세지 않는다) ·
  매치가 같은지는 객체가 아니라 **id 로** · 끄기가 실패하거나 JSON 이 아닌 500 이면 상태 줄 `⚠️ 스트리밍 오류`(LIVE 로 남기지
  않는다) · 방송이 없을 때 `/stop-stream` 은 `idle: true` 로 답하고 태블릿은 그때 '종료됨'·'[무효] 직접 붙이라' 를 띄우지 않는다
  (켜는 사이 /health 가 '방송 중' 을 읽은 경우) · 켜기가 실패하면 서버 상태를 다시 읽는다 · `/void-video` 는 60초 기다린다 ·
  OBS 끄기 재시도는 다시 붙을 때 한 번만(태블릿은 45초 기다린다).
- 검토 3차: 다른 경기의 끄기가 도는 중에 온 끄기는 거기 **얹히지 않는다**(`_stoppingFor`) — 끝나길 기다렸다 이 경기 것을
  따로 보낸다(얹히면 서버가 그 끄기를 '다른 경기'(skipped)로 답해 이 경기 방송이 남았다). 같은 경기 끄기는 예전처럼 한 번만 ·
  [매치 취소] 의 '[무효] 직접 붙여 주세요' 는 지금 방송·끄는 중이 **이 경기** 것일 때만(저장 → 다시하기 → 앞 경기를 끄는 사이
  [매치 취소] 에 그 안내가 떠, 방금 끝난 정상 경기 영상에 [무효] 를 붙일 뻔했다).
- ⚠️ 이미 그렇게 섞여 올라간 영상(26S3 Match39 방송에 Match 40·41)은 코드가 못 고친다 — 유튜브 스튜디오에서 제목을 손본다.

## 방송 이름 — 실명/닉네임 (2026-10-10 · 태블릿 c15)

사용자: *"리그 썸네일에 이름 나오자나. 사람들마다 이름 노출하는거 꺼려할수도 있어서. 닉네임으로 선택하면 닉네임이 보여지게 할 수 있어?"*
회원이 앱 **내 정보 → 방송 이름** 에서 실명(기본)/닉네임을 고른다. 서버가 리그 매치 응답의 사람마다
`onAir: { name, showPhoto, mode }` 를 붙이고, 태블릿은 그걸로 **바깥으로 나가는 곳**을 채운다.

- 방송 배열(`teamAAir` · `psAirName` · `psAirPhoto`) → Firebase(코트 TV·OBS 오버레이) · 앱 라이브 카드 ·
  `/start-stream`(유튜브 제목·설명·썸네일 — 하이라이트도 서버가 이 이름을 기억해 단다). 닉네임이면 **사진도 빠진다**
  (썸네일이 닉네임 첫 글자 이니셜). 닉네임이 비면 서버가 `선수` 를 준다 — 실명으로 되돌아가지 않는다.
- 태블릿 **화면은 실명 그대로**(코트의 선수·운영자) + 방송 이름이 다르면 그 아래 작게 `📺 닉네임`.
- `onAir` 가 없으면(옛 서버) 예전 실명 규칙 그대로. `make_thumbnail.py` 도 같은 규칙(`name_of` · `photo_of`).
- '다른 페어로 다시하기' 는 팀 배열을 손으로 채우지 않고 `applyTeams` 를 부른다(방송 배열이 앞 경기 것으로 남지 않게).
- `stream_server.py`·`thumbnail.py` 는 그대로 — **재시작 필요 없음**, 페이지만 갱신(`/reload-html`).
  이미 올라간 영상의 제목·썸네일은 바뀌지 않는다(필요하면 `make_thumbnail.py --video …` 로 다시).
- ⚠️ **배포 순서: 태블릿(c15 · `/reload-html`)·PS Control(b29) 먼저, 백엔드는 그 뒤.** 백엔드가 먼저 나가면 회원이 앱에서
  닉네임을 고를 수 있는데, 아직 c14 인 태블릿은 `onAir` 를 몰라 **실명**을 내보낸다(배지로 c15 인지 확인).
  반대 순서(c15 + 옛 백엔드)는 `onAir` 가 없을 뿐이라 예전과 똑같다.

## 등급(PAR) 반영 — 리그 설정이 기본 · 완료 화면에 결과 (2026-10-11 · 태블릿 c16)

사용자: *"리그 PAR 켜놓은걸 기준으로 하자. 그리고 PAR 반영된 경기는 결과에 표시해줘."*

- **새 매치는 리그 설정으로 태어난다**(서버 `utils/leagueMatchType.newMatchParFields`) — 웹어드민 리그 폼 'PAR 반영 기본'
  켬이면 `Competitive` + `parForced`(태블릿의 '반영'), 끔이면 `Friendly`('반영 안 함'). STEP 2 의 '지금' 표시가 그 서버 값이다.
  예전(2026-09-23 ~ 10-10)엔 리그와 무관하게 늘 '반영 안 함' 으로 태어났다. 태블릿 동작은 그대로 — **STEP 2 에서 한 번은 누른다**,
  '다른 페어로 다시하기' 는 직전에 고른 값을 서버에 적어 이어받는다(이제 새 매치 값과 다를 수 있어 실제로 뒤집는다).
- **완료 화면(STEP 5)에 한 줄** — `✅ 등급(PAR) 반영됨` / `⚠️ 등급(PAR) 반영 안 됨 — 혼성` 처럼. 저장한 뒤 이 매치를
  **서버에서 다시 읽은** `parStatus`·`parStatusText`(회원 앱 결과 칩·웹어드민 배지와 같은 말 — 백엔드 `utils/leagueParStatus`)로
  그린다. 저장 문장만 믿지 않는 이유: 2026-10-11 전 서버는 엔진이 저장 도중 실패하거나 PAR 단계를 건너뛰어도 '(PAR 반영 완료)' 라고 했다.
  서버가 `parStatus` 를 안 주면(옛 서버 · 피클볼 리그 · 다시 못 읽음) **아무것도 안 그린다**. 그 사이 다시하기로 넘어갔으면 안 덮는다.
- **저장 확인 창**도 이 매치 값대로 — 예전엔 '반영 안 함' 경기에도 '저장하면 PAR이 반영되고' 라고 했다.
  문구 규칙은 STEP 2 와 같다('등급(PAR)' 과 '순위 추가점수' 를 같이 · '경쟁' 이라고 쓰지 않는다).
  **무승부(세트 승수가 같다)면 등급(PAR)을 약속하지 않는다** — 서버 엔진이 무승부는 넣지 않는다(순위 추가점수는 그대로).
- 끝났는데 PAR 에 안 들어간 경기는 웹어드민 매치 목록 **[PAR 반영하기]**(슈퍼어드민 · 운영자 소급 반영)로 넣는다 — 태블릿에서는 못 바꾼다.
- `stream_server.py` 는 그대로 — **재시작 필요 없음**, 페이지만 갱신(`/reload-html`). 배지 `앱 c16` · SW `ps-court-v50`.
- 배포 순서 무관: c16 + 옛 백엔드면 완료 화면 줄이 안 뜰 뿐이고, 새 백엔드 + c15 면 완료 화면 줄과 확인 창 문장만 예전 그대로다.

## 방송 서버 잠금 · 이름 이스케이프 (2026-10-19 · 태블릿 c17 · SW v51)

보안 2차 검토: `obs.padelsociety.co.kr` 가 역방향 ssh 터널로 이 PC 의 `stream_server.py` 를 **인터넷에 그대로** 내놓는데,
인증이 없고 CORS 가 `*` 였다 — 누구나 방송을 켜고·끄고·영상에 [무효] 를 붙이고·하이라이트를 올릴 수 있었다.

- **바꾸는 요청은 운영자 로그인을 확인한다** — `/start-stream` · `/stop-stream` · `/void-video` · `/highlight`.
  태블릿이 운영자 토큰을 `Authorization: Bearer` 로 보내고(`streamHeaders`), 서버가 Playus 백엔드 `/api/user/myProfile` 에
  물어 역할(`league_operator` · `super_admin` · `center_admin`)을 본다. 통과는 10분, 거절은 30초 기억한다(토큰 해시로).
  ⚠️ 2026-11-09 부터는 역할이 아니라 **센터**로 본다(`/api/stream/operator-check` — 아래 '방송 PC 는 그 센터 사람만' 절).
  백엔드가 안 닿으면 12시간 안에 통과했던 토큰만 들인다(경기장 인터넷 끊김). 막히면 태블릿이 서버 말을 그대로 띄우고 401 이면 ⚙ 설정을 연다.
- ⚠️ **이제 태블릿이 운영자로 로그인돼 있어야** 자유 라이브·🎬 하이라이트·리그 방송 켜고 끄기·[무효] 가 된다(예전엔 로그인 없이도 됐다).
  **이 PC 에서 직접 연 페이지**(127.0.0.1 · 프록시 머리 없음)만 토큰 없이 통과한다.
- `/reload-html` · `/health` · `/status` 는 잠그지 않는다 — 페이지 다시 받기는 GitHub main 의 파일만 받으므로 20초에 한 번으로만 막았다.
- CORS 는 목록으로(`cors_origins` · 기본 github.io + obs 도메인 + 이 서버 자신) — `file://` 로 연 태블릿 페이지는 이제 방송 서버에 못 닿는다.
- 썸네일 선수 사진은 우리 API 호스트(`https` · api / dev.api.padelsociety.co.kr)에서만, 8MB 까지 받는다(`thumbnail.photo_hosts` 로 바꾼다).
- 태블릿(`ps_court_playus.html` · 옛 `ps_court.html`)의 이름·리그·카테고리·서버 문장을 전부 이스케이프해서 그린다(`psEsc`) — 회원이 이름이나
  리그 이름에 HTML 을 넣으면 태블릿에서 그대로 돌아 운영자 토큰을 빼 갈 수 있었다. 테스트 `tests/xss_escape.test.cjs`.
- **공개 순위 페이지 두 장**(`padel_live_ranking.html` · `padel_app/padel_app.html`)은 **내렸다**(2026-11-10 대표 결정 25 · 검토 S4-01) —
  태블릿·PS Control 과 **같은 출처**(github.io/PS-rank)인데 구글 시트를 무료 CORS 프록시로 받아 그려서, 프록시가 오염되면 `lg_token`·`ps_token` 을
  빼 갈 길이었다(11-09 에 이스케이프로 반쯤 막았다). 이제 스크립트·프록시가 없는 안내 한 장이다 — 순위는 Playus 앱 리그 상세 '순위' 탭.
  ⚠️ 이 두 주소에 스크립트·외부 데이터를 다시 넣지 말 것(테스트 ⑧ 가 막는다).
- ⚠️ **`onclick` 같은 `on*` 속성 안 JS 에 시트·서버 값을 끼워 넣지 않는다** — 브라우저가 속성 값의 `&#39;` 를 `'` 로 되돌린 뒤 JS 로 읽어서
  `psEsc` 로도 못 막는다. 요소를 만든 뒤 `.onclick = () => f(원래 값)` 클로저로 붙인다(📈 시뮬레이션 버튼 · 테스트 ⑧ 가 그 모양을 막는다).
- 운영이 끝난 `thingproxy.freeboard.io` 를 시트 프록시에서 뺐다(도메인을 넘겨받은 사람이 CSV 를 지어낸다) — 옛 태블릿
  `ps_court.html`(테스트 ⑦ — 순위 두 장은 내렸다). 남은 셋(codetabs → corsproxy → allorigins · 순서·재시도 그대로)도 남의 서버라 순위 숫자는 지어낼 수 있다
  (스크립트는 못 돈다) — 시트를 직접 받거나 백엔드를 거치게 하는 건 **남은 일**.
- 아메리카노 보드는 새 창이 이 태블릿을 되잡지 못하게 연다(`w.opener = null`).
- ⚠️ **python 이 바뀌었다 — `stream_server` 재시작이 필요하다. 방송 중엔 하지 말 것.** 설정 키는 `streaming_config.json.example`
  (`control_auth` · `cors_origins` · `thumbnail.photo_hosts`) — 안 적으면 기본값으로 켜진다.
- LAN 직접 접속(`http://PC-IP:5000`)은 토큰이 평문으로 와이파이를 지난다 — 가능하면 `https://obs.padelsociety.co.kr` 로 연다.

## 유튜브 끊김 — 지연 · 자동 종료 · 재연결 · 상태 감시 (2026-10-21 · 태블릿 c18 · SW v52)

신고 *"요즘 유투브가 자주 끊긴대"*. 서버 로그엔 켜고 끈 것만 남아 **어디서 끊겼는지 알 수 없었다.** 끊김을 키우던 설정 셋을 고치고,
방송 중에 어디가 문제인지 보이게 했다.

- **지연 `normal`** — 예전 기본 `ultraLow` 는 시청자 버퍼가 몇 초뿐이라 업로드가 조금만 밀려도 시청자 화면이 돌았다. 이제 15~30초 늦게
  보이는 대신 잘 안 멈춘다. `config.json` `youtube.latency` 는 `normal`·`low` 만 받는다(`ultraLow` 는 `normal` 로 바꾸고 로그에 남긴다).
- **YouTube 자동 종료 끔**(`enableAutoStop: False`) — 예전엔 업로드가 1분쯤 끊기면 YouTube 가 방송을 **끝냈고**, OBS 가 다시 붙어도
  끝난 방송이었다. 이제 방송은 stream_server 가 끝낸다(경기 저장 · [매치 취소]). 끝내기가 실패하면 15초 · 45초 · 2분 · 5분 · 10분 뒤에
  다시 끝내고(`live_broadcast.json` — git 제외), 시작 전(`created`·`ready`) 방송은 지운다. 서버가 방송 중에 죽었다 뜨면 OBS 가 송출 중일 땐
  그 방송을 이어받고, 아니면 남은 방송을 끝낸다.
- **OBS 자동 재연결** — 붙을 때마다 켜져 있는지 보고 꺼져 있거나 짧으면 켠다(재시도 3초 · 25번 · `/health` `broadcast_settings.reconnect`).
- **방송 상태 감시**(`stream_health.py` — 순수 모듈) — 방송 중 10초마다 OBS 송출 숫자(재연결 · 업로드 드롭 · 혼잡 · 인코딩/화면 밀림 ·
  RTSP 카메라 상태)를, 1분마다 YouTube 스트림 건강을 읽어 가장 큰 문제 하나를 고른다. `/health` 의 `stream_health` · 로그
  `⚠️ 방송 상태:`/`✅ 방송 상태 정상으로 돌아옴` · 태블릿 상태 줄 `🔴 LIVE · Match 39 · ⚠️ 업로드 불안정`(새 문제는 한 번 토스트).
  무엇을 하면 되는지는 `setup/SETUP_LIVESTREAM_PC.md` 8절 '유튜브가 자주 끊긴다' 표.
- ⚠️ **python 이 바뀌었다 — `stream_server` 재시작이 필요하다. 방송 중엔 하지 말 것.** 태블릿은 재시작 때 c18 을 받는다.
  이미 열려 있는 방송은 예전 설정(ultraLow · 자동 종료) 그대로다 — 다음 방송부터 바뀐다.
- 테스트 `tests/test_stream_stability.py` · `tests/stream_handover.test.cjs` ⑥-2.

## 유튜브 초록 화면 — 송출 영상 검사 · OBS 다시 켜기 (2026-11-11 · 태블릿 c21 · SW v55)

*"obs 미리보기 화면은 유투브 초록화면 나와도 원래 정상이야"* · *"라이브 시작할때 초록색 … 계속 새로운 라이브도 초록색"* → OBS 만 다시 켜자
풀렸다. 원인은 카메라가 아니라 **인코더**(그때 방송·녹화 둘 다 QuickSync). OBS 스크린샷은 인코더 앞이라 못 본다.
- 인코딩 점검 `hw_double` — 방송·녹화 인코더가 같은 그래픽 인코더(QSV·NVENC·AMD)면 맨 앞에 '유튜브 초록 화면 의심 1순위 → 녹화 인코더
  (스트리밍 인코더 사용)'. 카메라 하드웨어 디코딩은 더 권하지 않는다.
- **송출 영상 검사**(`_output_tick` · `stream_health.output_verdict`) — 녹화 = 스트리밍 인코더일 때 방송 45초 뒤·5분마다 리플레이 버퍼를 저장해
  끝쪽 키프레임을 풀어 본다. 두 번 연속 한 가지 색이고 OBS 화면은 정상이면 **OBS 를 다시 켜고 같은 유튜브 방송으로 이어 보낸다**(방송 하나에 한 번).
  상태 줄 `⚠️ 유튜브 초록 화면` · `/health` `output_check` · config `output_check.{enabled, auto_restart, every_s}` · `obs.obs_task`.
- **`POST /obs-restart`**(운영자) — taskkill → `PS-OBS-Studio` 작업 → 웹소켓 → 같은 키로 송출. 태블릿 레일 버튼이 'output' 일 때 [🔄 OBS 다시 켜기].
- 카메라를 다시 여는 곳은 `obs_controller._restart_media` 하나 · 켜기 전 카메라 새로 고침 뒤 화면 검사(`_fix_uniform_cameras`).
- ⚠️ **python 변경 — stream_server 재시작 필요(방송 중 금지).** 테스트 `tests/test_output_check.py` · `test_camera_refresh.py` ⑦⑧.

## (옛) 초록 화면 — 방송 화면 검사 · 카메라 다시 연결 (2026-11-01)

신고 *"유투브 또 이러네ㅜㅜ 나 외부여서 가서 고치지도 못하는데"* — 유튜브엔 초록 화면만 나가는데 `/health` 숫자는 전부 정상이었다
(카메라 영상을 푸는 하드웨어 디코더가 빈 프레임을 냈다 — 송출 숫자·카메라 '재생 중' 으로는 안 보인다).

- **방송 중 20초마다 화면을 찍는다**(`stream_health.frame_verdict` · `obs_controller.frame_pixels` — 32x18 스크린샷) — 카메라가
  두 번 연속 한 가지 색이면 **다시 열고**, 네 번째면 **하드웨어 디코딩을 끈다**(`camera_frame_step`). 카메라가 멀쩡한데 방송 장면만
  단색이면 알리기만 한다. 상태 줄 `⚠️ 카메라 화면 깨짐` · `/health` `camera_frame` · 로그 `📷`.
- **`POST /camera-restart`**(운영자 토큰) — 다시 열고 4초 뒤 확인 · 그래도 단색이면 하드웨어 디코딩 끄고 다시 확인 → `{ok, text, sources}` ·
  `{hwDecodeOff:true}` 면 바로 끈다. 태블릿 **[📷 카메라 다시 연결]**(하이라이트 레일 아래) · 밖에선 폰으로 `https://obs.padelsociety.co.kr`.
- 인코딩 점검이 더는 '카메라 하드웨어 디코딩 켜기' 를 권하지 않는다(10-24 에 권했다 — 켠 뒤 초록 화면).
- ⚠️ **python 변경 — stream_server 재시작 필요(방송 중 금지).** 테스트 `tests/test_camera_frame.py`. 사람이 할 일은
  `setup/SETUP_LIVESTREAM_PC.md` 8절 '유튜브에 초록 화면만 나간다'.

## 방송 PC 는 그 센터 사람만 — 센터 대조 (2026-11-09 · 방송 서버만 · 태블릿 그대로)

검토(S4-04 · B1-06): 운영자 확인이 myProfile 의 **역할만** 봐서 **어느 센터의 `center_admin` 이든**(가맹·비가맹 · 권한 0개 부관리자 포함)
본점 유튜브 방송을 켜고·끄고·아무 영상에나 [무효] 를 붙일 수 있었다. 대표 결정: 방송 PC 는 **본점 관리자 · 그 센터에 묶인 코트 태블릿 계정 · 슈퍼어드민만**.

- 서버가 Playus 백엔드 **`GET /api/stream/operator-check[?centerId=…]`** 에 묻는다 → `data.ok`(늘 200 · 거절 문장은 `data.message` 그대로 태블릿에 뜬다 — 403).
  판정은 백엔드 `utils/operatorScope.streamOperatorVerdict` 하나: 슈퍼어드민 ∨ 그 센터에 묶인 `league_operator`(`Users.operatorCenterIds` · 비어 있으면 본점) ∨
  그 센터 관리자(메인 · 부관리자 content · centerAdminFor).
- `config.json` `control_auth.center_id` = 이 방송 PC 의 센터 id — **김포본점 id 를 적어 둔다**. 비우면 백엔드의 본점 설정(서버 .env `HQ_CENTER_ID` ·
  없으면 `PUBLIC_BOOKING_CENTER_ID`)을 쓴다 — ⚠️ 어스센터(`isDefault` — 코트 0 개 가상 센터)가 **아니다**. 비어 있으면 시작 로그에 ⚠️, 모양이 틀리면 ❌ · 확인은 전부 403.
- ⚠️ **배포 순서**: ① 백엔드 서버 .env 에 `HQ_CENTER_ID=<김포본점 id>` ② 코트 태블릿 계정을 김포에 묶기(`node scripts/operator_centers.js` 로 먼저 점검 →
  `--email pscourt@padelsociety.co.kr --set <김포본점 id> --apply`) ③ 백엔드 배포 ④ 이 PC `config.json` 에 `center_id` 를 적고 **방송이 없을 때** stream_server 재시작.
  ①② 없이 ③ 을 하면 그 순간 공용 태블릿 계정이 김포 리그를 잃는다(백엔드 판정). ④ 는 ③ 뒤 — 새 stream_server 가 옛 백엔드를 만나면
  옛 역할 규칙(`center_admin` 빼고)으로 돈다.
- 통과 10분 · 거절 30초 · 백엔드가 안 닿으면 12시간 안에 통과했던 토큰만 — 예전 그대로. 그래서 계정의 센터를 바꾸면 **길게는 10분 뒤**에 따라온다.
- **옛 백엔드**(그 주소가 없어 404)면 예전 myProfile 역할로 본다 — 그때 기본 역할은 `league_operator` · `super_admin`(**`center_admin` 은 뺐다** — 센터를 못 가린다).
  `control_auth.roles` 는 이제 이 옛 길에만 쓴다. 백엔드를 먼저 배포하면 이 길은 안 탄다.
- ⚠️ **python 변경 — stream_server 재시작 필요(방송 중 금지).** 테스트 `tests/test_stream_server.py` ⑲ ⑳.

## 의존성 보안 경보 — Dependabot (2026-11-09 · 대표 결정 #19)

`.github/dependabot.yml` — `requirements.txt`(pip)의 **보안 업데이트만** 받는다(일반 버전 업데이트는 끔 · 보안 PR 은 하나로 묶음).
- ⚠️ 켜는 건 저장소 설정이다: GitHub › Settings › Code security 에서 **Dependency graph · Dependabot alerts · Dependabot security updates** 를 켠다.
  이 파일은 **main** 에 있어야 읽힌다.
- 보안 PR 을 머지해도 방송 PC 는 그대로다 — **방송이 없을 때** 아래 '새 버전 올리기' 다음에 `.venv\Scripts\pip install -r requirements.txt` 를 한 번 더 하고 재시작한다.
- `requirements.txt` 는 `>=` 범위라 다시 깔 때마다 다른 판이 들어온다 — 해시 잠금(`pip-compile --generate-hashes`)은 다음 일(검토 S7-08 (4)).
- 모노repo `playus/ps-rank/` 사본은 PR 을 열지 않는다 — 여기서 고친 뒤 그리로 되복사한다.
- ⚠️ **'주 1회' 가 아니다** — 보안 PR 은 취약점 권고가 나올 때마다 열린다(하나로 묶임 · 열린 보안 PR 한도는 GitHub 이 정한 10).
  파일의 '월 09:00' 은 일반 버전 업데이트의 주기라 지금은 쓰이지 않는다 — 일반 업데이트도 주 1회 받으려면 `open-pull-requests-limit` 을 0 → 3.

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
python tests\test_stream_server.py     # OBS·YouTube 없이 스트리밍 서버 규칙 (넘겨받기·이어 쓰기·겹침 포함)
python tests\test_obs_stop.py          # OBS 끄기는 멈춘 걸 확인해야 성공 · 켜기는 송출 중이면 거절
node tests\stream_handover.test.cjs    # 태블릿 — 끄기 기다리기 · 매치 id · 매치별 영상 주소
node tests\on_air_name.test.cjs        # 태블릿 — 방송 이름(onAir)으로 TV·앱 라이브·유튜브 · 화면은 실명
python tests\test_make_thumbnail_onair.py  # 썸네일 다시 만들기도 방송 이름 · 닉네임이면 사진 없음
node tests\par_step.test.cjs           # 태블릿 — 등급(PAR) STEP 2 · 다시하기 이어받기 · 저장 확인 · 완료 화면 PAR 한 줄
python tests\test_stream_stability.py  # 끊김 — 지연 normal · 자동 종료 끔 · 못 끝낸 방송 다시 끝내기 · OBS 재연결 · 방송 상태 판정
```
