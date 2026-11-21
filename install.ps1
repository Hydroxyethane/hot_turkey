param([string]$InstallDir)
$ErrorActionPreference = 'Stop'

$root = $InstallDir
if (-not $root) { $root = Split-Path -Parent $MyInvocation.MyCommand.Path }
$root = $root.TrimEnd('\')
$scriptPath = Join-Path $root 'hot_turkey.py'

if (-not (Test-Path $scriptPath)) {
    throw "Cannot find hot_turkey.py in $root"
}

# 1. Desktop shortcut
$ws = New-Object -ComObject WScript.Shell
$lnk = Join-Path $env:USERPROFILE 'Desktop\Hot Turkey.lnk'
$s = $ws.CreateShortcut($lnk)
$s.TargetPath = Join-Path $root 'run_hot_turkey.bat'
$s.WorkingDirectory = $root
$s.Description = 'Cold Turkey-style blocker'
$icon = Join-Path $root 'assets\hot_turkey.ico'
if (Test-Path $icon) { $s.IconLocation = $icon }
$s.Save()
Write-Host "Desktop shortcut created: $lnk"

# 2. Autostart via the per-user Run key (no admin needed)
$pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pythonw) {
    $pythonw = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
}
if (-not $pythonw) {
    throw "Cannot find pythonw.exe or python.exe - install Python 3.11+ first."
}
$command = '"{0}" "{1}"' -f $pythonw, $scriptPath
reg add 'HKCU\Software\Microsoft\Windows\CurrentVersion\Run' /v HotTurkey /t REG_SZ /d $command /f | Out-Null
Write-Host "Autostart enabled (HKCU Run key: HotTurkey)"
