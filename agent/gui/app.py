"""AI-LogOps Agent GUI.

tkinter 메인 윈도우 + pystray 시스템 트레이.
에이전트 asyncio 루프는 백그라운드 스레드에서 실행된다.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import tkinter as tk
from pathlib import Path
from queue import SimpleQueue
from tkinter import ttk
from collections.abc import Awaitable, Callable
from typing import Any, TYPE_CHECKING

from agent.gui.log_viewer import LogViewer

if TYPE_CHECKING:
    from agent.recording.watcher import RecordingWatcher


class GUILogHandler(logging.Handler):
    """로그 레코드를 SimpleQueue로 전달하는 핸들러."""

    def __init__(self, queue: SimpleQueue[str]) -> None:
        super().__init__()
        self._queue: SimpleQueue[str] = queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._queue.put(msg)
        except Exception:
            self.handleError(record)


class AgentGUI:
    """에이전트 GUI 메인 애플리케이션."""

    def __init__(self) -> None:
        self._root = tk.Tk()
        self._root.title("AI-LogOps Agent")
        self._root.geometry("380x340")
        self._root.resizable(False, False)
        self._root.configure(bg="#252526")

        # 로그 큐 + 핸들러
        self._log_queue: SimpleQueue[str] = SimpleQueue()
        self._log_handler = GUILogHandler(self._log_queue)
        formatter = logging.Formatter(
            "[%(asctime)s] %(name)s %(levelname)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        self._log_handler.setFormatter(formatter)

        # 상태
        self._agent_thread: threading.Thread | None = None
        self._agent_loop: asyncio.AbstractEventLoop | None = None
        self._log_viewer: LogViewer | None = None
        self._tray_icon: Any | None = None  # pystray.Icon (optional dep)
        self._running: bool = False

        # 상태 변수
        self._status_var = tk.StringVar(value="준비")
        self._agent_id_var = tk.StringVar(value="-")
        self._version_var = tk.StringVar(value="-")
        self._provider_var = tk.StringVar(value="-")
        self._uptime_var = tk.StringVar(value="00:00:00")
        self._log_count_var = tk.StringVar(value="0")

        self._start_time: float = 0.0

        self._build_ui()
        self._setup_tray()

        self._root.protocol("WM_DELETE_WINDOW", self._minimize_to_tray)

    def _build_ui(self) -> None:
        root = self._root

        # ── 헤더 ──
        header = tk.Frame(root, bg="#007acc", height=44)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        tk.Label(
            header,
            text="AI-LogOps Agent",
            bg="#007acc",
            fg="white",
            font=("Segoe UI Semibold", 13),
        ).pack(side=tk.LEFT, padx=12, pady=8)

        self._status_dot = tk.Label(
            header,
            text="●",
            bg="#007acc",
            fg="#888888",
            font=("Segoe UI", 14),
        )
        self._status_dot.pack(side=tk.RIGHT, padx=12)

        # ── 상태 패널 ──
        info_frame = tk.Frame(root, bg="#252526", padx=16, pady=12)
        info_frame.pack(fill=tk.X)

        rows = [
            ("상태", self._status_var),
            ("에이전트 ID", self._agent_id_var),
            ("버전", self._version_var),
            ("LLM Provider", self._provider_var),
            ("가동 시간", self._uptime_var),
            ("로그 수신", self._log_count_var),
        ]
        for i, (label_text, var) in enumerate(rows):
            tk.Label(
                info_frame,
                text=label_text,
                bg="#252526",
                fg="#888888",
                font=("Segoe UI", 9),
                anchor="w",
            ).grid(row=i, column=0, sticky="w", pady=2)

            tk.Label(
                info_frame,
                textvariable=var,
                bg="#252526",
                fg="#d4d4d4",
                font=("Segoe UI", 9),
                anchor="w",
            ).grid(row=i, column=1, sticky="w", padx=(16, 0), pady=2)

        info_frame.columnconfigure(1, weight=1)

        # ── 버튼 영역 ──
        btn_frame = tk.Frame(root, bg="#252526", padx=16, pady=8)
        btn_frame.pack(fill=tk.X)

        self._start_btn = tk.Button(
            btn_frame,
            text="▶ 에이전트 시작",
            command=self._start_agent,
            bg="#0e639c",
            fg="white",
            activebackground="#1177bb",
            activeforeground="white",
            relief=tk.FLAT,
            font=("Segoe UI", 10),
            padx=12,
            pady=4,
            cursor="hand2",
        )
        self._start_btn.pack(fill=tk.X, pady=(0, 6))

        self._log_btn = tk.Button(
            btn_frame,
            text="📋 실시간 로그 보기",
            command=self._open_log_viewer,
            bg="#3c3c3c",
            fg="#cccccc",
            activebackground="#4c4c4c",
            activeforeground="white",
            relief=tk.FLAT,
            font=("Segoe UI", 10),
            padx=12,
            pady=4,
            cursor="hand2",
        )
        self._log_btn.pack(fill=tk.X, pady=(0, 6))

        # ── 하단 바 ──
        footer = tk.Frame(root, bg="#1e1e1e", height=24)
        footer.pack(fill=tk.X, side=tk.BOTTOM)
        footer.pack_propagate(False)

        tk.Label(
            footer,
            text="트레이로 최소화: 닫기 버튼 클릭",
            bg="#1e1e1e",
            fg="#555555",
            font=("Segoe UI", 8),
        ).pack(side=tk.LEFT, padx=8)

    def _setup_tray(self) -> None:
        """시스템 트레이 아이콘 설정."""
        try:
            import pystray
            from PIL import Image, ImageDraw

            # 간단한 아이콘 생성 (파란 원에 흰 A)
            image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.ellipse([4, 4, 60, 60], fill="#007acc")
            draw.text((20, 14), "A", fill="white")

            menu = pystray.Menu(
                pystray.MenuItem("열기", self._show_from_tray, default=True),
                pystray.MenuItem("로그 보기", self._tray_open_log),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("종료", self._quit_app),
            )

            self._tray_icon = pystray.Icon(
                "ai-logops",
                image,
                "AI-LogOps Agent",
                menu,
            )

            tray_thread = threading.Thread(
                target=self._tray_icon.run,
                daemon=True,
            )
            tray_thread.start()
        except ImportError:
            pass  # pystray 없으면 트레이 기능 비활성화

    def _minimize_to_tray(self) -> None:
        """창 닫기 시 트레이로 최소화. 트레이 없으면 완전 종료."""
        if self._tray_icon is not None:
            self._root.withdraw()
        else:
            self._quit_app()

    def _show_from_tray(self) -> None:
        """트레이에서 창 복원."""
        self._root.after(0, self._root.deiconify)
        self._root.after(10, self._root.lift)

    def _tray_open_log(self) -> None:
        self._root.after(0, self._open_log_viewer)

    def _quit_app(self) -> None:
        """완전 종료. 백그라운드 스레드가 남아있더라도 프로세스를 확실히 종료."""
        import os

        self._running = False

        if self._agent_loop is not None:
            self._agent_loop.call_soon_threadsafe(self._agent_loop.stop)

        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()  # type: ignore[union-attr]
            except Exception:
                pass

        try:
            self._root.destroy()
        except Exception:
            pass

        # asyncio 루프/Telegram 폴링 등 데몬 스레드가 남아있을 수 있으므로
        # 일정 시간 후 강제 종료하여 좀비 프로세스를 방지한다.
        def _force_exit() -> None:
            import time

            time.sleep(3)
            os._exit(0)

        import threading

        threading.Thread(target=_force_exit, daemon=True).start()

    # ── 에이전트 실행 ──

    def _start_agent(self) -> None:
        """에이전트를 백그라운드 스레드에서 시작."""
        if self._running:
            return

        self._running = True
        self._start_btn.configure(text="⏹ 실행 중...", state=tk.DISABLED, bg="#3c3c3c")
        self._status_var.set("시작 중...")
        self._status_dot.configure(fg="#cca700")

        import time

        self._start_time = time.time()

        # 로그 핸들러를 루트 로거에 등록
        root_logger = logging.getLogger()
        root_logger.addHandler(self._log_handler)
        root_logger.setLevel(logging.INFO)

        self._agent_thread = threading.Thread(
            target=self._agent_thread_main,
            daemon=True,
        )
        self._agent_thread.start()

        # 업타임 갱신 타이머
        self._update_uptime()
        # 상태 폴링
        self._root.after(2000, self._update_agent_status)

    def _agent_thread_main(self) -> None:
        """백그라운드 스레드: asyncio 루프 + 에이전트 실행."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._agent_loop = loop

        try:
            loop.run_until_complete(self._run_agent_async())
        except Exception:
            logging.getLogger("gui").exception("에이전트 스레드 오류")
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.close()
            asyncio.set_event_loop(None)
            self._running = False
            self._root.after(0, self._on_agent_stopped)

    async def _run_agent_async(self) -> None:
        """에이전트 코어 로직을 실행한다. win_service._run_agent()를 GUI용으로 재사용."""
        import contextlib
        import os as _os

        from agent.core.process_mgr import acquire_instance_lock

        base_dir = self._get_base_dir()
        if not acquire_instance_lock(base_dir):
            logger = logging.getLogger("agent.gui")
            logger.error("another agent instance is already running. exiting.")
            self._root.after(
                0,
                lambda: self._status_var.set("중복 실행 감지 — 종료됨"),
            )
            return

        from agent.core.deploy_handler import DeployHandler
        from agent.core.log_cmd_handler import LogCmdHandler
        from agent.core.log_watcher import LogWatcher
        from agent.core.process_monitor import ProcessMonitorLoop
        from agent.core.process_mgr import ProcessManager
        from agent.core.scheduler import ProcessScheduler
        from agent.core.system_monitor import SystemMonitor
        from agent.core.tcp_client import TCPClient
        from agent.llm.router import LLMRouter
        from agent.llm.subscription import SubscriptionClient
        from agent.telegram.poller import AgentTelegramPoller
        from agent.updater.process_deploy import ProcessDeployer
        from agent.updater.self_update import SelfUpdater
        from shared.utils import load_dotenv, setup_file_logging

        logger = logging.getLogger("agent.gui")

        base_dir = self._get_base_dir()
        setup_file_logging(base_dir / "log")
        load_dotenv(base_dir / ".env")
        config = self._load_config(base_dir)

        agent_cfg = _m(config.get("agent"))
        telegram_cfg = _m(config.get("telegram"))
        monitoring_cfg = _m(config.get("monitoring"))
        process_cfg = _m(config.get("target_process"))
        schedule_cfg = _m(config.get("schedule"))
        connection_cfg = _m(config.get("connection"))

        from agent import __version__ as agent_version

        agent_id = _s(agent_cfg.get("id"), "agent-unknown")
        agent_ver = agent_version

        self._root.after(0, lambda: self._agent_id_var.set(agent_id))
        self._root.after(0, lambda: self._version_var.set(agent_ver))

        process_args = _ls(process_cfg.get("args"), [])
        auto_restart = _b(process_cfg.get("auto_restart"), False)
        check_interval = _i(process_cfg.get("check_interval"), 30)
        restart_times = _ls(schedule_cfg.get("restart_times"), [])

        tcp_client = TCPClient(
            agent_id=agent_id,
            version=agent_ver,
            token=_s(connection_cfg.get("token"), ""),
            host=_s(connection_cfg.get("host"), "127.0.0.1"),
            port=_i(connection_cfg.get("port"), 9500),
            heartbeat_interval=_i(connection_cfg.get("heartbeat_interval"), 30),
            reconnect_attempts=_i(connection_cfg.get("reconnect_attempts"), 5),
            reconnect_delay=_i(connection_cfg.get("reconnect_delay"), 10),
        )
        process_mgr = ProcessManager(
            process_name=_s(process_cfg.get("name"), ""),
            process_path=_s(process_cfg.get("path"), ""),
            backup_dir=_s(process_cfg.get("backup_dir"), "./backups"),
        )
        deploy_handler = DeployHandler(
            tcp_client=tcp_client,
            process_mgr=process_mgr,
            transfer_dir=_s(
                monitoring_cfg.get("transfer_dir"), "agent/storage/transfers"
            ),
        )
        log_cmd_handler_ref: list[LogCmdHandler | None] = [None]

        def _build_log_sender() -> Callable[[str, str], Awaitable[None]]:
            async def _send(filename: str, line: str) -> None:
                if not tcp_client.is_connected:
                    return
                h = log_cmd_handler_ref[0]
                if h is None or not h.is_realtime_active:
                    return
                with contextlib.suppress(ConnectionError, OSError):
                    await tcp_client.send_log_line(filename, line)
                # GUI 로그 카운트 갱신
                count = int(self._log_count_var.get()) + 1
                self._root.after(0, lambda c=count: self._log_count_var.set(str(c)))

            return _send

        watcher = LogWatcher(
            watch_dirs=_ls(monitoring_cfg.get("log_folders"), []),
            extensions=_ls(monitoring_cfg.get("watch_extensions"), [".log"]),
            on_new_line=_build_log_sender(),  # type: ignore[arg-type]
        )
        poller = AgentTelegramPoller(
            bot_token=_s(telegram_cfg.get("bot_token"), ""),
            admin_chat_id=_i(telegram_cfg.get("admin_chat_id"), 0),
        )

        system_monitor = SystemMonitor(
            target_process_name=_s(process_cfg.get("name"), ""),
        )
        poller.system_monitor = system_monitor
        updater = SelfUpdater(install_dir=base_dir, is_service_mode=False)
        deploy_handler.updater = updater
        poller.updater = updater
        poller.log_watcher = watcher

        process_deployer = ProcessDeployer(
            process_mgr=process_mgr,
            update_dir=updater.update_dir,
            log_folders=_ls(monitoring_cfg.get("log_folders"), []),
        )
        poller.process_deployer = process_deployer
        poller.process_args = process_args or None

        # LLM
        llm_cfg = _m(config.get("llm"))
        openai_cfg = _m(llm_cfg.get("openai"))
        claude_cfg = _m(llm_cfg.get("claude"))
        openrouter_cfg = _m(llm_cfg.get("openrouter"))

        openai_key = _s(openai_cfg.get("api_key"), "") or _os.environ.get(
            "OPENAI_API_KEY", ""
        )
        claude_key = _s(claude_cfg.get("api_key"), "") or _os.environ.get(
            "ANTHROPIC_API_KEY", ""
        )
        openrouter_key = _s(openrouter_cfg.get("api_key"), "") or _os.environ.get(
            "OPENROUTER_API_KEY", ""
        )

        llm_router = LLMRouter(
            default_provider=_s(llm_cfg.get("default_provider"), "openai"),
            openai_api_key=openai_key,
            openai_model=_s(openai_cfg.get("model"), "gpt-4o-mini"),
            claude_api_key=claude_key,
            claude_model=_s(claude_cfg.get("model"), "claude-sonnet-4-20250514"),
            openrouter_api_key=openrouter_key,
            openrouter_model=_s(openrouter_cfg.get("model"), "openai/gpt-4o-mini"),
            system_prompt=_s(
                llm_cfg.get("system_prompt"),
                "당신은 산업용 소프트웨어 로그 분석 전문가입니다.",
            ),
            max_tokens=_i(llm_cfg.get("max_tokens"), 2000),
        )
        poller.llm_router = llm_router

        # 구독
        sub_cfg = _m(llm_cfg.get("subscription"))
        sub_server_url = _s(sub_cfg.get("server_url"), "")
        sub_key = _s(sub_cfg.get("key"), "")
        subscription_client: SubscriptionClient | None = None

        if sub_server_url:
            subscription_client = SubscriptionClient(
                server_url=sub_server_url,
                agent_id=agent_id,
                revalidate_hours=_i(sub_cfg.get("revalidate_hours"), 24),
            )
            poller.subscription_client = subscription_client
            auth_mode = _s(llm_cfg.get("auth_mode"), "apikey")
            if sub_key and auth_mode == "subscription":
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

        log_cmd_handler = LogCmdHandler(
            log_watcher=watcher,
            tcp_client=tcp_client,
            history_max_mb=_i(monitoring_cfg.get("history_max_mb"), 10),
        )
        log_cmd_handler_ref[0] = log_cmd_handler
        tcp_client.on_cmd_log = log_cmd_handler.handle_cmd_log
        tcp_client.on_log_file_select = log_cmd_handler.handle_file_select
        tcp_client.on_cmd_deploy = deploy_handler.handle_cmd_deploy
        tcp_client.on_file_chunk = deploy_handler.handle_file_chunk

        async def _connect(ip: str, port: int) -> None:
            tcp_client.host = ip
            tcp_client.port = port
            connected = await tcp_client.connect()
            if connected:
                logger.info("connected to server: %s:%s", ip, port)

        async def _disconnect() -> None:
            await tcp_client.disconnect()

        poller.on_connect = _connect
        poller.on_disconnect = _disconnect

        monitor_task: asyncio.Task[None] | None = None
        scheduler_task: asyncio.Task[None] | None = None

        logger.info("에이전트 시작: watcher + poller 초기화...")
        await watcher.start()
        await poller.start()

        # GUI 상태 갱신
        provider_name = (
            llm_router.current_provider if llm_router.is_available else "없음"
        )
        self._root.after(0, lambda: self._status_var.set("실행 중"))
        self._root.after(0, lambda: self._status_dot.configure(fg="#73c991"))
        self._root.after(0, lambda: self._provider_var.set(provider_name))

        try:
            await poller.send_message(
                f"Agent started (GUI mode).\nID: {agent_id}\nVersion: {agent_ver}"
            )
        except Exception:
            logger.warning("Telegram 시작 메시지 전송 실패")

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

        if restart_times:
            scheduler = ProcessScheduler(
                process_mgr=process_mgr,
                restart_times=restart_times,
                process_args=process_args or None,
                on_notify=_notify,
            )
            scheduler_task = asyncio.create_task(scheduler.run())

        # ── Recording: CMD_REC 명령으로 동적 시작/중지 ──
        recording_cfg = _m(config.get("recording"))
        _rec_watcher: RecordingWatcher | None = None  # noqa: F821
        _rec_watcher_task: asyncio.Task[None] | None = None

        async def _handle_cmd_rec(payload_data: bytes) -> None:
            nonlocal _rec_watcher, _rec_watcher_task

            from shared.protocol import (
                CmdRecAckPayload,
                CmdRecPayload,
                PacketType,
                RecAckStatus,
                RecAction,
            )

            try:
                cmd = CmdRecPayload.unpack(payload_data)
            except ValueError:
                logger.warning("invalid CMD_REC payload")
                return

            if cmd.action == RecAction.START:
                if _rec_watcher is not None:
                    logger.info("RecordingWatcher already running, ignoring START")
                    ack = CmdRecAckPayload(
                        action=RecAction.START, status=RecAckStatus.SUCCESS
                    )
                    await tcp_client.send_packet(PacketType.CMD_REC_ACK, ack.pack())
                    return

                watch_dir = _s(recording_cfg.get("watch_dir"), "")
                if not watch_dir:
                    logger.warning("recording.watch_dir not configured")
                    ack = CmdRecAckPayload(
                        action=RecAction.START, status=RecAckStatus.FAILED
                    )
                    await tcp_client.send_packet(PacketType.CMD_REC_ACK, ack.pack())
                    return

                from agent.recording.watcher import RecordingWatcher
                from agent.recording.models import AnalysisResult

                date_filter = cmd.date  # YYYYMMDD or ""

                async def _on_new_recording(
                    filename: str, filepath: str, result: AnalysisResult
                ) -> None:
                    logger.info(
                        "recording callback: filename=%s status=%s file=%s",
                        filename,
                        getattr(getattr(result, "status", None), "value", "?"),
                        filepath,
                    )
                    if not tcp_client.is_connected:
                        logger.warning("not connected — skipping filename=%s", filename)
                        return
                    from shared.protocol import (
                        PacketType as _PT,
                        RecAnalysisPayload,
                    )

                    payload = RecAnalysisPayload(
                        filename=filename,
                        status=getattr(
                            getattr(result, "status", None), "value", "EMPTY"
                        ),
                        left_rms_db=getattr(
                            getattr(result, "left", None), "rms_db", -96.0
                        ),
                        right_rms_db=getattr(
                            getattr(result, "right", None), "rms_db", -96.0
                        ),
                        left_silence_ratio=getattr(
                            getattr(result, "left", None), "silence_ratio", 1.0
                        ),
                        right_silence_ratio=getattr(
                            getattr(result, "right", None), "silence_ratio", 1.0
                        ),
                        dropout_count=getattr(result, "dropout_count", 0),
                        duration_wav=getattr(result, "duration_wav", 0.0),
                        duration_smdr=getattr(result, "duration_smdr", 0.0),
                        is_stereo=getattr(result, "is_stereo", False),
                    )
                    with contextlib.suppress(ConnectionError, OSError):
                        await tcp_client.send_packet(
                            _PT.REC_ANALYSIS_RESULT, payload.pack()
                        )
                    if _b(recording_cfg.get("alert_on_anomaly"), True):
                        status_val = getattr(
                            getattr(result, "status", None), "value", "?"
                        )
                        if status_val not in ("OK",):
                            with contextlib.suppress(Exception):
                                left_db = getattr(
                                    getattr(result, "left", None), "rms_db", -96.0
                                )
                                right_db = getattr(
                                    getattr(result, "right", None), "rms_db", -96.0
                                )
                                await poller.send_message(
                                    f"[녹취 이상] filename={filename}\n"
                                    f"상태: {status_val}\n"
                                    f"L: {left_db:.1f}dB / R: {right_db:.1f}dB"
                                )

                _rec_watcher = RecordingWatcher(
                    watch_dir=watch_dir,
                    extensions=_ls(recording_cfg.get("extensions"), [".wav"]),
                    on_new_recording=_on_new_recording,
                    date_filter=date_filter,
                )
                _rec_watcher_task = asyncio.create_task(_rec_watcher.start())
                logger.info(
                    "RecordingWatcher started: watch_dir=%s date_filter=%s",
                    watch_dir,
                    date_filter or "(all)",
                )

                ack = CmdRecAckPayload(
                    action=RecAction.START, status=RecAckStatus.SUCCESS
                )
                await tcp_client.send_packet(PacketType.CMD_REC_ACK, ack.pack())

            elif cmd.action == RecAction.STOP:
                if _rec_watcher is not None:
                    await _rec_watcher.stop()
                    _rec_watcher = None
                if _rec_watcher_task is not None:
                    _rec_watcher_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await _rec_watcher_task
                    _rec_watcher_task = None
                logger.info("RecordingWatcher stopped")

                ack = CmdRecAckPayload(
                    action=RecAction.STOP, status=RecAckStatus.SUCCESS
                )
                await tcp_client.send_packet(PacketType.CMD_REC_ACK, ack.pack())

        tcp_client.on_cmd_rec = _handle_cmd_rec

        # Upload handler (always register, works when watcher sends results)
        from agent.recording.uploader import RecordingUploader
        from shared.protocol import (
            PacketType,
            RecUploadAckPayload,
            RecUploadReqPayload,
        )

        _uploader = RecordingUploader(agent_id=agent_id)

        async def _handle_rec_upload_req(payload_data: bytes) -> None:
            req = RecUploadReqPayload.unpack(payload_data)
            watch_dir = _s(recording_cfg.get("watch_dir"), "")
            target: str | None = None
            from pathlib import Path as _Path

            for f in _Path(watch_dir).rglob("*.wav"):
                if f.name == req.filename:
                    target = str(f)
                    break
            if target is None:
                logger.warning("filename=%s not found in %s", req.filename, watch_dir)
                return
            filename_out, status, file_size = await _uploader.upload(
                filename=req.filename,
                filepath=target,
                upload_url=req.upload_url,
            )
            ack = RecUploadAckPayload(
                filename=filename_out, status=status, file_size=file_size
            )
            with contextlib.suppress(ConnectionError, OSError):
                await tcp_client.send_packet(PacketType.REC_UPLOAD_ACK, ack.pack())

        tcp_client.on_rec_upload_req = _handle_rec_upload_req

        async def _handle_stt_result(payload_data: bytes) -> None:
            from shared.protocol import SttResultPayload

            try:
                stt = SttResultPayload.unpack(payload_data)
            except (ValueError, KeyError):
                logger.warning("invalid STT_RESULT payload")
                return

            db_cfg = _m(recording_cfg.get("db"))
            if not db_cfg:
                logger.warning("recording.db not configured, cannot save STT result")
                return

            try:
                from agent.db.connection import get_connection
                from agent.db.helpers import insert_transcript, insert_call_quality

                conn = await asyncio.to_thread(get_connection, db_cfg)
                tid = await asyncio.to_thread(
                    insert_transcript,
                    conn,
                    stt.filename,
                    stt.full_text,
                    stt.agent_text,
                    stt.customer_text,
                    stt.segments_json,
                    stt.duration_sec,
                    stt.word_count,
                )
                await asyncio.to_thread(
                    insert_call_quality,
                    conn,
                    stt.filename,
                    tid,
                    stt.first_response_sec,
                    stt.agent_talk_ratio,
                    stt.customer_talk_ratio,
                    stt.silence_ratio,
                    stt.required_phrase_hit,
                    stt.required_phrase_list,
                    stt.forbidden_word_hit,
                    stt.forbidden_word_list,
                    stt.score_total,
                    stt.score_response,
                    stt.score_phrase,
                    stt.score_silence,
                )
                logger.info(
                    "STT result saved to DB: filename=%s transcript_id=%s",
                    stt.filename,
                    tid,
                )
            except Exception:
                logger.exception(
                    "Failed to save STT result to DB: filename=%s", stt.filename
                )

        tcp_client.on_stt_result = _handle_stt_result

        async def _handle_rec_data_req(payload_data: bytes) -> None:
            from shared.protocol import RecDataReqPayload, RecDataRespPayload

            try:
                req = RecDataReqPayload.unpack(payload_data)
            except (ValueError, KeyError):
                logger.warning("invalid REC_DATA_REQ payload")
                return

            db_cfg = _m(recording_cfg.get("db"))
            if not db_cfg:
                logger.warning("recording.db not configured, cannot query recordings")
                return

            try:
                from agent.db.connection import get_connection
                from agent.db.helpers import (
                    query_recordings_list,
                    query_recording_detail,
                )

                conn = await asyncio.to_thread(get_connection, db_cfg)

                if req.query_type == "detail":
                    row = await asyncio.to_thread(
                        query_recording_detail, conn, req.filename
                    )
                    records: list[dict[str, object]] = [row] if row else []
                else:
                    records = await asyncio.to_thread(
                        query_recordings_list, conn, req.date_str
                    )

                # Convert datetime objects to ISO string for JSON serialization
                import datetime as _dt

                serializable: list[dict[str, object]] = []
                for rec in records:
                    row_dict: dict[str, object] = {}
                    for k, v in rec.items():
                        if isinstance(v, _dt.datetime):
                            row_dict[k] = v.isoformat()
                        elif isinstance(v, bytes):
                            row_dict[k] = v.decode("utf-8", errors="replace")
                        else:
                            row_dict[k] = v
                    serializable.append(row_dict)

                resp = RecDataRespPayload(
                    query_type=req.query_type, records=serializable
                )
                with contextlib.suppress(ConnectionError, OSError):
                    await tcp_client.send_packet(PacketType.REC_DATA_RESP, resp.pack())
                logger.info(
                    "REC_DATA_RESP sent: query_type=%s records=%d",
                    req.query_type,
                    len(serializable),
                )
            except Exception:
                logger.exception("Failed to handle REC_DATA_REQ")

        tcp_client.on_rec_data_req = _handle_rec_data_req

        logger.info("에이전트 실행 중 — 대기 루프 진입")

        # config에 host/port가 설정되어 있으면 자동 접속 시도
        _auto_connect = bool(tcp_client.host and tcp_client.port)
        _reconnect_delay = max(
            tcp_client.reconnect_delay, 10
        )  # config.yaml 값 사용, 최소 10초
        _reconnect_counter = 0
        _first_reconnect = True  # 첫 재접속은 빠르게 (5초)

        if _auto_connect:
            logger.info("auto-connect: %s:%s ...", tcp_client.host, tcp_client.port)
            connected = await tcp_client.connect()
            if connected:
                logger.info("auto-connect: success")
                self._root.after(
                    0,
                    lambda: self._status_var.set("서버 접속됨"),
                )
            else:
                logger.warning("auto-connect: failed, will retry")

        # 에이전트 대기 루프
        try:
            while self._running:
                await asyncio.sleep(1)

                if tcp_client.is_connected:
                    _reconnect_counter = 0
                    _first_reconnect = True
                    # heartbeat (실패 시 즉시 연결 종료)
                    try:
                        await asyncio.wait_for(tcp_client.send_heartbeat(), timeout=5.0)
                    except (ConnectionError, OSError, asyncio.TimeoutError):
                        logger.warning("heartbeat failed — closing connection")
                        await tcp_client.close_on_error()
                elif _auto_connect:
                    # 자동 재접속: 첫 시도는 빠르게(5초), 이후 reconnect_delay 간격
                    _reconnect_counter += 1
                    _threshold = 5 if _first_reconnect else _reconnect_delay
                    if _reconnect_counter >= _threshold:
                        _reconnect_counter = 0
                        _first_reconnect = False
                        logger.info(
                            "reconnecting to %s:%s ...",
                            tcp_client.host,
                            tcp_client.port,
                        )
                        connected = await tcp_client.connect()
                        if connected:
                            logger.info("reconnected successfully")
                            _first_reconnect = True
                            self._root.after(
                                0,
                                lambda: self._status_var.set("서버 재접속됨"),
                            )
        finally:
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
            if _rec_watcher is not None:
                with contextlib.suppress(Exception):
                    await _rec_watcher.stop()
            with contextlib.suppress(Exception):
                await poller.stop()
            with contextlib.suppress(Exception):
                await watcher.stop()
            with contextlib.suppress(Exception):
                await tcp_client.disconnect()

            from agent.core.process_mgr import release_instance_lock

            release_instance_lock(base_dir)
            logger.info("에이전트 종료 완료")

    def _on_agent_stopped(self) -> None:
        self._status_var.set("정지됨")
        self._status_dot.configure(fg="#f44747")
        self._start_btn.configure(text="▶ 에이전트 시작", state=tk.NORMAL, bg="#0e639c")

    # ── 로그 뷰어 ──

    def _open_log_viewer(self) -> None:
        if self._log_viewer is not None:
            try:
                self._log_viewer.deiconify()
                self._log_viewer.lift()
                return
            except tk.TclError:
                self._log_viewer = None

        self._log_viewer = LogViewer(self._root, self._log_queue)

    # ── 업타임 ──

    def _update_uptime(self) -> None:
        if not self._running or self._start_time == 0:
            return
        import time

        elapsed = int(time.time() - self._start_time)
        h, rem = divmod(elapsed, 3600)
        m, s = divmod(rem, 60)
        self._uptime_var.set(f"{h:02d}:{m:02d}:{s:02d}")
        self._root.after(1000, self._update_uptime)

    def _update_agent_status(self) -> None:
        """에이전트 실행 중 상태 갱신."""
        if not self._running:
            return
        self._root.after(5000, self._update_agent_status)

    # ── 유틸 ──

    @staticmethod
    def _get_base_dir() -> Path:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parents[1]

    @staticmethod
    def _load_config(base_dir: Path) -> dict[str, object]:
        from shared.utils import load_yaml_config
        from typing import cast

        config_path = base_dir / "config.yaml"
        return cast(dict[str, object], load_yaml_config(str(config_path)))

    # ── 실행 ──

    def run(self) -> None:
        """tkinter 메인루프 시작. 에이전트 자동 시작."""
        self._root.after(100, self._start_agent)
        self._root.mainloop()


# ── 헬퍼 (타입 변환) ──


def _m(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        from typing import cast

        return {str(k): v for k, v in cast(dict[object, object], value).items()}
    return {}


def _s(value: object, default: str) -> str:
    return value if isinstance(value, str) else default


def _i(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    return default


def _b(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return default


def _ls(value: object, default: list[str]) -> list[str]:
    if not isinstance(value, list):
        return default
    return [str(x) for x in value if x]


def run_gui() -> None:
    """GUI 엔트리포인트."""
    app = AgentGUI()
    app.run()


if __name__ == "__main__":
    run_gui()
