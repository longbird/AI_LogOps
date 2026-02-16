from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from shared.utils import audit_log

from server.telegram.rate_limiter import RateLimiter


@dataclass
class ParsedCommand:
    command: str
    args: list[str]
    chat_id: int
    raw_text: str


class TelegramHandler:
    """텔레그램 명령 라우터. 스펙 섹션 6."""

    def __init__(self, admin_chat_ids: list[int]):
        """admin_chat_ids: 명령을 수락할 chat_id 목록."""
        self.admin_chat_ids: set[int] = set(admin_chat_ids)
        self.rate_limiter: RateLimiter = RateLimiter()
        self._handlers: dict[str, Callable[[ParsedCommand], Awaitable[str]]] = {}

    def register(
        self, command: str, handler: Callable[[ParsedCommand], Awaitable[str]]
    ) -> None:
        """명령 핸들러 등록. handler(ParsedCommand) -> str (응답 메시지)"""
        normalized = command.strip().lstrip("/").lower()
        if normalized:
            self._handlers[normalized] = handler

    def parse(self, text: str, chat_id: int) -> ParsedCommand | None:
        """'/' 시작 텍스트를 ParsedCommand로 파싱. 비명령이면 None."""
        stripped = text.strip()
        if not stripped.startswith("/"):
            return None

        parts = stripped.split()
        if not parts:
            return None

        command_token = parts[0]
        if len(command_token) <= 1:
            return None

        return ParsedCommand(
            command=command_token[1:].lower(),
            args=parts[1:],
            chat_id=chat_id,
            raw_text=stripped,
        )

    async def handle(self, text: str, chat_id: int) -> str:
        """명령 처리. 응답 메시지 반환."""
        if chat_id not in self.admin_chat_ids:
            return "Unauthorized."

        if not self.rate_limiter.check(chat_id):
            return "Too many requests. Please wait."

        parsed = self.parse(text=text, chat_id=chat_id)
        if parsed is None:
            return "Unknown command"

        command_handler = self._handlers.get(parsed.command)
        if command_handler is None:
            return f"Unknown command: /{parsed.command}"

        audit_log(
            action=parsed.command.upper(),
            agent_id="server",
            user=f"telegram:{chat_id}",
            detail=parsed.raw_text,
        )
        return await command_handler(parsed)
