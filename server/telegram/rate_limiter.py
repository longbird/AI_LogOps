from __future__ import annotations

import time
from collections import defaultdict


class RateLimiter:
    """슬라이딩 윈도우 기반 요청 제한. 스펙 섹션 8.1."""

    def __init__(self, window: int = 10, max_requests: int = 5):
        """window: 초 단위 윈도우. max_requests: 윈도우 내 최대 허용 수."""
        self.window: int = window
        self.max_requests: int = max_requests
        self._requests: defaultdict[int, list[float]] = defaultdict(list)

    def check(self, chat_id: int) -> bool:
        """True=허용, False=차단. 허용 시 요청 기록."""
        now = time.time()
        window_start = now - self.window
        timestamps = self._requests[chat_id]
        timestamps[:] = [ts for ts in timestamps if ts >= window_start]

        if len(timestamps) >= self.max_requests:
            return False

        timestamps.append(now)
        return True
