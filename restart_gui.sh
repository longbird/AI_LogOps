#!/bin/bash
# Claude Code 세션에서 서버 GUI 재시작용
cd "$(dirname "$0")" 2>/dev/null || true

if [ -f .gui.pid ]; then
    PID=$(cat .gui.pid)
    echo "[재시작] 기존 프로세스 종료 (PID=$PID)..."
    taskkill //F //T //PID "$PID" 2>/dev/null || kill "$PID" 2>/dev/null
    rm -f .gui.pid
    sleep 2
else
    echo "[재시작] 기존 프로세스 없음"
fi

echo "[재시작] 서버 GUI 시작..."
python run_server_gui.py 2>&1 &
disown
sleep 3
echo "[재시작] 완료 (PID=$(cat .gui.pid 2>/dev/null))"
