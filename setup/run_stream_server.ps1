#requires -Version 5.1
<#
  Supervisor for stream_server.py.

  Registered as the "PS-StreamServer" scheduled task by setup_livestream_pc.ps1.
  Keeps the Flask server alive: if it exits for any reason (OBS crash, unhandled
  error, YouTube API blip) it is restarted after a short pause, so an unattended
  PC recovers without anybody walking over to it.

  stdout/stderr go to logs\stream_server_YYYYMMDD.log because the task runs with
  a hidden window and there is nothing to read otherwise.

  Foreground use (first YouTube sign-in, or debugging):
    powershell -NoProfile -ExecutionPolicy Bypass -File .\run_stream_server.ps1 -Foreground
  (ASCII only - Windows PowerShell 5.1 misreads non-ASCII without a BOM.)
#>
[CmdletBinding()]
param(
  [string]$RepoDir,
  [int]$RestartDelaySeconds = 10,
  [int]$LogRetentionDays = 14,
  [switch]$Foreground
)

$ErrorActionPreference = "Stop"

# Resolve the script's own folder HERE, not in a param() default: Windows
# PowerShell 5.1 does not reliably have $PSScriptRoot populated while param
# defaults are evaluated, and Split-Path then dies on an empty -Path. The
# scheduled task passes -RepoDir explicitly, but the -Foreground run does not.
if (-not $RepoDir) {
  $ScriptDir = $PSScriptRoot
  if (-not $ScriptDir) { $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
  if (-not $ScriptDir) { $ScriptDir = (Get-Location).Path }
  $RepoDir = Split-Path -Parent $ScriptDir
}

$server = Join-Path $RepoDir "stream_server.py"
$venvPy = Join-Path $RepoDir ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPy) { $venvPy } else { "python" }

if (-not (Test-Path $server)) { throw "stream_server.py not found at $server" }

$logDir = Join-Path $RepoDir "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

# Drop stale logs so an always-on PC does not slowly fill its disk.
Get-ChildItem -Path $logDir -Filter "stream_server_*.log" -ErrorAction SilentlyContinue |
  Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$LogRetentionDays) } |
  Remove-Item -Force -ErrorAction SilentlyContinue

# Python switches stdout to the locale encoding (cp949 on Korean Windows) the
# moment it is redirected, and stream_server.py's startup banner contains emoji -
# so a supervised run died on UnicodeEncodeError before Flask ever bound :5000,
# while an interactive run was fine. UTF-8 mode makes the encoding explicit.
$env:PYTHONUTF8       = "1"
$env:PYTHONIOENCODING = "utf-8"

if ($Foreground) {
  # Interactive run: no redirection, so the YouTube OAuth prompt and its browser
  # hand-off stay visible. This is the run that creates youtube_token.pickle.
  Write-Host "Foreground run - Ctrl+C to stop. Python: $python"
  & $python $server
  exit $LASTEXITCODE
}

# Below, native stderr is redirected into the log file. Windows PowerShell 5.1
# (which is what the scheduled task runs) surfaces redirected native stderr as
# NativeCommandError records, so under "Stop" they terminate - and stream_server.py
# logs through Python's logging module, i.e. to stderr. That would read as "the
# server died" on its very first log line, restarting it forever.
# A supervisor loop wants to survive everything anyway, so: Continue.
$ErrorActionPreference = "Continue"

while ($true) {
  $stamp = Get-Date -Format "yyyyMMdd"
  $log   = Join-Path $logDir "stream_server_$stamp.log"
  $start = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

  Add-Content -Path $log -Value "[$start] starting stream_server.py (python: $python)"

  try {
    # cmd does the redirection, not PowerShell: `*>>` would re-decode the child's
    # UTF-8 bytes through the console codepage (mojibake in the log) and turn every
    # stderr line into a NativeCommandError record. cmd appends the bytes as-is.
    # -u keeps Python unbuffered so the log is current instead of lagging a block.
    & cmd.exe /c "`"$python`" -u `"$server`" >> `"$log`" 2>&1"
    $code = $LASTEXITCODE
  } catch {
    Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] launch failed: $($_.Exception.Message)"
    $code = -1
  }

  $stop = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  Add-Content -Path $log -Value "[$stop] exited (code $code) - restarting in ${RestartDelaySeconds}s"
  Start-Sleep -Seconds $RestartDelaySeconds
}
