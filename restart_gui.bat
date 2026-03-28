@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

if exist .gui.pid (
    set /p PID=<.gui.pid
    echo [재시작] 기존 프로세스 종료 중 (PID=%PID%)...
    taskkill /F /T /PID %PID% >nul 2>&1
    del .gui.pid >nul 2>&1
    timeout /t 2 /nobreak >nul
) else (
    echo [재시작] 기존 프로세스 없음
)

echo [재시작] 서버 GUI 시작 중...
start "" pythonw run_server_gui.py
echo [재시작] 완료
timeout /t 2 /nobreak >nul
