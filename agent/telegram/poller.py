from __future__ import annotations

# pyright: reportMissingImports=false, reportArgumentType=false, reportAttributeAccessIssue=false, reportUnusedCallResult=false

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol

from telegram.ext import Application

from shared.utils import setup_logging

if TYPE_CHECKING:
    from agent.core.log_watcher import LogWatcher
    from agent.core.system_monitor import SystemMonitor
    from agent.llm.router import LLMRouter
    from agent.llm.subscription import SubscriptionClient
    from agent.updater.process_deploy import ProcessDeployer
    from agent.updater.self_update import SelfUpdater


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

    def __init__(
        self,
        bot_token: str,
        admin_chat_id: int,
        server_bot_token: str = "",
        server_chat_id: int = 0,
    ):
        """admin_chat_id: 단일 관리자 ID."""
        self.bot_token: str = bot_token
        self.admin_chat_id: int = admin_chat_id
        self.server_bot_token: str = server_bot_token
        self.server_chat_id: int = server_chat_id
        self._server_bot: _BotLike | None = None
        self.application: _ApplicationLike | None = None
        self.on_connect: Callable[[str, int], Awaitable[None]] | None = None
        self.on_disconnect: Callable[[], Awaitable[None]] | None = None
        self._state: str = "STANDBY"
        self._logger: logging.Logger = setup_logging(self.__class__.__name__)
        self.system_monitor: SystemMonitor | None = None
        self.updater: SelfUpdater | None = None
        self.process_deployer: ProcessDeployer | None = None
        self.process_args: list[str] | None = None
        self.log_watcher: LogWatcher | None = None
        self.llm_router: LLMRouter | None = None
        self.subscription_client: SubscriptionClient | None = None
        self.rec_watcher: object | None = None  # RecordingWatcher (optional)

    async def start(self) -> None:
        """Telegram 봇 초기화 (send-only).

        명령 수신(폴링)은 하지 않고 알림 전송만 수행한다.
        다중 에이전트 환경에서 동일 봇 토큰으로 폴링 충돌을 방지하기 위함.
        명령 라우팅은 서버 봇 → TCP 경유로 처리된다.

        봇 토큰이 유효하지 않으면 경고만 남기고 에이전트는 계속 실행된다.
        Telegram 없이도 TCP 서버 접속, 로그 감시 등 핵심 기능은 정상 동작한다.
        """
        if self.application is not None:
            return

        # 토큰 유효성 사전 검사 — 플레이스홀더나 빈 값이면 즉시 반환
        _placeholders = {"", "YOUR_BOT_TOKEN", "YOUR_AGENT_BOT_TOKEN"}
        if not self.bot_token or self.bot_token in _placeholders:
            self._logger.warning(
                "telegram bot token not configured ('%s'), "
                "telegram disabled — agent will run without Telegram",
                self.bot_token if self.bot_token else "(empty)",
            )
            return

        self._logger.info(">>> telegram: building Application (send-only)...")
        application = Application.builder().token(self.bot_token).build()

        # send-only: 폴링/커맨드 핸들러 등록하지 않음
        self._logger.info(">>> telegram: application.initialize()...")
        try:
            await application.initialize()
        except Exception as exc:
            self._logger.warning(
                "telegram initialization failed: %s — "
                "telegram disabled, agent will run without Telegram",
                exc,
            )
            return

        self._logger.info(">>> telegram: initialized (send-only, no polling)")
        self.application = application

        # 서버봇 초기화 (결과 전송용)
        if self.server_bot_token and self.server_chat_id:
            try:
                from telegram import Bot

                self._server_bot = Bot(token=self.server_bot_token)
                self._logger.info("서버봇 초기화 완료 (결과 전송용)")
            except Exception:
                self._logger.exception("서버봇 초기화 실패")

    async def stop(self) -> None:
        """Telegram 봇 종료."""
        application = self.application
        if application is None:
            return

        _ = await application.shutdown()
        self.application = None

    async def send_message(self, text: str) -> None:
        """관리자에게 메시지 전송. Telegram 미설정 시 무시."""
        application = self.application
        if application is None:
            return

        _ = await application.bot.send_message(chat_id=self.admin_chat_id, text=text)

    async def send_to_server(self, text: str) -> None:
        """서버봇을 통해 서버에 실행 결과 전송."""
        if self._server_bot is None:
            self._logger.debug("서버봇 미설정, 결과 전송 생략")
            return

        try:
            _ = await self._server_bot.send_message(
                chat_id=self.server_chat_id, text=text
            )
            self._logger.info("서버에 결과 전송 완료: %s", text[:80])
        except Exception:
            self._logger.exception("서버 결과 전송 실패")

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
        await self.send_to_server(f"[STATUS] {status_text}")

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
        await self.send_to_server(f"[CONNECT] {ip}:{port}")

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
        await self.send_to_server("[DISCONNECT] 연결 해제됨")
        self._logger.info("disconnect command handled")

    async def _cmd_last(self, update: _UpdateLike, context: _ContextLike) -> None:
        """감시 로그의 마지막 N줄을 전송한다. Usage: /last [N]"""
        if not self._is_admin(update):
            return
        message = update.effective_message
        if message is None:
            return

        if self.log_watcher is None:
            _ = await message.reply_text("로그 감시가 설정되지 않았습니다.")
            return

        args = context.args or []
        n = 10
        if args:
            try:
                n = max(1, min(int(args[0]), 100))
            except ValueError:
                _ = await message.reply_text("Usage: /last [N]  (N: 1~100, 기본 10)")
                return

        entries = self.log_watcher.get_latest_files_by_dir()
        if not entries:
            _ = await message.reply_text("감시 중인 로그 파일이 없습니다.")
            return

        for dir_path, file_path in entries:
            lines = self.log_watcher.read_last_n_lines(file_path, n)
            from pathlib import Path

            header = f"[{dir_path}] {Path(file_path).name} (last {len(lines)})"
            body = "\n".join(lines) if lines else "(empty)"
            text = f"{header}\n{body}"
            # Telegram 메시지 4096자 제한
            if len(text) > 4000:
                text = text[:4000] + "\n...(truncated)"
            _ = await message.reply_text(text)
            await self.send_to_server(f"[LOG] {text}")

    async def _handle_document(
        self, update: _UpdateLike, context: _ContextLike
    ) -> None:
        """zip/파트 파일 수신. 단일 zip 또는 분할 파트(*.partNN.zip) 지원."""
        del context
        if not self._is_admin(update):
            return
        message = update.effective_message
        if message is None:
            return

        doc = getattr(message, "document", None)
        if doc is None:
            return

        file_name: str = getattr(doc, "file_name", "") or ""
        lower_name = file_name.lower()
        if not (lower_name.endswith(".zip") or lower_name.endswith(".exe")):
            _ = await message.reply_text("zip 또는 exe 파일만 지원합니다.")
            return

        if self.updater is None:
            _ = await message.reply_text("업데이터가 설정되지 않았습니다.")
            return

        _ = await message.reply_text(f"파일 수신 중: {file_name}")
        self._logger.info("receiving file: %s", file_name)

        try:
            tg_file = await doc.get_file()
            data = await tg_file.download_as_bytearray()
        except Exception:
            self._logger.exception("failed to download file from Telegram")
            _ = await message.reply_text("파일 다운로드 실패.")
            return

        raw = bytes(data)

        # 분할 파트 감지: *.partNN.zip (예: AILogOps-Agent.part01of03.zip)
        import re

        match = re.search(r"\.part(\d+)of(\d+)\.zip$", file_name, re.IGNORECASE)
        if match:
            part_num = int(match.group(1))
            total_parts = int(match.group(2))
            status_msg = self.updater.receive_part(part_num, total_parts, raw)
            _ = await message.reply_text(status_msg)

            if self.updater.all_parts_received:
                _ = await message.reply_text("모든 파트 수신 완료. 병합 중...")
                ok = await self.updater.merge_parts()
                if ok:
                    _ = await message.reply_text(self._staged_ready_message(len(raw)))
                else:
                    _ = await message.reply_text("파트 병합 실패. 로그를 확인하세요.")
            return

        # 단일 zip 또는 exe/기타 파일
        if lower_name.endswith(".zip"):
            ok = await self.updater.receive_zip(raw)
        else:
            ok = await self.updater.receive_file(raw, file_name)

        if ok:
            _ = await message.reply_text(self._staged_ready_message(len(raw)))
        else:
            _ = await message.reply_text("파일 처리 실패. 로그를 확인하세요.")

    async def _cmd_update(self, update: _UpdateLike, context: _ContextLike) -> None:
        """업데이트 실행: updater.bat 생성 → 서비스 재시작."""
        del context
        if not self._is_admin(update):
            return
        message = update.effective_message
        if message is None:
            return

        if self.updater is None:
            _ = await message.reply_text("업데이터가 설정되지 않았습니다.")
            return

        if not self.updater.update_dir.exists():
            _ = await message.reply_text(
                "업데이트 파일이 없습니다. zip 파일을 먼저 전송하세요."
            )
            return

        _ = await message.reply_text("업데이트를 시작합니다.\n서비스가 재시작됩니다...")
        await self.send_to_server("[UPDATE] 에이전트 업데이트 시작")
        self._logger.info("executing self-update via /update command")
        self.updater.execute_update()

    async def _cmd_deploy(self, update: _UpdateLike, context: _ContextLike) -> None:
        """대상 프로세스에 스테이징된 파일을 배포한다."""
        del context
        if not self._is_admin(update):
            return
        message = update.effective_message
        if message is None:
            return

        if self.process_deployer is None:
            _ = await message.reply_text(
                "프로세스 배포가 설정되지 않았습니다.\n"
                "config.yaml의 target_process 섹션을 확인하세요."
            )
            return

        if not self.process_deployer.has_staged_files:
            _ = await message.reply_text(
                "스테이징된 파일이 없습니다.\nzip 또는 exe 파일을 먼저 전송하세요."
            )
            return

        summary = self.process_deployer.staged_file_summary()
        process_name = self.process_deployer.process_mgr.process_name
        _ = await message.reply_text(
            f"[{process_name}] 배포를 시작합니다...\n"
            f"파일 목록:\n{summary}\n\n"
            f"(백업 → 종료 → 로그 삭제 → 교체 → 시작 → 검증)"
        )
        self._logger.info("executing process deploy via /deploy command")

        result = await self.process_deployer.execute_deploy(
            process_args=self.process_args,
        )

        if result.success:
            files_text = ", ".join(result.replaced_files[:10])
            if len(result.replaced_files) > 10:
                files_text += f" 외 {len(result.replaced_files) - 10}개"
            _ = await message.reply_text(
                f"배포 성공!\n"
                f"PID: {result.pid}\n"
                f"교체 파일: {files_text}\n"
                f"백업: {result.backup_path or '(신규 배포)'}"
            )
            await self.send_to_server(
                f"[DEPLOY SUCCESS] PID: {result.pid}, 파일: {files_text}"
            )
        else:
            _ = await message.reply_text(f"배포 실패: {result.error}")
            await self.send_to_server(f"[DEPLOY FAIL] {result.error}")

    def _staged_ready_message(self, size: int) -> str:
        """스테이징 완료 후 안내 메시지 생성."""
        lines = [f"파일 수신 완료 ({size:,} bytes)."]
        lines.append("")
        lines.append("/update - 에이전트 업데이트")
        if self.process_deployer is not None:
            name = self.process_deployer.process_mgr.process_name
            lines.append(f"/deploy - 대상 프로세스({name}) 업데이트")
        return "\n".join(lines)

    # ── LLM 관련 핸들러 ─────────────────────────────────

    async def _handle_text(self, update: _UpdateLike, context: _ContextLike) -> None:
        """일반 텍스트 메시지 → LLM 채팅."""
        del context
        if not self._is_admin(update):
            return
        message = update.effective_message
        if message is None:
            return

        text: str = getattr(message, "text", "") or ""
        if not text.strip():
            return

        if self.llm_router is None or not self.llm_router.is_available:
            # 구독 모드인 경우 안내 메시지 변경
            if self.subscription_client is not None:
                _ = await message.reply_text(
                    "LLM이 활성화되지 않았습니다.\n"
                    "/subscribe <KEY> 명령으로 구독 인증하세요."
                )
            else:
                _ = await message.reply_text(
                    "LLM이 설정되지 않았습니다. config.yaml의 llm 섹션을 확인하세요."
                )
            return

        chat = update.effective_chat
        current_chat_id = chat.id if chat is not None else None

        _ = await message.reply_text(
            f"[{self.llm_router.current_provider}] 응답 생성 중..."
        )
        response = await self.llm_router.chat(text, chat_id=current_chat_id)
        await self._send_long_message(message, response)
        await self.send_to_server(f"[LLM] Q: {text[:50]}... A: {response[:200]}...")

    async def _cmd_analyze(self, update: _UpdateLike, context: _ContextLike) -> None:
        """최근 로그를 LLM에 전달하여 분석. Usage: /analyze [N]"""
        if not self._is_admin(update):
            return
        message = update.effective_message
        if message is None:
            return

        if self.llm_router is None or not self.llm_router.is_available:
            _ = await message.reply_text("LLM이 설정되지 않았습니다.")
            return
        if self.log_watcher is None:
            _ = await message.reply_text("로그 감시가 설정되지 않았습니다.")
            return

        args = context.args or []
        n = 50
        if args:
            try:
                n = max(1, min(int(args[0]), 200))
            except ValueError:
                _ = await message.reply_text("Usage: /analyze [N]  (N: 1~200, 기본 50)")
                return

        entries = self.log_watcher.get_latest_files_by_dir()
        if not entries:
            _ = await message.reply_text("감시 중인 로그 파일이 없습니다.")
            return

        # 각 디렉토리의 최신 로그를 수집
        from pathlib import Path

        log_sections: list[str] = []
        for dir_path, file_path in entries:
            lines = self.log_watcher.read_last_n_lines(file_path, n)
            header = f"[{dir_path}] {Path(file_path).name}"
            body = "\n".join(lines) if lines else "(empty)"
            log_sections.append(f"{header}\n{body}")

        log_context = "\n\n".join(log_sections)

        analysis_prompt = (
            "다음은 산업용 소프트웨어의 최근 로그입니다.\n"
            "에러, 경고, 이상 패턴을 분석하고 요약해주세요.\n"
            "문제가 있다면 원인과 조치 방안을 제안하세요.\n\n"
            f"--- 로그 시작 ---\n{log_context}\n--- 로그 끝 ---"
        )

        _ = await message.reply_text(
            f"[{self.llm_router.current_provider}] 로그 {n}줄 분석 중..."
        )
        response = await self.llm_router.chat(analysis_prompt)
        await self._send_long_message(message, response)
        await self.send_to_server(f"[ANALYZE] {response[:500]}...")

    async def _cmd_model(self, update: _UpdateLike, context: _ContextLike) -> None:
        """LLM provider 전환. Usage: /model [openai|claude]"""
        if not self._is_admin(update):
            return
        message = update.effective_message
        if message is None:
            return

        if self.llm_router is None:
            _ = await message.reply_text("LLM이 설정되지 않았습니다.")
            return

        args = context.args or []
        if not args:
            providers = ", ".join(self.llm_router.available_providers)
            _ = await message.reply_text(
                f"현재: {self.llm_router.current_provider}\n사용 가능: {providers}\nUsage: /model <provider>"
            )
            return

        name = args[0].lower()
        if self.llm_router.switch_provider(name):
            _ = await message.reply_text(f"LLM 전환: {name}")
        else:
            providers = ", ".join(self.llm_router.available_providers)
            _ = await message.reply_text(f"'{name}' 사용 불가. 가능: {providers}")

    # ── 구독 인증 핸들러 ────────────────────────────────

    async def _cmd_subscribe(self, update: _UpdateLike, context: _ContextLike) -> None:
        """OAuth Device Flow로 구독 인증. /subscribe 로 시작."""
        if not self._is_admin(update):
            return
        message = update.effective_message
        if message is None:
            return

        if self.subscription_client is None:
            _ = await message.reply_text(
                "구독 기능이 설정되지 않았습니다.\n"
                "config.yaml의 llm.subscription 섹션을 확인하세요."
            )
            return

        import asyncio

        _ = await message.reply_text("인증 링크 생성 중...")

        # 1. Device code 요청
        flow_info = await self.subscription_client.start_device_flow()
        if flow_info.error:
            _ = await message.reply_text(f"인증 시작 실패: {flow_info.error}")
            return

        # 2. 사용자에게 로그인 링크만 표시 (코드 입력 불필요)
        _ = await message.reply_text(
            f"아래 링크에서 로그인하세요.\n\n"
            f"{flow_info.login_uri}\n\n"
            f"({flow_info.expires_in // 60}분 내에 완료해야 합니다)"
        )

        # 3. 백그라운드에서 폴링 시작
        sub_client = self.subscription_client  # 클로저 캡처 (None 아님 확정)
        llm_router = self.llm_router

        async def _poll_and_apply() -> None:
            result = await sub_client.poll_for_token(
                device_code=flow_info.device_code,
                interval=flow_info.interval,
                timeout=flow_info.expires_in,
            )
            if not result.valid:
                try:
                    await self.send_message(f"구독 인증 실패: {result.message}")
                except Exception:
                    pass
                return

            if llm_router is None:
                return

            loaded = llm_router.apply_subscription(
                openai_api_key=result.openai_api_key,
                openai_model=result.openai_model,
                claude_api_key=result.claude_api_key,
                claude_model=result.claude_model,
                system_prompt=result.system_prompt,
                max_tokens=result.max_tokens,
            )
            sub_client.start_revalidation()

            providers_text = ", ".join(loaded) if loaded else "없음"
            try:
                await self.send_message(
                    f"구독 인증 성공!\n"
                    f"활성 Provider: {providers_text}\n\n"
                    f"LLM 기능을 사용할 수 있습니다."
                )
            except Exception:
                pass

        _ = asyncio.create_task(_poll_and_apply())

    async def _cmd_unsubscribe(
        self, update: _UpdateLike, context: _ContextLike
    ) -> None:
        """구독 인증 해제."""
        del context
        if not self._is_admin(update):
            return
        message = update.effective_message
        if message is None:
            return

        if self.subscription_client is None:
            _ = await message.reply_text("구독 기능이 설정되지 않았습니다.")
            return

        if not self.subscription_client.is_authenticated:
            _ = await message.reply_text("현재 인증된 구독이 없습니다.")
            return

        self.subscription_client.clear()
        if self.llm_router is not None:
            self.llm_router.clear_subscription()

        _ = await message.reply_text(
            "구독 인증이 해제되었습니다.\n"
            "LLM 기능이 비활성화됩니다.\n"
            "/subscribe 명령으로 다시 인증할 수 있습니다."
        )

    async def _cmd_subscription(
        self, update: _UpdateLike, context: _ContextLike
    ) -> None:
        """구독 상태 확인."""
        del context
        if not self._is_admin(update):
            return
        message = update.effective_message
        if message is None:
            return

        if self.subscription_client is None:
            _ = await message.reply_text("구독 기능이 설정되지 않았습니다.")
            return

        if not self.subscription_client.is_authenticated:
            _ = await message.reply_text(
                "구독 미인증 상태.\n/subscribe 명령으로 인증하세요."
            )
            return

        r = self.subscription_client.result
        token_masked = (
            self.subscription_client.access_token[:8] + "****"
            if self.subscription_client.access_token
            else "-"
        )
        providers = (
            ", ".join(self.llm_router.available_providers) if self.llm_router else "-"
        )
        current = self.llm_router.current_provider if self.llm_router else "-"
        _ = await message.reply_text(
            f"구독 상태: 인증됨\n"
            f"토큰: {token_masked}\n"
            f"Provider: {providers}\n"
            f"현재: {current}\n"
            f"만료: {r.expires_at or '무제한'}"
        )

    # ── Claude CLI 핸들러 ────────────────────────────────

    async def _cmd_claude_auth(
        self, update: _UpdateLike, context: _ContextLike
    ) -> None:
        """Claude CLI 인증 상태 확인. 미감지 시 자동 감지 시도."""
        del context
        if not self._is_admin(update):
            return
        message = update.effective_message
        if message is None:
            return

        if self.llm_router is None:
            _ = await message.reply_text("LLM 라우터가 설정되지 않았습니다.")
            return

        # 아직 claude-cli provider가 없으면 자동 감지 시도
        if "claude-cli" not in self.llm_router.available_providers:
            _ = await message.reply_text("Claude CLI 감지 중...")
            detected = await self.llm_router.auto_detect_claude_cli()
            if not detected:
                _ = await message.reply_text(
                    "Claude CLI를 사용할 수 없습니다.\n\n"
                    "확인 사항:\n"
                    "1. claude CLI 설치: npm install -g @anthropic-ai/claude-code\n"
                    "2. 로그인: claude login\n"
                    "3. PATH에 claude 명령이 있는지 확인"
                )
                return

        # 상태 표시
        from agent.llm.claude_cli_provider import ClaudeCLIProvider

        cli_provider = self.llm_router.claude_cli_provider
        if not isinstance(cli_provider, ClaudeCLIProvider):
            _ = await message.reply_text("Claude CLI provider 내부 오류.")
            return

        status = await cli_provider.get_auth_status()
        chat = update.effective_chat
        current_chat_id = chat.id if chat is not None else None
        session_id = (
            cli_provider.get_session_info(current_chat_id)
            if current_chat_id is not None
            else None
        )

        lines = [
            "Claude CLI 상태",
            f"  설치: {'✅' if status['installed'] else '❌'}",
            f"  경로: {status['cli_path']}",
            f"  버전: {status['version']}",
            f"  인증: {'✅' if status['authenticated'] else '❌'}",
            f"  모델: {status['model']}",
            f"  활성 세션: {status['active_sessions']}개",
        ]

        if session_id:
            lines.append(f"  현재 세션: {session_id[:12]}...")
        else:
            lines.append("  현재 세션: 없음 (새 대화 시 자동 생성)")

        providers = ", ".join(self.llm_router.available_providers)
        current = self.llm_router.current_provider
        lines.append(f"\n사용 가능 Provider: {providers}")
        lines.append(f"현재 Provider: {current}")

        if current != "claude-cli":
            lines.append("\n/model claude-cli 로 전환할 수 있습니다.")

        _ = await message.reply_text("\n".join(lines))

    async def _cmd_claude_reset(
        self, update: _UpdateLike, context: _ContextLike
    ) -> None:
        """현재 채팅의 Claude CLI 세션을 초기화한다."""
        del context
        if not self._is_admin(update):
            return
        message = update.effective_message
        if message is None:
            return

        if self.llm_router is None:
            _ = await message.reply_text("LLM 라우터가 설정되지 않았습니다.")
            return

        from agent.llm.claude_cli_provider import ClaudeCLIProvider

        cli_provider = self.llm_router.claude_cli_provider
        if not isinstance(cli_provider, ClaudeCLIProvider):
            _ = await message.reply_text(
                "Claude CLI provider가 활성화되지 않았습니다.\n"
                "/claude_auth 로 먼저 상태를 확인하세요."
            )
            return

        chat = update.effective_chat
        current_chat_id = chat.id if chat is not None else None
        if current_chat_id is None:
            _ = await message.reply_text("채팅 ID를 확인할 수 없습니다.")
            return

        had_session = cli_provider.reset_session(current_chat_id)
        if had_session:
            _ = await message.reply_text(
                "Claude CLI 대화 세션이 초기화되었습니다.\n"
                "다음 메시지부터 새 대화가 시작됩니다."
            )
        else:
            _ = await message.reply_text(
                "초기화할 세션이 없습니다.\n다음 메시지에서 새 대화가 시작됩니다."
            )

    async def _cmd_rec_status(self, update: _UpdateLike, context: _ContextLike) -> None:
        """녹취 감시 상태를 보여준다."""
        del context
        if not self._is_admin(update):
            return
        message = update.effective_message
        if message is None:
            return

        watcher = self.rec_watcher
        if watcher is None:
            _ = await message.reply_text(
                "녹취 감시가 설정되지 않았습니다.\n"
                "config.yaml의 recording 섹션을 확인하세요."
            )
            return

        # RecordingWatcher has processed_count property
        count = getattr(watcher, "processed_count", 0)
        watch_dir = getattr(watcher, "_watch_dir", "?")
        _ = await message.reply_text(
            f"녹취 감시 상태: 실행 중\n감시 폴더: {watch_dir}\n분석 완료: {count}건"
        )

    @staticmethod
    async def _send_long_message(message: _MessageLike, text: str) -> None:
        """Telegram 4096자 제한 대응: 긴 메시지 분할 전송."""
        max_len = 4000
        if len(text) <= max_len:
            _ = await message.reply_text(text)
            return
        # 분할 전송
        for i in range(0, len(text), max_len):
            chunk = text[i : i + max_len]
            _ = await message.reply_text(chunk)
