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

1단계 스크립트가 만들어 둔다. `PUT_..._HERE` 두 곳만 실제 값으로 바꾼다.

```jsonc
{
  "cors_origins": ["https://padelociety.github.io", "http://localhost:3000"],
  "obs": {
    "host": "localhost",
    "port": 4455,
    "password": "PUT_OBS_WEBSOCKET_PASSWORD_HERE"   // ← 3단계에서 OBS가 만들어준 비밀번호
  },
  "youtube": { "privacy": "public", "latency": "ultraLow" },
  "highlight": {
    "api_base": "https://api.padelsociety.co.kr",
    "upload_key": "PUT_HIGHLIGHT_UPLOAD_KEY_HERE"   // ← VPS의 HIGHLIGHT_UPLOAD_KEY 와 완전히 같은 값
  }
}
```

> **⚠️ 저장 인코딩은 UTF-8.** `stream_server.py`가 이 파일을 `encoding='utf-8'`로 열기 때문에
> 메모장에서 **ANSI로 저장하면 서버가 아예 안 뜬다.** 메모장 하단 인코딩이 `UTF-8`인지 확인
> (`UTF-8 with BOM`도 안 된다 — `json.load`가 BOM에서 깨진다).
> 그래서 이 템플릿은 한글을 한 글자도 넣지 않았다. 값도 ASCII만 넣으면 인코딩 사고가 안 난다.

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

stream_server는 OBS를 **WebSocket으로 원격 조종만** 한다. 실제 화면·소리·인코딩·
리플레이 버퍼는 전부 OBS에 미리 잡아둬야 한다. 여기서 빠뜨리면 나중에 태블릿에서
"버튼은 눌리는데 아무 일도 안 일어나는" 상태가 된다.

OBS 30 이상 기준. (obs-websocket 5.x가 내장된 **OBS 28 이상**이면 동작한다.)

### 3-0. 첫 실행

- **자동 구성 마법사가 뜨면 "취소"** — 아래에서 직접 잡는다. 마법사는 방송 설정까지
  건드리는데, 방송 설정은 stream_server가 매번 덮어쓰므로 의미가 없다.
- **설정 → 일반 → 시스템 트레이**: ☑ 시스템 트레이 아이콘 활성화
  자동 시작 작업이 `--minimize-to-tray` 로 띄우므로, 이게 꺼져 있으면 부팅할 때마다
  큰 창이 떠서 화면을 가린다.

### 3-1. WebSocket 서버 (필수) ⭐

**도구 → WebSocket 서버 설정**

- ☑ **WebSocket 서버 활성화**
- **서버 포트**: `4455` (기본값 그대로. 바꿨다면 `config.json`의 `obs.port`도 같이)
- ☑ **인증 활성화** (켜둔 채로 둔다)
- **연결 정보 표시** 버튼 → 뜬 창의 **서버 비밀번호** 복사

복사한 비밀번호를 `C:\dev\PS-rank\config.json` 의
`"password": "PUT_OBS_WEBSOCKET_PASSWORD_HERE"` 자리에 붙여넣는다. **UTF-8로 저장.**

> 이게 없으면 stream_server가 OBS를 전혀 제어하지 못한다 — 방송 시작도, 하이라이트도,
> 리플레이 버퍼 유지도 전부 이 연결 위에서 돌아간다.
> 태블릿에는 `📡 스트리밍 서버 꺼짐` 이 아니라 "시작을 눌렀는데 실패" 로 나타난다.

### 3-2. 출력 모드를 '고급'으로

**설정 → 출력 → 출력 모드: 고급**

단순 모드에는 리플레이 버퍼 탭이 없다. 반드시 고급으로 둔다.

**방송 탭** — 인코더만 정한다(서버·키는 건드리지 않는다):

| 항목 | 권장 |
|---|---|
| 인코더 | **하드웨어(NVENC / QSV / AMF)** — CPU 인코딩(x264)은 상시 운영에서 PC를 잡아먹는다 |
| 비트레이트 | 1080p60이면 `6000~9000 Kbps`, 1080p30이면 `4500~6000 Kbps` |
| 키프레임 간격 | **2초** (YouTube 요구사항. `0`(자동)으로 두면 거부될 수 있다) |
| 프로파일 | `high` |

### 3-3. 녹화 형식 — mp4 (하이라이트가 여기 얹힌다)

**설정 → 출력 → 녹화 탭**

- **녹화 형식: MPEG-4 (.mp4)** 또는 **하이브리드 MP4**
- **녹화 경로**: 여유 있는 드라이브 (하이라이트 원본이 여기 쌓인다)

리플레이 버퍼는 **녹화 탭의 형식·인코더를 그대로 따른다.** 별도 설정이 없다.

- 백엔드는 `mp4 / mkv / mov / webm` 만 받는다.
- 업로드 전에 ffmpeg로 `-movflags +faststart` 리먹스를 하는데 이건 **MP4 계열 전용**이다.
  mkv로 두면 리먹스가 실패해 원본이 그대로 올라가고, 폰에서 받으면서 재생할 때 끊긴다.

### 3-4. 리플레이 버퍼 (🎬 하이라이트 버튼) ⭐

**설정 → 출력 → 리플레이 버퍼 탭**

- ☑ **리플레이 버퍼 활성화**
- **최대 재생 시간: 90** (초)
- **최대 메모리**: `0`(무제한) 또는 넉넉히. 여기를 작게 잡으면 90초를 채우지 못하고
  실제로는 20~30초만 남는다.

> **90초인 이유**: 태블릿의 60초 버튼은 90초 버퍼의 **뒤쪽 60초만 잘라서** 올린다.
> 버퍼가 60초면 잘라낼 여유가 없어 앞부분이 붙거나 짧아진다.
> 이 길이는 **WebSocket으로 못 바꾼다** — 여기서 미리 잡아야 한다.

stream_server가 20초마다 버퍼 상태를 확인해 꺼져 있으면 다시 켠다. 그래서 **방송 중이
아니어도** 언제든 하이라이트를 저장할 수 있다(연습 경기 등).

### 3-5. 비디오

**설정 → 비디오**

- 기본(캔버스) 해상도 = 출력(조정된) 해상도 = `1920x1080`
- **FPS: 30** 권장

> `config.json`의 `"latency": "ultraLow"`(초저지연)는 YouTube에서 해상도·프레임 제한이
> 있다. 화질이 계속 떨어지면 `"low"` 로 바꾼다.

### 3-6. 소스와 소리

**장면에 소스가 하나도 없으면 순수한 검은 화면이 방송된다.** OBS도 stream_server도
`/health`도 전부 정상이라 아무 데서도 티가 나지 않는다 — 시청자만 아는 고장이다.
(그래서 `check_livestream_pc.ps1`이 OBS에 직접 물어 소스 개수를 확인한다.)

#### IP 카메라 (RTSP) — 현재 PS 코트 방식

**소스 + → 미디어 소스** → 이름 `코트 카메라` → 확인

| 항목 | 값 |
|---|---|
| **로컬 파일** | **체크 해제** ⚠️ 풀어야 입력 칸이 나온다 |
| 입력 | `rtsp://<계정>:<비밀번호>@<카메라IP>:554/Streaming/Channels/101` |
| 입력 형식 | 비움 |
| 네트워크 버퍼링 | `1` MB (지연 줄이려면 낮게) |
| 재연결 지연 시간 | `5` 초 |
| **소스가 비활성일 때 파일 닫기** | **체크 해제** ⚠️ 체크되면 매번 재접속해 끊긴다 |
| 가능한 경우 하드웨어 디코딩 사용 | 체크 |

Hikvision 계열 경로: `/Streaming/Channels/101`(메인) · `/102`(서브, 저해상도).

OBS에 넣기 전에 **ffmpeg으로 먼저 찔러보면** 원인이 카메라인지 OBS인지 바로 갈린다
(venv에 imageio-ffmpeg이 들어 있다):

```powershell
$ff = & C:\dev\PS-rank\.venv\Scripts\python.exe -c "import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())"
& $ff -rtsp_transport tcp -i "rtsp://<계정>:<비밀번호>@<카메라IP>:554/Streaming/Channels/101" -t 5 -f null NUL 2>&1 |
  Select-String "Input #|Stream #|401|Unauthorized|Error|error|failed"
```

`Stream #0:0: Video: h264 ... 1920x1080` 이 나오면 정상.

> **DDNS 주소보다 LAN IP가 낫다.** DDNS 호스트명을 쓰면 같은 건물 안 카메라
> 영상이 인터넷을 한 바퀴 돌아 들어온다. LAN에서 카메라를 찾으려면 554 포트를 스캔한다:
>
> ```powershell
> $prefix = ((Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '169.*' -and $_.IPAddress -ne '127.0.0.1' } | Select-Object -First 1).IPAddress) -replace '\.\d+$',''
> $t = 1..254 | ForEach-Object { $c = New-Object Net.Sockets.TcpClient; [pscustomobject]@{IP="$prefix.$_"; C=$c; T=$c.ConnectAsync("$prefix.$_", 554)} }
> Start-Sleep 4
> $t | ForEach-Object { if ($_.T.Status -eq 'RanToCompletion') { Write-Host ("  554 -> " + $_.IP) }; $_.C.Close() }
> ```
>
> 여러 개 나오면 NVR과 카메라가 섞인 것이다. **카메라 직결이 낫다**(NVR을 거치면 지연이 는다).
> Hikvision 공장 기본 IP는 보통 `x.x.x.64`.
>
> ⚠️ **LAN IP를 쓰면 반드시 공유기에서 DHCP 예약(MAC↔IP 고정)을 건다.** 안 하면 공유기가
> 재부팅되는 날 IP가 바뀌어 방송이 통째로 죽는데, 무인 PC라 아무도 모른다.
> 예약을 걸 수 없으면 차라리 DDNS 주소를 그대로 쓴다.

⚠️ RTSP URL에는 카메라 비밀번호가 들어간다. OBS 장면 파일(`%APPDATA%\obs-studio\basic\scenes\*.json`)에
평문으로 저장되니 그 파일을 공유하거나 커밋하지 않는다.

#### 캡처카드 / USB 웹캠

**소스 + → 비디오 캡처 장치** → 장치 드롭다운에서 선택.
드롭다운이 비어 있으면 카메라가 안 잡힌 것 — 포맷 직후엔 **캡처카드 드라이버부터** 확인한다:

```powershell
Get-PnpDevice -Class Camera,Media,Image -PresentOnly | Select-Object Status, Class, FriendlyName | Format-Table -AutoSize
```

#### 공통

소스를 넣었으면 우클릭 → **변환 → 화면에 맞추기**(`Ctrl+F`)로 화면을 채운다.

### 3-7. 점수판 오버레이 (브라우저 소스)

**소스 + → 브라우저** → 이름 `점수판`

| 항목 | 값 |
|---|---|
| URL | `https://padelociety.github.io/ps-scoreboard/obs.html` |
| 너비 | **680** (페이지가 680px 고정) |
| 높이 | **300** (배너가 뜰 여유 포함) |
| **보이지 않을 때 소스 종료** | **체크 해제** (라이브 갱신 유지) |
| 사용자 지정 CSS | **비운다** — 페이지가 이미 투명이다 |

넣은 뒤 소스 목록에서 **카메라 위**로 올리고, 화면 원하는 구석으로 옮긴다.
크기를 바꾸고 싶으면 모서리를 **비율 유지**로 드래그한다(가로세로 따로 늘리면 뭉개진다).

> ⚠️ **`tv.html`은 방송에 쓰지 않는다.** 그건 현장 TV·키오스크용이라 배경이 잉크로 꽉 차
> 있고, 뷰 전환 버튼·빌드 표시가 화면에 그대로 나간다. 방송용은 `obs.html`이다.

**자리 잡기**: 경기가 없으면 오버레이는 **아무것도 안 보인다**(정상 — 빈 점수판이
방송에 나가지 않게 한 것이다). 미리 위치를 잡으려면 URL 뒤에 **`?demo=1`** 을 붙여
샘플 점수판을 띄우고, 자리를 정한 뒤 파라미터를 빼서 저장한다.

**화면 구성**: 상단 제목바(리그·매치) / 좌측 PS 로고 패널 / 팀별로 세트 3칸 + 포인트.
스타포인트·골든포인트일 때는 하단에 전용 배너가 뜬다.

**동작**: 태블릿에서 점수를 누르면 Firebase를 거쳐 즉시 반영된다. 경기가 끝나면 리그앱이
`visible=false`로 내려 오버레이가 스스로 사라진다. 방송 중 조작할 것이 없다.

**소리도 반드시 확인한다.** 오디오 믹서에 레벨이 움직여야 한다.
무음으로 나가면 시청자에겐 방송이 고장 난 것처럼 보이는데, 로그에는 아무 문제도 안 남는다.

- 쓰지 않는 오디오 장치는 **설정 → 오디오**에서 '사용 안 함'으로 (기본 데스크톱 소리가
  섞여 나가는 사고 방지)

> 씬이 여러 개면 **방송에 쓸 씬을 활성(Program) 씬으로 두고 끝낸다.**
> stream_server는 씬을 자동으로 바꾸지 않는다 — 켜져 있는 씬 그대로 나간다.

### 3-7. 방송(스트림) 설정은 건드리지 않는다

**설정 → 방송** 은 손대지 않아도 된다. 방송을 시작할 때마다 stream_server가
YouTube에서 새 RTMP 주소·스트림 키를 발급받아 OBS에 밀어 넣는다(`rtmp_custom`).
여기에 수동으로 넣은 값은 다음 방송에서 덮어써진다.

### 3-8. 확인

OBS를 켜둔 채로:

```powershell
# config.json 을 다시 읽게 재시작. Restart-ScheduledTask 라는 cmdlet 은 없다 — Stop + Start.
Stop-ScheduledTask -TaskName PS-StreamServer
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Start-ScheduledTask -TaskName PS-StreamServer
Start-Sleep 15
Invoke-RestMethod http://127.0.0.1:5000/health
```

기대 결과:

```
ok            : True
streaming     : False
buffer_ready  : True      ← 이게 True 여야 하이라이트 버튼이 산다
```

`buffer_ready`가 `False`면 순서대로 의심한다:
1. OBS가 안 떠 있다
2. `config.json`의 `obs.password`가 틀렸다 (3-1을 다시)
3. 리플레이 버퍼가 비활성이다 (3-4를 다시)

로그: `Get-Content C:\dev\PS-rank\logs\stream_server_*.log -Tail 30`

### 3-9. 유지보수 — 하이라이트 원본이 쌓인다

🎬 를 누를 때마다 3-3의 **녹화 경로에 mp4가 하나씩 영구히 쌓인다**(업로드와 별개).
상시 운영이라 시즌이 지나면 수십 GB가 된다. 가끔 비워준다.

---

## 4단계 — VPS에 터널 공개키 등록 ⭐

**포맷으로 예전 SSH 키가 사라졌다.** 새 키를 VPS에 등록하지 않으면 터널이 절대 안 붙고,
태블릿은 계속 VPS 정적 폴백 페이지만 보게 된다(스트리밍 버튼 없음).

1단계에서 출력된 공개키(`ssh-ed25519 AAAA... obs-tunnel`)를 준비한다.

> ⚠️ **아래 리눅스 명령들을 PowerShell 창에 통째로 붙여넣지 않는다.**
> `ssh root@...` 와 그 뒤 명령을 함께 붙이면, PowerShell이 뒷줄까지 자기 문법으로
> 파싱하려다 `'<' 연산자는 나중에 사용하도록 예약되어 있습니다` 로 죽는다.
> 아래 **방법 A**(PowerShell에서 한 줄)를 쓰면 이 문제가 없다.

**방법 A — PowerShell에서 한 줄 (권장)**

`AAAA...` 부분만 1단계에서 출력된 실제 공개키로 바꿔 붙여넣는다:

```powershell
ssh root@62.72.56.88 "id obstunnel >/dev/null 2>&1 || adduser --disabled-password --gecos '' obstunnel; install -d -m 700 -o obstunnel -g obstunnel /home/obstunnel/.ssh; echo 'ssh-ed25519 AAAA...여기에_붙여넣기... obs-tunnel' >> /home/obstunnel/.ssh/authorized_keys; chmod 600 /home/obstunnel/.ssh/authorized_keys; chown -R obstunnel:obstunnel /home/obstunnel/.ssh; echo '--- REGISTERED ---'; cat /home/obstunnel/.ssh/authorized_keys"
```

끝에 현재 `authorized_keys` 내용을 찍어준다.

**방법 B — VPS에 접속해서**

먼저 `ssh root@62.72.56.88` 만 실행하고, **리눅스 프롬프트(`root@...:~#`)가 뜬 뒤에** 아래를 붙여넣는다:

```bash
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
(`nano /home/obstunnel/.ssh/authorized_keys` 로 열어 방금 넣은 줄만 남긴다.)

### 죽은 터널이 포트를 붙잡는 문제 (권장)

PC가 정전으로 갑자기 죽으면 VPS의 sshd가 한동안 5001 포트를 물고 있어,
PC가 복구돼도 터널이 `remote port forwarding failed` 로 재연결에 실패할 수 있다.
VPS에서 한 번만 설정해두면 알아서 정리된다:

PowerShell에서 한 줄로:

```powershell
ssh root@62.72.56.88 "grep -q '^ClientAliveInterval' /etc/ssh/sshd_config || printf '\n# dead reverse tunnels: free port 5001 quickly after the OBS PC loses power\nClientAliveInterval 30\nClientAliveCountMax 3\n' >> /etc/ssh/sshd_config; sshd -t && systemctl reload sshd && echo SSHD_OK"
```

### 확인

먼저 키가 실제로 먹는지 PC에서 직접 시험한다 (작업 스케줄러가 하는 것과 똑같은 명령):

```powershell
ssh -N -T -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new `
    -i "$env:USERPROFILE\.ssh\obs_tunnel_ed25519" `
    -R 127.0.0.1:5001:127.0.0.1:5000 obstunnel@62.72.56.88
```

- **아무 출력 없이 멈춰 있으면 성공이다** (터널이 붙어 대기 중). `Ctrl+C` 로 끊는다.
- `Permission denied (publickey)` → 공개키가 아직 VPS에 안 들어갔다.
- `remote port forwarding failed for listen port 5001` → 죽은 옛 터널이 포트를 잡고 있다.
  아래 sshd 설정을 넣거나, VPS에서 `ss -tlnp | grep 5001` 로 잡은 프로세스를 정리한다.

성공하면 작업으로 상시 실행:

```powershell
Start-ScheduledTask -TaskName OBS-VPS-Tunnel
ssh root@62.72.56.88 "ss -tlnp | grep 5001"     # VPS에서 포트가 잡혔는지
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
