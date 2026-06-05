# OBS PC -> VPS persistent reverse tunnel (auto-reconnect)
# Exposes local stream_server :5000 as VPS localhost:5001.
# nginx serves it at https://obs.padelsociety.co.kr
# (ASCII only - Windows PowerShell 5.1 misreads non-ASCII without a BOM.)

$VPS_USER = "obstunnel"
$VPS_HOST = "62.72.56.88"
$KEY      = "$env:USERPROFILE\.ssh\obs_tunnel_ed25519"

Write-Host "OBS reverse tunnel starting. Target=$VPS_USER@$VPS_HOST  (Ctrl+C to stop)"
Write-Host "stream_server :5000  ->  VPS :5001"
Write-Host ""

while ($true) {
  $t = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  Write-Host "[$t] connecting..."
  ssh -N -T -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new -i "$KEY" -R 127.0.0.1:5001:127.0.0.1:5000 "$VPS_USER@$VPS_HOST"
  $t2 = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  Write-Host "[$t2] disconnected - reconnecting in 5s"
  Start-Sleep -Seconds 5
}
