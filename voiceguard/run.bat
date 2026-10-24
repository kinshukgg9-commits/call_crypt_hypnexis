@echo off
rem VoiceGuard: double-click to start. Sets itself up on first use, then opens the page.
setlocal
cd /d "%~dp0"
title VoiceGuard server (close this window to stop)
if not defined PORT set PORT=8000

if not exist ".venv\Scripts\python.exe" (
    echo First run: setting up. This takes a few minutes and needs internet.
    py -3.11 -m venv .venv
    if errorlevel 1 (
        echo.
        echo Python 3.11 was not found. Install it from https://www.python.org/downloads/ and run this again.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Installing the requirements failed. Check your internet connection and run this again.
        pause
        exit /b 1
    )
)

rem Open the page as soon as the server answers (skipped when VOICEGUARD_NO_BROWSER is set).
if not defined VOICEGUARD_NO_BROWSER start "" /min powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 180;$i++){try{Invoke-WebRequest http://localhost:%PORT%/health -UseBasicParsing|Out-Null;Start-Process http://localhost:%PORT%;break}catch{Start-Sleep 1}}"

echo.
echo Starting VoiceGuard on http://localhost:%PORT%
echo The page opens by itself. Keep this window open; close it or press Ctrl+C to stop.
echo.
".venv\Scripts\python.exe" -m uvicorn backend.app:app --port %PORT% --workers 1

echo.
echo The server stopped. If it says the address is already in use, VoiceGuard is already
echo running in another window: just open http://localhost:%PORT%
pause
