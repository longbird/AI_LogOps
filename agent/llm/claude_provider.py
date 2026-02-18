from __future__ import annotations

import logging
from typing import Any

import httpx

from shared.utils import setup_logging

_ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"


class ClaudeProvider:
    """Anthropic Claude Messages — httpx REST 호출 (SDK 불필요).

    api_key 는 다음 두 형태 모두 지원:
      - API Key (sk-ant-...): x-api-key 헤더로 전송
      - OAuth Token (eyJ... 등): Authorization: Bearer 헤더로 전송
    """

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514") -> None:
        self._api_key: str = api_key
        self.model: str = model
        self._logger: logging.Logger = setup_logging("claude_provider")

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "anthropic-version": _ANTHROPIC_VERSION,
        }
        # x-api-key 가 Anthropic API 표준 인증 방식
        headers["x-api-key"] = self._api_key
        return headers

    async def chat(
        self,
        user_message: str,
        system_prompt: str = "",
        max_tokens: int = 2000,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system_prompt or "You are a helpful assistant.",
            "messages": [{"role": "user", "content": user_message}],
        }

        self._logger.info(
            "claude request: model=%s, len=%d", self.model, len(user_message)
        )
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                _ANTHROPIC_API_URL, json=payload, headers=self._build_headers()
            )

        if resp.status_code != 200:
            error_body = resp.text[:500]
            self._logger.error("claude error %d: %s", resp.status_code, error_body)
            return f"[Claude Error] HTTP {resp.status_code}: {error_body}"

        data: dict[str, Any] = resp.json()
        content_blocks = data.get("content", [])
        if not content_blocks:
            return "[Claude Error] No content in response"

        content: str = content_blocks[0].get("text", "")
        self._logger.info("claude response: len=%d", len(content))
        return content
