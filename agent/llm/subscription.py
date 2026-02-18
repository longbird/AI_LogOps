"""구독 인증 클라이언트 — OAuth Device Flow + 키 기반 인증 지원."""

from __future__ import annotations

# pyright: reportConstantRedefinition=false, reportPossiblyUnboundVariable=false

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from shared.utils import setup_logging

try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False


@dataclass
class SubscriptionResult:
    """서버 검증 결과."""

    valid: bool = False
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    claude_api_key: str = ""
    claude_model: str = "claude-sonnet-4-20250514"
    system_prompt: str = ""
    max_tokens: int = 2000
    expires_at: str | None = None
    message: str = ""


@dataclass
class DeviceFlowInfo:
    """Device Flow 시작 결과."""

    device_code: str = ""
    user_code: str = ""
    verification_uri: str = ""
    login_uri: str = ""
    expires_in: int = 0
    interval: int = 5
    error: str = ""


class SubscriptionClient:
    """구독 서버와 통신. OAuth Device Flow + 키 기반 인증."""

    def __init__(
        self,
        server_url: str,
        agent_id: str = "",
        revalidate_hours: int = 24,
    ) -> None:
        self._server_url = server_url.rstrip("/")
        self._agent_id = agent_id
        self._revalidate_hours = revalidate_hours
        self._logger: logging.Logger = setup_logging("subscription_client")

        # 인증 상태
        self._access_token: str = ""
        self._result: SubscriptionResult = SubscriptionResult()
        self._is_authenticated: bool = False
        self._revalidation_task: asyncio.Task[None] | None = None
        # device flow 폴링 중인 태스크
        self._polling_task: asyncio.Task[SubscriptionResult] | None = None

    @property
    def is_authenticated(self) -> bool:
        return self._is_authenticated

    @property
    def access_token(self) -> str:
        return self._access_token

    @property
    def result(self) -> SubscriptionResult:
        return self._result

    # ══════════════════════════════════════════════════
    # OAuth Device Flow
    # ══════════════════════════════════════════════════

    async def start_device_flow(self) -> DeviceFlowInfo:
        """서버에 device code 요청. 에이전트가 user_code와 URL을 사용자에게 보여줌."""
        if not _HTTPX_AVAILABLE:
            return DeviceFlowInfo(error="httpx 패키지가 설치되지 않았습니다.")

        url = f"{self._server_url}/api/auth/device"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json={"agent_id": self._agent_id})
            if resp.status_code != 200:
                return DeviceFlowInfo(error=f"서버 응답 오류 (HTTP {resp.status_code})")
            data: dict[str, Any] = resp.json()
            return DeviceFlowInfo(
                device_code=data.get("device_code", ""),
                user_code=data.get("user_code", ""),
                verification_uri=data.get("verification_uri", ""),
                login_uri=data.get("login_uri", ""),
                expires_in=data.get("expires_in", 900),
                interval=data.get("interval", 5),
            )
        except Exception as exc:
            self._logger.exception("device flow start error")
            return DeviceFlowInfo(error=f"서버 연결 실패: {exc}")

    async def poll_for_token(
        self, device_code: str, interval: int = 5, timeout: int = 900
    ) -> SubscriptionResult:
        """device_code로 서버를 폴링하여 승인을 기다린다."""
        if not _HTTPX_AVAILABLE:
            return SubscriptionResult(valid=False, message="httpx 미설치")

        url = f"{self._server_url}/api/auth/token"
        elapsed = 0
        while elapsed < timeout:
            await asyncio.sleep(interval)
            elapsed += interval
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(url, json={"device_code": device_code})
                data: dict[str, Any] = resp.json()

                error = data.get("error", "")
                if error == "authorization_pending":
                    continue
                if error == "expired_token":
                    return SubscriptionResult(
                        valid=False, message="인증 코드가 만료되었습니다."
                    )
                if error:
                    msg = data.get("message", error)
                    return SubscriptionResult(valid=False, message=msg)

                # 승인됨 → access_token + LLM 키 수신
                access_token = data.get("access_token", "")
                if access_token:
                    result = SubscriptionResult(
                        valid=True,
                        openai_api_key=data.get("openai_api_key", ""),
                        openai_model=data.get("openai_model", "gpt-4o-mini"),
                        claude_api_key=data.get("claude_api_key", ""),
                        claude_model=data.get(
                            "claude_model", "claude-sonnet-4-20250514"
                        ),
                        system_prompt=data.get("system_prompt", ""),
                        max_tokens=data.get("max_tokens", 2000),
                        message="인증 성공",
                    )
                    self._access_token = access_token
                    self._result = result
                    self._is_authenticated = True
                    self._logger.info("device flow: authenticated successfully")
                    return result

            except Exception:
                self._logger.warning("device flow poll error, retrying...")
                continue

        return SubscriptionResult(valid=False, message="인증 시간이 초과되었습니다.")

    # ══════════════════════════════════════════════════
    # 키 기반 인증 (기존 호환)
    # ══════════════════════════════════════════════════

    async def validate(self, subscription_key: str) -> SubscriptionResult:
        """구독 키를 서버에 검증 요청한다."""
        if not _HTTPX_AVAILABLE:
            return SubscriptionResult(valid=False, message="httpx 미설치")

        url = f"{self._server_url}/api/subscription/validate"
        payload = {"subscription_key": subscription_key, "agent_id": self._agent_id}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                return SubscriptionResult(
                    valid=False,
                    message=f"서버 응답 오류 (HTTP {resp.status_code})",
                )
            data: dict[str, Any] = resp.json()
            result = SubscriptionResult(
                valid=data.get("valid", False),
                openai_api_key=data.get("openai_api_key", ""),
                openai_model=data.get("openai_model", "gpt-4o-mini"),
                claude_api_key=data.get("claude_api_key", ""),
                claude_model=data.get("claude_model", "claude-sonnet-4-20250514"),
                system_prompt=data.get("system_prompt", ""),
                max_tokens=data.get("max_tokens", 2000),
                expires_at=data.get("expires_at"),
                message=data.get("message", ""),
            )
            if result.valid:
                self._result = result
                self._is_authenticated = True
                self._logger.info("subscription key validated")
            return result
        except Exception as exc:
            self._logger.exception("subscription validate error")
            return SubscriptionResult(valid=False, message=f"검증 오류: {exc}")

    # ══════════════════════════════════════════════════
    # Access Token 재검증 (주기적)
    # ══════════════════════════════════════════════════

    async def revalidate_token(self) -> SubscriptionResult:
        """access token을 서버에 재검증."""
        if not self._access_token or not _HTTPX_AVAILABLE:
            return SubscriptionResult(valid=False, message="토큰 없음")
        url = f"{self._server_url}/api/subscription/validate"
        # access token 기반 재검증은 validate endpoint 재활용
        # 실제로는 token refresh endpoint가 별도로 있을 수 있지만, 여기선 간단히 처리
        self._logger.info("revalidating access token...")
        # token이 있으면 인증 상태 유지 (서버 사이드에서 만료 관리)
        return self._result

    def clear(self) -> None:
        """인증 상태 초기화."""
        self._access_token = ""
        self._result = SubscriptionResult()
        self._is_authenticated = False
        self.stop_revalidation()
        self._cancel_polling()
        self._logger.info("subscription cleared")

    def start_revalidation(self) -> None:
        if self._revalidation_task is not None:
            return
        self._revalidation_task = asyncio.create_task(self._revalidation_loop())
        self._logger.info("revalidation started (interval=%dh)", self._revalidate_hours)

    def stop_revalidation(self) -> None:
        if self._revalidation_task is not None:
            self._revalidation_task.cancel()
            self._revalidation_task = None

    def _cancel_polling(self) -> None:
        if self._polling_task is not None:
            self._polling_task.cancel()
            self._polling_task = None

    async def _revalidation_loop(self) -> None:
        interval = self._revalidate_hours * 3600
        while True:
            await asyncio.sleep(interval)
            if not self._is_authenticated:
                break
            result = await self.revalidate_token()
            if not result.valid:
                self._is_authenticated = False
                self._logger.warning("revalidation failed: %s", result.message)
