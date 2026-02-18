from __future__ import annotations

import logging
from typing import Any

import httpx

from shared.utils import setup_logging

_OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"


class OpenAIProvider:
    """OpenAI Chat Completions — httpx REST 호출 (SDK 불필요)."""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        self._api_key: str = api_key
        self.model: str = model
        self._logger: logging.Logger = setup_logging("openai_provider")

    async def chat(
        self,
        user_message: str,
        system_prompt: str = "",
        max_tokens: int = 2000,
    ) -> str:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_message})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        self._logger.info(
            "openai request: model=%s, len=%d", self.model, len(user_message)
        )
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(_OPENAI_API_URL, json=payload, headers=headers)

        if resp.status_code != 200:
            error_body = resp.text[:500]
            self._logger.error("openai error %d: %s", resp.status_code, error_body)
            return f"[OpenAI Error] HTTP {resp.status_code}: {error_body}"

        data: dict[str, Any] = resp.json()
        choices = data.get("choices", [])
        if not choices:
            return "[OpenAI Error] No choices in response"

        content: str = choices[0].get("message", {}).get("content", "")
        self._logger.info("openai response: len=%d", len(content))
        return content
