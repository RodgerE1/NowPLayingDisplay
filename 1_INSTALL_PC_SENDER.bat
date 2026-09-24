@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1
if errorlevel 1 (
    echo Python was not found.
    echo Install 64-bit Python 3.9 or newer, then run this file again.
    pause
    exit /b 1
)

echo Creating the private Python environment...
py -3 -m venv .venv
if errorlevel 1 goto :failed

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
if errorlevel 1 goto :failed

python -m pip install -r requirements.txt
if errorlevel 1 goto :failed

echo.
echo Installation complete.
echo Run 2_RUN_PC_SENDER.bat for the visible console.
echo Run 2_RUN_PC_SENDER_TRAY.bat for the system-tray version.
pause
exit /b 0

:failed
echo.
echo Installation failed. Copy the error shown above and send it to me.
pause
exit /b 1
