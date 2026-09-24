@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo The companion has not been installed yet.
    echo Run 1_INSTALL_PC_SENDER.bat first.
    pause
    exit /b 1
)

if not exist "tray_sender.py" (
    echo tray_sender.py was not found.
    pause
    exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" "tray_sender.py"
exit /b 0
