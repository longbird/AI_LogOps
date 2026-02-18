#!/usr/bin/env python3
"""AI-LogOps Subscription CLI - 관리 진입점.

사용법:
    python3 manage.py create --label "Factory-01" --openai-key "sk-..." --claude-key "sk-ant-..."
    python3 manage.py list
    python3 manage.py info <KEY>
    python3 manage.py revoke <KEY>
    python3 manage.py adduser --email admin@example.com --password pass123 --subscription-key <KEY>
"""

import os
import sys

# 패키지 import 경로 설정: 이 파일의 2단계 상위 = 프로젝트 루트
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from server.subscription.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
