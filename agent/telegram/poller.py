from __future__ import annotations

# pyright: reportMissingImports=false, reportArgumentType=false, reportAttributeAccessIssue=false, reportUnusedCallResult=false

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol

from telegram.ext import Application, CommandHandler

from shared.utils import setup_logging

if TYPE_CHECKING:
    from agent.core.system_monitor import SystemMonitor


class _MessageLike(Protocol):
    async def reply_text(self, text: str) -> object: ...


class _ChatLike(Protocol):
    id: int


class _UpdateLike(Protocol):
    effective_chat: _ChatLike | None
    effective_message: _MessageLike | None


class _ContextLike(Protocol):
    args: list[str] | None


class _BotLike(Protocol):
    async def send_message(self, chat_id: int, text: str) -> object: ...


class _UpdaterLike(Protocol):
    async def start_polling(self) -> object: ...

    async def stop(self) -> object: ...


class _ApplicationLike(Protocol):
    bot: _BotLike
    updater: _UpdaterLike | None

    def add_handler(self, handler: object) -> object: ...

    async def initialize(self) -> object: ...

    async def start(self) -> object: ...

    async def stop(self) -> object: ...

    async def shutdown(self) -> object: ...


class AgentTelegramPoller:
    """Agent 측 텔레그램 봇. 스펙 섹션 2.1 TelegramPoller."""

    def __init__(self, bot_token: str, admin_chat_id: int):
        """admin_chat_id: 단일 관리자 ID."""
        self.bot_token: str = bot_token
        self.admin_chat_id: int = admin_chat_id
        self.application: _ApplicationLike | None = None
        self.on_connect: Callable[[str, int], Awaitable[None]] | None = None
        self.on_disconnect: Callable[[], Awaitable[None]] | None = None
        self._state: str = "STANDBY"
        self._logger: logging.Logger = setup_logging(self.__class__.__name__)
        self.system_monitor: SystemMonitor | None = None

    async def start(self) -> None:
        """python-telegram-bot Application 초기화 + polling 시작."""
        if self.application is not None:
            return

        application = Application.builder().token(self.bot_token).build()
        application.add_handler(CommandHandler("status", self._cmd_status))
        application.add_handler(CommandHandler("connect", self._cmd_connect))
        application.add_handler(CommandHandler("disconnect", self._cmd_disconnect))

        await application.initialize()
        await application.start()
        if application.updater is not None:
            _ = await application.updater.start_polling()
        self.application = application

    async def stop(self) -> None:
        """polling 정지 + shutdown."""
        application = self.application
        if application is None:
            return

        if application.updater is not None:
            _ = await application.updater.stop()
        _ = await application.stop()
        _ = await application.shutdown()
        self.application = None

    async def send_message(self, text: str) -> None:
        """관리자에게 메시지 전송."""
        application = self.application
        if application is None:
            raise RuntimeError("telegram poller is not started")

        _ = await application.bot.send_message(chat_id=self.admin_chat_id, text=text)

    def _is_admin(self, update: _UpdateLike) -> bool:
        chat = update.effective_chat
        if chat is None:
            return False
        return chat.id == self.admin_chat_id

    async def _cmd_status(self, update: _UpdateLike, context: _ContextLike) -> None:
        del context
        if not self._is_admin(update):
            return
        if update.effective_message is None:
            return

        if self.system_monitor is not None:
            report = self.system_monitor.format_status_report()
            status_text = f"Agent: {self._state}\n\n{report}"
        else:
            status_text = f"Agent: {self._state}"

        _ = await update.effective_message.reply_text(status_text)

    async def _cmd_connect(self, update: _UpdateLike, context: _ContextLike) -> None:
        if not self._is_admin(update):
            return

        message = update.effective_message
        if message is None:
            return

        args = context.args or []
        if len(args) != 2:
            _ = await message.reply_text("Usage: /connect <IP> <PORT>")
            return

        ip = args[0]
        try:
            port = int(args[1])
        except ValueError:
            _ = await message.reply_text("PORT must be an integer.")
            return

        if self.on_connect is None:
            _ = await message.reply_text("Connect callback is not configured.")
            return

        await self.on_connect(ip, port)
        self._state = "CONNECTED"
        _ = await message.reply_text(f"Connecting to {ip}:{port}")

    async def _cmd_disconnect(self, update: _UpdateLike, context: _ContextLike) -> None:
        del context
        if not self._is_admin(update):
            return

        message = update.effective_message
        if message is None:
            return

        if self.on_disconnect is None:
            _ = await message.reply_text("Disconnect callback is not configured.")
            return

        await self.on_disconnect()
        self._state = "STANDBY"
        _ = await message.reply_text("Disconnected.")
        self._logger.info("disconnect command handled")
