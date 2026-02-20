from __future__ import annotations

# pyright: reportAttributeAccessIssue=false

import logging

from shared.utils import setup_logging


class LLMRouter:
    """OpenAI / Claude / OpenRouter / Claude-CLI 라우터. provider 전환 지원. apikey + subscription + CLI 이중 모드."""

    def __init__(
        self,
        default_provider: str = "openai",
        openai_api_key: str = "",
        openai_model: str = "gpt-4o-mini",
        claude_api_key: str = "",
        claude_model: str = "claude-sonnet-4-20250514",
        openrouter_api_key: str = "",
        openrouter_model: str = "openai/gpt-4o-mini",
        claude_cli_model: str = "sonnet",
        claude_cli_auto_detect: bool = True,
        system_prompt: str = "",
        max_tokens: int = 2000,
    ) -> None:
        self._providers: dict[str, object] = {}
        self.current_provider: str = default_provider
        self.system_prompt: str = system_prompt
        self.max_tokens: int = max_tokens
        self._logger: logging.Logger = setup_logging("llm_router")

        # Claude CLI provider 참조 (세션 관리용)
        self._claude_cli_provider: object | None = None
        self._claude_cli_model: str = claude_cli_model
        self._claude_cli_auto_detect: bool = claude_cli_auto_detect

        if openai_api_key:
            self._load_openai(openai_api_key, openai_model)

        if claude_api_key:
            self._load_claude(claude_api_key, claude_model)

        if openrouter_api_key:
            self._load_openrouter(openrouter_api_key, openrouter_model)

        if self.current_provider not in self._providers and self._providers:
            self.current_provider = next(iter(self._providers))
            self._logger.info(
                "default provider unavailable, switched to %s", self.current_provider
            )

    # ── provider 로드 ──────────────────────────────────

    def _load_openai(self, api_key: str, model: str) -> bool:
        try:
            from agent.llm.openai_provider import OpenAIProvider

            self._providers["openai"] = OpenAIProvider(api_key=api_key, model=model)
            self._logger.info("OpenAI provider loaded: %s", model)
            return True
        except Exception:
            self._logger.warning("OpenAI provider unavailable", exc_info=True)
            return False

    def _load_claude(self, api_key: str, model: str) -> bool:
        try:
            from agent.llm.claude_provider import ClaudeProvider

            self._providers["claude"] = ClaudeProvider(api_key=api_key, model=model)
            self._logger.info("Claude provider loaded: %s", model)
            return True
        except Exception:
            self._logger.warning("Claude provider unavailable", exc_info=True)
            return False

    def _load_openrouter(self, api_key: str, model: str) -> bool:
        try:
            from agent.llm.openrouter_provider import OpenRouterProvider

            self._providers["openrouter"] = OpenRouterProvider(
                api_key=api_key, model=model
            )
            self._logger.info("OpenRouter provider loaded: %s", model)
            return True
        except Exception:
            self._logger.warning("OpenRouter provider unavailable", exc_info=True)
            return False

    def _load_claude_cli(self, model: str) -> bool:
        """Claude CLI provider를 로드한다. API 키 불필요."""
        try:
            from agent.llm.claude_cli_provider import ClaudeCLIProvider

            provider = ClaudeCLIProvider(model=model)
            self._providers["claude-cli"] = provider
            self._claude_cli_provider = provider
            self._logger.info("Claude CLI provider loaded: %s", model)
            return True
        except Exception:
            self._logger.warning("Claude CLI provider unavailable", exc_info=True)
            return False

    async def auto_detect_claude_cli(self) -> bool:
        """Claude CLI 설치/인증 상태를 감지하여 자동으로 provider에 추가한다.

        Returns:
            True: CLI 감지 + 인증 확인, provider 추가됨
            False: CLI 미설치 또는 인증 안 됨
        """
        if not self._claude_cli_auto_detect:
            return False

        if "claude-cli" in self._providers:
            self._logger.debug("claude-cli provider already loaded, skipping detect")
            return True

        if not self._load_claude_cli(self._claude_cli_model):
            return False

        from agent.llm.claude_cli_provider import ClaudeCLIProvider

        provider = self._providers.get("claude-cli")
        if not isinstance(provider, ClaudeCLIProvider):
            return False

        detected = await provider.detect()
        if not detected:
            self._providers.pop("claude-cli", None)
            self._claude_cli_provider = None
            self._logger.info("claude CLI not available, provider removed")
            return False

        # 다른 provider가 없으면 claude-cli를 기본으로 설정
        if not any(k != "claude-cli" for k in self._providers):
            self.current_provider = "claude-cli"
            self._logger.info(
                "claude-cli set as default provider (no others available)"
            )

        return True

    @property
    def claude_cli_provider(self) -> object | None:
        """Claude CLI provider 인스턴스 (세션 관리 등에 사용)."""
        return self._claude_cli_provider

    # ── 구독 인증으로 provider 재로드 ──────────────────

    def apply_subscription(
        self,
        *,
        openai_api_key: str = "",
        openai_model: str = "gpt-4o-mini",
        claude_api_key: str = "",
        claude_model: str = "claude-sonnet-4-20250514",
        system_prompt: str = "",
        max_tokens: int = 0,
    ) -> list[str]:
        """구독에서 받은 키로 provider를 재로드한다. 로드된 provider 목록을 반환."""
        loaded: list[str] = []

        if openai_api_key:
            self._providers.pop("openai", None)
            if self._load_openai(openai_api_key, openai_model):
                loaded.append("openai")

        if claude_api_key:
            self._providers.pop("claude", None)
            if self._load_claude(claude_api_key, claude_model):
                loaded.append("claude")

        if system_prompt:
            self.system_prompt = system_prompt
        if max_tokens > 0:
            self.max_tokens = max_tokens

        # current_provider 재설정
        if self.current_provider not in self._providers and self._providers:
            self.current_provider = next(iter(self._providers))

        self._logger.info(
            "subscription applied: providers=%s, current=%s",
            loaded,
            self.current_provider,
        )
        return loaded

    def clear_subscription(self) -> None:
        """구독으로 로드된 provider를 제거한다."""
        self._providers.clear()
        self._logger.info("all providers cleared (subscription revoked)")

    @property
    def available_providers(self) -> list[str]:
        return list(self._providers.keys())

    @property
    def is_available(self) -> bool:
        return len(self._providers) > 0

    def switch_provider(self, name: str) -> bool:
        """provider 전환. 성공 시 True."""
        if name in self._providers:
            self.current_provider = name
            self._logger.info("switched to provider: %s", name)
            return True
        return False

    async def chat(
        self,
        user_message: str,
        system_prompt: str | None = None,
        chat_id: int | None = None,
    ) -> str:
        """현재 provider로 LLM 호출.

        Args:
            user_message: 사용자 메시지
            system_prompt: 시스템 프롬프트 (None이면 기본값 사용)
            chat_id: Telegram chat_id (claude-cli 세션 관리용)
        """
        provider = self._providers.get(self.current_provider)
        if provider is None:
            return "[LLM] 사용 가능한 LLM provider가 없습니다."

        prompt = system_prompt if system_prompt is not None else self.system_prompt
        try:
            if self.current_provider == "claude-cli" and chat_id is not None:
                # Claude CLI provider는 chat_id를 추가로 받아 세션 관리
                result: str = await provider.chat(  # type: ignore[union-attr]
                    user_message=user_message,
                    system_prompt=prompt,
                    max_tokens=self.max_tokens,
                    chat_id=chat_id,
                )
            else:
                # OpenAIProvider, ClaudeProvider 등 기존 provider
                result = await provider.chat(  # type: ignore[union-attr]
                    user_message=user_message,
                    system_prompt=prompt,
                    max_tokens=self.max_tokens,
                )
            return result
        except Exception as exc:
            self._logger.exception("LLM call failed (%s)", self.current_provider)
            return f"[LLM Error] {self.current_provider}: {exc}"
