#requires -Version 5.1
<#
  Health check for the livestream PC - run it after setup, and any time the
  tablet says "streaming server is off".

    powershell -NoProfile -ExecutionPolicy Bypass -File .\check_livestream_pc.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File .\check_livestream_pc.ps1 -Fix
    powershell -NoProfile -ExecutionPolicy Bypass -File .\check_livestream_pc.ps1 -Restart

  Checks the whole chain, in the order it breaks in practice:
    OBS -> OBS WebSocket -> stream_server :5000 -> reverse tunnel -> public URL
  plus the "stays powered on" settings.

  -Fix additionally repairs what can be repaired from here (all idempotent):
  strips a BOM from config.json, restarts a missing or duplicated stream_server,
  asks OBS to arm the replay buffer AND prints OBS's refusal reason, starts the
  tunnel task - then re-verifies.

  -Restart forces a clean restart of stream_server (run as Administrator). Needed
  after editing config.json or re-authenticating YouTube - a running server keeps
  the settings and the account it started with.

  On any failure it also prints a Diagnostics block (processes, OBS probe, OBS
  profile settings, log tail) so one paste carries everything needed to help.
  (ASCII only - Windows PowerShell 5.1 misreads non-ASCII without a BOM.)
#>
[CmdletBinding()]
param(
  [string]$RepoDir,
  [string]$PublicUrl = "https://obs.padelsociety.co.kr",
  # Try to fix what can be fixed from here (restart a stuck server, arm the replay
  # buffer, strip a BOM), then re-verify. Everything it does is idempotent.
  [switch]$Fix,
  # Force a clean restart of stream_server even when it looks healthy. Needed after
  # changing config.json or re-authenticating YouTube: the server reads config once
  # at startup and caches the YouTube client after the first use, so a running
  # process keeps using the old settings and the old account.
  [switch]$Restart
)

# Resolve the script's own folder HERE, not in a param() default: Windows
# PowerShell 5.1 does not reliably have $PSScriptRoot populated while param
# defaults are evaluated, and Split-Path then dies on an empty -Path.
$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) { $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $ScriptDir) { $ScriptDir = (Get-Location).Path }
if (-not $RepoDir)   { $RepoDir   = Split-Path -Parent $ScriptDir }

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

function Info ($m) { Write-Host ("  " + $m) -ForegroundColor DarkGray }

$IsElevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
              ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

function Get-StreamServerProcs {
  # Win32_Process.CommandLine reads as $null for a process we are not allowed to
  # inspect - and the scheduled task runs elevated (RunLevel Highest). So from a
  # normal shell the command-line filter matches nothing and the server looks
  # absent while it is plainly serving :5000. Fall back to "any python.exe":
  # this is a dedicated PC, nothing else here runs Python.
  $all = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue)
  $named = @($all | Where-Object { $_.CommandLine -and $_.CommandLine -match 'stream_server\.py' })
  if ($named.Count -gt 0) { return $named }
  return $all
}

# One server can legitimately show up as two processes: the interpreter re-execs
# itself (same command line, child of the first), and only the child actually
# serves - the log shows a single Flask banner and there is no port conflict.
# So count ROOTS: a stream_server process whose parent is not itself one.
# Two roots = two real servers fighting over :5000. Parent+child = one server.
function Get-StreamServerRoots {
  $all = Get-StreamServerProcs
  $ids = @($all | ForEach-Object { $_.ProcessId })
  @($all | Where-Object { $ids -notcontains $_.ParentProcessId })
}

# The supervisor (run_stream_server.ps1) and the cmd it uses for redirection.
# These MUST die before python does: Stop-ScheduledTask does not reap them, and a
# surviving supervisor relaunches python 10s later - so killing python alone leaves
# two servers fighting over :5000 once the task is started again.
function Get-SupervisorProcs {
  @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -in 'powershell.exe','cmd.exe' -and
                   $_.CommandLine -and $_.CommandLine -match 'run_stream_server\.ps1|stream_server\.py' })
}

# Stop the whole stream_server tree: supervisors first, then python.
function Stop-StreamServerTree {
  Stop-ScheduledTask -TaskName PS-StreamServer -ErrorAction SilentlyContinue
  foreach ($p in Get-SupervisorProcs)   { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
  Start-Sleep 1
  foreach ($p in Get-StreamServerProcs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
  Start-Sleep 2
  # One more sweep - a supervisor may have spawned python right before it died.
  foreach ($p in Get-SupervisorProcs)   { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
  foreach ($p in Get-StreamServerProcs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
}

# Which YouTube channel the stored token actually belongs to, and whether that
# channel may go live. Signing in with the wrong Google account (a personal one
# instead of the PS brand channel) produces a 403 liveStreamingNotEnabled at the
# worst possible moment - the first point of a real match. This surfaces it early.
# Loads the pickle directly instead of calling _ensure_auth(), which would pop a
# browser if the refresh failed; a health check must never block on a login.
function Invoke-YouTubeProbe ($venvPy, $RepoDir) {
  $py = @'
import json, os, pickle, sys
repo = sys.argv[1]
tok  = os.path.join(repo, 'youtube_token.pickle')
try:
    from googleapiclient.discovery import build
    from google.auth.transport.requests import Request
    with open(tok, 'rb') as f:
        creds = pickle.load(f)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds.valid:
        print('token=invalid')
        sys.exit(0)
    yt = build('youtube', 'v3', credentials=creds)
except Exception as e:
    print('token_error=' + str(e)[:200])
    sys.exit(0)

try:
    items = yt.channels().list(part='snippet', mine=True).execute().get('items', [])
    if items:
        for c in items:
            print('channel=' + c['snippet']['title'] + '  id=' + c['id'])
    else:
        print('channel=(none - the token has no channel)')
except Exception as e:
    print('channel_error=' + str(e)[:200])

# Read-only probe: it 403s exactly like liveBroadcasts().insert would, but
# creates nothing.
try:
    yt.liveBroadcasts().list(part='id', broadcastStatus='all', maxResults=1).execute()
    print('live_enabled=True')
except Exception as e:
    print('live_enabled=False')
    print('live_error=' + str(e)[:300])
'@
  $tmp = Join-Path $env:TEMP "ps_yt_probe.py"
  [IO.File]::WriteAllText($tmp, $py, (New-Object System.Text.UTF8Encoding $false))
  $out = Join-Path $env:TEMP "ps_yt_probe.out"
  Remove-Item $out -Force -ErrorAction SilentlyContinue
  $env:PYTHONUTF8 = "1"
  $ErrorActionPreference = "Continue"
  # cd into the repo so youtube_token.pickle resolves the same way the server sees it.
  & cmd.exe /c "cd /d `"$RepoDir`" && `"$venvPy`" `"$tmp`" `"$RepoDir`" > `"$out`" 2>&1"
  if (Test-Path $out) { Get-Content $out -Encoding UTF8 -ErrorAction SilentlyContinue } else { @() }
}

# Talks to OBS through the venv (obsws-python lives there) and reports what OBS
# actually says - start_replay_buffer failures are swallowed by the server's
# keepalive loop on purpose, so this is the only place the reason surfaces.
function Invoke-ObsProbe ($venvPy, $cfgPath, [switch]$StartBuffer) {
  $py = @'
import json, sys, time
import obsws_python as o
cfg = json.load(open(sys.argv[1], encoding='utf-8'))['obs']
r = o.ReqClient(host=cfg.get('host', 'localhost'), port=int(cfg.get('port', 4455)),
                password=cfg.get('password', ''), timeout=5)
print('obs_version=' + r.get_version().obs_version)
active = r.get_replay_buffer_status().output_active
print('buffer_before=' + str(active))
# A scene with no sources streams a black screen and nothing anywhere reports it.
try:
    sc = r.get_current_program_scene()
    name = getattr(sc, 'current_program_scene_name', None) or getattr(sc, 'scene_name', '')
    items = r.get_scene_item_list(name).scene_items
    print('scene=' + str(name))
    print('sources=' + str(len(items)))
    for it in items:
        print('  source: ' + str(it.get('sourceName')) + '  enabled=' + str(it.get('sceneItemEnabled')))
except Exception as e:
    print('scene_error=' + repr(e))
if not active and len(sys.argv) > 2:
    try:
        r.start_replay_buffer()
        time.sleep(2)
        print('buffer_after=' + str(r.get_replay_buffer_status().output_active))
    except Exception as e:
        print('start_error=' + repr(e))
'@
  $tmp = Join-Path $env:TEMP "ps_obs_probe.py"
  [IO.File]::WriteAllText($tmp, $py, (New-Object System.Text.UTF8Encoding $false))
  $env:PYTHONUTF8 = "1"
  $ErrorActionPreference = "Continue"

  # Route the output through a file instead of PowerShell's native-command pipe.
  # Reading the pipe decodes with the console codepage (CP949 here) and mojibakes
  # Korean scene/source names; and switching [Console]::OutputEncoding to fix that
  # makes the console print every wide character twice. Writing raw bytes with cmd
  # and reading them back as UTF-8 avoids the console encoding entirely.
  $out = Join-Path $env:TEMP "ps_obs_probe.out"
  Remove-Item $out -Force -ErrorAction SilentlyContinue
  $extra = if ($StartBuffer) { ' start' } else { '' }
  & cmd.exe /c "`"$venvPy`" `"$tmp`" `"$cfgPath`"$extra > `"$out`" 2>&1"
  if (Test-Path $out) { Get-Content $out -Encoding UTF8 -ErrorAction SilentlyContinue } else { @() }
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

  # Check it the way stream_server.py actually reads it. .NET strips a BOM while
  # reading, so a Notepad "UTF-8 with BOM" save passes every check above and then
  # crashes Python with 'Expecting value: line 1 column 1'. Test with Python itself.
  if (Test-Path $venvPy) {
    $bytes = [IO.File]::ReadAllBytes($cfgPath)
    $bom = if ($bytes.Length -ge 3) { "{0:X2} {1:X2} {2:X2}" -f $bytes[0],$bytes[1],$bytes[2] } else { "" }
    $ErrorActionPreference = "Continue"
    $pyOut = & $venvPy -c "import json,sys;json.load(open(sys.argv[1],encoding='utf-8'))" $cfgPath 2>&1
    Chk "config.json readable by Python" ($LASTEXITCODE -eq 0) `
        ("first bytes: $bom" + $(if ($LASTEXITCODE -ne 0) { "  |  " + (("$pyOut" -split "`n")[-1]).Trim() } else { "" })) `
        "STEP 2 - re-save config.json as UTF-8 WITHOUT BOM (EF BB BF at the start is the BOM)"
  }
}

# ----------------------------------------------------------------- YouTube
Write-Host ""
Write-Host "-- YouTube" -ForegroundColor Cyan
if ((Test-Path $venvPy) -and (Test-Path (Join-Path $RepoDir "youtube_token.pickle"))) {
  $yt = (Invoke-YouTubeProbe $venvPy $RepoDir) -join "`n"
  $chan = if ($yt -match 'channel=(.+)') { $Matches[1].Trim() } else { '(unknown)' }

  Chk "signed in to a YouTube channel" ($yt -match 'channel=' -and $yt -notmatch 'channel=\(none') $chan `
      "STEP 5 - delete youtube_token.pickle and sign in again with the PS channel"

  if ($yt -match 'live_enabled=True') {
    Chk "channel may go live" $true $null
  } else {
    $why = if ($yt -match 'live_error=(.+)') { $Matches[1].Trim() } else { $yt }
    Chk "channel may go live" $false $why `
        ("STEP 5 - this channel cannot stream. If it is the wrong account: delete " +
         "youtube_token.pickle and re-authenticate, picking the PS channel. If it is the " +
         "right one: enable live streaming at https://www.youtube.com/features (24h wait on first activation)")
  }
} else {
  Skip "YouTube" "no token yet - complete the first sign-in (STEP 5)"
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

# A scene with no sources streams a pure black screen. OBS is happy, the server is
# happy, /health is happy - only a viewer would ever notice. Ask OBS directly.
if ((Test-Path $venvPy) -and (Test-Path $cfgPath)) {
  $probe = (Invoke-ObsProbe $venvPy $cfgPath) -join "`n"
  if ($probe -match 'sources=(\d+)') {
    $srcCount = [int]$Matches[1]
    $sceneName = if ($probe -match 'scene=(.+)') { $Matches[1].Trim() } else { "?" }
    Chk "scene has at least one source" ($srcCount -gt 0) "scene '$sceneName' has $srcCount source(s)" `
        "STEP 3 - add the court camera in OBS: Sources + > Video Capture Device (an empty scene streams black)"
  } else {
    Skip "scene source check" "could not query OBS (see Diagnostics below)"
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

# Two servers means the loser cannot bind :5000, exits, and its supervisor relaunches
# it every 10s forever. /health still answers (the winner holds the port) so nothing
# else in this script would notice.
$ssRoots = @(Get-StreamServerRoots)
Chk "exactly one stream_server" ($ssRoots.Count -eq 1) `
    ("servers: " + $ssRoots.Count + "  (python processes: " + @(Get-StreamServerProcs).Count + ")" +
     $(if (-not $IsElevated) { "  - run as Administrator for an exact match" } else { "" })) `
    "re-run this script with -Fix from an ADMIN PowerShell (duplicates fight over :5000)"

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
# Fast startup only runs when BOTH are 1, so either being 0 means a power cut is
# followed by a real cold boot. `powercfg /hibernate off` clears HibernateEnabled
# and leaves HiberbootEnabled alone - demanding both be 0 was a false alarm.
Chk "fast startup cannot run" (-not (($hibOn -eq 1) -and ($fastOn -eq 1))) `
    "HibernateEnabled=$hibOn  HiberbootEnabled=$fastOn (either 0 is enough)" `
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

# ================================================================= repairs
# Everything here is safe to repeat. Run with -Fix.
if ($Restart) {
  Write-Host ""
  Write-Host "-- Restart" -ForegroundColor Cyan
  if (-not $IsElevated) {
    Write-Host "  [!!] not running as Administrator - the task's processes cannot be stopped." -ForegroundColor Yellow
  }
  Info "stopping stream_server (supervisors first) ..."
  Stop-StreamServerTree
  Start-ScheduledTask -TaskName PS-StreamServer -ErrorAction SilentlyContinue
  Info "waiting for startup ..."
  Start-Sleep 18
  Info "now running: $(@(Get-StreamServerRoots).Count) server(s)"
  try {
    $h3 = Invoke-RestMethod -Uri "http://127.0.0.1:5000/health" -TimeoutSec 5
    Write-Host ("  /health  ok=" + $h3.ok + "  buffer_ready=" + $h3.buffer_ready) `
      -ForegroundColor $(if ($h3.ok) { "Green" } else { "Yellow" })
  } catch {
    Write-Host ("  /health down: " + $_.Exception.Message) -ForegroundColor Red
  }
}

if ($Fix) {
  Write-Host ""
  Write-Host "-- Repairs (-Fix)" -ForegroundColor Cyan
  if (-not $IsElevated) {
    # The task's processes run elevated; Stop-Process on them fails from here.
    Write-Host "  [!!] not running as Administrator - cannot stop the task's processes." -ForegroundColor Yellow
    Write-Host "       If a repair below does not stick, re-run this from an admin PowerShell." -ForegroundColor Yellow
  }

  # 1) config.json BOM - Python cannot read past it, PowerShell can.
  if (Test-Path $cfgPath) {
    $b = [IO.File]::ReadAllBytes($cfgPath)
    if ($b.Length -ge 3 -and $b[0] -eq 0xEF -and $b[1] -eq 0xBB -and $b[2] -eq 0xBF) {
      [IO.File]::WriteAllText($cfgPath, [IO.File]::ReadAllText($cfgPath),
                              (New-Object System.Text.UTF8Encoding $false))
      Info "config.json: removed UTF-8 BOM"
    } else { Info "config.json: no BOM" }
  }

  # 2) stream_server: none running, or several fighting over :5000.
  $procs = Get-StreamServerProcs
  if (@(Get-StreamServerRoots).Count -ne 1) {
    Info "servers: $(@(Get-StreamServerRoots).Count)  python: $($procs.Count)  supervisors: $(@(Get-SupervisorProcs).Count) - restarting clean"
    Stop-StreamServerTree
    $left = @(Get-StreamServerProcs).Count + @(Get-SupervisorProcs).Count
    if ($left) { Info "WARNING: $left process(es) survived the stop - restart Windows if this repeats" }
    Start-ScheduledTask -TaskName PS-StreamServer -ErrorAction SilentlyContinue
    Info "waiting for startup (it downloads the court page first, 15s timeout) ..."
    Start-Sleep 18
    Info "now running: $(@(Get-StreamServerRoots).Count) server(s), $(@(Get-StreamServerProcs).Count) python process(es)"
  } else { Info "stream_server: 1 process, leaving it alone" }

  # 3) Replay buffer.
  if (Test-Path $venvPy) {
    Info "asking OBS to arm the replay buffer ..."
    Invoke-ObsProbe $venvPy $cfgPath -StartBuffer | ForEach-Object { Info "  $_" }
  }

  # 4) Tunnel task (harmless if the key is not registered yet - it just retries).
  if (-not (Get-Process ssh -ErrorAction SilentlyContinue)) {
    Start-ScheduledTask -TaskName OBS-VPS-Tunnel -ErrorAction SilentlyContinue
    Info "started OBS-VPS-Tunnel"
  }

  # Re-verify the two that matter after repairs.
  Write-Host ""
  Write-Host "-- After repairs" -ForegroundColor Cyan
  Start-Sleep 22   # the server re-checks the buffer every 20s
  try {
    $h2 = Invoke-RestMethod -Uri "http://127.0.0.1:5000/health" -TimeoutSec 5
    Write-Host ("  /health  ok=" + $h2.ok + "  buffer_ready=" + $h2.buffer_ready) `
      -ForegroundColor $(if ($h2.ok -and $h2.buffer_ready) { "Green" } else { "Yellow" })
  } catch {
    Write-Host ("  /health still down: " + $_.Exception.Message) -ForegroundColor Red
  }
}

# ============================================================= diagnostics
# One block holding everything needed to diagnose remotely - so a single paste
# answers the question instead of another round of one-command-at-a-time.
if ($fail -gt 0 -or $Fix) {
  Write-Host ""
  Write-Host "-- Diagnostics (paste this whole block when asking for help)" -ForegroundColor Cyan

  Write-Host "  [stream_server processes]"
  $procs = Get-StreamServerProcs
  if ($procs.Count -eq 0) { Info "(none)" }
  foreach ($p in $procs) { Info ("python  pid=" + $p.ProcessId + "  ppid=" + $p.ParentProcessId + "  started=" + $p.CreationDate) }
  foreach ($p in Get-SupervisorProcs) { Info ($p.Name + "  pid=" + $p.ProcessId + "  ppid=" + $p.ParentProcessId) }

  Write-Host "  [OBS probe]"
  if ((Test-Path $venvPy) -and (Test-Path $cfgPath)) {
    Invoke-ObsProbe $venvPy $cfgPath | ForEach-Object { Info $_ }
  } else { Info "(venv or config.json missing)" }

  Write-Host "  [OBS profile settings]"
  if ($basicIni -and (Test-Path $basicIni)) {
    foreach ($k in @("RecFilePath", "RecFormat2", "RecEncoder", "RecRB", "RecRBTime", "RecRBSize")) {
      Info ("$k = " + (Get-IniValue $basicIni "AdvOut" $k))
    }
  } else { Info "(basic.ini not found)" }

  Write-Host "  [stream_server log, last 25 lines]"
  $log = Get-ChildItem (Join-Path $RepoDir "logs") -Filter "stream_server_*.log" -ErrorAction SilentlyContinue |
           Sort-Object LastWriteTime | Select-Object -Last 1
  if ($log) {
    # -Encoding UTF8: the log holds Python's UTF-8 output, and Get-Content would
    # otherwise read it with the ANSI codepage and print mojibake.
    Get-Content $log.FullName -Tail 25 -Encoding UTF8 -ErrorAction SilentlyContinue |
      ForEach-Object { Info $_ }
  } else { Info "(no log file)" }
}

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
    Write-Host "  TO DO (in check order - inner layers first):" -ForegroundColor Yellow
    # Insertion order IS the diagnostic order (files -> config -> OBS -> ports ->
    # tasks -> tunnel -> power). Sorting this alphabetically would scramble it.
    $i = 1
    foreach ($t in $ordered) { Write-Host ("   {0}) {1}" -f $i, $t); $i++ }
  }
  Write-Host ""
  Write-Host "  Chain: OBS -> WebSocket 4455 -> stream_server 5000 -> tunnel -> public URL"
  Write-Host "  Full walkthrough: $(Join-Path $ScriptDir 'SETUP_LIVESTREAM_PC.md')"
}
Write-Host "============================================================"
Write-Host ""
