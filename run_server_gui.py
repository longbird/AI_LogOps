#!/usr/bin/env python3
"""AI-LogOps Server Management GUI 진입점.

서버 프로세스를 관리하는 데스크톱 GUI 애플리케이션.

사용법:
    python run_server_gui.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def main() -> None:
    from server.gui.app import ServerGUI

    app = ServerGUI()
    app.run()


if __name__ == "__main__":
    main()
