from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

pytest = importlib.import_module("pytest")

from server.ai.provider import (
    AIProviderFactory,
    BaseAIProvider,
    ClaudeProvider,
    OpenAIProvider,
)


class _DummyProvider(BaseAIProvider):
    def __init__(
        self,
        responses: list[str] | None = None,
        failures: int = 0,
        fallback: BaseAIProvider | None = None,
    ) -> None:
        super().__init__(api_key="dummy", model="dummy", fallback=fallback)
        self.provider_name: str = "dummy"
        self._responses: list[str] = responses or ["ok"]
        self._failures: int = failures
        self._calls: int = 0

    async def _call_api(self, prompt: str, system: str = "") -> str:
        self._calls += 1
        if self._calls <= self._failures:
            raise RuntimeError("temporary failure")
        return self._responses[-1]


class TestAIProviderFactory:
    def test_create_openai(self) -> None:
        provider = AIProviderFactory.create("openai", api_key="k1", model="gpt-4o-mini")

        assert isinstance(provider, OpenAIProvider)
        assert provider.api_key == "k1"
        assert provider.model == "gpt-4o-mini"
        assert provider.provider_name == "openai"

    def test_create_claude(self) -> None:
        provider = AIProviderFactory.create(
            "claude", api_key="k2", model="claude-3-5-sonnet"
        )

        assert isinstance(provider, ClaudeProvider)
        assert provider.api_key == "k2"
        assert provider.model == "claude-3-5-sonnet"
        assert provider.provider_name == "claude"

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown AI provider"):
            AIProviderFactory.create("unknown", api_key="k", model="m")


class TestRetryLogic:
    @pytest.mark.asyncio
    async def test_retries_on_failure(self) -> None:
        provider = _DummyProvider(responses=["recovered"], failures=2)

        with patch(
            "server.ai.provider.asyncio.sleep", new_callable=AsyncMock
        ) as sleep_mock:
            result = await provider.generate("prompt", backoff=[0, 0, 0])

        assert result == "recovered"
        assert provider._calls == 3
        assert sleep_mock.await_count == 2

    @pytest.mark.asyncio
    async def test_fallback_on_exhausted_retries(self) -> None:
        fallback = _DummyProvider(responses=["fallback answer"], failures=0)
        primary = _DummyProvider(failures=3, fallback=fallback)

        with patch("server.ai.provider.asyncio.sleep", new_callable=AsyncMock):
            result = await primary.generate_with_fallback("prompt", backoff=[0, 0, 0])

        assert result == "fallback answer"
        assert primary._calls == 3
        assert fallback._calls == 1


class TestOpenAIProvider:
    @pytest.mark.asyncio
    async def test_call_api_returns_first_choice_content(self) -> None:
        provider = OpenAIProvider(api_key="key", model="gpt-4o-mini")

        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content="openai result"))]
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        openai_module = SimpleNamespace(AsyncOpenAI=Mock(return_value=mock_client))
        with patch(
            "server.ai.provider.importlib.import_module", return_value=openai_module
        ):
            result = await provider.generate(
                "hello", system="system prompt", backoff=[0, 0, 0]
            )

        assert result == "openai result"
        mock_client.chat.completions.create.assert_awaited_once_with(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "hello"},
            ],
        )


class TestClaudeProvider:
    @pytest.mark.asyncio
    async def test_call_api_returns_first_content_text(self) -> None:
        provider = ClaudeProvider(api_key="key", model="claude-3-5-sonnet")

        mock_client = Mock()
        mock_response = Mock()
        mock_response.content = [Mock(text="claude result")]
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        anthropic_module = SimpleNamespace(
            AsyncAnthropic=Mock(return_value=mock_client)
        )
        with patch(
            "server.ai.provider.importlib.import_module", return_value=anthropic_module
        ):
            result = await provider.generate(
                "hello", system="system prompt", backoff=[0, 0, 0]
            )

        assert result == "claude result"
        mock_client.messages.create.assert_awaited_once_with(
            model="claude-3-5-sonnet",
            max_tokens=4096,
            system="system prompt",
            messages=[{"role": "user", "content": "hello"}],
        )
