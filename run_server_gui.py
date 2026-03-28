#!/usr/bin/env python3
"""AI-LogOps Server Management GUI 진입점.

서버 프로세스를 관리하는 데스크톱 GUI 애플리케이션.

사용법:
    python run_server_gui.py
"""

from __future__ import annotations

import atexit
import os
import signal
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

PID_FILE = ROOT_DIR / ".gui.pid"


def _kill_existing() -> None:
    """기존 GUI 프로세스가 남아 있으면 종료."""
    if not PID_FILE.exists():
        return
    try:
        old_pid = int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        PID_FILE.unlink(missing_ok=True)
        return
    # 프로세스 트리 전체 종료 (GUI + 서버 subprocess)
    if sys.platform == "win32":
        os.system(f"taskkill /F /T /PID {old_pid} >nul 2>&1")
    else:
        try:
            os.kill(old_pid, signal.SIGTERM)
        except OSError:
            pass
    PID_FILE.unlink(missing_ok=True)


def _write_pid() -> None:
    PID_FILE.write_text(str(os.getpid()))


def _cleanup_pid() -> None:
    PID_FILE.unlink(missing_ok=True)


def main() -> None:
    _kill_existing()
    _write_pid()
    atexit.register(_cleanup_pid)

    from server.gui.app import ServerGUI

    app = ServerGUI()
    app.run()


if __name__ == "__main__":
    main()
