"""실시간 로그 스트림에서 잠금/데드락 등 이상 패턴을 감지하여 알림."""

from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable

from shared.utils import setup_logging

logger = setup_logging("log_alert_detector")

NotifyCallback = Callable[[str], Awaitable[None]]

# ── 기본 감지 패턴 (대소문자 무시) ────────────────────────────
DEFAULT_PATTERNS: list[dict[str, str]] = [
    # 데드락
    {"name": "deadlock", "pattern": r"deadlock|데드락|교착\s*상태"},
    {
        "name": "deadlock_victim",
        "pattern": r"was\s+deadlocked\s+on|deadlock\s+victim",
    },
    # 잠금 대기 / 타임아웃
    {
        "name": "lock_timeout",
        "pattern": (
            r"lock\s*(wait\s*)?timeout|잠금\s*시간\s*초과|"
            r"lock\s+request\s+time\s*out"
        ),
    },
    {
        "name": "waiting_for_lock",
        "pattern": r"waiting\s+for\s+(table\s+)?lock|잠금\s*대기",
    },
    # 블로킹
    {
        "name": "blocking",
        "pattern": (
            r"blocking\s+(session|process|query)|"
            r"blocked\s+by\s+process|블로킹|차단\s*발생"
        ),
    },
    # 래치 / 스핀락
    {"name": "latch_timeout", "pattern": r"latch\s+timeout|latch\s+contention"},
    # 일반 DB 이상
    {
        "name": "connection_pool_exhausted",
        "pattern": (
            r"connection\s+pool\s+(exhaust|full|timeout)|"
            r"cannot\s+obtain\s+connection|연결\s*풀\s*고갈"
        ),
    },
]


class LogAlertDetector:
    """로그 라인을 검사하여 잠금/데드락 패턴 감지 시 알림 콜백 호출.

    Parameters
    ----------
    on_notify:
        알림 전송 콜백 (Telegram 등).
    patterns:
        ``[{"name": str, "pattern": str}, ...]`` 형태의 감지 패턴 목록.
        None이면 DEFAULT_PATTERNS 사용.
    cooldown:
        동일 패턴명 기준 재알림 억제 시간(초). 기본 300초(5분).
    enabled:
        False면 감지 비활성화.
    """

    def __init__(
        self,
        on_notify: NotifyCallback,
        patterns: list[dict[str, str]] | None = None,
        cooldown: float = 300.0,
        enabled: bool = True,
    ) -> None:
        self._notify = on_notify
        self._cooldown = cooldown
        self._enabled = enabled
        self._last_alert: dict[str, float] = {}  # pattern_name → timestamp

        raw = patterns if patterns is not None else DEFAULT_PATTERNS
        self._compiled: list[tuple[str, re.Pattern[str]]] = []
        for entry in raw:
            name = entry.get("name", "unknown")
            pat = entry.get("pattern", "")
            if pat:
                try:
                    self._compiled.append((name, re.compile(pat, re.IGNORECASE)))
                except re.error:
                    logger.warning("invalid alert pattern '%s': %s", name, pat)

        if self._enabled:
            logger.info(
                "log alert detector: %d patterns, cooldown=%ds",
                len(self._compiled),
                int(self._cooldown),
            )

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    async def check_line(self, filename: str, line: str) -> None:
        """로그 라인 검사. 패턴 매칭 시 알림 전송 (쿨다운 적용)."""
        if not self._enabled or not line:
            return

        now = time.monotonic()

        for name, regex in self._compiled:
            if not regex.search(line):
                continue

            # 쿨다운 체크
            last = self._last_alert.get(name, 0.0)
            if now - last < self._cooldown:
                return

            self._last_alert[name] = now
            alert_msg = (
                f"⚠️ [로그 이상 감지] {name}\n파일: {filename}\n내용: {line[:200]}"
            )
            logger.warning("alert triggered: %s in %s", name, filename)

            try:
                await self._notify(alert_msg)
            except Exception:
                logger.exception("failed to send alert notification")

            # 한 라인에서 첫 매칭만 알림 (중복 방지)
            return
