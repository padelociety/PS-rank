# 라이브스트림 PC — 포맷 후 처음부터 세팅

빠델소사이어티 리그 라이브 방송용 PC(= **OBS PC**)를 새 Windows에서 다시 세우는 절차.
전원 고장·포맷으로 **저장돼 있던 것이 전부 사라진 상태**를 전제로 쓴다.

> 배경 문서: VPS/nginx 쪽 운영은 모노repo(private `padelociety/playus`)의
> `backend/nginx/deploy_obs_court.md` — 이 PC가 꺼졌을 때 서비스가 어떻게 되는지.

---

## 1. 이 PC가 하는 일

```
 태블릿 ──https──▶ obs.padelsociety.co.kr  (VPS 62.72.56.88 / nginx)
                     │
                     ├─ /api/  ────────────▶ Playus 백엔드 prod :4001   (이 PC 안 거침)
                     │
                     └─ 그 외 ──역SSH터널──▶ [이 PC] stream_server.py :5000
                                                  │
                                                  ├─ OBS WebSocket :4455  → 스트림 시작/중지, 리플레이 버퍼
                                                  ├─ YouTube Data API      → 라이브 방송 생성/종료
                                                  └─ 하이라이트 클립       → 백엔드 /api/highlight/upload
```

이 PC 위에서 **항상 돌고 있어야 하는 것 3가지**:

| # | 무엇 | 어떻게 | 죽으면 |
|---|---|---|---|
| 1 | **OBS Studio** | 작업 스케줄러 `PS-OBS-Studio` | 스트림·하이라이트 전부 불가 |
| 2 | **stream_server.py** (:5000) | 작업 스케줄러 `PS-StreamServer` | 앱에 `📡 스트리밍 서버 꺼짐` |
| 3 | **역SSH터널** (:5000 → VPS :5001) | 작업 스케줄러 `OBS-VPS-Tunnel` | 태블릿이 VPS 정적 폴백 페이지를 봄 |

**PC가 꺼져도 리그 운영 자체는 계속된다** — 점수 입력·매치 선택·순위는 `/api`(백엔드) + Firebase 직결이라
이 PC를 안 탄다. PC 없이 못 쓰는 건 **스트림 시작/중지·하이라이트뿐**이다.
그래도 라이브가 리그의 핵심이니 **항상 켜져 있어야** 한다.

---

## 2. 준비물 (시작 전에 확보)

| 필요한 것 | 어디서 | 없으면 |
|---|---|---|
| VPS root 접근 (`root@62.72.56.88`) | 데스크톱의 SSH 키 | **4단계 터널 등록 불가** → 태블릿이 이 PC에 못 붙음 |
| `HIGHLIGHT_UPLOAD_KEY` 값 | VPS `/etc/playus/.env.prod` | 하이라이트 업로드만 안 됨 (방송은 됨) |
| Google 계정 (YouTube 채널 소유) | — | 라이브 방송 생성 불가 |
| 캡처카드 / 카메라 | 코트 장비 | 화면 소스 없음 |
| BIOS 진입 방법 (Del / F2) | 메인보드 | **0단계 불가** → 정전 후 자동 복구 안 됨 |

---

## 3. 순서

0. BIOS — 전원 복구 시 자동 부팅
1. 자동 설치 스크립트 실행
2. 비밀 파일 3종 채우기
3. OBS 설정
4. VPS에 터널 공개키 등록
5. YouTube 최초 인증
6. 자동 로그온
7. 검증 + 정전 테스트

---

## 0단계 — BIOS: 전원이 돌아오면 자동으로 켜지게 ⭐

**전원이 망가졌다 고쳐진 PC라 이게 가장 중요하다.** 이 설정이 없으면 정전 한 번에
누가 현장에 가서 전원 버튼을 누를 때까지 라이브가 죽는다.

부팅 중 `Del` 또는 `F2` → 아래 항목을 찾아 **Power On**(또는 Last State)으로:

| 메인보드 | 항목 이름 |
|---|---|
| ASUS | Advanced → APM Configuration → **Restore AC Power Loss** |
| MSI | Settings → Advanced → Power Management Setup → **Restore after AC Power Loss** |
| Gigabyte | Settings → Platform Power → **AC BACK** |
| ASRock | Advanced → Chipset Configuration → **Restore on AC/Power Loss** |

- `Power Off`(기본값)면 **절대 자동 복구되지 않는다**. 반드시 바꾼다.
- `Last State`도 괜찮지만, 정전 시점에 꺼져 있었다면 안 켜진다. **`Power On`이 가장 안전**하다.
- 같이 켜두면 좋은 것: **ErP / Deep Sleep = Disabled** (켜져 있으면 위 설정이 무시되는 보드가 있다.)

> 이 설정만은 Windows에서 확인할 방법이 없다. **7단계에서 플러그를 뽑아 직접 시험**한다.

---

## 1단계 — 자동 설치 스크립트

### 1-1. 저장소 클론

PC가 쓰는 코드와 이 세팅 키트는 **public repo `padelociety/PS-rank`** 에 있다.
공개 저장소라 로그인·토큰이 필요 없다. 관리자 PowerShell에서:

```powershell
# 1) Git 설치 (이미 있으면 그냥 넘어간다)
winget install --id Git.Git -e --source winget --accept-source-agreements --accept-package-agreements

# 2) 방금 설치한 Git을 '이 창'에 인식시킨다 (새 창 여는 것과 같은 효과)
$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")

# 3) 확인 후 클론
git --version
git clone https://github.com/padelociety/PS-rank.git C:\dev\PS-rank
```

> ⚠️ **`'git' 용어가 ... 인식되지 않습니다`** 가 뜨면 Git이 망가진 게 아니라
> **PATH가 이 창에 아직 반영되지 않은 것**이다. 설치 프로그램이 PATH를 바꿔도
> 이미 떠 있는 PowerShell 창에는 적용되지 않는다.
> 위 2)번 줄을 실행하거나, PowerShell 창을 **관리자 권한으로 새로 열면** 된다.

<details>
<summary>winget도 없는 구형 Windows라면</summary>

https://github.com/padelociety/PS-rank/archive/refs/heads/main.zip 을 받아
아무 임시 폴더(예: `C:\dev\setup-tmp`)에 풀고, 거기서 1-2를 실행한다.
`C:\dev\PS-rank` **에 직접 풀지 않는다** — 스크립트가 그 경로에 clone을 하기 때문에
비어 있어야 한다. 스크립트가 실행 중 자기 자신을 `C:\dev\PS-rank\setup\` 로 복사하므로
임시 폴더는 나중에 지워도 된다.
</details>

### 1-2. 관리자 PowerShell에서 실행

**Win+X → 터미널(관리자)** 로 새 창을 열고:

```powershell
cd C:\dev\PS-rank\setup
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_livestream_pc.ps1
```

지금 열려 있는 일반 창에서 바로 승격시키려면 (UAC 창이 뜨면 **예**):

```powershell
Start-Process powershell -Verb RunAs -ArgumentList '-NoExit','-NoProfile','-ExecutionPolicy','Bypass','-File','C:\dev\PS-rank\setup\setup_livestream_pc.ps1'
```

> `-NoExit` 를 빼지 않는다. 없으면 스크립트가 끝나는 순간 창이 닫혀
> **VPS에 등록해야 할 SSH 공개키 출력을 놓친다**(4단계에서 필요).

> 관리자가 아니면 스크립트가 `Run this script as Administrator` 로 **일부러 멈춘다.**
> 절전 설정·작업 스케줄러 등록·방화벽 규칙이 전부 관리자 권한을 요구하는데,
> 그냥 진행하면 절반만 적용된 채 "된 것처럼" 끝나기 때문이다.

스크립트가 하는 일:

| 단계 | 내용 |
|---|---|
| 1 | winget으로 **Python 3.12 / Git / OBS Studio** 설치 |
| 2 | `https://github.com/padelociety/PS-rank.git` → `C:\dev\PS-rank` 클론 |
| 3 | `.venv` 생성 + `requirements.txt` 설치 |
| 4 | `config.json` 생성(예제 복사) |
| 5 | **절전·최대절전·빠른시작·USB 선택적 절전 전부 끄기**, 화면보호기 끄기, Windows Update 자동 재부팅 차단 |
| 6 | 방화벽 TCP 5000 인바운드 허용 (Private/Domain) |
| 7 | 터널용 SSH 키 생성 → **공개키를 화면에 출력** |
| 8 | 작업 스케줄러 3개 등록 (`OBS-VPS-Tunnel` / `PS-StreamServer` / `PS-OBS-Studio`) |
| 9 | 자동 로그온 상태 확인 |

끝나면 **"Still to do by hand"** 목록이 뜬다. 그게 2~6단계다.

- 몇 번을 다시 돌려도 안전하다(이미 된 건 건너뛴다).
- winget이 없는 구형 Windows면 Python/Git/OBS를 직접 설치하고 `-SkipInstall`로 다시 돌린다.

> **출력된 공개키는 지우지 말고 남겨둔다** — 4단계에서 쓴다.

---

## 2단계 — 비밀 파일 3종 (repo에 없다)

전부 `C:\dev\PS-rank\` 루트에 둔다. 셋 다 **git에 절대 올라가면 안 된다**
(PS-rank는 **public repo**다). 스크립트가 `.git/info/exclude`에 등록해 두지만,
직접 `git add` 하지 않도록 주의한다.

### ① `config.json`

`streaming_config.json.example`을 복사한 것. 두 곳을 채운다.

```jsonc
{
  "obs": {
    "host": "localhost",
    "port": 4455,
    "password": "여기 ← 3단계에서 OBS가 만들어준 비밀번호"
  },
  "youtube": { "privacy": "public", "latency": "ultraLow" },
  "highlight": {
    "api_base": "https://api.padelsociety.co.kr",
    "upload_key": "여기 ← VPS의 HIGHLIGHT_UPLOAD_KEY 와 완전히 같은 값"
  }
}
```

`upload_key` 값 확인:

```bash
ssh root@62.72.56.88 "grep HIGHLIGHT_UPLOAD_KEY /etc/playus/.env.prod"
```

값이 다르면 업로드가 **403**으로 조용히 실패한다(방송은 정상). 서버에 키가 아예 없으면 503이다.

### ② `client_secrets.json` — YouTube OAuth

1. [Google Cloud Console](https://console.cloud.google.com/) → 기존 프로젝트 선택(또는 새로 생성)
2. **API 및 서비스 → 라이브러리 → YouTube Data API v3 → 사용 설정**
3. **사용자 인증 정보 → 사용자 인증 정보 만들기 → OAuth 클라이언트 ID → 애플리케이션 유형: 데스크톱 앱**
4. JSON 다운로드 → `C:\dev\PS-rank\client_secrets.json` 으로 저장

> **⚠️ OAuth 동의 화면을 반드시 "프로덕션"으로 게시할 것.**
> **"테스트" 상태로 두면 refresh token이 7일마다 만료된다** — 무인 PC에서 이건 곧
> *"지난주엔 됐는데 오늘 방송이 안 켜져요"* 로 나타난다. 매번 사람이 붙어 재인증해야 한다.
> `API 및 서비스 → OAuth 동의 화면 → 앱 게시` 를 눌러 **In production** 으로 만든다.
> (내부 사용이라 Google 심사는 필요 없다. 경고 화면은 "고급 → 계속"으로 통과.)

### ③ `youtube_token.pickle`

직접 만들지 않는다. **5단계**에서 최초 인증할 때 자동 생성된다.

---

## 3단계 — OBS 설정 (수동, 스크립트로 불가)

OBS를 한 번 실행하고 아래를 설정한다.

### 3-1. WebSocket 서버 (필수)

**도구 → WebSocket 서버 설정**
- ☑ **WebSocket 서버 활성화**
- 포트 `4455`
- **연결 정보 표시** → 비밀번호 복사 → `config.json`의 `obs.password`에 붙여넣기

이게 없으면 stream_server가 OBS를 전혀 제어하지 못한다.

### 3-2. 리플레이 버퍼 (하이라이트 버튼)

**설정 → 출력 → 출력 모드: 고급 → 리플레이 버퍼 탭**
- ☑ **리플레이 버퍼 활성화**
- **최대 재생 시간: 90초** (앱의 60초 버튼은 이 90초에서 뒤쪽만 잘라 쓴다)

**설정 → 출력 → 녹화 탭**
- **녹화 형식: mp4** — 백엔드가 `mp4/mkv/mov/webm`만 받고, 폰 재생 호환도 mp4가 제일 낫다.

> 버퍼 길이는 WebSocket으로 못 바꾼다. 여기서 미리 잡아야 한다.
> stream_server가 20초마다 버퍼를 확인해 꺼져 있으면 다시 켜므로, 방송 중이 아니어도
> 하이라이트를 저장할 수 있다.

### 3-3. 소스

장면에 **캡처카드 / 웹캠**을 추가하고 화면이 나오는지 확인한다.

### 3-4. 스트림 설정은 건드리지 않는다

**설정 → 방송** 은 손대지 않아도 된다. 방송을 시작할 때마다 stream_server가
YouTube에서 새 RTMP 주소·스트림 키를 발급받아 OBS에 밀어 넣는다(`rtmp_custom`).

---

## 4단계 — VPS에 터널 공개키 등록 ⭐

**포맷으로 예전 SSH 키가 사라졌다.** 새 키를 VPS에 등록하지 않으면 터널이 절대 안 붙고,
태블릿은 계속 VPS 정적 폴백 페이지만 보게 된다(스트리밍 버튼 없음).

1단계에서 출력된 공개키(`ssh-ed25519 AAAA... obs-tunnel`)를 준비하고:

```bash
ssh root@62.72.56.88

# obstunnel 계정이 이미 있는지 확인 (예전에 쓰던 계정이 남아 있을 것)
id obstunnel || adduser --disabled-password --gecos "" obstunnel

install -d -m 700 -o obstunnel -g obstunnel /home/obstunnel/.ssh

# ↓ 1단계에서 출력된 공개키를 그대로 붙여넣는다 (한 줄)
cat >> /home/obstunnel/.ssh/authorized_keys <<'EOF'
ssh-ed25519 AAAA...여기에_붙여넣기... obs-tunnel
EOF

chmod 600 /home/obstunnel/.ssh/authorized_keys
chown -R obstunnel:obstunnel /home/obstunnel/.ssh
```

**옛날 키 줄은 지운다** — 개인키는 포맷과 함께 사라져 이제 아무도 쓸 수 없다.

### 죽은 터널이 포트를 붙잡는 문제 (권장)

PC가 정전으로 갑자기 죽으면 VPS의 sshd가 한동안 5001 포트를 물고 있어,
PC가 복구돼도 터널이 `remote port forwarding failed` 로 재연결에 실패할 수 있다.
VPS에서 한 번만 설정해두면 알아서 정리된다:

```bash
ssh root@62.72.56.88
grep -q '^ClientAliveInterval' /etc/ssh/sshd_config || cat >> /etc/ssh/sshd_config <<'EOF'

# 죽은 역터널을 빨리 정리 — OBS PC가 정전으로 사라져도 5001이 오래 잠기지 않게
ClientAliveInterval 30
ClientAliveCountMax 3
EOF
sshd -t && systemctl reload sshd
```

### 확인

PC에서 터널 작업을 시작하고:

```powershell
Start-ScheduledTask -TaskName OBS-VPS-Tunnel
```

VPS에서 포트가 잡혔는지:

```bash
ssh root@62.72.56.88 "ss -tlnp | grep 5001"
```

---

## 5단계 — YouTube 최초 인증 (포그라운드로 딱 한 번)

`stream_server.py`는 첫 방송 때 브라우저를 띄워 Google 로그인을 요구한다.
스케줄 작업은 **숨김 창**으로 돌기 때문에, 그 상태로 두면 인증 창을 볼 수 없다.
반드시 한 번은 손으로 띄운다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\dev\PS-rank\setup\run_stream_server.ps1 -Foreground
```

인증만 따로 마치고 싶으면(방송을 실제로 켜지 않고):

```powershell
cd C:\dev\PS-rank
.\.venv\Scripts\python.exe -c "import json;from youtube_api import YouTubeAPI;YouTubeAPI(json.load(open('config.json',encoding='utf-8')))._ensure_auth()"
```

- 브라우저가 열리면 **YouTube 채널을 가진 계정**으로 로그인 → 권한 허용
- "이 앱은 확인되지 않았습니다" → **고급 → (앱 이름)(으)로 이동**
- 성공하면 `C:\dev\PS-rank\youtube_token.pickle` 이 생긴다

이 파일이 생긴 뒤에는 스케줄 작업(숨김 실행)으로 계속 돌아간다.

---

## 6단계 — 자동 로그온 ⭐

자동 시작 작업 3개는 전부 **로그온 시(AtLogOn)** 트리거다. OBS가 화면을 캡처하려면
실제 사용자 세션이 필요하기 때문이다. 자동 로그온이 꺼져 있으면 **부팅 후 잠금 화면에서
멈춰 아무것도 시작되지 않는다** — 0단계 BIOS 설정이 무의미해진다.

**권장: Sysinternals Autologon** (비밀번호를 LSA 비밀 영역에 암호화 저장)

1. https://learn.microsoft.com/sysinternals/downloads/autologon 에서 받기
2. `Autologon.exe` 실행 → 사용자명 / 도메인 / 비밀번호 입력 → **Enable**

`netplwiz`("사용자 이름과 암호를 입력해야…" 체크 해제)도 되지만, 최신 Windows 11에서는
그 체크박스가 숨겨져 있는 경우가 많다.

> **⚠️ 자동 로그온 계정 = 1단계를 실행한 계정이어야 한다.**
> 작업 3개는 스크립트를 돌린 계정으로 등록되고, 터널 개인키도 그 계정 프로필
> (`%USERPROFILE%\.ssh`)에 있다. 다른 계정으로 자동 로그온하면 **부팅은 되는데 아무것도 안 뜬다.**
> 1단계 실행 시 다른 관리자 계정으로 UAC 승격했다면 특히 주의. `check_livestream_pc.ps1`이
> 이 불일치를 잡아준다.

> 이 PC는 자동 로그온 = **물리적으로 접근하면 누구나 바탕화면에 닿는다**는 뜻이다.
> 무인 방송 PC라 감수하는 트레이드오프다. PC를 잠글 수 있는 공간에 둔다.

---

## 7단계 — 검증

### 7-1. 자동 점검 스크립트

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\dev\PS-rank\setup\check_livestream_pc.ps1
```

파일 → 포트 → 작업 → 터널 → 공개 URL → 전원 설정 순서로 전부 확인하고
`ALL CLEAR` 또는 실패 항목을 알려준다.

`https://obs.padelsociety.co.kr/health` 검사는 **JSON이 오는지**까지 본다.
터널이 죽어 있으면 nginx가 정적 폴백 HTML을 **200으로** 돌려주기 때문에,
상태 코드만 봐서는 살아 있는 것처럼 보인다.

### 7-2. 손으로 확인

| 확인 | 기대 결과 |
|---|---|
| `curl http://localhost:5000/health` | `{"ok":true, ..., "buffer_ready":true}` |
| `curl https://obs.padelsociety.co.kr/health` | 위와 같은 **JSON** (HTML이면 터널 죽음) |
| 태블릿에서 `https://obs.padelsociety.co.kr` | 리그앱 + 스트리밍 버튼 노출 |
| 태블릿에서 매치 선택 → 스코어 입력 | YouTube 라이브 생성 + OBS 송출 시작 |
| 태블릿 🎬 버튼 | 잠시 뒤 하이라이트가 앱 목록에 뜸 |

### 7-3. 정전 복구 테스트 ⭐ (꼭 한다)

**리그 없는 날에** 한 번은 실제로 해본다:

1. 전원 케이블을 **뽑는다** (정상 종료가 아니라 진짜 전원 차단)
2. 30초 뒤 다시 꽂는다
3. **아무것도 만지지 않고** 기다린다
4. 5분 뒤 다른 기기에서 `https://obs.padelsociety.co.kr/health` → **JSON이 나와야 성공**

여기서 실패하면:

| 증상 | 원인 |
|---|---|
| 아예 안 켜짐 | 0단계 BIOS 설정 (`Restore on AC Power Loss`) |
| 켜지는데 잠금 화면에서 멈춤 | 6단계 자동 로그온 |
| 로그온은 되는데 OBS 팝업에서 멈춤 | OBS 안전 모드 대화상자 → 작업의 `--disable-shutdown-check` 인자 확인 |
| 전부 뜨는데 `/health`가 HTML | 4단계 터널 키 등록 / VPS 5001 잠김 |

---

## 8. 장애 대응

### 태블릿에 `📡 스트리밍 서버 꺼짐`

`check_livestream_pc.ps1` 부터 돌린다. 고치는 순서는 **항상 안쪽부터**:

```
OBS → OBS WebSocket(4455) → stream_server(5000) → 터널 → 공개 URL
```

```powershell
Get-ScheduledTask OBS-VPS-Tunnel, PS-StreamServer, PS-OBS-Studio | Select TaskName, State
Start-ScheduledTask -TaskName PS-StreamServer     # 필요한 것만 다시 시작
Get-Content C:\dev\PS-rank\logs\stream_server_*.log -Tail 50
```

### 태블릿에 리그앱은 뜨는데 스트리밍 버튼이 없다

VPS 폴백 페이지를 보고 있는 것 = **터널이 죽었다**. 점수 입력·순위는 정상 동작하니
경기는 계속 진행하고, 터널만 살리면 자동 복귀한다(되돌리는 조치 불필요).

### 하이라이트가 앱에 안 뜬다

1. `buffer_ready`가 `true`인가 → 아니면 OBS 리플레이 버퍼가 꺼진 것 (3-2)
2. 로그에 `하이라이트 업로드 실패 403` → `config.json`의 `upload_key` ≠ VPS `HIGHLIGHT_UPLOAD_KEY`
3. 로그에 `503` → VPS `.env`에 `HIGHLIGHT_UPLOAD_KEY`가 아예 없음
4. 로그에 `upload_key 없음` → `config.json`을 아직 안 채웠음 (PC에 파일만 저장됨)

### 방송 시작이 갑자기 안 된다 (전엔 됐는데)

십중팔구 **YouTube refresh token 만료**다. 로그에 `invalid_grant`가 보인다.

- 근본 원인: OAuth 동의 화면이 **"테스트"** 상태 → 2단계 ②의 경고 참고, **프로덕션으로 게시**
- 임시 복구: `youtube_token.pickle` 삭제 후 5단계 재인증
- 그 외: YouTube 채널의 라이브 스트리밍 권한 / 일일 한도(방송 생성은 하루 제한이 있다)

### Windows Update로 재부팅됐다

자동 로그온 + 작업 3개가 다시 뜨므로 보통은 알아서 복구된다.
1단계에서 `NoAutoRebootWithLoggedOnUsers`를 걸어 로그온 중 자동 재부팅은 막아뒀다.

---

## 9. 부록 — 요약표

### 포트

| 포트 | 무엇 | 노출 |
|---|---|---|
| 5000 | stream_server (Flask) | LAN(방화벽 Private/Domain) + 역터널 |
| 4455 | OBS WebSocket | localhost 전용 |
| 5001 | VPS 쪽 터널 수신 | VPS `127.0.0.1` 전용 |

### 이 PC의 파일

| 경로 | git | 비고 |
|---|---|---|
| `C:\dev\PS-rank\` | public repo 클론 | 런타임 코드 |
| `C:\dev\PS-rank\config.json` | **금지** | OBS 비번 + 업로드 키 |
| `C:\dev\PS-rank\client_secrets.json` | **금지** | YouTube OAuth |
| `C:\dev\PS-rank\youtube_token.pickle` | **금지** | 5단계에서 생성 |
| `C:\dev\PS-rank\.venv\` | 제외 | Python 패키지 |
| `C:\dev\PS-rank\logs\` | 제외 | 14일 자동 정리 |
| `%USERPROFILE%\.ssh\obs_tunnel_ed25519` | — | 터널 개인키 |

### 작업 스케줄러

| 이름 | 트리거 | 실행 |
|---|---|---|
| `PS-OBS-Studio` | 로그온 시 | `obs64.exe --startreplaybuffer --minimize-to-tray --disable-shutdown-check` |
| `PS-StreamServer` | 로그온 시 | `setup\run_stream_server.ps1` (죽으면 10초 뒤 재시작) |
| `OBS-VPS-Tunnel` | 로그온 시 | `obs_tunnel\obs_tunnel.ps1` (끊기면 5초 뒤 재연결) |

### 관련 문서

- `../obs_tunnel/setup_obs_tunnel.ps1` — 터널만 따로 세팅하던 예전 스크립트(이 문서 1단계가 대체)
- 모노repo(private `padelociety/playus`) 쪽:
  - `backend/nginx/deploy_obs_court.md` — VPS nginx·폴백 페이지 운영
  - `backend/nginx/obs.padelsociety.co.kr.conf` — nginx 설정 원본

---

## 10. 두 repo 동기화 (이 키트를 고칠 때)

이 `setup/` 폴더는 **두 곳에 같은 내용으로** 있다.

| repo | 역할 |
|---|---|
| `padelociety/playus` (private) — `ps-rank/setup/` | 원본. 모노repo에서 같이 관리 |
| `padelociety/PS-rank` (public) — `setup/` | **PC가 실제로 clone하는 쪽** |

PC는 public 쪽만 보므로, 키트를 고쳤으면 **PS-rank에도 반영해야 현장에 반영된다.**

```powershell
# 데스크톱에서
cd C:\dev\PS-rank
git pull
robocopy C:\dev\playus\ps-rank\setup .\setup /MIR
copy C:\dev\playus\ps-rank\.gitignore .\.gitignore
git add -A setup .gitignore
git commit -m "세팅 키트 동기화"
git push
```

그리고 PC에서 `git -C C:\dev\PS-rank pull` 하면 최신 키트가 내려온다.

> `stream_server.py` 등 런타임 코드도 마찬가지다. OBS PC의 stream_server는 시작할 때마다
> **PS-rank main의 raw**에서 `ps_court_playus.html`을 받아 서빙한다(`COURT_HTML_URL`).
> 즉 앱 페이지는 PS-rank가 진실의 원천이다.
