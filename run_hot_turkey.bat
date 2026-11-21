@echo off
REM Launch Hot Turkey with pythonw (no console window). Fallbacks included.
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0hot_turkey.py"
    exit /b 0
)
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%~dp0hot_turkey.py"
    exit /b 0
)
where python >nul 2>nul
if %errorlevel%==0 (
    start "" python "%~dp0hot_turkey.py"
    exit /b 0
)
echo Hot Turkey could not find a Python installation.
echo Install Python 3.11+ from python.org (check "Add python.exe to PATH").
pause
exit /b 1
