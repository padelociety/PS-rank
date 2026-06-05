# ============================================================
#  OBS PC → VPS 상시 역터널 (자동 재연결)
#  stream_server(localhost:5000) 을 VPS의 localhost:5001 로 노출.
#  VPS nginx 가 https://obs.padelsociety.co.kr 로 서빙함.
#  setup_obs_tunnel.ps1 이 부팅 시 이 스크립트를 자동 실행하도록 등록함.
# ============================================================

$VPS_USER = "obstunnel"            # VPS의 전용 터널 계정 (서버쪽에서 생성됨)
$VPS_HOST = "62.72.56.88"
$KEY      = "$env:USERPROFILE\.ssh\obs_tunnel_ed25519"

Write-Host "OBS 역터널 시작. 대상=$VPS_USER@$VPS_HOST  (Ctrl+C 종료)"
Write-Host "stream_server :5000  →  VPS :5001"
Write-Host ""

while ($true) {
  $t = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  Write-Host "[$t] 연결 중..."
  # -N: 명령 실행 안 함(포워딩만) / -T: 의사터미널 없음
  # ServerAlive*: 끊김 감지 / ExitOnForwardFailure: 포워드 실패 시 즉시 종료(→재연결)
  ssh -N -T `
    -o ServerAliveInterval=30 -o ServerAliveCountMax=3 `
    -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new `
    -i "$KEY" `
    -R 127.0.0.1:5001:127.0.0.1:5000 `
    "$VPS_USER@$VPS_HOST"
  $t2 = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  Write-Host "[$t2] 연결 끊김 — 5초 후 재연결"
  Start-Sleep -Seconds 5
}
