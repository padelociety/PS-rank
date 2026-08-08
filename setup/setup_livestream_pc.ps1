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

# streaming_config.json.example ships Korean placeholder text in these fields.
# A real OBS password / upload key is printable ASCII, so "has a non-ASCII
# character" is a locale-safe way to spot an unfilled placeholder.
function Is-Placeholder ($v) {
  if (-not $v) { return $true }
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

function Ensure-Winget-Package ($id, $label, [scriptblock]$probe) {
  if (& $probe) { Ok "$label already installed"; return }
  if (-not (Have-Cmd winget)) {
    Warn "$label missing and winget is unavailable - install it by hand"
    Todo "Install $label manually"
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
  Try-Step "python install" { Ensure-Winget-Package "Python.Python.3.12" "Python 3.12" { (Have-Cmd py) -or (Have-Cmd python) } }
  Try-Step "git install"    { Ensure-Winget-Package "Git.Git"            "Git"          { Have-Cmd git } }
  Try-Step "obs install"    { Ensure-Winget-Package "OBSProject.OBSStudio" "OBS Studio" {
      Test-Path "$env:ProgramFiles\obs-studio\bin\64bit\obs64.exe" } }
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
  $here  = (Resolve-Path $PSScriptRoot).Path.TrimEnd('\')
  $there = (Resolve-Path $setupDir).Path.TrimEnd('\')
  if ($here -ieq $there) {
    Ok "already running from $setupDir"
  } else {
    Copy-Item (Join-Path $PSScriptRoot "*.ps1") $setupDir -Force
    Copy-Item (Join-Path $PSScriptRoot "*.md")  $setupDir -Force -ErrorAction SilentlyContinue
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
    # `py -3 -m venv` vs `python -m venv` - the -3 selector only exists on the launcher.
    if (Have-Cmd py) { py -3 -m venv (Join-Path $RepoDir ".venv") }
    else             { python -m venv (Join-Path $RepoDir ".venv") }
    Ok "venv created"
  } else { Ok "venv already exists" }

  & $venvPy -m pip install --upgrade pip --quiet
  & $venvPy -m pip install -r (Join-Path $RepoDir "requirements.txt") --quiet
  Ok "packages installed (flask, obsws-python, google-api-python-client, imageio-ffmpeg ...)"
}

# ------------------------------------------------------------------- 4 config
Step "4/9  config.json"

$cfgPath = Join-Path $RepoDir "config.json"
Try-Step "config.json" {
  if (Test-Path $cfgPath) {
    Ok "config.json already present"
    $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
    if (Is-Placeholder $cfg.obs.password) {
      Warn "config.json obs.password is still a placeholder"
      Todo "Fill config.json > obs.password with the OBS WebSocket password"
    }
    if (Is-Placeholder $cfg.highlight.upload_key) {
      Warn "config.json highlight.upload_key is still a placeholder"
      Todo "Fill config.json > highlight.upload_key (must equal HIGHLIGHT_UPLOAD_KEY in the VPS .env)"
    }
  } else {
    Copy-Item (Join-Path $RepoDir "streaming_config.json.example") $cfgPath
    Ok "config.json created from the example - it still holds placeholders"
    Todo "Edit $cfgPath : obs.password + highlight.upload_key"
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
  # Turning hibernation off also kills Fast Startup, which is what makes a machine
  # come back in a half-resumed state after the power is cut.
  Try-Step "hibernate off" {
    powercfg /hibernate off
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
  function Register-Task ($name, $exe, $args, $workdir) {
    $a = if ($workdir) {
      New-ScheduledTaskAction -Execute $exe -Argument $args -WorkingDirectory $workdir
    } else {
      New-ScheduledTaskAction -Execute $exe -Argument $args
    }
    $t = New-ScheduledTaskTrigger -AtLogOn -User $user
    $s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
           -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
           -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $name -Action $a -Trigger $t -Settings $s `
      -User $user -RunLevel Highest -Force | Out-Null
    Ok "task registered: $name"
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
    Register-Task "PS-OBS-Studio" $obs `
      "--startreplaybuffer --minimize-to-tray --disable-shutdown-check" `
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
