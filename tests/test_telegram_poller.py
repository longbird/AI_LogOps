from __future__ import annotations

import pytest
from unittest.mock import MagicMock, AsyncMock

from agent.telegram.poller import AgentTelegramPoller


class TestAgentTelegramStatusEnhanced:
    async def test_status_with_system_monitor(self):
        """Agent /status should include system monitoring data when monitor is set."""
        poller = AgentTelegramPoller(bot_token="fake", admin_chat_id=123)

        mock_monitor = MagicMock()
        mock_monitor.format_status_report.return_value = (
            "=== System Status ===\nCPU: 45.0%\nMemory: 62.5%\nHandle: 5000\nGDI: 1200"
        )
        poller.system_monitor = mock_monitor

        mock_update = MagicMock()
        mock_update.effective_chat = MagicMock(id=123)
        mock_update.effective_message = AsyncMock()

        mock_context = MagicMock()
        mock_context.args = None

        await poller._cmd_status(mock_update, mock_context)

        reply_text = mock_update.effective_message.reply_text.call_args[0][0]
        assert "Agent: STANDBY" in reply_text
        assert "CPU: 45.0%" in reply_text
        assert "Memory: 62.5%" in reply_text

    async def test_status_without_system_monitor(self):
        """Agent /status without monitor should show basic state only."""
        poller = AgentTelegramPoller(bot_token="fake", admin_chat_id=123)
        # system_monitor is None by default

        mock_update = MagicMock()
        mock_update.effective_chat = MagicMock(id=123)
        mock_update.effective_message = AsyncMock()

        mock_context = MagicMock()
        mock_context.args = None

        await poller._cmd_status(mock_update, mock_context)

        reply_text = mock_update.effective_message.reply_text.call_args[0][0]
        assert reply_text == "Agent: STANDBY"
