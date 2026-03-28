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
from agent.core.server_connection import (
    RecordingOwnership,
    ServerConfig,
    ServerConnection,
)

if TYPE_CHECKING:
    from agent.core.log_cmd_handler import LogCmdHandler
    from agent.core.log_watcher import LogWatcher
    from agent.core.process_mgr import ProcessManager
    from agent.core.process_monitor import ProcessMonitorLoop
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
        self._connections: list[ServerConnection] = []
        self._process_monitors: dict[str, ProcessMonitorLoop] = {}

    @property
    def connections(self) -> list[ServerConnection]:
        return self._connections

    # ──────────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────────

    async def run(self) -> None:  # noqa: C901, PLR0912, PLR0915
        """Main agent lifecycle."""
        from agent.core.log_alert_detector import LogAlertDetector
        from agent.core.log_watcher import LogWatcher
        from agent.core.process_mgr import (
            ProcessManager,
            acquire_instance_lock,
            release_instance_lock,
        )
        from agent.core.process_monitor import ProcessMonitorLoop
        from agent.core.scheduler import ProcessScheduler
        from agent.core.system_monitor import SystemMonitor
        from agent.llm.router import LLMRouter
        from agent.llm.subscription import SubscriptionClient
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
        watcher: LogWatcher | None = None
        poller: AgentTelegramPoller | None = None
        monitor_tasks: list[asyncio.Task[None]] = []
        scheduler_tasks: list[asyncio.Task[None]] = []
        heartbeat_tasks: list[asyncio.Task[None]] = []
        subscription_client: SubscriptionClient | None = None
        connections: list[ServerConnection] = []

        try:
            # 2. Load .env + config
            load_dotenv(self._base_dir / ".env")
            cfg = ConfigView(self._load_config())
            logger.info("config loaded: %s", list(cfg.raw().keys()))

            # 3. Config sections
            agent_cfg = cfg.sub("agent")
            telegram_cfg = cfg.sub("telegram")
            monitoring_cfg = cfg.sub("monitoring")
            schedule_cfg = cfg.sub("schedule")
            recording_cfg = cfg.sub("recording")
            rec_client_cfg = cfg.sub("rec_client")

            from agent import __version__ as agent_version

            agent_id = agent_cfg.s("id", "agent-unknown")
            agent_ver = agent_version

            # Parse target_process (list[dict] or legacy dict → auto-wrap)
            raw_tp = cfg.raw().get("target_process", {})
            if isinstance(raw_tp, dict):
                process_list: list[dict] = [raw_tp] if raw_tp.get("name") else []
            elif isinstance(raw_tp, list):
                process_list = [p for p in raw_tp if isinstance(p, dict) and p.get("name")]
            else:
                process_list = []

            restart_times = schedule_cfg.ls("restart_times", [])

            # 4. Parse server configs
            server_configs = self._parse_server_configs(cfg)
            if not server_configs:
                logger.warning(
                    "no server configured: set servers:[] or connection: host"
                )
                await self._stop_event.wait()
                return

            # 5. Shared LogWatcher + fan-out callback + alert detector
            alert_cfg = monitoring_cfg.sub("alert")
            alert_detector: LogAlertDetector | None = None

            async def _send_log(filename: str, line: str, folder_index: int) -> None:
                # 이상 패턴 감지 (alert detector가 활성화된 경우)
                if alert_detector is not None:
                    await alert_detector.check_line(filename, line)

                tasks: list[Awaitable[None]] = []
                for conn in connections:
                    if (
                        conn.tcp_client.is_connected
                        and conn.log_cmd_handler.is_realtime_active
                    ):
                        tasks.append(
                            conn.tcp_client.send_log_line(filename, line, folder_index)
                        )
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                self._hooks.on_log_sent()

            watcher = LogWatcher(
                watch_dirs=monitoring_cfg.ls("log_folders", []),
                extensions=monitoring_cfg.ls("watch_extensions", [".log"]),
                on_new_line=_send_log,
            )

            # 6. Process Managers (multi-process)
            process_mgrs: dict[str, ProcessManager] = {}
            process_configs: dict[str, dict] = {}
            for proc in process_list:
                pname = proc.get("name", "")
                if not pname:
                    continue
                mgr = ProcessManager(
                    process_name=pname,
                    process_path=proc.get("path", ""),
                    backup_dir=proc.get("backup_dir", "./backups"),
                )
                process_mgrs[pname] = mgr
                process_configs[pname] = {
                    "args": proc.get("args", []) or [],
                    "auto_restart": bool(proc.get("auto_restart", False)),
                    "check_interval": int(proc.get("check_interval", 30)),
                }
                logger.info("process manager: %s → %s", pname, proc.get("path", ""))

            # Backward compat: first process as default
            default_process_mgr: ProcessManager = (
                next(iter(process_mgrs.values()))
                if process_mgrs
                else ProcessManager(process_name="", process_path="", backup_dir="./backups")
            )
            default_process_cfg = next(iter(process_configs.values()), {})
            default_process_args: list[str] | None = default_process_cfg.get("args") or None

            # 6-1. Rec Client ProcessManager (optional)
            rec_client_mgr: ProcessManager | None = None
            rec_client_name = rec_client_cfg.s("name", "")
            rec_client_path = rec_client_cfg.s("path", "")
            rec_client_args = rec_client_cfg.ls("args", [])
            if rec_client_name and rec_client_path:
                rec_client_mgr = ProcessManager(
                    process_name=rec_client_name,
                    process_path=rec_client_path,
                    backup_dir=rec_client_cfg.s("backup_dir", "./backups"),
                )
                logger.info("rec_client process manager: %s", rec_client_name)

            # 7. Shared Telegram poller
            poller = AgentTelegramPoller(
                bot_token=telegram_cfg.s("bot_token", ""),
                admin_chat_id=telegram_cfg.i("admin_chat_id", 0),
            )

            # 8. Shared SystemMonitor
            system_monitor = SystemMonitor(
                target_process_names=list(process_mgrs.keys()),
            )
            poller.system_monitor = system_monitor

            # 9. Shared SelfUpdater
            updater = SelfUpdater(
                install_dir=self._base_dir,
                is_service_mode=self._hooks.is_service_mode,
            )
            poller.updater = updater
            poller.log_watcher = watcher

            # 10. Process Deployers (one per process)
            process_deployers: dict[str, ProcessDeployer] = {}
            for pname, pmgr in process_mgrs.items():
                deployer = ProcessDeployer(
                    process_mgr=pmgr,
                    update_dir=updater.update_dir,
                    log_folders=monitoring_cfg.ls("log_folders", []),
                )
                process_deployers[pname] = deployer
            # Default deployer for backward compat
            default_deployer: ProcessDeployer | None = (
                next(iter(process_deployers.values())) if process_deployers else None
            )

            # 11. Wire poller shared dependencies
            poller.process_deployer = default_deployer
            poller.process_args = default_process_args

            # 12. Shared LLMRouter
            llm_cfg = cfg.sub("llm")
            openai_cfg = llm_cfg.sub("openai")
            claude_cfg = llm_cfg.sub("claude")
            openrouter_cfg = llm_cfg.sub("openrouter")

            openai_key = openai_cfg.s("api_key", "") or os.environ.get(
                "OPENAI_API_KEY", ""
            )
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

            # 13. Subscription client
            sub_cfg = llm_cfg.sub("subscription")
            sub_server_url = sub_cfg.s("server_url", "")
            sub_key = sub_cfg.s("key", "")

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

            # 14. Provider ready hook
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

            # 15. Shared ownership + lock
            rec_ownership = RecordingOwnership()
            deploy_lock = asyncio.Lock()

            # 16. Per-server connection objects
            remote_commands_cfg = cfg.raw().get("remote_commands", [])
            for srv_cfg in server_configs:
                conn = self._create_server_connection(
                    srv_cfg=srv_cfg,
                    agent_id=agent_id,
                    agent_ver=agent_ver,
                    watcher=watcher,
                    process_mgrs=process_mgrs,
                    process_configs=process_configs,
                    monitoring_cfg=monitoring_cfg,
                    recording_cfg=recording_cfg,
                    rec_ownership=rec_ownership,
                    deploy_lock=deploy_lock,
                    rec_client_mgr=rec_client_mgr,
                    rec_client_args=rec_client_args or None,
                    remote_commands=remote_commands_cfg,
                    full_config=cfg,
                    process_deployers=process_deployers,
                )
                conn.deploy_handler.updater = updater
                conn.deploy_handler.process_deployers = process_deployers
                connections.append(conn)

            self._connections = connections
            self.tcp_client = connections[0].tcp_client
            self._update_aggregate_connection()

            # 17. Start watcher + poller
            await watcher.start()
            await poller.start()
            logger.info("watcher + poller started")

            # 18. Startup Telegram message
            self._hooks.on_status("실행 중")
            with contextlib.suppress(Exception):
                await poller.send_message(
                    f"Agent started.\nID: {agent_id}\nVersion: {agent_ver}"
                )

            # 19. Notification callback + Log alert detector
            async def _notify(message: str) -> None:
                with contextlib.suppress(Exception):
                    await poller.send_message(message)

            alert_enabled = alert_cfg.b("enabled", True)
            custom_patterns = alert_cfg.raw().get("patterns")
            pattern_list = (
                list(custom_patterns) if isinstance(custom_patterns, list) else None
            )
            alert_detector = LogAlertDetector(
                on_notify=_notify,
                patterns=pattern_list,
                cooldown=float(alert_cfg.i("cooldown", 300)),
                enabled=alert_enabled,
            )

            for pname, pmgr in process_mgrs.items():
                pcfg = process_configs[pname]
                loop = ProcessMonitorLoop(
                    process_mgr=pmgr,
                    check_interval=float(pcfg["check_interval"]),
                    process_args=pcfg["args"] or None,
                    on_notify=_notify,
                    enabled=True,
                    auto_restart=pcfg["auto_restart"],
                )
                self._process_monitors[pname] = loop
                monitor_tasks.append(asyncio.create_task(loop.run()))

            # 20. ProcessScheduler (one per process)
            if restart_times:
                for pname, pmgr in process_mgrs.items():
                    pcfg = process_configs[pname]
                    scheduler = ProcessScheduler(
                        process_mgr=pmgr,
                        restart_times=restart_times,
                        process_args=pcfg["args"] or None,
                        on_notify=_notify,
                    )
                    scheduler_tasks.append(asyncio.create_task(scheduler.run()))

            # 21. Per-server connection-lost callback
            async def _on_server_connection_lost(conn: ServerConnection) -> None:
                await conn.rec_controller.on_connection_lost()
                conn.log_cmd_handler._realtime_active = False
                self._update_aggregate_connection()
                logger.info("[%s] connection lost: handlers reset", conn.config.name)

            # 22. Start per-server heartbeat tasks
            for conn in connections:
                heartbeat_tasks.append(
                    asyncio.create_task(
                        self._server_heartbeat_loop(conn, _on_server_connection_lost)
                    )
                )

            # 23. Wait stop event
            await self._stop_event.wait()
        except Exception:
            logger.exception("agent runtime loop crashed")
        finally:
            # 24. Cancel per-server heartbeat tasks
            for task in heartbeat_tasks:
                task.cancel()
            for task in heartbeat_tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await task

            # 25. Cleanup shared/background tasks
            for task in monitor_tasks:
                task.cancel()
            for task in monitor_tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            for task in scheduler_tasks:
                task.cancel()
            for task in scheduler_tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if subscription_client is not None:
                subscription_client.stop_revalidation()

            # 26. Cleanup per-server connections
            for conn in connections:
                with contextlib.suppress(Exception):
                    await conn.cleanup()

            # 27. Cleanup shared components
            if poller is not None:
                with contextlib.suppress(Exception):
                    await poller.stop()
            if watcher is not None:
                with contextlib.suppress(Exception):
                    await watcher.stop()

            self._connections = []
            self.tcp_client = None
            release_instance_lock(self._base_dir)
            logger.info("agent runtime loop ended")

    # ──────────────────────────────────────────────
    # Heartbeat / auto-connect / reconnect
    # ──────────────────────────────────────────────

    async def _server_heartbeat_loop(
        self,
        conn: ServerConnection,
        on_connection_lost: Callable[[ServerConnection], Awaitable[None]],
    ) -> None:
        """Per-server heartbeat, auto-connect, and reconnect loop."""
        logger = self._logger
        tcp_client = conn.tcp_client
        srv = conn.config
        interval = max(1, srv.heartbeat_interval)
        next_heartbeat = 0.0
        loop = asyncio.get_running_loop()

        auto_connect = bool(srv.host and srv.port)
        reconnect_delay = max(srv.reconnect_delay, 10)
        # 시간 기반 재접속: 카운터 대신 다음 재접속 시각을 추적
        _FIRST_RECONNECT_DELAY = 5  # 끊긴 직후 첫 재시도까지 5초
        next_reconnect = 0.0  # 0 = 미예약

        if auto_connect:
            logger.info("[%s] auto-connect: %s:%s ...", srv.name, srv.host, srv.port)
            connected = await tcp_client.connect()
            if connected:
                logger.info("[%s] auto-connect: success", srv.name)
                self._update_aggregate_connection()
            else:
                logger.warning("[%s] auto-connect: failed, will retry", srv.name)
                next_reconnect = loop.time() + _FIRST_RECONNECT_DELAY

        while not self._stop_event.is_set():
            await asyncio.sleep(1)
            now = loop.time()

            if tcp_client.is_connected:
                # 연결 중: heartbeat interval 마다 전송
                next_reconnect = 0.0
                if now >= next_heartbeat:
                    next_heartbeat = now + interval
                    try:
                        process_statuses: dict[str, int] = {}
                        for pname, mon in self._process_monitors.items():
                            process_statuses[pname] = mon.process_status
                        await asyncio.wait_for(
                            tcp_client.send_heartbeat(
                                process_statuses=process_statuses,
                            ),
                            timeout=5.0,
                        )
                    except (ConnectionError, OSError, asyncio.TimeoutError):
                        logger.warning(
                            "[%s] heartbeat failed — closing connection", srv.name
                        )
                        await tcp_client.close_on_error()
                        await on_connection_lost(conn)
                        next_reconnect = now + _FIRST_RECONNECT_DELAY
            elif auto_connect:
                # 미연결: 시간 기반 재접속
                if next_reconnect == 0.0:
                    next_reconnect = now + _FIRST_RECONNECT_DELAY
                elif now >= next_reconnect:
                    logger.info(
                        "[%s] reconnecting to %s:%s ...",
                        srv.name,
                        srv.host,
                        srv.port,
                    )
                    connected = await tcp_client.connect()
                    if connected:
                        logger.info("[%s] reconnected successfully", srv.name)
                        next_reconnect = 0.0
                        self._update_aggregate_connection()
                    else:
                        next_reconnect = now + reconnect_delay

    def _update_aggregate_connection(self) -> None:
        any_connected = any(c.tcp_client.is_connected for c in self._connections)
        self._hooks.on_connection_change(any_connected)

    def _parse_server_configs(self, cfg: ConfigView) -> list[ServerConfig]:
        """Parse server list from config. Supports both `servers:` and `connection:`."""
        servers_list = cfg.raw().get("servers")
        if isinstance(servers_list, list) and servers_list:
            result: list[ServerConfig] = []
            for i, entry in enumerate(servers_list):
                if not isinstance(entry, dict):
                    continue
                result.append(
                    ServerConfig(
                        name=str(entry.get("name", f"server-{i}")),
                        host=str(entry.get("host", "127.0.0.1")),
                        port=int(entry.get("port", 9500)),
                        token=str(entry.get("token", "")),
                        heartbeat_interval=int(entry.get("heartbeat_interval", 30)),
                        reconnect_attempts=int(entry.get("reconnect_attempts", 5)),
                        reconnect_delay=int(entry.get("reconnect_delay", 60)),
                    )
                )
            return result

        conn_cfg = cfg.sub("connection")
        host = conn_cfg.s("host", "")
        if not host:
            return []
        return [
            ServerConfig(
                name="default",
                host=host,
                port=conn_cfg.i("port", 9500),
                token=conn_cfg.s("token", ""),
                heartbeat_interval=conn_cfg.i("heartbeat_interval", 30),
                reconnect_attempts=conn_cfg.i("reconnect_attempts", 5),
                reconnect_delay=conn_cfg.i("reconnect_delay", 60),
            )
        ]

    def _create_server_connection(
        self,
        srv_cfg: ServerConfig,
        agent_id: str,
        agent_ver: str,
        watcher: LogWatcher,
        process_mgrs: dict[str, ProcessManager],
        process_configs: dict[str, dict],
        monitoring_cfg: ConfigView,
        recording_cfg: ConfigView,
        rec_ownership: RecordingOwnership,
        deploy_lock: asyncio.Lock,
        rec_client_mgr: ProcessManager | None = None,
        rec_client_args: list[str] | None = None,
        remote_commands: list | None = None,
        full_config: ConfigView | None = None,
        process_deployers: dict | None = None,
    ) -> ServerConnection:
        from agent.core.ctrl_handler import CtrlHandler
        from agent.core.deploy_handler import DeployHandler
        from agent.core.log_cmd_handler import LogCmdHandler
        from agent.core.tcp_client import TCPClient
        from agent.recording.controller import RecordingController

        tcp_client = TCPClient(
            agent_id=agent_id,
            version=agent_ver,
            token=srv_cfg.token,
            host=srv_cfg.host,
            port=srv_cfg.port,
            heartbeat_interval=srv_cfg.heartbeat_interval,
            reconnect_attempts=srv_cfg.reconnect_attempts,
            reconnect_delay=srv_cfg.reconnect_delay,
        )

        log_cmd_handler = LogCmdHandler(
            log_watcher=watcher,
            tcp_client=tcp_client,
            history_max_mb=monitoring_cfg.i("history_max_mb", 10),
        )
        tcp_client.on_cmd_log = log_cmd_handler.handle_cmd_log
        tcp_client.on_log_file_select = log_cmd_handler.handle_file_select

        deploy_handler = DeployHandler(
            tcp_client=tcp_client,
            process_mgrs=process_mgrs,
            transfer_dir=monitoring_cfg.s("transfer_dir", "agent/storage/transfers"),
        )
        tcp_client.on_cmd_deploy = deploy_handler.handle_cmd_deploy

        from agent.core.file_handler import FileHandler
        file_handler = FileHandler(
            tcp_client=tcp_client,
            config=full_config if full_config is not None else monitoring_cfg,
        )
        tcp_client.on_cmd_file_list = file_handler.handle_cmd_file_list
        tcp_client.on_cmd_file_get = file_handler.handle_cmd_file_get
        tcp_client.on_cmd_file_put = file_handler.handle_cmd_file_put
        tcp_client.on_cmd_file_run = file_handler.handle_cmd_file_run

        # CMD_DEPLOY 수신 시 stale file_put receiver 리셋
        _orig_deploy_handler = deploy_handler.handle_cmd_deploy

        async def _deploy_with_reset(payload_data: bytes) -> None:
            file_handler.reset_receiver()
            await _orig_deploy_handler(payload_data)

        tcp_client.on_cmd_deploy = _deploy_with_reset

        async def _route_file_chunk(payload_data: bytes) -> None:
            """FILE_CHUNK를 활성 전송 컨텍스트로 라우팅."""
            if file_handler.is_receiving_file:
                await file_handler.handle_file_chunk_for_put(payload_data)
            else:
                await deploy_handler.handle_file_chunk(payload_data)

        tcp_client.on_file_chunk = _route_file_chunk

        ctrl_handler = CtrlHandler(
            tcp_client=tcp_client,
            process_mgrs=process_mgrs,
            process_configs=process_configs,
            rec_client_mgr=rec_client_mgr,
            rec_client_args=rec_client_args,
            process_deployers=process_deployers,
        )
        tcp_client.on_cmd_ctrl = ctrl_handler.handle_cmd_ctrl

        rec_controller = RecordingController(
            tcp_client=tcp_client,
            recording_cfg=recording_cfg,
            logger=self._logger,
            server_name=srv_cfg.name,
            ownership=rec_ownership,
        )
        tcp_client.on_cmd_rec = rec_controller.handle_cmd_rec
        tcp_client.on_rec_upload_req = rec_controller.handle_rec_upload_req
        tcp_client.on_stt_result = rec_controller.handle_stt_result
        tcp_client.on_rec_data_req = rec_controller.handle_rec_data_req

        from agent.core.config_handler import ConfigHandler
        from agent.core.exec_handler import ExecHandler

        config_handler = ConfigHandler(
            base_dir=self._base_dir,
            tcp_client=tcp_client,
        )
        tcp_client.on_cmd_config = config_handler.handle_cmd_config

        exec_handler = ExecHandler(
            tcp_client=tcp_client,
            remote_commands=list(remote_commands) if remote_commands else [],
            config_path=self._base_dir / "config.yaml",
        )
        tcp_client.on_cmd_exec = exec_handler.handle_cmd_exec

        _ = deploy_lock
        return ServerConnection(
            config=srv_cfg,
            tcp_client=tcp_client,
            log_cmd_handler=log_cmd_handler,
            deploy_handler=deploy_handler,
            rec_controller=rec_controller,
        )

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
