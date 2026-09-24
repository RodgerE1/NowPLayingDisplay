@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo The companion has not been installed yet.
    echo Run 1_INSTALL_PC_SENDER.bat first.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"
python pc_sender.py

echo.
echo The PC sender has stopped.
pause
