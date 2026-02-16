from __future__ import annotations

import pytest

from server.telegram.handler import ParsedCommand, TelegramHandler
from server.telegram.rate_limiter import RateLimiter


def test_rate_limiter_allows_under_limit() -> None:
    limiter = RateLimiter(window=10, max_requests=5)

    for _ in range(5):
        assert limiter.check(chat_id=1001) is True


def test_rate_limiter_blocks_over_limit() -> None:
    limiter = RateLimiter(window=10, max_requests=3)

    assert limiter.check(chat_id=1001) is True
    assert limiter.check(chat_id=1001) is True
    assert limiter.check(chat_id=1001) is True
    assert limiter.check(chat_id=1001) is False


def test_rate_limiter_different_chats_independent() -> None:
    limiter = RateLimiter(window=10, max_requests=1)

    assert limiter.check(chat_id=1) is True
    assert limiter.check(chat_id=1) is False
    assert limiter.check(chat_id=2) is True


def test_parse_valid_command() -> None:
    handler = TelegramHandler(admin_chat_ids=[101])

    parsed = handler.parse("/connect PC-01 1.2.3.4 9500", chat_id=101)

    assert parsed == ParsedCommand(
        command="connect",
        args=["PC-01", "1.2.3.4", "9500"],
        chat_id=101,
        raw_text="/connect PC-01 1.2.3.4 9500",
    )


def test_parse_non_command() -> None:
    handler = TelegramHandler(admin_chat_ids=[101])

    assert handler.parse("hello", chat_id=101) is None


@pytest.mark.asyncio
async def test_handle_unauthorized() -> None:
    handler = TelegramHandler(admin_chat_ids=[101])

    response = await handler.handle("/status", chat_id=999)

    assert response == "Unauthorized."


@pytest.mark.asyncio
async def test_handle_rate_limited() -> None:
    handler = TelegramHandler(admin_chat_ids=[101])
    handler.rate_limiter = RateLimiter(window=10, max_requests=1)

    first = await handler.handle("/foobar", chat_id=101)
    second = await handler.handle("/foobar", chat_id=101)

    assert first == "Unknown command: /foobar"
    assert second == "Too many requests. Please wait."


@pytest.mark.asyncio
async def test_handle_unknown_command() -> None:
    handler = TelegramHandler(admin_chat_ids=[101])

    response = await handler.handle("/foobar", chat_id=101)

    assert response == "Unknown command: /foobar"


@pytest.mark.asyncio
async def test_handle_registered_command(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = TelegramHandler(admin_chat_ids=[101])
    captured: dict[str, str] = {}

    def fake_audit_log(
        action: str,
        agent_id: str,
        user: str,
        detail: str,
        log_path: str = "storage/audit.jsonl",
    ) -> None:
        captured["action"] = action
        captured["agent_id"] = agent_id
        captured["user"] = user
        captured["detail"] = detail
        captured["log_path"] = log_path

    async def ping_handler(parsed: ParsedCommand) -> str:
        assert parsed.command == "ping"
        assert parsed.args == ["now"]
        return "pong"

    monkeypatch.setattr("server.telegram.handler.audit_log", fake_audit_log)
    handler.register("ping", ping_handler)

    response = await handler.handle("/ping now", chat_id=101)

    assert response == "pong"
    assert captured["action"] == "PING"
    assert captured["agent_id"] == "server"
    assert captured["user"] == "telegram:101"
    assert captured["detail"] == "/ping now"


@pytest.mark.asyncio
async def test_handle_status_command() -> None:
    handler = TelegramHandler(admin_chat_ids=[101])

    async def status_handler(_: ParsedCommand) -> str:
        return "Server: OK"

    handler.register("status", status_handler)

    response = await handler.handle("/status", chat_id=101)

    assert response == "Server: OK"
