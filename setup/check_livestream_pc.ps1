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

$pass = 0; $fail = 0; $todo = @()

# Read as UTF-8 explicitly: Windows PowerShell 5.1's Get-Content falls back to the
# system ANSI codepage (CP949 on Korean Windows) for files without a BOM, which
# turns this UTF-8 file into mojibake and makes ConvertFrom-Json fail on a file
# that is perfectly fine (Python opens it with encoding='utf-8').
function Read-Utf8 ($path) { [IO.File]::ReadAllText($path, [Text.Encoding]::UTF8) }

# True while a field still holds a template value rather than a real secret.
# Covers the ASCII PUT_..._HERE template and the older Korean-placeholder example.
function Is-Placeholder ($v) {
  if (-not $v) { return $true }
  if ($v -match '^PUT_.*_HERE$') { return $true }
  return ($v -notmatch '^[\x20-\x7E]+$')
}

# $action, when given, is what the operator should DO about a failure. Collected
# and reprinted at the end so the run ends with a work list, not just red lines.
function Chk ($label, $ok, $detail, $action) {
  if ($ok) { Write-Host ("  [ OK ] " + $label) -ForegroundColor Green; $script:pass++ }
  else {
    Write-Host ("  [FAIL] " + $label) -ForegroundColor Red
    $script:fail++
    if ($action) { $script:todo += $action }
  }
  if ($detail) { Write-Host ("         " + $detail) -ForegroundColor DarkGray }
}

function Skip ($label, $detail) {
  Write-Host ("  [ -- ] " + $label) -ForegroundColor DarkGray
  if ($detail) { Write-Host ("         " + $detail) -ForegroundColor DarkGray }
}

# Minimal INI reader for OBS's basic.ini / global.ini.
function Get-IniValue ($path, $section, $key) {
  if (-not $path -or -not (Test-Path $path)) { return $null }
  $cur = ""
  foreach ($line in [IO.File]::ReadAllLines($path, [Text.Encoding]::UTF8)) {
    $t = $line.Trim()
    if ($t -match '^\[(.+)\]$') { $cur = $Matches[1]; continue }
    if ($cur -ieq $section -and $t -match '^([^=]+)=(.*)$') {
      if ($Matches[1].Trim() -ieq $key) { return $Matches[2].Trim() }
    }
  }
  return $null
}

Write-Host ""
Write-Host "============================================================"
Write-Host "  PS Livestream PC - health check"
Write-Host "  repo: $RepoDir"
Write-Host "============================================================"

# ----------------------------------------------------------------- files
Write-Host ""
Write-Host "-- Files" -ForegroundColor Cyan
$venvPy = Join-Path $RepoDir ".venv\Scripts\python.exe"

Chk "stream_server.py"      (Test-Path (Join-Path $RepoDir "stream_server.py")) $null `
    "STEP 1 - re-run setup_livestream_pc.ps1 as Administrator"
Chk "python venv"           (Test-Path $venvPy) $null `
    "STEP 1 - install Python 3 and re-run setup_livestream_pc.ps1"
Chk "config.json"           (Test-Path (Join-Path $RepoDir "config.json")) "OBS password + highlight upload key" `
    "STEP 1 - re-run setup_livestream_pc.ps1 (it writes the template)"
Chk "client_secrets.json"   (Test-Path (Join-Path $RepoDir "client_secrets.json")) "YouTube OAuth client" `
    "STEP 2 - download client_secrets.json from Google Cloud Console (Desktop app) into $RepoDir"
Chk "youtube_token.pickle"  (Test-Path (Join-Path $RepoDir "youtube_token.pickle")) "created by the first foreground sign-in" `
    "STEP 5 - run: run_stream_server.ps1 -Foreground   (browser sign-in, once)"
Chk "tunnel ssh key"        (Test-Path (Join-Path $env:USERPROFILE ".ssh\obs_tunnel_ed25519")) $null `
    "STEP 1 - re-run setup_livestream_pc.ps1 to generate the tunnel key"

if (Test-Path $venvPy) {
  # A pip failure during setup only shows up much later as the server dying on boot.
  $ErrorActionPreference = "Continue"
  & $venvPy -c "import flask, obsws_python, googleapiclient" 2>&1 | Out-Null
  Chk "python packages import" ($LASTEXITCODE -eq 0) "flask / obsws-python / google-api-python-client" `
      "STEP 1 - re-run setup_livestream_pc.ps1 (pip install failed)"
}

$cfgPath = Join-Path $RepoDir "config.json"
if (Test-Path $cfgPath) {
  try {
    $cfg = (Read-Utf8 $cfgPath) | ConvertFrom-Json
    Chk "config.json obs.password set" (-not (Is-Placeholder $cfg.obs.password)) $null `
        "STEP 3 - copy the OBS WebSocket password into config.json > obs.password"
    Chk "config.json highlight.upload_key set" (-not (Is-Placeholder $cfg.highlight.upload_key)) `
        "must equal HIGHLIGHT_UPLOAD_KEY in the VPS .env" `
        "STEP 2 - put the VPS HIGHLIGHT_UPLOAD_KEY into config.json > highlight.upload_key"
  } catch {
    Chk "config.json parses as JSON" $false $_.Exception.Message `
        "STEP 2 - fix config.json (must be valid JSON, saved as UTF-8 without BOM)"
  }
}

# ------------------------------------------------------------- OBS settings
# Read straight out of the active profile's basic.ini. A wrong recording format
# or a disabled replay buffer otherwise only surfaces as "the highlight button
# does nothing" or choppy playback on a phone.
Write-Host ""
Write-Host "-- OBS settings (basic.ini)" -ForegroundColor Cyan
$obsRoot   = Join-Path $env:APPDATA "obs-studio"
$globalIni = Join-Path $obsRoot "global.ini"
$profDir   = Get-IniValue $globalIni "Basic" "ProfileDir"
$basicIni  = $null
if ($profDir) { $basicIni = Join-Path $obsRoot "basic\profiles\$profDir\basic.ini" }
if (-not ($basicIni -and (Test-Path $basicIni))) {
  $firstProf = Get-ChildItem (Join-Path $obsRoot "basic\profiles") -Directory -ErrorAction SilentlyContinue |
                 Select-Object -First 1
  if ($firstProf) { $basicIni = Join-Path $firstProf.FullName "basic.ini" }
}

if (-not ($basicIni -and (Test-Path $basicIni))) {
  Skip "OBS profile not found" "looked under $obsRoot - run OBS once, or run this check as the account that runs OBS"
} else {
  Write-Host "         profile: $basicIni" -ForegroundColor DarkGray
  $mode = Get-IniValue $basicIni "Output" "Mode"
  Chk "output mode = Advanced" ($mode -ieq "Advanced") "found: $mode" `
      "STEP 3 - Settings > Output > Output Mode: Advanced (Simple mode has no Replay Buffer tab)"

  $recFmt = Get-IniValue $basicIni "AdvOut" "RecFormat2"
  if (-not $recFmt) { $recFmt = Get-IniValue $basicIni "AdvOut" "RecFormat" }
  Chk "recording format is MP4" ($recFmt -and $recFmt -match "mp4") "found: $recFmt" `
      "STEP 3 - Settings > Output > Recording > Recording Format: MPEG-4 (.mp4) or Hybrid MP4"

  $rb = Get-IniValue $basicIni "AdvOut" "RecRB"
  Chk "replay buffer enabled" ($rb -ieq "true") "found: $rb" `
      "STEP 3 - Settings > Output > Replay Buffer > tick Enable Replay Buffer"

  $rbTime = Get-IniValue $basicIni "AdvOut" "RecRBTime"
  $rbOk = $rbTime -and ([int]$rbTime -ge 90)
  Chk "replay buffer >= 90s" $rbOk "found: ${rbTime}s" `
      "STEP 3 - Settings > Output > Replay Buffer > Maximum Replay Time: 90 (the 60s button trims from it)"

  $ws = Get-IniValue $basicIni "OBSWebSocket" "ServerEnabled"
  if ($null -ne $ws) {
    Chk "websocket server enabled (profile)" ($ws -ieq "true") $null `
        "STEP 3 - Tools > WebSocket Server Settings > Enable WebSocket server"
  }
}

# --------------------------------------------------------------- processes
Write-Host ""
Write-Host "-- Processes and ports" -ForegroundColor Cyan
Chk "OBS running" ([bool](Get-Process obs64 -ErrorAction SilentlyContinue)) $null `
    "STEP 3 - start OBS (task PS-OBS-Studio starts it at logon)"

function Port-Open ($p) {
  try { (Test-NetConnection -ComputerName 127.0.0.1 -Port $p -WarningAction SilentlyContinue).TcpTestSucceeded }
  catch { $false }
}
Chk "OBS WebSocket :4455"  (Port-Open 4455) $null `
    "STEP 3 - Tools > WebSocket Server Settings > Enable WebSocket server (port 4455)"
Chk "stream_server :5000"  (Port-Open 5000) $null `
    "Start-ScheduledTask -TaskName PS-StreamServer   (then check logs\stream_server_*.log)"

# stream_server health - also tells us whether the replay buffer is armed
try {
  $h = Invoke-RestMethod -Uri "http://127.0.0.1:5000/health" -TimeoutSec 5
  Chk "local /health responds" $true ("streaming=" + $h.streaming + "  buffer_ready=" + $h.buffer_ready)
  Chk "replay buffer armed" ([bool]$h.buffer_ready) "needed for the highlight button" `
      "STEP 3 - check in order: OBS running / config.json obs.password correct / Replay Buffer enabled"
} catch {
  Chk "local /health responds" $false $_.Exception.Message `
      "Start-ScheduledTask -TaskName PS-StreamServer   (then check logs\stream_server_*.log)"
}

# ------------------------------------------------------------------ tasks
Write-Host ""
Write-Host "-- Scheduled tasks" -ForegroundColor Cyan
foreach ($t in @("OBS-VPS-Tunnel", "PS-StreamServer", "PS-OBS-Studio")) {
  $task = Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
  if ($task) {
    $info = Get-ScheduledTaskInfo -TaskName $t -ErrorAction SilentlyContinue
    Chk "task $t" ($task.State -ne "Disabled") ("state=" + $task.State + "  lastRun=" + $info.LastRunTime) `
        "Enable-ScheduledTask -TaskName $t"
  } else {
    Chk "task $t" $false "not registered" "STEP 1 - re-run setup_livestream_pc.ps1 as Administrator"
  }
}

# ----------------------------------------------------------------- tunnel
Write-Host ""
Write-Host "-- Reverse tunnel and public URL" -ForegroundColor Cyan
$sshUp = [bool](Get-Process ssh -ErrorAction SilentlyContinue)
Chk "ssh tunnel process" $sshUp "obs_tunnel.ps1 keeps :5000 published as VPS :5001" `
    "Start-ScheduledTask -TaskName OBS-VPS-Tunnel"

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
        "got the VPS static fallback (HTML), not stream_server - the tunnel is down" `
        "STEP 4 - register the tunnel public key on the VPS, then Start-ScheduledTask OBS-VPS-Tunnel"
  }
} catch {
  Chk "$PublicUrl reachable" $false $_.Exception.Message `
      "STEP 4 - check the VPS / network"
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
Chk "sleep disabled on AC" ($acIdx -eq "0x00000000") ("scheme: " + $scheme.Trim()) `
    "STEP 1 - re-run setup_livestream_pc.ps1 as Administrator"

# Registry rather than `powercfg /a`, whose wording is localized too.
$hibOn  = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\Power" `
             -Name HibernateEnabled -ErrorAction SilentlyContinue).HibernateEnabled
$fastOn = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power" `
             -Name HiberbootEnabled -ErrorAction SilentlyContinue).HiberbootEnabled
Chk "hibernate/fast startup off" (($hibOn -ne 1) -and ($fastOn -ne 1)) `
    "keeps the PC from resuming into a half state after a power cut" `
    "STEP 1 - re-run setup_livestream_pc.ps1 as Administrator"

$win  = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
$auto = (Get-ItemProperty -Path $win -Name AutoAdminLogon -ErrorAction SilentlyContinue).AutoAdminLogon
$whom = (Get-ItemProperty -Path $win -Name DefaultUserName -ErrorAction SilentlyContinue).DefaultUserName
Chk "automatic sign-in" ($auto -eq "1") "without it, a reboot stops at the lock screen and nothing auto-starts" `
    "STEP 6 - turn on automatic sign-in for $env:USERNAME (Sysinternals Autologon.exe)"
if ($auto -eq "1") {
  # The tasks and the tunnel key belong to one account; signing in as another
  # boots to a desktop where none of them exist.
  Chk "automatic sign-in account matches" ($whom -and ($whom -ieq $env:USERNAME)) `
      "auto sign-in: '$whom'   tasks/key belong to: '$env:USERNAME'" `
      "STEP 6 - point automatic sign-in at $env:USERNAME (or re-run setup as '$whom')"
}

$au = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU"
$nr = (Get-ItemProperty -Path $au -Name NoAutoRebootWithLoggedOnUsers -ErrorAction SilentlyContinue).NoAutoRebootWithLoggedOnUsers
Chk "Windows Update auto-reboot blocked" ($nr -eq 1) $null `
    "STEP 1 - re-run setup_livestream_pc.ps1 as Administrator"

Write-Host ""
Write-Host "  BIOS 'Restore on AC Power Loss = Power On' cannot be read from Windows -" -ForegroundColor DarkGray
Write-Host "  confirm it by hand in the BIOS, or by pulling the plug once and watching it boot." -ForegroundColor DarkGray

# ---------------------------------------------------------------- summary
Write-Host ""
Write-Host "============================================================"
if ($fail -eq 0) {
  Write-Host "  ALL CLEAR - $pass checks passed" -ForegroundColor Green
  Write-Host ""
  Write-Host "  Left to verify by hand:"
  Write-Host "   - BIOS 'Restore on AC Power Loss' = Power On"
  Write-Host "   - pull the plug once and confirm it comes back on its own"
  Write-Host "   - a real broadcast from the tablet (stream + highlight button)"
} else {
  Write-Host "  $fail FAILED / $pass passed" -ForegroundColor Red

  # De-duplicate: several checks can point at the same fix (e.g. re-run setup).
  $seen = @(); $ordered = @()
  foreach ($t in $todo) { if ($seen -notcontains $t) { $seen += $t; $ordered += $t } }

  if ($ordered.Count) {
    Write-Host ""
    Write-Host "  TO DO (inner layers first - a later step cannot work before an earlier one):" -ForegroundColor Yellow
    $i = 1
    foreach ($t in ($ordered | Sort-Object)) { Write-Host ("   {0}) {1}" -f $i, $t); $i++ }
  }
  Write-Host ""
  Write-Host "  Chain: OBS -> WebSocket 4455 -> stream_server 5000 -> tunnel -> public URL"
  Write-Host "  Full walkthrough: $(Join-Path $PSScriptRoot 'SETUP_LIVESTREAM_PC.md')"
}
Write-Host "============================================================"
Write-Host ""
