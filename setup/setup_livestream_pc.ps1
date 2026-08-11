#requires -Version 5.1
<#
  PS Livestream PC - one-shot provisioning after a fresh Windows install.

  Installs Python/OBS/Git, clones PS-rank, builds the venv, locks the machine
  into "never sleeps, comes back after a power cut" mode, generates the reverse
  tunnel SSH key, and registers the three auto-start tasks.

  RUN AS ADMINISTRATOR:
    powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_livestream_pc.ps1

  It is safe to re-run: every step is skipped when already done.
  Manual steps it CANNOT do for you are printed at the end.
  (ASCII only - Windows PowerShell 5.1 misreads non-ASCII without a BOM.)
#>
[CmdletBinding()]
param(
  [string]$RepoDir = "C:\dev\PS-rank",
  [string]$RepoUrl = "https://github.com/padelociety/PS-rank.git",
  [string]$VpsUser = "obstunnel",
  [string]$VpsHost = "62.72.56.88",
  [switch]$SkipInstall,
  [switch]$SkipPower,
  [switch]$SkipTasks,
  [switch]$SkipFirewall
)

$ErrorActionPreference = "Stop"
$script:Warnings = @()
$script:Manual   = @()

# Where this script lives. Resolved with fallbacks because $PSScriptRoot is empty
# under some invocation paths in Windows PowerShell 5.1.
$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) { $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $ScriptDir) { $ScriptDir = (Get-Location).Path }

function Say  ($m) { Write-Host "  $m" }
function Step ($m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Ok   ($m) { Write-Host "  [OK] $m" -ForegroundColor Green }
function Warn ($m) { Write-Host "  [!!] $m" -ForegroundColor Yellow; $script:Warnings += $m }
function Todo ($m) { $script:Manual += $m }

# Each step is independent - a failure must not abort the ones after it.
function Try-Step ($name, [scriptblock]$body) {
  try { & $body }
  catch { Warn "$name failed: $($_.Exception.Message)" }
}

# Read/write JSON as UTF-8 explicitly. Windows PowerShell 5.1's Get-Content falls
# back to the system ANSI codepage (CP949 on Korean Windows) for files without a
# BOM, which turns this UTF-8 file into mojibake and breaks ConvertFrom-Json.
# Set-Content -Encoding UTF8 would add a BOM, which Python's json.load then chokes on.
function Read-Utf8 ($path) { [IO.File]::ReadAllText($path, [Text.Encoding]::UTF8) }
function Write-Utf8NoBom ($path, $text) {
  [IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding $false))
}

# True while a field still holds a template value rather than a real secret.
# Covers both templates: the ASCII PUT_..._HERE one written below, and the older
# streaming_config.json.example whose placeholders are Korean text (a real OBS
# password / upload key is always printable ASCII).
function Is-Placeholder ($v) {
  if (-not $v) { return $true }
  if ($v -match '^PUT_.*_HERE$') { return $true }
  return ($v -notmatch '^[\x20-\x7E]+$')
}

# ---------------------------------------------------------------- admin check
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]$id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw "Run this script as Administrator (right-click PowerShell > Run as administrator)."
}

Write-Host ""
Write-Host "============================================================"
Write-Host "  PS Livestream PC setup"
Write-Host "  repo dir : $RepoDir"
Write-Host "  tunnel   : $VpsUser@$VpsHost  (local :5000 -> VPS :5001)"
Write-Host "============================================================"

# ------------------------------------------------------------------ 1 install
Step "1/9  Software (Python / OBS Studio / Git)"

function Have-Cmd ($name) {
  $c = Get-Command $name -ErrorAction SilentlyContinue
  # The Microsoft Store python.exe stub is a 0-byte reparse point - not a real Python.
  if ($c -and $c.Source -and (Test-Path $c.Source) -and ((Get-Item $c.Source).Length -eq 0)) { return $false }
  return [bool]$c
}

# Returns a hashtable @{Exe=...; Pre=@(...)} that actually runs Python 3, or $null.
# "python resolves on PATH" is NOT the same as "Python works": Windows ships an
# App Execution Alias stub for python.exe that only advertises the Microsoft Store,
# and a fresh install often leaves PATH untouched entirely. So probe by running it.
function Resolve-Python {
  $cands = @(
    @{ Exe = "py";     Pre = @("-3") },
    @{ Exe = "python"; Pre = @() }
  )
  foreach ($glob in @("$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe",
                      "$env:ProgramFiles\Python3*\python.exe",
                      "${env:ProgramFiles(x86)}\Python3*\python.exe",
                      "C:\Python3*\python.exe")) {
    Get-Item $glob -ErrorAction SilentlyContinue | Sort-Object FullName |
      ForEach-Object { $cands += @{ Exe = $_.FullName; Pre = @() } }
  }

  # The probes below run native commands whose stderr we do not want turning into
  # terminating errors under "Stop" - a failing candidate must just be skipped.
  $old = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    foreach ($c in $cands) {
      if (($c.Exe -notmatch '[\\/]') -and -not (Have-Cmd $c.Exe)) { continue }
      if (($c.Exe -match '[\\/]')    -and -not (Test-Path $c.Exe)) { continue }
      try {
        # Build one argument list and splat a variable: @(...) inline is the array
        # subexpression operator, not splatting, and the two are only accidentally
        # equivalent here.
        $probeArgs = @($c.Pre) + @("-c", "import sys;print(sys.version_info[0])")
        $out = & $c.Exe @probeArgs
        if ($LASTEXITCODE -eq 0 -and "$out".Trim() -eq "3") { return $c }
      } catch { }
    }
  } finally { $ErrorActionPreference = $old }
  return $null
}

function Ensure-Winget-Package ($id, $label, [scriptblock]$probe, $hint) {
  if (& $probe) { Ok "$label already installed"; return }
  if (-not (Have-Cmd winget)) {
    # winget is absent on LTSC/N images and anywhere App Installer was removed.
    Warn "$label missing and winget is unavailable - install it by hand"
    Todo ("Install $label manually" + $(if ($hint) { " - $hint" } else { "" }))
    return
  }
  Say "installing $label ..."
  winget install --id $id -e --silent --accept-source-agreements --accept-package-agreements | Out-Null
  if (& $probe) { Ok "$label installed" }
  else { Warn "$label install did not register yet - reopen PowerShell and re-run" }
}

if ($SkipInstall) {
  Say "skipped (-SkipInstall)"
} else {
  Try-Step "python install" { Ensure-Winget-Package "Python.Python.3.12" "Python 3.12" `
      { [bool](Resolve-Python) } `
      "https://www.python.org/downloads/windows/ and TICK 'Add python.exe to PATH'" }
  Try-Step "git install"    { Ensure-Winget-Package "Git.Git" "Git" { Have-Cmd git } "https://git-scm.com/download/win" }
  Try-Step "obs install"    { Ensure-Winget-Package "OBSProject.OBSStudio" "OBS Studio" `
      { Test-Path "$env:ProgramFiles\obs-studio\bin\64bit\obs64.exe" } "https://obsproject.com/download" }
  # PATH additions from winget only reach new processes - refresh ours.
  $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
              [Environment]::GetEnvironmentVariable("Path","User")
}

if (-not (Have-Cmd ssh)) {
  Warn "OpenSSH client missing - the reverse tunnel cannot run"
  Todo "Install OpenSSH Client: Settings > System > Optional features > Add > OpenSSH Client"
}

# --------------------------------------------------------------------- 2 repo
Step "2/9  PS-rank repository"

# Do NOT redirect git's stderr here. git reports normal progress on stderr, and
# with $ErrorActionPreference = "Stop" a redirected native stderr becomes a
# terminating NativeCommandError - a successful clone would be reported as failed.
Try-Step "repo clone" {
  if (Test-Path (Join-Path $RepoDir ".git")) {
    Ok "already cloned - pulling latest"
    git -C $RepoDir pull --ff-only
  } else {
    $parent = Split-Path -Parent $RepoDir
    if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    git clone $RepoUrl $RepoDir
    Ok "cloned to $RepoDir"
  }
}
if (-not (Test-Path (Join-Path $RepoDir "stream_server.py"))) {
  throw "stream_server.py not found under $RepoDir - fix the clone before continuing."
}

# The scheduled task points at run_stream_server.ps1 by absolute path. If these
# scripts were run straight out of Downloads, that path is one folder-cleanup away
# from breaking every boot - so park a copy inside the repo and task against that.
$setupDir = Join-Path $RepoDir "setup"
Try-Step "setup scripts in repo" {
  if (-not (Test-Path $setupDir)) { New-Item -ItemType Directory -Path $setupDir -Force | Out-Null }
  $here  = (Resolve-Path $ScriptDir).Path.TrimEnd('\')
  $there = (Resolve-Path $setupDir).Path.TrimEnd('\')
  if ($here -ieq $there) {
    Ok "already running from $setupDir"
  } else {
    Copy-Item (Join-Path $ScriptDir "*.ps1") $setupDir -Force
    Copy-Item (Join-Path $ScriptDir "*.md")  $setupDir -Force -ErrorAction SilentlyContinue
    Ok "setup scripts copied to $setupDir (tasks will reference that copy)"
  }
}

# Belt and braces: the repo is PUBLIC and the secret files live in its root, so
# one stray `git add .` would publish the YouTube OAuth secret and upload key.
Try-Step "git exclude" {
  $exclude = Join-Path $RepoDir ".git\info\exclude"
  $want = @("config.json", "client_secrets.json", "youtube_token.pickle", "ssl/", "logs/", ".venv/")
  $have = if (Test-Path $exclude) { Get-Content $exclude } else { @() }
  $add  = $want | Where-Object { $have -notcontains $_ }
  if ($add) {
    Add-Content -Path $exclude -Value (@("", "# livestream PC secrets - never commit") + $add)
    Ok "git exclude updated ($($add -join ', '))"
  } else { Ok "git exclude already covers the secret files" }
}

# ----------------------------------------------------------------- 3 python env
Step "3/9  Python venv + packages"

$venvPy = Join-Path $RepoDir ".venv\Scripts\python.exe"
Try-Step "venv" {
  if (-not (Test-Path $venvPy)) {
    $py = Resolve-Python
    if (-not $py) {
      throw ("no working Python 3 found. Install it from https://www.python.org/downloads/windows/ " +
             "and TICK 'Add python.exe to PATH' in the installer, then re-run this script. " +
             "If PATH holds the Microsoft Store stub, turn it off in " +
             "Settings > Apps > Advanced app settings > App execution aliases (python.exe / python3.exe).")
    }
    Say "using python: $($py.Exe) $($py.Pre -join ' ')"
    $venvArgs = @($py.Pre) + @("-m", "venv", (Join-Path $RepoDir ".venv"))
    & $py.Exe @venvArgs
    # venv failures are not always non-zero exits (the Store stub just prints and
    # leaves), so confirm the interpreter really exists before claiming success.
    if (-not (Test-Path $venvPy)) { throw "venv did not produce $venvPy" }
    Ok "venv created"
  } else { Ok "venv already exists" }

  & $venvPy -m pip install --upgrade pip --quiet
  & $venvPy -m pip install -r (Join-Path $RepoDir "requirements.txt") --quiet
  # Import the two that matter most - a silent pip failure would otherwise only
  # surface later as the stream server dying on boot.
  & $venvPy -c "import flask, obsws_python, googleapiclient"
  if ($LASTEXITCODE -ne 0) { throw "packages did not import - check the pip output above" }
  Ok "packages installed (flask, obsws-python, google-api-python-client, imageio-ffmpeg ...)"
}

# ------------------------------------------------------------------- 4 config
Step "4/9  config.json"

$cfgPath = Join-Path $RepoDir "config.json"

# Written fresh instead of copied from streaming_config.json.example. The example
# carries Korean comment keys and placeholder values; stream_server.py opens the
# file as UTF-8, so one editor re-saving it as ANSI breaks the server at startup.
# An ASCII-only config removes that whole failure mode.
$CONFIG_TEMPLATE = @'
{
  "cors_origins": [
    "https://padelociety.github.io",
    "http://localhost:3000"
  ],
  "obs": {
    "host": "localhost",
    "port": 4455,
    "password": "PUT_OBS_WEBSOCKET_PASSWORD_HERE"
  },
  "youtube": {
    "privacy": "public",
    "latency": "ultraLow"
  },
  "highlight": {
    "api_base": "https://api.padelsociety.co.kr",
    "upload_key": "PUT_HIGHLIGHT_UPLOAD_KEY_HERE"
  }
}
'@

Try-Step "config.json" {
  $cfg = $null
  if (Test-Path $cfgPath) {
    # Strip a byte order mark if an editor added one. .NET reads straight through
    # a BOM, but stream_server.py opens this with encoding='utf-8' and json.load
    # then dies with "Unexpected UTF-8 BOM" - a crash loop every check above misses.
    $bytes = [IO.File]::ReadAllBytes($cfgPath)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
      Write-Utf8NoBom $cfgPath ([IO.File]::ReadAllText($cfgPath))
      Ok "config.json had a UTF-8 BOM - rewritten without it"
    }
    try { $cfg = (Read-Utf8 $cfgPath) | ConvertFrom-Json }
    catch { Warn "config.json does not parse as JSON: $($_.Exception.Message)" }
  }

  # Replace only when there is provably nothing to lose: unreadable, or both
  # secrets still unset. A half-filled config is never touched.
  $empty = (-not $cfg) -or ((Is-Placeholder $cfg.obs.password) -and (Is-Placeholder $cfg.highlight.upload_key))
  if ($empty) {
    Write-Utf8NoBom $cfgPath $CONFIG_TEMPLATE
    $cfg = $CONFIG_TEMPLATE | ConvertFrom-Json
    Ok "config.json written from the clean template (nothing was filled in yet)"
  } else {
    Ok "config.json already present"
  }

  if (Is-Placeholder $cfg.obs.password) {
    Warn "config.json obs.password is still a placeholder"
    Todo "Fill $cfgPath > obs.password with the OBS WebSocket password (OBS > Tools > WebSocket Server Settings)"
  }
  if (Is-Placeholder $cfg.highlight.upload_key) {
    Warn "config.json highlight.upload_key is still a placeholder"
    Todo "Fill $cfgPath > highlight.upload_key (must equal HIGHLIGHT_UPLOAD_KEY in the VPS .env)"
  }
}

foreach ($f in @("client_secrets.json", "youtube_token.pickle")) {
  if (-not (Test-Path (Join-Path $RepoDir $f))) {
    Warn "$f missing"
    if ($f -eq "client_secrets.json") {
      Todo "Download client_secrets.json (Google Cloud Console > OAuth client, Desktop app) into $RepoDir"
    } else {
      Todo "Run stream_server.py once in the FOREGROUND to complete YouTube sign-in (creates youtube_token.pickle)"
    }
  } else { Ok "$f present" }
}

# -------------------------------------------------------------------- 5 power
Step "5/9  Power: never sleep, survive a power cut"

if ($SkipPower) {
  Say "skipped (-SkipPower)"
} else {
  Try-Step "powercfg timeouts" {
    powercfg /change monitor-timeout-ac   0
    powercfg /change standby-timeout-ac   0
    powercfg /change hibernate-timeout-ac 0
    powercfg /change disk-timeout-ac      0
    Ok "display/sleep/hibernate/disk timeouts set to Never (on AC)"
  }
  # Fast Startup is what makes a machine come back half-resumed after the power is
  # cut. `powercfg /hibernate off` stops it working but leaves HiberbootEnabled at
  # 1, so clear that too - otherwise re-enabling hibernation later silently brings
  # Fast Startup back.
  Try-Step "hibernate off" {
    powercfg /hibernate off
    New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power" `
      -Name HiberbootEnabled -Value 0 -PropertyType DWord -Force | Out-Null
    Ok "hibernation + fast startup disabled"
  }
  # USB selective suspend drops capture cards mid-stream.
  Try-Step "usb selective suspend" {
    powercfg /setacvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 `
             48e6b7a6-50f5-4782-a5d4-53bb8f07e226 0
    powercfg /setactive SCHEME_CURRENT
    Ok "USB selective suspend disabled"
  }
  Try-Step "screen saver off" {
    New-ItemProperty -Path "HKCU:\Control Panel\Desktop" -Name ScreenSaveActive `
      -Value "0" -PropertyType String -Force | Out-Null
    New-ItemProperty -Path "HKCU:\Control Panel\Desktop" -Name ScreenSaverIsSecure `
      -Value "0" -PropertyType String -Force | Out-Null
    Ok "screen saver disabled"
  }
  # Windows Update rebooting at 3am mid-league-week is the classic silent outage.
  Try-Step "windows update no auto reboot" {
    $au = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU"
    if (-not (Test-Path $au)) { New-Item -Path $au -Force | Out-Null }
    New-ItemProperty -Path $au -Name NoAutoRebootWithLoggedOnUsers `
      -Value 1 -PropertyType DWord -Force | Out-Null
    Ok "Windows Update will not auto-reboot while signed in"
  }
  Todo "BIOS/UEFI: set 'Restore on AC Power Loss' (or 'AC Back' / 'After Power Failure') to POWER ON"
}

# ----------------------------------------------------------------- 6 firewall
Step "6/9  Firewall (tablet on the same LAN, port 5000)"

if ($SkipFirewall) {
  Say "skipped (-SkipFirewall)"
} else {
  Try-Step "firewall rule" {
    $name = "PS Court stream_server 5000"
    if (Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue) {
      Ok "rule already exists"
    } else {
      New-NetFirewallRule -DisplayName $name -Direction Inbound -Protocol TCP `
        -LocalPort 5000 -Action Allow -Profile Private,Domain | Out-Null
      Ok "inbound TCP 5000 allowed on Private/Domain networks"
    }
    Say "note: normal operation goes through https://obs.padelsociety.co.kr, so this"
    Say "      rule only matters for a direct http://<PC-IP>:5000 fallback."
  }
}

# --------------------------------------------------------------- 7 tunnel key
Step "7/9  Reverse tunnel SSH key"

$sshDir  = Join-Path $env:USERPROFILE ".ssh"
$keyPath = Join-Path $sshDir "obs_tunnel_ed25519"

Try-Step "ssh key" {
  if (-not (Test-Path $sshDir)) { New-Item -ItemType Directory -Path $sshDir -Force | Out-Null }
  if (Test-Path $keyPath) {
    Ok "key already exists: $keyPath"
  } else {
    # cmd /c form matches the original installer - quoting for an empty passphrase
    # is unreliable through PowerShell's argument parser.
    cmd /c "ssh-keygen -t ed25519 -f ""$keyPath"" -N """" -C obs-tunnel" | Out-Null
    Ok "new key generated: $keyPath"
  }
}

if (Test-Path "$keyPath.pub") {
  $pub = (Get-Content "$keyPath.pub" -Raw).Trim()
  Write-Host ""
  Write-Host "  ---------- PUBLIC KEY - register this on the VPS ----------" -ForegroundColor Yellow
  Write-Host "  $pub"
  Write-Host "  -----------------------------------------------------------" -ForegroundColor Yellow
  Todo "On the VPS, append the public key above to /home/$VpsUser/.ssh/authorized_keys (see SETUP_LIVESTREAM_PC.md step 4)"
}

# --------------------------------------------------------------- 8 auto-start
Step "8/9  Auto-start tasks (run at logon)"

if ($SkipTasks) {
  Say "skipped (-SkipTasks)"
} else {
  $user = "$env:USERDOMAIN\$env:USERNAME"

  # AtLogOn (not AtStartup) on purpose: OBS needs an interactive desktop session,
  # and obs_tunnel.ps1 resolves its key through $env:USERPROFILE, which is only
  # correct inside a real user session. Pair this with auto sign-in so that
  # "powered on" always means "logged on".
  # NOTE: do not name a parameter $args. It is an automatic variable (the array of
  # unbound arguments), so inside the function it is an Object[] no matter what was
  # passed - and -Argument then fails with "cannot convert to System.String".
  function Register-Task ($taskName, $exe, $argLine, $workDir) {
    $a = if ($workDir) {
      New-ScheduledTaskAction -Execute $exe -Argument $argLine -WorkingDirectory $workDir
    } else {
      New-ScheduledTaskAction -Execute $exe -Argument $argLine
    }
    $t = New-ScheduledTaskTrigger -AtLogOn -User $user
    $s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
           -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
           -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $taskName -Action $a -Trigger $t -Settings $s `
      -User $user -RunLevel Highest -Force | Out-Null
    Ok "task registered: $taskName"
  }

  Try-Step "task: tunnel" {
    $tunnel = Join-Path $RepoDir "obs_tunnel\obs_tunnel.ps1"
    if (-not (Test-Path $tunnel)) { throw "obs_tunnel.ps1 not found at $tunnel" }
    Register-Task "OBS-VPS-Tunnel" "powershell.exe" `
      "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$tunnel`"" $null
  }

  Try-Step "task: stream server" {
    $runner = Join-Path $setupDir "run_stream_server.ps1"
    if (-not (Test-Path $runner)) { throw "run_stream_server.ps1 not found at $runner" }
    Register-Task "PS-StreamServer" "powershell.exe" `
      "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$runner`" -RepoDir `"$RepoDir`"" $null
  }

  Try-Step "task: obs studio" {
    $obs = "$env:ProgramFiles\obs-studio\bin\64bit\obs64.exe"
    if (-not (Test-Path $obs)) { throw "obs64.exe not found at $obs" }
    # --disable-shutdown-check is load-bearing: without it, the first boot after a
    # power cut stops on the "OBS closed unexpectedly - start in safe mode?" dialog
    # and nothing streams until somebody walks over and clicks it.
    #
    # NOT --minimize-to-tray. It hides OBS on a dedicated PC where nothing else uses
    # the screen, and then the operator cannot see the preview, the bitrate, or a
    # modal dialog that is blocking a stream - they just see "OBS is already running"
    # with no window anywhere. Worse, with the tray icon disabled it is unreachable.
    Register-Task "PS-OBS-Studio" $obs `
      "--startreplaybuffer --disable-shutdown-check" `
      "$env:ProgramFiles\obs-studio\bin\64bit"
  }
}

# ------------------------------------------------------------- 9 auto sign-in
Step "9/9  Automatic sign-in check"

# The tunnel key lives in this account's profile and the tasks are registered for
# this account, so the account that auto-signs-in must be the SAME one. Elevating
# with a different admin account splits them and nothing starts at logon.
Say "tasks and tunnel key are bound to: $env:USERDOMAIN\$env:USERNAME"
Say "the account set to sign in automatically must be this same account."

Try-Step "autologon check" {
  $win = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
  $auto = (Get-ItemProperty -Path $win -Name AutoAdminLogon -ErrorAction SilentlyContinue).AutoAdminLogon
  $whom = (Get-ItemProperty -Path $win -Name DefaultUserName -ErrorAction SilentlyContinue).DefaultUserName
  if ($auto -eq "1" -and $whom -and ($whom -ine $env:USERNAME)) {
    Warn "automatic sign-in is ON but for '$whom', not '$env:USERNAME' - the auto-start tasks will not fire"
    Todo "Point automatic sign-in at $env:USERNAME, or re-run this script while signed in as $whom"
  } elseif ($auto -eq "1") {
    Ok "automatic sign-in is ON for $env:USERNAME"
  } else {
    Warn "automatic sign-in is OFF - after a power cut the PC boots to the lock screen and nothing starts"
    Todo "Turn on automatic sign-in (Sysinternals Autologon.exe, or netplwiz) - see SETUP_LIVESTREAM_PC.md step 6"
  }
}

# ------------------------------------------------------------------- summary
Write-Host ""
Write-Host "============================================================"
Write-Host "  Setup finished"
Write-Host "============================================================"

if ($script:Warnings.Count) {
  Write-Host ""
  Write-Host "  Warnings:" -ForegroundColor Yellow
  $script:Warnings | ForEach-Object { Write-Host "   - $_" -ForegroundColor Yellow }
}

if ($script:Manual.Count) {
  Write-Host ""
  Write-Host "  Still to do by hand (in order):" -ForegroundColor Yellow
  $i = 1
  $script:Manual | ForEach-Object { Write-Host "   $i) $_"; $i++ }
} else {
  Write-Host ""
  Ok "no manual steps left"
}

Write-Host ""
Write-Host "  Also do the OBS side by hand (Tools > WebSocket Server Settings,"
Write-Host "  Settings > Output > Replay Buffer 90s, recording format mp4)."
Write-Host "  Full walkthrough: $setupDir\SETUP_LIVESTREAM_PC.md"
Write-Host ""
Write-Host "  First YouTube sign-in (must be in the foreground, once):"
Write-Host "    powershell -NoProfile -ExecutionPolicy Bypass -File `"$setupDir\run_stream_server.ps1`" -Foreground"
Write-Host ""
Write-Host "  Verify when done:"
Write-Host "    powershell -NoProfile -ExecutionPolicy Bypass -File `"$setupDir\check_livestream_pc.ps1`""
Write-Host ""
