#requires -Version 5.1
<#
  Health check for the livestream PC - run it after setup, and any time the
  tablet says "streaming server is off".

    powershell -NoProfile -ExecutionPolicy Bypass -File .\check_livestream_pc.ps1

  Checks the whole chain, in the order it breaks in practice:
    OBS -> OBS WebSocket -> stream_server :5000 -> reverse tunnel -> public URL
  plus the "stays powered on" settings.
  (ASCII only - Windows PowerShell 5.1 misreads non-ASCII without a BOM.)
#>
[CmdletBinding()]
param(
  [string]$RepoDir   = (Split-Path -Parent $PSScriptRoot),
  [string]$PublicUrl = "https://obs.padelsociety.co.kr"
)

$pass = 0; $fail = 0

# streaming_config.json.example ships Korean placeholder text in these fields.
# A real OBS password / upload key is printable ASCII, so "has a non-ASCII
# character" is a locale-safe way to spot an unfilled placeholder.
function Is-Placeholder ($v) {
  if (-not $v) { return $true }
  return ($v -notmatch '^[\x20-\x7E]+$')
}

function Chk ($label, $ok, $detail) {
  if ($ok) { Write-Host ("  [ OK ] " + $label) -ForegroundColor Green; $script:pass++ }
  else     { Write-Host ("  [FAIL] " + $label) -ForegroundColor Red;   $script:fail++ }
  if ($detail) { Write-Host ("         " + $detail) -ForegroundColor DarkGray }
}

Write-Host ""
Write-Host "============================================================"
Write-Host "  PS Livestream PC - health check"
Write-Host "  repo: $RepoDir"
Write-Host "============================================================"

# ----------------------------------------------------------------- files
Write-Host ""
Write-Host "-- Files" -ForegroundColor Cyan
Chk "stream_server.py"      (Test-Path (Join-Path $RepoDir "stream_server.py")) $null
Chk "python venv"           (Test-Path (Join-Path $RepoDir ".venv\Scripts\python.exe")) $null
Chk "config.json"           (Test-Path (Join-Path $RepoDir "config.json")) "OBS password + highlight upload key"
Chk "client_secrets.json"   (Test-Path (Join-Path $RepoDir "client_secrets.json")) "YouTube OAuth client"
Chk "youtube_token.pickle"  (Test-Path (Join-Path $RepoDir "youtube_token.pickle")) "created by the first foreground sign-in"
Chk "tunnel ssh key"        (Test-Path (Join-Path $env:USERPROFILE ".ssh\obs_tunnel_ed25519")) $null

$cfgPath = Join-Path $RepoDir "config.json"
if (Test-Path $cfgPath) {
  try {
    $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
    Chk "config.json obs.password set" (-not (Is-Placeholder $cfg.obs.password)) $null
    Chk "config.json highlight.upload_key set" (-not (Is-Placeholder $cfg.highlight.upload_key)) `
        "must equal HIGHLIGHT_UPLOAD_KEY in the VPS .env"
  } catch { Chk "config.json parses as JSON" $false $_.Exception.Message }
}

# --------------------------------------------------------------- processes
Write-Host ""
Write-Host "-- Processes and ports" -ForegroundColor Cyan
Chk "OBS running" ([bool](Get-Process obs64 -ErrorAction SilentlyContinue)) $null

function Port-Open ($p) {
  try { (Test-NetConnection -ComputerName 127.0.0.1 -Port $p -WarningAction SilentlyContinue).TcpTestSucceeded }
  catch { $false }
}
Chk "OBS WebSocket :4455"  (Port-Open 4455) "OBS > Tools > WebSocket Server Settings > Enable"
Chk "stream_server :5000"  (Port-Open 5000) "task PS-StreamServer"

# stream_server health - also tells us whether the replay buffer is armed
try {
  $h = Invoke-RestMethod -Uri "http://127.0.0.1:5000/health" -TimeoutSec 5
  Chk "local /health responds" $true ("streaming=" + $h.streaming + "  buffer_ready=" + $h.buffer_ready)
  Chk "replay buffer armed" ([bool]$h.buffer_ready) "needed for the highlight button (OBS > Output > Replay Buffer)"
} catch {
  Chk "local /health responds" $false $_.Exception.Message
}

# ------------------------------------------------------------------ tasks
Write-Host ""
Write-Host "-- Scheduled tasks" -ForegroundColor Cyan
foreach ($t in @("OBS-VPS-Tunnel", "PS-StreamServer", "PS-OBS-Studio")) {
  $task = Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
  if ($task) {
    $info = Get-ScheduledTaskInfo -TaskName $t -ErrorAction SilentlyContinue
    Chk "task $t" ($task.State -ne "Disabled") ("state=" + $task.State + "  lastRun=" + $info.LastRunTime)
  } else {
    Chk "task $t" $false "not registered - re-run setup_livestream_pc.ps1"
  }
}

# ----------------------------------------------------------------- tunnel
Write-Host ""
Write-Host "-- Reverse tunnel and public URL" -ForegroundColor Cyan
$sshUp = [bool](Get-Process ssh -ErrorAction SilentlyContinue)
Chk "ssh tunnel process" $sshUp "obs_tunnel.ps1 keeps :5000 published as VPS :5001"

# nginx falls back to a static copy of the league page when the tunnel is down,
# and that fallback answers /health with HTML and a 200. So a 200 proves nothing -
# only a JSON body means the request actually reached this PC.
try {
  $r = Invoke-WebRequest -Uri "$PublicUrl/health" -TimeoutSec 10 -UseBasicParsing
  $body = $r.Content
  $isJson = $false
  try { $isJson = [bool](($body | ConvertFrom-Json).PSObject.Properties.Name -contains "ok") } catch { $isJson = $false }
  if ($isJson) {
    Chk "$PublicUrl reaches THIS PC" $true "tunnel is live"
  } else {
    Chk "$PublicUrl reaches THIS PC" $false `
        "got the VPS static fallback (HTML), not stream_server - the tunnel is down"
  }
} catch {
  Chk "$PublicUrl reachable" $false $_.Exception.Message
}

# ------------------------------------------------------------------ power
Write-Host ""
Write-Host "-- Stays powered on" -ForegroundColor Cyan
$scheme = (powercfg /getactivescheme) -join ""
# powercfg labels are localized, so match on the hex values instead of the text:
# a STANDBYIDLE query ends with the AC index then the DC index. 0 = never sleep.
$q   = (powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE) -join "`n"
$hex = [regex]::Matches($q, '0x[0-9a-fA-F]{8}') | ForEach-Object { $_.Value }
$acIdx = if ($hex.Count -ge 2) { $hex[$hex.Count - 2] } else { $null }
Chk "sleep disabled on AC" ($acIdx -eq "0x00000000") ("scheme: " + $scheme.Trim())

# Registry rather than `powercfg /a`, whose wording is localized too.
$hibOn  = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\Power" `
             -Name HibernateEnabled -ErrorAction SilentlyContinue).HibernateEnabled
$fastOn = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power" `
             -Name HiberbootEnabled -ErrorAction SilentlyContinue).HiberbootEnabled
Chk "hibernate/fast startup off" (($hibOn -ne 1) -and ($fastOn -ne 1)) `
    "keeps the PC from resuming into a half state after a power cut"

$win  = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
$auto = (Get-ItemProperty -Path $win -Name AutoAdminLogon -ErrorAction SilentlyContinue).AutoAdminLogon
Chk "automatic sign-in" ($auto -eq "1") "without it, a reboot stops at the lock screen and nothing auto-starts"

$au = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU"
$nr = (Get-ItemProperty -Path $au -Name NoAutoRebootWithLoggedOnUsers -ErrorAction SilentlyContinue).NoAutoRebootWithLoggedOnUsers
Chk "Windows Update auto-reboot blocked" ($nr -eq 1) $null

Write-Host ""
Write-Host "  BIOS 'Restore on AC Power Loss = Power On' cannot be read from Windows -" -ForegroundColor DarkGray
Write-Host "  confirm it by hand in the BIOS, or by pulling the plug once and watching it boot." -ForegroundColor DarkGray

# ---------------------------------------------------------------- summary
Write-Host ""
Write-Host "============================================================"
if ($fail -eq 0) {
  Write-Host "  ALL CLEAR - $pass checks passed" -ForegroundColor Green
} else {
  Write-Host "  $fail FAILED / $pass passed" -ForegroundColor Red
  Write-Host "  Fix order: OBS -> WebSocket -> stream_server -> tunnel -> public URL"
}
Write-Host "============================================================"
Write-Host ""
