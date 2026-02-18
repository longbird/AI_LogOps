#!/usr/bin/env python3
"""AI-LogOps Subscription Server - 실행 진입점.

사용법:
    python3 run_server.py [--host 0.0.0.0] [--port 8500]
"""

import os
import sys

# 패키지 import 경로 설정: 이 파일의 2단계 상위 = 프로젝트 루트
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from server.subscription.app import main  # noqa: E402

if __name__ == "__main__":
    main()
