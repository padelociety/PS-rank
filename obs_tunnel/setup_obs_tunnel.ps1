# ============================================================
#  OBS PC 역터널 1회 설치 스크립트
#  ★ 반드시 "관리자 권한 PowerShell" 에서 실행하세요. ★
#
#  하는 일:
#   1) 터널 전용 SSH 키 생성 (없으면)
#   2) 공개키를 화면에 출력 → 이걸 복사해서 전달
#   3) 부팅 시 자동으로 obs_tunnel.ps1 이 켜지도록 작업 스케줄 등록
# ============================================================

$ErrorActionPreference = "Stop"
$sshDir = "$env:USERPROFILE\.ssh"
$key    = "$sshDir\obs_tunnel_ed25519"

if (-not (Test-Path $sshDir)) { New-Item -ItemType Directory -Path $sshDir | Out-Null }

# 1) 키 생성 (없을 때만) — 빈 암호. cmd 경유가 빈 암호 처리에 안정적.
if (-not (Test-Path $key)) {
  cmd /c "ssh-keygen -t ed25519 -f ""$key"" -N """" -C obs-tunnel"
  Write-Host "`n[OK] 터널 키 생성: $key"
} else {
  Write-Host "[OK] 터널 키 이미 있음: $key"
}

# 2) 공개키 출력
Write-Host "`n=================  아래 한 줄(공개키)을 복사해서 전달하세요  ================="
Get-Content "$key.pub"
Write-Host "==========================================================================`n"

# 3) 부팅 시 자동 실행 작업 등록
$runner   = Join-Path $PSScriptRoot "obs_tunnel.ps1"
if (-not (Test-Path $runner)) { throw "obs_tunnel.ps1 을 찾을 수 없음: $runner (같은 폴더에 둬야 함)" }

$action   = New-ScheduledTaskAction -Execute "powershell.exe" `
              -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$runner`""
$trigger  = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
              -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName "OBS-VPS-Tunnel" -Action $action -Trigger $trigger `
  -Settings $settings -RunLevel Highest -Force | Out-Null

Write-Host "[OK] 작업 스케줄 'OBS-VPS-Tunnel' 등록됨 (부팅 시 자동 시작)."
Write-Host ""
Write-Host "다음 단계:"
Write-Host "  - 위 공개키를 전달 → 서버(VPS)에 등록되면"
Write-Host "  - 지금 바로 켜기:  Start-ScheduledTask -TaskName OBS-VPS-Tunnel"
Write-Host "  - 상태 확인:       Get-ScheduledTask -TaskName OBS-VPS-Tunnel"
