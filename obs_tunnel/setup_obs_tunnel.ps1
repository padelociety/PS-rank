# OBS PC reverse-tunnel one-time installer.
# RUN AS ADMINISTRATOR.
#   1) generate tunnel SSH key (if missing)
#   2) print public key (copy and send it)
#   3) register a startup task to auto-run obs_tunnel.ps1
# (ASCII only - Windows PowerShell 5.1 misreads non-ASCII without a BOM.)

$ErrorActionPreference = "Stop"
$sshDir = "$env:USERPROFILE\.ssh"
$key    = "$sshDir\obs_tunnel_ed25519"

if (-not (Test-Path $sshDir)) { New-Item -ItemType Directory -Path $sshDir | Out-Null }

# 1) create key (only if missing) - empty passphrase via cmd for reliability
if (-not (Test-Path $key)) {
  cmd /c "ssh-keygen -t ed25519 -f ""$key"" -N """" -C obs-tunnel"
  Write-Host "[OK] key created: $key"
} else {
  Write-Host "[OK] key already exists: $key"
}

# 2) print public key
Write-Host ""
Write-Host "================= COPY THE PUBLIC KEY BELOW AND SEND IT ================="
Get-Content "$key.pub"
Write-Host "========================================================================"
Write-Host ""

# 3) register startup task
$runner = Join-Path $PSScriptRoot "obs_tunnel.ps1"
if (-not (Test-Path $runner)) { throw "obs_tunnel.ps1 not found at $runner (keep both files in the same folder)" }

$action   = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$runner`""
$trigger  = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "OBS-VPS-Tunnel" -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Force | Out-Null

Write-Host "[OK] startup task 'OBS-VPS-Tunnel' registered (auto-starts on boot)."
Write-Host "Start it now with:  Start-ScheduledTask -TaskName OBS-VPS-Tunnel"
