from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
import importlib
import logging

from shared.utils import setup_logging


class BaseAIProvider(ABC):
    def __init__(
        self,
        api_key: str,
        model: str,
        fallback: BaseAIProvider | None = None,
    ) -> None:
        self.api_key: str = api_key
        self.model: str = model
        self.fallback: BaseAIProvider | None = fallback
        self.provider_name: str = "base"
        self._logger: logging.Logger = setup_logging(self.__class__.__name__)

    @abstractmethod
    async def _call_api(self, prompt: str, system: str = "") -> str:
        raise NotImplementedError

    async def generate(
        self,
        prompt: str,
        system: str = "",
        max_retries: int = 3,
        backoff: list[int] | None = None,
    ) -> str:
        delays = backoff or [5, 15, 30]
        last_error: Exception | None = None

        for attempt in range(max_retries):
            try:
                return await self._call_api(prompt=prompt, system=system)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt == max_retries - 1:
                    break

                delay_index = min(attempt, len(delays) - 1)
                wait_seconds = delays[delay_index]
                self._logger.warning(
                    "provider request failed; retrying: provider=%s attempt=%s delay=%s",
                    self.provider_name,
                    attempt + 1,
                    wait_seconds,
                )
                await asyncio.sleep(wait_seconds)

        if last_error is None:
            raise RuntimeError("AI provider request failed without exception")
        raise last_error

    async def generate_with_fallback(
        self,
        prompt: str,
        system: str = "",
        max_retries: int = 3,
        backoff: list[int] | None = None,
    ) -> str:
        try:
            return await self.generate(
                prompt=prompt,
                system=system,
                max_retries=max_retries,
                backoff=backoff,
            )
        except Exception:  # noqa: BLE001
            if self.fallback is None:
                raise
            self._logger.warning(
                "provider failed; using fallback: provider=%s fallback=%s",
                self.provider_name,
                self.fallback.provider_name,
            )
            return await self.fallback.generate(
                prompt=prompt,
                system=system,
                max_retries=max_retries,
                backoff=backoff,
            )


class OpenAIProvider(BaseAIProvider):
    def __init__(
        self,
        api_key: str,
        model: str,
        fallback: BaseAIProvider | None = None,
    ) -> None:
        super().__init__(api_key=api_key, model=model, fallback=fallback)
        self.provider_name: str = "openai"

    async def _call_api(self, prompt: str, system: str = "") -> str:
        try:
            openai_module = importlib.import_module("openai")
        except ImportError as exc:
            raise RuntimeError("openai package is not installed") from exc

        client = openai_module.AsyncOpenAI(api_key=self.api_key)
        response = await client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        content = response.choices[0].message.content
        return content if content is not None else ""


class ClaudeProvider(BaseAIProvider):
    def __init__(
        self,
        api_key: str,
        model: str,
        fallback: BaseAIProvider | None = None,
    ) -> None:
        super().__init__(api_key=api_key, model=model, fallback=fallback)
        self.provider_name: str = "claude"

    async def _call_api(self, prompt: str, system: str = "") -> str:
        try:
            anthropic_module = importlib.import_module("anthropic")
        except ImportError as exc:
            raise RuntimeError("anthropic package is not installed") from exc

        client = anthropic_module.AsyncAnthropic(api_key=self.api_key)
        response = await client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        first_block = response.content[0]
        text = getattr(first_block, "text", None)
        if isinstance(text, str):
            return text
        raise RuntimeError("Claude response did not include text content")


class AIProviderFactory:
    @staticmethod
    def create(
        name: str,
        api_key: str,
        model: str,
        fallback: BaseAIProvider | None = None,
    ) -> BaseAIProvider:
        normalized = name.strip().lower()
        if normalized == "openai":
            return OpenAIProvider(api_key=api_key, model=model, fallback=fallback)
        if normalized == "claude":
            return ClaudeProvider(api_key=api_key, model=model, fallback=fallback)
        raise ValueError(f"Unknown AI provider: {name}")
