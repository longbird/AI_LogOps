"""Claude Code CLI spawn provider.

claude CLI 바이너리를 child process로 실행하여 LLM 호출을 수행한다.
API 키 없이 claude login 인증을 사용한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from dataclasses import dataclass, field

from shared.utils import setup_logging


@dataclass(slots=True)
class CLIResult:
    """claude CLI 실행 결과."""

    result: str = ""
    session_id: str = ""
    duration_ms: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    error: str = ""


class ClaudeCLIProvider:
    """Claude Code CLI를 subprocess로 spawn하여 LLM 호출.

    기존 OpenAIProvider / ClaudeProvider와 동일한 chat() 인터페이스를 제공한다.
    API 키 대신 claude CLI의 로컬 인증(claude login)을 사용한다.
    """

    def __init__(
        self,
        model: str = "sonnet",
        timeout: float = 120.0,
        max_turns: int | None = None,
    ) -> None:
        self.model: str = model
        self.timeout: float = timeout
        self.max_turns: int | None = max_turns
        self._logger: logging.Logger = setup_logging("claude_cli_provider")

        # chat_id(int) -> claude session_id(str) 매핑
        self._sessions: dict[int, str] = {}

        # CLI 상태
        self._cli_path: str | None = None
        self._cli_version: str | None = None
        self._is_authenticated: bool = False

    # ══════════════════════════════════════════════════
    # 상태 확인
    # ══════════════════════════════════════════════════

    @property
    def is_available(self) -> bool:
        """CLI가 설치되어 있고 인증된 상태인지."""
        return self._cli_path is not None and self._is_authenticated

    @property
    def cli_version(self) -> str | None:
        return self._cli_version

    @property
    def sessions(self) -> dict[int, str]:
        return self._sessions

    async def detect(self) -> bool:
        """claude CLI 설치 + 인증 상태를 자동 감지한다.

        Returns:
            True: CLI 설치됨 + 인증됨, provider 사용 가능
            False: CLI 미설치 또는 인증 안 됨
        """
        # 1) CLI 바이너리 탐색
        cli_path = shutil.which("claude")
        if cli_path is None:
            self._logger.info("claude CLI not found in PATH")
            self._cli_path = None
            self._is_authenticated = False
            return False

        self._cli_path = cli_path
        self._logger.info("claude CLI found: %s", cli_path)

        # 2) 버전 확인
        try:
            proc = await asyncio.create_subprocess_exec(
                cli_path,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            version_text = stdout.decode("utf-8", errors="replace").strip()
            self._cli_version = version_text
            self._logger.info("claude CLI version: %s", version_text)
        except Exception:
            self._logger.warning("failed to get claude CLI version")
            self._cli_version = None

        # 3) 인증 확인 — 간단한 쿼리 실행
        test_result = await self._spawn(
            prompt="Reply with only the word OK",
            max_turns=1,
            timeout=30.0,
        )
        if test_result.error:
            self._logger.warning("claude CLI auth check failed: %s", test_result.error)
            self._is_authenticated = False
            return False

        self._is_authenticated = True
        self._logger.info("claude CLI authenticated and ready")
        return True

    # ══════════════════════════════════════════════════
    # chat() — 기존 provider와 동일한 인터페이스
    # ══════════════════════════════════════════════════

    async def chat(
        self,
        user_message: str,
        system_prompt: str = "",
        max_tokens: int = 2000,
        chat_id: int | None = None,
    ) -> str:
        """LLMRouter에서 호출하는 메인 인터페이스.

        Args:
            user_message: 사용자 프롬프트
            system_prompt: 시스템 프롬프트 (--append-system-prompt로 전달)
            max_tokens: 미사용 (CLI에서 자체 관리)
            chat_id: Telegram chat_id. 세션 유지에 사용

        Returns:
            응답 텍스트. 에러 시 [Claude CLI Error] 접두사 메시지
        """
        del max_tokens  # CLI가 자체 관리

        if not self.is_available:
            return "[Claude CLI Error] CLI가 설치되지 않았거나 인증되지 않았습니다."

        # 세션 복원
        resume_session = self._sessions.get(chat_id) if chat_id is not None else None

        result = await self._spawn(
            prompt=user_message,
            system_prompt=system_prompt or None,
            resume_session=resume_session,
        )

        if result.error:
            self._logger.error("claude CLI error: %s", result.error)
            return f"[Claude CLI Error] {result.error}"

        # 세션 ID 저장
        if chat_id is not None and result.session_id:
            self._sessions[chat_id] = result.session_id

        self._logger.info(
            "claude-cli response: len=%d, tokens=%d/%d, cost=$%.4f, session=%s",
            len(result.result),
            result.input_tokens,
            result.output_tokens,
            result.cost_usd,
            result.session_id[:8] if result.session_id else "N/A",
        )

        return result.result

    # ══════════════════════════════════════════════════
    # 세션 관리
    # ══════════════════════════════════════════════════

    def reset_session(self, chat_id: int) -> bool:
        """특정 chat_id의 세션을 초기화한다.

        Returns:
            True: 세션이 존재하여 초기화됨
            False: 세션이 없었음
        """
        removed = self._sessions.pop(chat_id, None)
        if removed is not None:
            self._logger.info(
                "session reset: chat_id=%d, old_session=%s",
                chat_id,
                removed[:8],
            )
            return True
        return False

    def reset_all_sessions(self) -> int:
        """모든 세션을 초기화한다. 초기화된 세션 수를 반환."""
        count = len(self._sessions)
        self._sessions.clear()
        self._logger.info("all sessions reset: count=%d", count)
        return count

    def get_session_info(self, chat_id: int) -> str | None:
        """chat_id의 세션 ID를 반환. 없으면 None."""
        return self._sessions.get(chat_id)

    # ══════════════════════════════════════════════════
    # 인증 상태 조회
    # ══════════════════════════════════════════════════

    async def get_auth_status(self) -> dict[str, object]:
        """인증 및 CLI 상태 정보를 반환한다."""
        return {
            "installed": self._cli_path is not None,
            "cli_path": self._cli_path or "N/A",
            "version": self._cli_version or "N/A",
            "authenticated": self._is_authenticated,
            "model": self.model,
            "active_sessions": len(self._sessions),
        }

    # ══════════════════════════════════════════════════
    # 내부: CLI 프로세스 spawn
    # ══════════════════════════════════════════════════

    async def _spawn(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        resume_session: str | None = None,
        max_turns: int | None = None,
        timeout: float | None = None,
    ) -> CLIResult:
        """claude CLI를 subprocess로 실행하고 JSON 결과를 파싱한다."""

        cli = self._cli_path or "claude"
        args: list[str] = [
            cli,
            "-p",
            "--output-format",
            "json",
            "--model",
            self.model,
        ]

        if system_prompt:
            args.extend(["--append-system-prompt", system_prompt])

        if resume_session:
            args.extend(["--resume", resume_session])

        effective_max_turns = max_turns or self.max_turns
        if effective_max_turns is not None:
            args.extend(["--max-turns", str(effective_max_turns)])

        args.append(prompt)

        effective_timeout = timeout or self.timeout
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}

        self._logger.debug(
            "spawning: model=%s, resume=%s, prompt_len=%d",
            self.model,
            resume_session[:8] if resume_session else "new",
            len(prompt),
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError:
            return CLIResult(error="claude CLI를 찾을 수 없습니다")
        except OSError as exc:
            return CLIResult(error=f"프로세스 생성 실패: {exc}")

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=effective_timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return CLIResult(error=f"타임아웃 ({effective_timeout}초)")

        if proc.returncode != 0:
            stderr_text = stderr.decode("utf-8", errors="replace")[:500]
            return CLIResult(
                error=f"프로세스 종료 코드 {proc.returncode}: {stderr_text}"
            )

        raw = stdout.decode("utf-8", errors="replace").strip()
        if not raw:
            return CLIResult(error="빈 응답")

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return CLIResult(error=f"JSON 파싱 실패: {raw[:200]}")

        # 에러 응답 확인
        if data.get("is_error"):
            return CLIResult(
                error=data.get("result", "알 수 없는 오류"),
                session_id=data.get("session_id", ""),
            )

        usage = data.get("usage", {})
        return CLIResult(
            result=data.get("result", ""),
            session_id=data.get("session_id", ""),
            duration_ms=data.get("duration_ms", 0),
            cost_usd=data.get("total_cost_usd", 0.0),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )
