# fix_obs_broadcast.ps1 - "설정된 방송 없음" 대화상자를 영구히 없앤다.
#
# 증상
#   태블릿에서 "매치 시작"을 누르면 OBS에 "설정된 방송 없음 / 방송을 진행하기 전에
#   방송 설정이 필요합니다"가 뜨고, 태블릿은 "스트리밍 오류"로 끝난다.
#   OBS 제어판에 "방송 설정 관리" 버튼이 보이면 이 경우가 맞다 - 그 버튼은
#   계정 연동형 서비스(YouTube - RTMPS)일 때만 나온다.
#
# 원인
#   OBS는 시작할 때 두 군데에서 방송 설정을 읽는다.
#     - 프로필\service.json      : 서비스 종류(rtmp_common = YouTube 연동 / rtmp_custom)
#     - 프로필\basic.ini [Auth]  : 로그인해 둔 계정 토큰
#   여기에 YouTube 연동이 남아 있으면 OBS 화면은 "방송을 먼저 고르라"는 모드로 켜지고,
#   websocket으로 StartStream이 오면 송출 대신 저 모달만 띄운다. 모달이 UI 스레드를
#   붙잡아 뒤이은 websocket 요청까지 같이 막힌다.
#   stream_server가 websocket으로 rtmp_custom을 넣긴 하지만 그건 '지금 이 순간'의 값일
#   뿐, 화면 쪽 모드는 시작할 때 정해진 그대로다. 그래서 점검에서는 rtmp_custom으로
#   보이는데 실제로는 계속 막히는, 앞뒤가 안 맞아 보이는 상태가 된다.
#
# 하는 일
#   1) OBS 종료 (켜져 있는 채로 설정 파일을 고치면 종료할 때 메모리 값으로 덮어쓴다)
#   2) service.json을 rtmp_custom + YouTube RTMP 서버로 다시 쓴다 (스트림 키는 유지)
#   3) basic.ini에서 [Auth]와 [YouTube*] 섹션(로그인 토큰)을 지운다
#   4) PS-OBS-Studio 예약 작업을 --minimize-to-tray 없이 재등록 (창이 보이게)
#   5) OBS를 다시 띄우고 :4455가 열릴 때까지 기다린다
#
# 사용법:  관리자 PowerShell에서
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\setup\fix_obs_broadcast.ps1

[CmdletBinding()]
param(
  [string]$RtmpServer = "rtmp://a.rtmp.youtube.com/live2",
  [switch]$NoRestart   # 파일만 고치고 OBS는 직접 켜고 싶을 때
)

$ErrorActionPreference = "Stop"

function Say  ($m) { Write-Host "       $m" -ForegroundColor Gray }
function Ok   ($m) { Write-Host "  [OK] $m" -ForegroundColor Green }
function Warn ($m) { Write-Host "  [!!] $m" -ForegroundColor Yellow }
function Bad  ($m) { Write-Host "  [XX] $m" -ForegroundColor Red }
function Step ($m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }

# BOM 없는 UTF-8. Set-Content -Encoding UTF8 은 PowerShell 5.1에서 BOM을 붙이는데,
# OBS의 JSON/INI 파서는 BOM이 붙은 첫 줄을 못 읽는다.
function Write-Utf8NoBom ($path, $text) {
  [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
}

Write-Host ""
Write-Host "OBS 방송 설정 정리 ('설정된 방송 없음' 대화상자 제거)" -ForegroundColor White

# ------------------------------------------------------------- 활성 프로필 찾기
Step "1/5  OBS 프로필 찾기"

$obsRoot = Join-Path $env:APPDATA "obs-studio"
if (-not (Test-Path $obsRoot)) {
  Bad "OBS 설정 폴더가 없어요: $obsRoot"
  Say "OBS를 실행하는 그 계정으로 이 스크립트를 돌려야 합니다."
  exit 1
}

# global.ini의 [Basic] ProfileDir이 지금 쓰는 프로필. 못 읽으면 프로필이 하나뿐인
# 경우가 대부분이라 첫 번째 폴더로 대체한다.
$profileDir = $null
$globalIni = Join-Path $obsRoot "global.ini"
if (Test-Path $globalIni) {
  $inBasic = $false
  foreach ($line in (Get-Content $globalIni -Encoding UTF8 -ErrorAction SilentlyContinue)) {
    $t = $line.Trim()
    if ($t -match '^\[(.+)\]$') { $inBasic = ($Matches[1] -ieq "Basic"); continue }
    if ($inBasic -and $t -match '^ProfileDir=(.*)$') { $profileDir = $Matches[1].Trim(); break }
  }
}

$profPath = $null
if ($profileDir) { $profPath = Join-Path $obsRoot "basic\profiles\$profileDir" }
if (-not ($profPath -and (Test-Path $profPath))) {
  $first = Get-ChildItem (Join-Path $obsRoot "basic\profiles") -Directory -ErrorAction SilentlyContinue |
             Select-Object -First 1
  if ($first) { $profPath = $first.FullName }
}
if (-not ($profPath -and (Test-Path $profPath))) {
  Bad "OBS 프로필 폴더를 못 찾았어요 ($obsRoot\basic\profiles)"
  exit 1
}
Ok "프로필: $profPath"

$serviceJson = Join-Path $profPath "service.json"
$basicIni    = Join-Path $profPath "basic.ini"

# 지금 뭐가 들어 있는지 먼저 보여준다 - 진단이 맞았는지 눈으로 확인할 수 있게.
$oldKey = ""
if (Test-Path $serviceJson) {
  try {
    $old = Get-Content $serviceJson -Raw -Encoding UTF8 | ConvertFrom-Json
    $oldType    = "$($old.type)"
    $oldService = "$($old.settings.service)"
    $oldKey     = "$($old.settings.key)"
    Say ("현재 service.json: type=" + $(if ($oldType) { $oldType } else { "(없음)" }) +
         $(if ($oldService) { "  service=$oldService" } else { "" }))
    if ($oldType -ieq "rtmp_common" -or $oldService -match "YouTube") {
      Warn "여기가 원인입니다 - 계정 연동형 서비스라 OBS가 '방송을 고르라'는 모달을 띄웁니다."
    }
  } catch {
    Warn "service.json을 읽지 못했어요(형식 깨짐). 새로 씁니다."
  }
} else {
  Say "service.json이 없어요. 새로 만듭니다."
}

# ------------------------------------------------------------------ OBS 종료
Step "2/5  OBS 종료"

$procs = @(Get-Process obs64 -ErrorAction SilentlyContinue)
if ($procs.Count -gt 0) {
  $procs | Stop-Process -Force -ErrorAction SilentlyContinue
  for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 300
    if (-not (Get-Process obs64 -ErrorAction SilentlyContinue)) { break }
  }
  if (Get-Process obs64 -ErrorAction SilentlyContinue) {
    Bad "OBS가 안 죽었어요. 작업 관리자에서 obs64.exe를 끝낸 뒤 다시 실행해주세요."
    exit 1
  }
  Ok "OBS 종료됨 ($($procs.Count)개)"
} else {
  Say "OBS는 실행 중이 아니었어요."
}

# ------------------------------------------------------------ service.json 교체
Step "3/5  방송 서비스를 '사용자 지정'으로 고정"

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"

# 백업 이름에 타임스탬프를 붙이는 건 일부러다. OBS 자신이 'service.json.bak'을
# 직전 버전 보관용으로 쓰기 때문에, 그 이름을 뺏으면 안 된다.
if (Test-Path $serviceJson) {
  Copy-Item $serviceJson "$serviceJson.bak-$stamp" -Force
  Say "백업: service.json.bak-$stamp"
}

# 키는 살린다. stream_server가 방송마다 websocket으로 다시 넣지만, 사람이 OBS에서
# 직접 '방송 시작'을 눌러도 되게 남겨두는 편이 낫다.
$json = ([ordered]@{
  type     = "rtmp_custom"
  settings = [ordered]@{
    server   = $RtmpServer
    key      = $oldKey
    use_auth = $false
  }
} | ConvertTo-Json -Depth 5)

Write-Utf8NoBom $serviceJson $json
Ok ("service.json = rtmp_custom / $RtmpServer / key=" +
    $(if ($oldKey) { "유지됨" } else { "비어 있음(서버가 방송마다 넣음)" }))

# ---------------------------------------------------- basic.ini 로그인 토큰 제거
Step "4/5  저장된 YouTube 로그인 지우기 (basic.ini)"

# service.json만 고치면 부족하다. OBS는 시작할 때 basic.ini의 [Auth] Type을 보고
# 저장해 둔 계정을 되살리고, 그 순간 다시 '방송 고르기' 모드가 된다.
if (Test-Path $basicIni) {
  $lines = @(Get-Content $basicIni -Encoding UTF8)
  Copy-Item $basicIni "$basicIni.bak-$stamp" -Force

  $out = New-Object System.Collections.Generic.List[string]
  $dropping = $false
  $dropped  = New-Object System.Collections.Generic.List[string]

  foreach ($line in $lines) {
    $t = $line.Trim()
    if ($t -match '^\[(.+)\]$') {
      $name = $Matches[1]
      # [Auth]는 어떤 서비스로 로그인했는지, [YouTube*]는 그 토큰이 들어 있는 곳.
      $dropping = ($name -ieq "Auth") -or ($name -imatch '^YouTube')
      if ($dropping) { $dropped.Add($name) | Out-Null; continue }
    }
    if (-not $dropping) { $out.Add($line) | Out-Null }
  }

  if ($dropped.Count -gt 0) {
    Write-Utf8NoBom $basicIni (($out -join "`r`n") + "`r`n")
    Ok ("지운 섹션: " + ($dropped -join ", ") + "   (백업: basic.ini.bak-$stamp)")
  } else {
    Say "지울 로그인 정보가 없었어요 (이미 깨끗함)."
    Remove-Item "$basicIni.bak-$stamp" -Force -ErrorAction SilentlyContinue
  }
} else {
  Warn "basic.ini가 없어요: $basicIni"
}

# ------------------------------------------------------ 작업 재등록 + OBS 재시작
Step "5/5  OBS 자동 실행 작업 재등록 + 재시작"

$obsExe = "$env:ProgramFiles\obs-studio\bin\64bit\obs64.exe"
if (-not (Test-Path $obsExe)) {
  Bad "obs64.exe를 못 찾았어요: $obsExe"
  exit 1
}

# --minimize-to-tray가 붙어 있으면 OBS가 트레이에만 떠서, 화면엔 안 보이는데
# '이미 실행 중'이라는 말만 듣게 된다. 전용 PC라 숨길 이유가 없으니 뺀다.
$obsArgs = "--startreplaybuffer --disable-shutdown-check"

$id      = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
             [Security.Principal.WindowsBuiltInRole]::Administrator)

if ($isAdmin) {
  try {
    $user = "$env:USERDOMAIN\$env:USERNAME"
    $a = New-ScheduledTaskAction -Execute $obsExe -Argument $obsArgs `
           -WorkingDirectory "$env:ProgramFiles\obs-studio\bin\64bit"
    $t = New-ScheduledTaskTrigger -AtLogOn -User $user
    $s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
           -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
           -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName "PS-OBS-Studio" -Action $a -Trigger $t -Settings $s `
      -User $user -RunLevel Highest -Force | Out-Null
    Ok "작업 재등록됨: PS-OBS-Studio (트레이 숨김 없음)"
  } catch {
    Warn "작업 재등록 실패: $($_.Exception.Message)"
  }
} else {
  Warn "관리자 권한이 아니라 예약 작업은 그대로 뒀어요 (OBS는 그래도 다시 띄웁니다)."
  Say  "창이 보이게 하려면 관리자 PowerShell에서 이 스크립트를 한 번 더 돌려주세요."
}

if ($NoRestart) {
  Say "-NoRestart - OBS는 직접 켜주세요."
  Write-Host ""
  exit 0
}

Start-Process $obsExe -ArgumentList $obsArgs `
  -WorkingDirectory "$env:ProgramFiles\obs-studio\bin\64bit"
Say "OBS 시작 중..."

# :4455가 열려야 stream_server가 붙는다. 안 열리면 WebSocket 서버가 꺼졌거나
# OBS가 또 무슨 대화상자에 걸린 것.
$wsUp = $false
for ($i = 0; $i -lt 40; $i++) {
  Start-Sleep -Milliseconds 500
  try {
    $c = New-Object System.Net.Sockets.TcpClient
    $c.Connect("127.0.0.1", 4455)
    $c.Close()
    $wsUp = $true
    break
  } catch { }
}

Write-Host ""
if ($wsUp) {
  Ok "OBS WebSocket :4455 열림"
  Write-Host "  OBS 제어판에서 '방송 설정 관리' 버튼이 사라졌는지 확인해주세요." -ForegroundColor White
  Write-Host "  사라졌으면 태블릿에서 매치를 시작하면 됩니다." -ForegroundColor White
} else {
  Bad "20초 안에 :4455가 안 열렸어요."
  Say "OBS 화면에 대화상자가 떠 있는지 보고, 도구 > WebSocket 서버 설정이 켜져 있는지 확인해주세요."
}
Write-Host ""
Write-Host "전체 점검:  powershell -NoProfile -ExecutionPolicy Bypass -File .\setup\check_livestream_pc.ps1" -ForegroundColor DarkGray
Write-Host ""
