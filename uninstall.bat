@echo off
REM Uninstall Hot Turkey: removes desktop shortcut and autostart entry.
REM Your config, logs and hosts backup are kept.
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -Command "$lnk = Join-Path $env:USERPROFILE 'Desktop\Hot Turkey.lnk'; if (Test-Path $lnk) { Remove-Item $lnk; Write-Host 'Desktop shortcut removed' } else { Write-Host 'No desktop shortcut found' }; reg delete 'HKCU\Software\Microsoft\Windows\CurrentVersion\Run' /v HotTurkey /f 2>$null; Write-Host 'Autostart removed'"
pause
