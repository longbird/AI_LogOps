from __future__ import annotations

# pyright: reportMissingImports=false, reportUnknownMemberType=false, reportPrivateUsage=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false

import asyncio
import contextlib
import logging
import subprocess
import sys
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, cast

if TYPE_CHECKING:
    from agent.recording.watcher import RecordingWatcher

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
from shared.protocol import PacketType
from shared.utils import (
    load_dotenv,
    load_yaml_config,
    setup_file_logging,
    setup_logging,
)

try:
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil
except ImportError as exc:  # pragma: no cover - exercised via stubs in tests
    raise RuntimeError("pywin32 is required to run Windows service mode") from exc


class AILogOpsAgentService(win32serviceutil.ServiceFramework):
    _svc_name_: ClassVar[str] = "AILogOps-Agent"
    _svc_display_name_: ClassVar[str] = "AI-LogOps Agent Service"
    _svc_description_: ClassVar[str] = "AI-LogOps 원격 로그 분석 및 자동 배포 에이전트"
    _svc_start_type_: ClassVar[int] = win32service.SERVICE_AUTO_START
    _is_service_mode: ClassVar[bool] = False

    def __init__(self, args: list[str]):
        super().__init__(args)
        setup_file_logging(_get_base_dir() / "log")
        self._logger: logging.Logger = setup_logging(self.__class__.__name__)
        self._stop_handle: int = win32event.CreateEvent(None, False, False, None)
        self._stop_requested: threading.Event = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    def SvcStop(self) -> None:
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self._stop_requested.set()
        win32event.SetEvent(self._stop_handle)

        loop = self._loop
        if loop is not None and loop.is_running():
            _ = loop.call_soon_threadsafe(lambda: None)

    def SvcDoRun(self) -> None:
        self.ReportServiceStatus(win32service.SERVICE_RUNNING)
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""),
        )

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run_agent())
        except Exception:
            self._logger.exception("FATAL: _run_agent() crashed")
        finally:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                _ = task.cancel()
            if pending:
                _ = self._loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            self._loop.close()
            asyncio.set_event_loop(None)

            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STOPPED,
                (self._svc_name_, ""),
            )

    async def _run_agent(self) -> None:
        logger = self._logger

        # 중복 인스턴스 방지
        from agent.core.process_mgr import acquire_instance_lock

        base_dir = _get_base_dir()
        if not acquire_instance_lock(base_dir):
            logger.error("another agent instance is already running. exiting.")
            return

        logger.info(">>> _run_agent: loading .env + config...")
        load_dotenv(base_dir / ".env")
        config = _load_agent_config()
        logger.info(">>> config loaded: %s", list(config.keys()))

        agent_cfg = _as_mapping(config.get("agent"))
        telegram_cfg = _as_mapping(config.get("telegram"))
        monitoring_cfg = _as_mapping(config.get("monitoring"))
        process_cfg = _as_mapping(config.get("target_process"))
        schedule_cfg = _as_mapping(config.get("schedule"))
        connection_cfg = _as_mapping(config.get("connection"))

        process_args = _to_list_str(process_cfg.get("args"), [])
        auto_restart = _to_bool(process_cfg.get("auto_restart"), False)
        check_interval = _to_int(process_cfg.get("check_interval"), 30)
        restart_times = _to_list_str(schedule_cfg.get("restart_times"), [])

        from agent import __version__ as agent_version

        tcp_client = TCPClient(
            agent_id=_to_str(agent_cfg.get("id"), "agent-unknown"),
            version=agent_version,
            token=_to_str(connection_cfg.get("token"), ""),
            host=_to_str(connection_cfg.get("host"), "127.0.0.1"),
            port=_to_int(connection_cfg.get("port"), 9500),
            heartbeat_interval=_to_int(connection_cfg.get("heartbeat_interval"), 30),
            reconnect_attempts=_to_int(connection_cfg.get("reconnect_attempts"), 5),
            reconnect_delay=_to_int(connection_cfg.get("reconnect_delay"), 10),
        )
        process_mgr = ProcessManager(
            process_name=_to_str(process_cfg.get("name"), ""),
            process_path=_to_str(process_cfg.get("path"), ""),
            backup_dir=_to_str(process_cfg.get("backup_dir"), "./backups"),
        )
        deploy_handler = DeployHandler(
            tcp_client=tcp_client,
            process_mgr=process_mgr,
            transfer_dir=_to_str(
                monitoring_cfg.get("transfer_dir"),
                "agent/storage/transfers",
            ),
        )
        log_cmd_handler_ref: list[LogCmdHandler | None] = [None]
        watcher = LogWatcher(
            watch_dirs=_to_list_str(monitoring_cfg.get("log_folders"), []),
            extensions=_to_list_str(monitoring_cfg.get("watch_extensions"), [".log"]),
            on_new_line=self._build_log_sender(tcp_client, log_cmd_handler_ref),
        )
        bot_token_val = _to_str(telegram_cfg.get("bot_token"), "")
        admin_chat_id_val = _to_int(telegram_cfg.get("admin_chat_id"), 0)
        logger.info(
            ">>> telegram config: token=%s...%s, admin_chat_id=%s",
            bot_token_val[:8] if len(bot_token_val) > 8 else "(empty)",
            bot_token_val[-4:] if len(bot_token_val) > 4 else "",
            admin_chat_id_val,
        )
        poller = AgentTelegramPoller(
            bot_token=bot_token_val,
            admin_chat_id=admin_chat_id_val,
        )

        system_monitor = SystemMonitor(
            target_process_name=_to_str(process_cfg.get("name"), ""),
        )
        poller.system_monitor = system_monitor

        updater = SelfUpdater(
            install_dir=_get_base_dir(),
            is_service_mode=self.__class__._is_service_mode,
        )
        deploy_handler.updater = updater
        poller.updater = updater
        poller.log_watcher = watcher

        process_deployer = ProcessDeployer(
            process_mgr=process_mgr,
            update_dir=updater.update_dir,
            log_folders=_to_list_str(monitoring_cfg.get("log_folders"), []),
        )
        poller.process_deployer = process_deployer
        poller.process_args = process_args or None

        import os as _os

        llm_cfg = _as_mapping(config.get("llm"))
        auth_mode = _to_str(llm_cfg.get("auth_mode"), "apikey")
        openai_cfg = _as_mapping(llm_cfg.get("openai"))
        claude_cfg = _as_mapping(llm_cfg.get("claude"))
        openrouter_cfg = _as_mapping(llm_cfg.get("openrouter"))

        # config.yaml 값 → 비어있으면 환경변수 폴백
        openai_key = _to_str(openai_cfg.get("api_key"), "") or _os.environ.get(
            "OPENAI_API_KEY", ""
        )
        claude_key = _to_str(claude_cfg.get("api_key"), "") or _os.environ.get(
            "ANTHROPIC_API_KEY", ""
        )
        openrouter_key = _to_str(openrouter_cfg.get("api_key"), "") or _os.environ.get(
            "OPENROUTER_API_KEY", ""
        )

        llm_router = LLMRouter(
            default_provider=_to_str(llm_cfg.get("default_provider"), "openai"),
            openai_api_key=openai_key,
            openai_model=_to_str(openai_cfg.get("model"), "gpt-4o-mini"),
            claude_api_key=claude_key,
            claude_model=_to_str(claude_cfg.get("model"), "claude-sonnet-4-20250514"),
            openrouter_api_key=openrouter_key,
            openrouter_model=_to_str(openrouter_cfg.get("model"), "openai/gpt-4o-mini"),
            system_prompt=_to_str(
                llm_cfg.get("system_prompt"),
                "당신은 산업용 소프트웨어 로그 분석 전문가입니다. 한국어로 답변하세요.",
            ),
            max_tokens=_to_int(llm_cfg.get("max_tokens"), 2000),
        )
        poller.llm_router = llm_router

        # ── 구독 인증 클라이언트 설정 ──
        sub_cfg = _as_mapping(llm_cfg.get("subscription"))
        sub_server_url = _to_str(sub_cfg.get("server_url"), "")
        sub_key = _to_str(sub_cfg.get("key"), "")
        sub_revalidate_hours = _to_int(sub_cfg.get("revalidate_hours"), 24)
        subscription_client: SubscriptionClient | None = None

        if sub_server_url:
            agent_id = _to_str(agent_cfg.get("id"), "agent-unknown")
            subscription_client = SubscriptionClient(
                server_url=sub_server_url,
                agent_id=agent_id,
                revalidate_hours=sub_revalidate_hours,
            )
            poller.subscription_client = subscription_client

            # config에 구독 키가 있으면 자동 인증 시도
            if sub_key and auth_mode == "subscription":
                logger.info(">>> auto-validating subscription key from config...")
                sub_result = await subscription_client.validate(sub_key)
                if sub_result.valid:
                    loaded = llm_router.apply_subscription(
                        openai_api_key=sub_result.openai_api_key,
                        openai_model=sub_result.openai_model,
                        claude_api_key=sub_result.claude_api_key,
                        claude_model=sub_result.claude_model,
                        system_prompt=sub_result.system_prompt,
                        max_tokens=sub_result.max_tokens,
                    )
                    subscription_client.start_revalidation()
                    logger.info(">>> subscription auto-auth OK: providers=%s", loaded)
                else:
                    logger.warning(
                        ">>> subscription auto-auth failed: %s", sub_result.message
                    )

        logger.info(
            ">>> LLM router: auth_mode=%s, providers=%s, default=%s",
            auth_mode,
            llm_router.available_providers,
            llm_router.current_provider,
        )

        log_cmd_handler = LogCmdHandler(
            log_watcher=watcher,
            tcp_client=tcp_client,
            history_max_mb=_to_int(monitoring_cfg.get("history_max_mb"), 10),
        )
        log_cmd_handler_ref[0] = log_cmd_handler
        tcp_client.on_cmd_log = log_cmd_handler.handle_cmd_log
        tcp_client.on_log_file_select = log_cmd_handler.handle_file_select

        tcp_client.on_cmd_deploy = deploy_handler.handle_cmd_deploy
        tcp_client.on_file_chunk = deploy_handler.handle_file_chunk

        poller.on_connect = self._build_connect_handler(
            tcp_client=tcp_client,
        )
        poller.on_disconnect = self._build_disconnect_handler(tcp_client)

        monitor_loop: ProcessMonitorLoop | None = None
        monitor_task: asyncio.Task[None] | None = None
        scheduler: ProcessScheduler | None = None
        scheduler_task: asyncio.Task[None] | None = None

        logger.info(">>> starting watcher...")
        await watcher.start()
        logger.info(">>> watcher started. starting poller...")
        await poller.start()
        logger.info(">>> poller started.")

        agent_id = _to_str(agent_cfg.get("id"), "agent-unknown")
        agent_ver = agent_version
        watch_dirs_cfg = _to_list_str(monitoring_cfg.get("log_folders"), [])
        watch_ext_cfg = {
            e.lower()
            for e in _to_list_str(monitoring_cfg.get("watch_extensions"), [".log"])
        }
        watch_summary = _build_watch_summary(watch_dirs_cfg, watch_ext_cfg)
        try:
            await poller.send_message(
                f"Agent started.\nID: {agent_id}\nVersion: {agent_ver}\n{watch_summary}"
            )
            logger.info(">>> startup message sent to Telegram")
        except Exception:
            logger.exception(">>> failed to send startup message")

        async def _notify(message: str) -> None:
            with contextlib.suppress(Exception):
                await poller.send_message(message)

        if auto_restart:
            current_monitor_loop = ProcessMonitorLoop(
                process_mgr=process_mgr,
                check_interval=float(check_interval),
                process_args=process_args or None,
                on_notify=_notify,
                enabled=True,
            )
            monitor_loop = current_monitor_loop
            monitor_task = asyncio.create_task(current_monitor_loop.run())

        if restart_times:
            current_scheduler = ProcessScheduler(
                process_mgr=process_mgr,
                restart_times=restart_times,
                process_args=process_args or None,
                on_notify=_notify,
            )
            scheduler = current_scheduler
            scheduler_task = asyncio.create_task(current_scheduler.run())

        process_watch_task = asyncio.create_task(
            self._process_watch_loop(process_mgr, _notify)
        )

        # ── Recording: CMD_REC 명령으로 동적 시작/중지 ──
        recording_cfg = _as_mapping(config.get("recording"))
        _rec_watcher: RecordingWatcher | None = None
        _rec_watcher_task: asyncio.Task[None] | None = None

        async def _handle_cmd_rec(payload_data: bytes) -> None:
            nonlocal _rec_watcher, _rec_watcher_task

            from shared.protocol import (
                CmdRecAckPayload,
                CmdRecPayload,
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

                watch_dir = _to_str(recording_cfg.get("watch_dir"), "")
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
                    rec_no: int, filepath: str, result: AnalysisResult
                ) -> None:
                    if not tcp_client.is_connected:
                        return
                    from shared.protocol import RecAnalysisPayload

                    payload = RecAnalysisPayload(
                        rec_no=rec_no,
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
                            PacketType.REC_ANALYSIS_RESULT, payload.pack()
                        )
                    if _to_bool(recording_cfg.get("alert_on_anomaly"), True):
                        anomaly_count = getattr(result, "anomalies", 0)
                        if anomaly_count > 0:
                            msg = (
                                f"Recording anomaly detected\n"
                                f"rec_no={rec_no}\n"
                                f"anomalies={anomaly_count}\n"
                                f"file={filepath}"
                            )
                            with contextlib.suppress(Exception):
                                await poller.send_message(msg)

                _rec_watcher = RecordingWatcher(
                    watch_dir=watch_dir,
                    extensions=_to_list_str(recording_cfg.get("extensions"), [".wav"]),
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

        _uploader = RecordingUploader(
            agent_id=_to_str(agent_cfg.get("id"), "agent-unknown"),
        )

        async def _handle_rec_upload_req(payload_data: bytes) -> None:
            from shared.protocol import RecUploadReqPayload, RecUploadAckPayload

            req = RecUploadReqPayload.unpack(payload_data)
            watch_dir = _to_str(recording_cfg.get("watch_dir"), "")
            target: str | None = None
            from pathlib import Path as _Path

            for f in _Path(watch_dir).rglob("*.wav"):
                try:
                    if int(f.stem) == req.rec_no:
                        target = str(f)
                        break
                except ValueError:
                    continue
            if target is None:
                logger.warning("rec_no=%s not found in %s", req.rec_no, watch_dir)
                return
            rec_no, status, file_size = await _uploader.upload(
                rec_no=req.rec_no,
                filepath=target,
                upload_url=req.upload_url,
            )
            ack = RecUploadAckPayload(rec_no=rec_no, status=status, file_size=file_size)
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

            db_cfg = _as_mapping(recording_cfg.get("db"))
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
                    stt.rec_no,
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
                    stt.rec_no,
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
                    "STT result saved to DB: rec_no=%s transcript_id=%s",
                    stt.rec_no,
                    tid,
                )
            except Exception:
                logger.exception(
                    "Failed to save STT result to DB: rec_no=%s", stt.rec_no
                )

        tcp_client.on_stt_result = _handle_stt_result

        async def _handle_rec_data_req(payload_data: bytes) -> None:
            from shared.protocol import RecDataReqPayload, RecDataRespPayload

            try:
                req = RecDataReqPayload.unpack(payload_data)
            except (ValueError, KeyError):
                logger.warning("invalid REC_DATA_REQ payload")
                return

            db_cfg = _as_mapping(recording_cfg.get("db"))
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
                        query_recording_detail, conn, req.rec_no
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

        logger.info(
            ">>> entering heartbeat_loop (stop_requested=%s)",
            self._stop_requested.is_set(),
        )
        try:
            await self._heartbeat_loop(tcp_client)
            logger.info(">>> heartbeat_loop exited normally")
        except Exception:
            logger.exception(">>> heartbeat_loop crashed")
        finally:
            _ = process_watch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await process_watch_task
            if monitor_loop is not None:
                monitor_loop.stop()
            if scheduler is not None:
                scheduler.stop()
            if monitor_task is not None:
                _ = monitor_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await monitor_task
            if scheduler_task is not None:
                _ = scheduler_task.cancel()
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
            logger.info("agent service loop ended")

    def _build_log_sender(
        self,
        tcp_client: TCPClient,
        log_cmd_handler_ref: list[LogCmdHandler | None],
    ) -> Callable[[str, str], Awaitable[None]]:
        async def _send_log(filename: str, line: str) -> None:
            if not tcp_client.is_connected:
                return
            log_cmd_handler = log_cmd_handler_ref[0]
            if log_cmd_handler is None or not log_cmd_handler.is_realtime_active:
                return
            with contextlib.suppress(ConnectionError, OSError):
                await tcp_client.send_log_line(filename, line)

        return _send_log

    def _build_connect_handler(
        self,
        tcp_client: TCPClient,
    ) -> Callable[[str, int], Awaitable[None]]:
        async def _connect(ip: str, port: int) -> None:
            tcp_client.host = ip
            tcp_client.port = port
            connected = await tcp_client.connect()
            if not connected:
                self._logger.warning("connect command failed: %s:%s", ip, port)
                return
            self._logger.info("connected to server: %s:%s", ip, port)

        return _connect

    def _build_disconnect_handler(
        self,
        tcp_client: TCPClient,
    ) -> Callable[[], Awaitable[None]]:
        async def _disconnect() -> None:
            await tcp_client.disconnect()

        return _disconnect

    async def _process_watch_loop(
        self,
        process_mgr: ProcessManager,
        notify: Callable[[str], Awaitable[None]],
        interval: int = 600,
    ) -> None:
        """10분마다 대상 프로세스 실행 여부를 확인하고 미실행 시 알린다."""
        while True:
            await asyncio.sleep(interval)
            pid = process_mgr.find_pid()
            if pid is None:
                await notify(
                    f"[Process Watch] {process_mgr.process_name} is NOT running."
                )

    async def _heartbeat_loop(self, tcp_client: TCPClient) -> None:
        interval = max(1, tcp_client.heartbeat_interval)
        next_heartbeat = 0.0
        loop = asyncio.get_running_loop()
        iteration = 0

        # ── 자동 접속 / 재접속 ──
        _auto_connect = bool(tcp_client.host and tcp_client.port)
        _reconnect_delay = max(
            tcp_client.reconnect_delay, 10
        )  # config.yaml 값 사용, 최소 10초
        _reconnect_counter = 0
        _first_reconnect = True  # 첫 재접속은 빠르게 (5초)

        if _auto_connect:
            self._logger.info(
                "auto-connect: %s:%s ...", tcp_client.host, tcp_client.port
            )
            connected = await tcp_client.connect()
            if connected:
                self._logger.info("auto-connect: success")
            else:
                self._logger.warning("auto-connect: failed, will retry")

        while not self._stop_requested.is_set():
            iteration += 1
            wait_result = await asyncio.to_thread(
                win32event.WaitForSingleObject,
                self._stop_handle,
                1000,
            )
            if wait_result == 0:
                self._logger.info(">>> stop event signaled at iter=%d", iteration)
                break

            now = loop.time()
            if now < next_heartbeat:
                continue
            next_heartbeat = now + interval

            if tcp_client.is_connected:
                _reconnect_counter = 0
                _first_reconnect = True
                # heartbeat (실패 시 즉시 연결 종료)
                try:
                    await asyncio.wait_for(tcp_client.send_heartbeat(), timeout=5.0)
                except (ConnectionError, OSError, asyncio.TimeoutError):
                    self._logger.warning("heartbeat failed — closing connection")
                    await tcp_client.close_on_error()
            elif _auto_connect:
                # 자동 재접속: 첫 시도는 빠르게(5초), 이후 reconnect_delay 간격
                _reconnect_counter += 1
                _threshold = 5 if _first_reconnect else _reconnect_delay
                if _reconnect_counter >= _threshold:
                    _reconnect_counter = 0
                    _first_reconnect = False
                    self._logger.info(
                        "reconnecting to %s:%s ...",
                        tcp_client.host,
                        tcp_client.port,
                    )
                    connected = await tcp_client.connect()
                    if connected:
                        self._logger.info("reconnected successfully")
                        _first_reconnect = True


def _read_last_line_of(filepath: str) -> str:
    """파일의 마지막 줄을 읽는다. 최대 200자."""
    try:
        p = Path(filepath)
        with p.open("rb") as f:
            _ = f.seek(0, 2)
            pos = f.tell()
            if pos == 0:
                return "(empty)"
            buf = min(pos, 4096)
            _ = f.seek(pos - buf)
            data = f.read(buf)
            lines = data.split(b"\n")
            last = lines[-1] if lines[-1] else (lines[-2] if len(lines) > 1 else b"")
            text = last.decode("utf-8", errors="replace").strip()
            return text[:200] if text else "(empty)"
    except OSError:
        return "(read error)"


def _build_watch_summary(watch_dirs: list[str], extensions: set[str]) -> str:
    """감시 폴더별 마지막 파일의 마지막 줄 요약을 생성한다."""
    if not watch_dirs:
        return "No watched folders."
    parts: list[str] = []
    for watch_dir in watch_dirs:
        dir_path = Path(watch_dir)
        dir_label = str(dir_path)
        if not dir_path.is_dir():
            parts.append(f"[{dir_label}] (folder not found)")
            continue
        # 해당 폴더에서 확장자 매칭 파일을 이름순 정렬 → 마지막 = 최신
        files = sorted(
            f
            for f in dir_path.rglob("*")
            if f.is_file() and f.suffix.lower() in extensions
        )
        if not files:
            parts.append(f"[{dir_label}] (no log files)")
            continue
        latest = files[-1]
        last_line = _read_last_line_of(str(latest))
        parts.append(f"[{dir_label}] {latest.name}\n  {last_line}")
    return "\n".join(parts)


def _as_mapping(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        result: dict[str, object] = {}
        for key, item in mapping.items():
            result[str(key)] = item
        return result
    return {}


def _get_base_dir() -> Path:
    """Get base directory for development and frozen execution."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _load_agent_config() -> dict[str, object]:
    config_path = _get_base_dir() / "config.yaml"
    return cast(dict[str, object], load_yaml_config(str(config_path)))


def _to_str(value: object, default: str) -> str:
    if isinstance(value, str):
        return value
    return default


def _to_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            return int(value)
    return default


def _to_list_str(value: object, default: list[str]) -> list[str]:
    if not isinstance(value, list):
        return default

    items = cast(list[object], value)
    result: list[str] = []
    for item in items:
        text = item if isinstance(item, str) else str(item)
        if text:
            result.append(text)
    return result


def _to_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return default


def configure_failure_actions(service_name: str) -> None:
    command = [
        "sc",
        "failure",
        service_name,
        "reset=",
        "86400",
        "actions=",
        "restart/60000/restart/60000/restart/60000",
    ]
    logger = setup_logging("win_service")
    try:
        _ = subprocess.run(command, check=True, capture_output=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        logger.warning("failed to configure recovery options: %s", exc)


def _launch_gui() -> None:
    """콘솔 창을 숨기고 GUI를 시작한다."""
    import ctypes

    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE

    from agent.gui.app import run_gui

    run_gui()


def main(argv: list[str] | None = None) -> None:
    args = sys.argv if argv is None else argv

    if len(args) == 1:
        # SCM이 인수 없이 실행 → 서비스 디스패처 시도
        # 실패 시(더블클릭 등 비-SCM 컨텍스트) → GUI 모드 폴백
        try:
            AILogOpsAgentService._is_service_mode = True
            servicemanager.Initialize()
            servicemanager.PrepareToHostSingle(AILogOpsAgentService)
            servicemanager.StartServiceCtrlDispatcher()
        except Exception:
            AILogOpsAgentService._is_service_mode = False
            _launch_gui()
    else:
        cmd = args[1].lower()
        if cmd == "gui":
            _launch_gui()
            return

        # 커맨드라인: install / start / stop / remove / debug
        win32serviceutil.HandleCommandLine(AILogOpsAgentService)
        if cmd == "install":
            configure_failure_actions(AILogOpsAgentService._svc_name_)


if __name__ == "__main__":
    main()
