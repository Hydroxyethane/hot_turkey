@echo off
REM Install Hot Turkey: desktop shortcut + per-user autostart.
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -InstallDir "%~dp0"
if errorlevel 1 (
    echo.
    echo Install failed - see messages above.
    pause
    exit /b 1
)
echo.
echo Hot Turkey installed. Look for the desktop shortcut, or launch from here:
echo   run_hot_turkey.bat
echo.
pause
