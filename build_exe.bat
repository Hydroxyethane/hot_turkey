@echo off
REM Build a standalone .exe with PyInstaller (run on Windows with Python 3.11+).
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller
if errorlevel 1 goto :err
python -m PyInstaller --onefile --windowed --name "Hot Turkey" --noconfirm --icon "assets\hot_turkey.ico" hot_turkey.py
if errorlevel 1 goto :err
echo.
echo Built: %cd%\dist\Hot Turkey.exe
echo Run it as administrator for full website blocking.
pause
exit /b 0
:err
echo.
echo Build failed - see messages above.
pause
exit /b 1
