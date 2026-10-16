@echo off
title RUETHAI KASIKRANUN RECEIPT SYSTEM
cd /d "%~dp0"

if not exist app.py (
    echo app.py not found in %~dp0
    echo Keep bill.bat in the same folder as app.py and receipts.db.
    pause
    exit /b 1
)

rem --- find Python (py launcher first, then python) ---
set PY=
py -3 --version >nul 2>nul && set PY=py -3
if not defined PY python --version >nul 2>nul && set PY=python
if not defined PY (
    echo Python was not found. Install Python 3 from python.org and tick "Add Python to PATH".
    pause
    exit /b 1
)

rem --- install Flask the first time only ---
%PY% -c "import flask" >nul 2>nul
if errorlevel 1 (
    echo Installing Flask, first run only...
    %PY% -m pip install flask
    if errorlevel 1 (
        echo Flask install failed. Check the internet connection and try again.
        pause
        exit /b 1
    )
)

rem --- open the browser a few seconds after the server starts ---
start "" /min cmd /c "ping -n 4 127.0.0.1 >nul & start http://127.0.0.1:5000"

echo.
echo RUETHAI KASIKRANUN RECEIPT SYSTEM is running at http://127.0.0.1:5000
echo Keep this window open while you work. Close it to stop the program.
echo.
%PY% app.py

echo.
echo The program has stopped.
pause
