"""AgentRuntime — shared orchestrator for service and GUI agent modes."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from agent.core.config_view import ConfigView

if TYPE_CHECKING:
    from agent.core.log_cmd_handler import LogCmdHandler
    from agent.core.tcp_client import TCPClient
    from agent.recording.controller import RecordingController


@dataclass
class AgentHooks:
    """Mode-specific callbacks. GUI implements these; Service uses defaults."""

    on_status: Callable[[str], None] = field(default=lambda _s: None)
    on_connection_change: Callable[[bool], None] = field(default=lambda _c: None)
    on_log_sent: Callable[[], None] = field(default=lambda: None)
    on_duplicate_instance: Callable[[str], None] = field(default=lambda _m: None)
    on_provider_ready: Callable[[str], None] = field(default=lambda _p: None)
    is_service_mode: bool = False


class AgentRuntime:
    """Shared agent lifecycle: config -> components -> connect -> loop -> cleanup.

    Both Windows Service and GUI mode create an instance and call ``run()``,
    providing mode-specific behaviour via :class:`AgentHooks`.
    """

    def __init__(
        self,
        base_dir: Path,
        stop_event: asyncio.Event,
        hooks: AgentHooks | None = None,
    ) -> None:
        self._base_dir = base_dir
        self._stop_event = stop_event
        self._hooks = hooks or AgentHooks()
        self._logger = logging.getLogger("agent.runtime")
        # Exposed so GUI can access for force-reconnect
        self.tcp_client: TCPClient | None = None

    # ──────────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────────

    async def run(self) -> None:  # noqa: C901, PLR0912, PLR0915
        """Main agent lifecycle."""
        from agent.core.deploy_handler import DeployHandler
        from agent.core.log_cmd_handler import LogCmdHandler
        from agent.core.log_watcher import LogWatcher
        from agent.core.process_mgr import (
            ProcessManager,
            acquire_instance_lock,
            release_instance_lock,
        )
        from agent.core.process_monitor import ProcessMonitorLoop
        from agent.core.scheduler import ProcessScheduler
        from agent.core.system_monitor import SystemMonitor
        from agent.core.tcp_client import TCPClient
        from agent.llm.router import LLMRouter
        from agent.llm.subscription import SubscriptionClient
        from agent.recording.controller import RecordingController
        from agent.telegram.poller import AgentTelegramPoller
        from agent.updater.process_deploy import ProcessDeployer
        from agent.updater.self_update import SelfUpdater
        from shared.utils import load_dotenv

        logger = self._logger

        # 1. Instance lock
        if not acquire_instance_lock(self._base_dir):
            msg = "another agent instance is already running. exiting."
            logger.error(msg)
            self._hooks.on_duplicate_instance(msg)
            return

        # 2. Load .env + config
        load_dotenv(self._base_dir / ".env")
        cfg = ConfigView(self._load_config())
        logger.info("config loaded: %s", list(cfg.raw().keys()))

        # 3. Config sections
        agent_cfg = cfg.sub("agent")
        telegram_cfg = cfg.sub("telegram")
        monitoring_cfg = cfg.sub("monitoring")
        process_cfg = cfg.sub("target_process")
        schedule_cfg = cfg.sub("schedule")
        connection_cfg = cfg.sub("connection")
        recording_cfg = cfg.sub("recording")

        from agent import __version__ as agent_version

        agent_id = agent_cfg.s("id", "agent-unknown")
        agent_ver = agent_version

        process_args = process_cfg.ls("args", [])
        auto_restart = process_cfg.b("auto_restart", False)
        check_interval = process_cfg.i("check_interval", 30)
        restart_times = schedule_cfg.ls("restart_times", [])

        # 4. TCPClient
        tcp_client = TCPClient(
            agent_id=agent_id,
            version=agent_ver,
            token=connection_cfg.s("token", ""),
            host=connection_cfg.s("host", "127.0.0.1"),
            port=connection_cfg.i("port", 9500),
            heartbeat_interval=connection_cfg.i("heartbeat_interval", 30),
            reconnect_attempts=connection_cfg.i("reconnect_attempts", 5),
            reconnect_delay=connection_cfg.i("reconnect_delay", 10),
        )
        self.tcp_client = tcp_client

        # 5. ProcessManager
        process_mgr = ProcessManager(
            process_name=process_cfg.s("name", ""),
            process_path=process_cfg.s("path", ""),
            backup_dir=process_cfg.s("backup_dir", "./backups"),
        )

        # 6. DeployHandler
        deploy_handler = DeployHandler(
            tcp_client=tcp_client,
            process_mgr=process_mgr,
            transfer_dir=monitoring_cfg.s("transfer_dir", "agent/storage/transfers"),
        )

        # 7. LogWatcher + log sender callback
        log_cmd_handler_ref: list[LogCmdHandler | None] = [None]

        async def _send_log(filename: str, line: str) -> None:
            if not tcp_client.is_connected:
                return
            handler = log_cmd_handler_ref[0]
            if handler is None or not handler.is_realtime_active:
                return
            with contextlib.suppress(ConnectionError, OSError):
                await tcp_client.send_log_line(filename, line)
            self._hooks.on_log_sent()

        watcher = LogWatcher(
            watch_dirs=monitoring_cfg.ls("log_folders", []),
            extensions=monitoring_cfg.ls("watch_extensions", [".log"]),
            on_new_line=_send_log,
        )

        # 8. Telegram poller
        poller = AgentTelegramPoller(
            bot_token=telegram_cfg.s("bot_token", ""),
            admin_chat_id=telegram_cfg.i("admin_chat_id", 0),
        )

        # 9. SystemMonitor
        system_monitor = SystemMonitor(
            target_process_name=process_cfg.s("name", ""),
        )
        poller.system_monitor = system_monitor

        # 10. SelfUpdater
        updater = SelfUpdater(
            install_dir=self._base_dir,
            is_service_mode=self._hooks.is_service_mode,
        )
        deploy_handler.updater = updater
        poller.updater = updater
        poller.log_watcher = watcher

        # 11. ProcessDeployer
        process_deployer = ProcessDeployer(
            process_mgr=process_mgr,
            update_dir=updater.update_dir,
            log_folders=monitoring_cfg.ls("log_folders", []),
        )
        deploy_handler.process_deployer = process_deployer

        # 12. Wire poller
        poller.process_deployer = process_deployer
        poller.process_args = process_args or None

        # 13. LLMRouter
        llm_cfg = cfg.sub("llm")
        openai_cfg = llm_cfg.sub("openai")
        claude_cfg = llm_cfg.sub("claude")
        openrouter_cfg = llm_cfg.sub("openrouter")

        openai_key = openai_cfg.s("api_key", "") or os.environ.get("OPENAI_API_KEY", "")
        claude_key = claude_cfg.s("api_key", "") or os.environ.get(
            "ANTHROPIC_API_KEY", ""
        )
        openrouter_key = openrouter_cfg.s("api_key", "") or os.environ.get(
            "OPENROUTER_API_KEY", ""
        )

        llm_router = LLMRouter(
            default_provider=llm_cfg.s("default_provider", "openai"),
            openai_api_key=openai_key,
            openai_model=openai_cfg.s("model", "gpt-4o-mini"),
            claude_api_key=claude_key,
            claude_model=claude_cfg.s("model", "claude-sonnet-4-20250514"),
            openrouter_api_key=openrouter_key,
            openrouter_model=openrouter_cfg.s("model", "openai/gpt-4o-mini"),
            system_prompt=llm_cfg.s(
                "system_prompt",
                "당신은 산업용 소프트웨어 로그 분석 전문가입니다. 한국어로 답변하세요.",
            ),
            max_tokens=llm_cfg.i("max_tokens", 2000),
        )
        poller.llm_router = llm_router

        # 14. Subscription client
        sub_cfg = llm_cfg.sub("subscription")
        sub_server_url = sub_cfg.s("server_url", "")
        sub_key = sub_cfg.s("key", "")
        subscription_client: SubscriptionClient | None = None

        if sub_server_url:
            subscription_client = SubscriptionClient(
                server_url=sub_server_url,
                agent_id=agent_id,
                revalidate_hours=sub_cfg.i("revalidate_hours", 24),
            )
            poller.subscription_client = subscription_client

            auth_mode = llm_cfg.s("auth_mode", "apikey")
            if sub_key and auth_mode == "subscription":
                logger.info("auto-validating subscription key from config...")
                sub_result = await subscription_client.validate(sub_key)
                if sub_result.valid:
                    llm_router.apply_subscription(
                        openai_api_key=sub_result.openai_api_key,
                        openai_model=sub_result.openai_model,
                        claude_api_key=sub_result.claude_api_key,
                        claude_model=sub_result.claude_model,
                        system_prompt=sub_result.system_prompt,
                        max_tokens=sub_result.max_tokens,
                    )
                    subscription_client.start_revalidation()
                    logger.info("subscription auto-auth OK")
                else:
                    logger.warning(
                        "subscription auto-auth failed: %s", sub_result.message
                    )

        # 15. Provider ready hook
        provider_name = (
            llm_router.current_provider if llm_router.is_available else "없음"
        )
        self._hooks.on_provider_ready(provider_name)
        logger.info(
            "LLM router: auth_mode=%s, providers=%s, default=%s",
            llm_cfg.s("auth_mode", "apikey"),
            llm_router.available_providers,
            llm_router.current_provider,
        )

        # 16. LogCmdHandler
        log_cmd_handler = LogCmdHandler(
            log_watcher=watcher,
            tcp_client=tcp_client,
            history_max_mb=monitoring_cfg.i("history_max_mb", 10),
        )
        log_cmd_handler_ref[0] = log_cmd_handler
        tcp_client.on_cmd_log = log_cmd_handler.handle_cmd_log
        tcp_client.on_log_file_select = log_cmd_handler.handle_file_select

        # 17. Deploy callbacks
        tcp_client.on_cmd_deploy = deploy_handler.handle_cmd_deploy
        tcp_client.on_file_chunk = deploy_handler.handle_file_chunk

        # 18. Poller connect/disconnect
        async def _poller_connect(ip: str, port: int) -> None:
            tcp_client.host = ip
            tcp_client.port = port
            connected = await tcp_client.connect()
            if connected:
                logger.info("connected to server: %s:%s", ip, port)
                self._hooks.on_connection_change(True)

        async def _poller_disconnect() -> None:
            await tcp_client.disconnect()

        poller.on_connect = _poller_connect
        poller.on_disconnect = _poller_disconnect

        # 19. RecordingController
        rec_controller = RecordingController(
            tcp_client=tcp_client,
            recording_cfg=recording_cfg,
            logger=logger,
        )
        tcp_client.on_cmd_rec = rec_controller.handle_cmd_rec
        tcp_client.on_rec_upload_req = rec_controller.handle_rec_upload_req
        tcp_client.on_stt_result = rec_controller.handle_stt_result
        tcp_client.on_rec_data_req = rec_controller.handle_rec_data_req

        # 20. Start watcher + poller
        await watcher.start()
        await poller.start()
        logger.info("watcher + poller started")

        # 21. Startup Telegram message
        self._hooks.on_status("실행 중")
        with contextlib.suppress(Exception):
            await poller.send_message(
                f"Agent started.\nID: {agent_id}\nVersion: {agent_ver}"
            )

        # 22. ProcessMonitorLoop
        monitor_task: asyncio.Task[None] | None = None
        scheduler_task: asyncio.Task[None] | None = None

        async def _notify(message: str) -> None:
            with contextlib.suppress(Exception):
                await poller.send_message(message)

        if auto_restart:
            monitor_loop = ProcessMonitorLoop(
                process_mgr=process_mgr,
                check_interval=float(check_interval),
                process_args=process_args or None,
                on_notify=_notify,
                enabled=True,
            )
            monitor_task = asyncio.create_task(monitor_loop.run())

        # 23. ProcessScheduler
        if restart_times:
            scheduler = ProcessScheduler(
                process_mgr=process_mgr,
                restart_times=restart_times,
                process_args=process_args or None,
                on_notify=_notify,
            )
            scheduler_task = asyncio.create_task(scheduler.run())

        # Connection-lost handler
        async def _on_connection_lost() -> None:
            await rec_controller.on_connection_lost()
            lch = log_cmd_handler_ref[0]
            if lch is not None:
                lch._realtime_active = False
            self._hooks.on_connection_change(False)
            logger.info("connection lost: rec_watcher stopped, realtime log disabled")

        # 24-25. Auto-connect + heartbeat/reconnect loop
        try:
            await self._heartbeat_loop(tcp_client, _on_connection_lost)
        except Exception:
            logger.exception("heartbeat loop crashed")
        finally:
            # 26. Cleanup
            if monitor_task is not None:
                monitor_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await monitor_task
            if scheduler_task is not None:
                scheduler_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await scheduler_task
            if subscription_client is not None:
                subscription_client.stop_revalidation()
            await rec_controller.cleanup()
            with contextlib.suppress(Exception):
                await poller.stop()
            with contextlib.suppress(Exception):
                await watcher.stop()
            with contextlib.suppress(Exception):
                await tcp_client.disconnect()
            release_instance_lock(self._base_dir)
            logger.info("agent runtime loop ended")

    # ──────────────────────────────────────────────
    # Heartbeat / auto-connect / reconnect
    # ──────────────────────────────────────────────

    async def _heartbeat_loop(
        self,
        tcp_client: TCPClient,
        on_connection_lost: Callable[[], Awaitable[None]],
    ) -> None:
        logger = self._logger
        interval = max(1, tcp_client.heartbeat_interval)
        next_heartbeat = 0.0
        loop = asyncio.get_running_loop()

        auto_connect = bool(tcp_client.host and tcp_client.port)
        reconnect_delay = max(tcp_client.reconnect_delay, 10)
        reconnect_counter = 0
        first_reconnect = True

        if auto_connect:
            logger.info("auto-connect: %s:%s ...", tcp_client.host, tcp_client.port)
            connected = await tcp_client.connect()
            if connected:
                logger.info("auto-connect: success")
                self._hooks.on_connection_change(True)
            else:
                logger.warning("auto-connect: failed, will retry")

        while not self._stop_event.is_set():
            await asyncio.sleep(1)

            now = loop.time()
            if now < next_heartbeat:
                continue
            next_heartbeat = now + interval

            if tcp_client.is_connected:
                reconnect_counter = 0
                first_reconnect = True
                try:
                    await asyncio.wait_for(tcp_client.send_heartbeat(), timeout=5.0)
                except (ConnectionError, OSError, asyncio.TimeoutError):
                    logger.warning("heartbeat failed — closing connection")
                    await tcp_client.close_on_error()
                    await on_connection_lost()
            elif auto_connect:
                reconnect_counter += 1
                threshold = 5 if first_reconnect else reconnect_delay
                if reconnect_counter >= threshold:
                    reconnect_counter = 0
                    first_reconnect = False
                    logger.info(
                        "reconnecting to %s:%s ...",
                        tcp_client.host,
                        tcp_client.port,
                    )
                    connected = await tcp_client.connect()
                    if connected:
                        logger.info("reconnected successfully")
                        first_reconnect = True
                        self._hooks.on_connection_change(True)

    # ──────────────────────────────────────────────
    # Config loading
    # ──────────────────────────────────────────────

    def _load_config(self) -> dict[str, object]:
        config_path = self._base_dir / "config.yaml"
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            return data
        return {}
