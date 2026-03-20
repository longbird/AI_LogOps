from __future__ import annotations

# pyright: reportMissingImports=false, reportUnknownMemberType=false, reportPrivateUsage=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false

import asyncio
import contextlib
import logging
import subprocess
import sys
import threading
from pathlib import Path
from typing import ClassVar

from agent.core.agent_runtime import AgentHooks, AgentRuntime
from shared.utils import setup_file_logging, setup_logging

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
    _svc_description_: ClassVar[str] = "AI-LogOps ?먭꺽 濡쒓렇 遺꾩꽍 諛??먮룞 諛고룷 ?먯씠?꾪듃"
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
        stop_event = asyncio.Event()

        async def _wait_for_service_stop() -> None:
            while not self._stop_requested.is_set():
                result = await asyncio.to_thread(
                    win32event.WaitForSingleObject,
                    self._stop_handle,
                    1000,
                )
                if result == 0:
                    stop_event.set()
                    return

        hooks = AgentHooks(is_service_mode=True)
        runtime = AgentRuntime(
            base_dir=_get_base_dir(),
            stop_event=stop_event,
            hooks=hooks,
        )

        stop_task = asyncio.create_task(_wait_for_service_stop())
        try:
            await runtime.run()
        finally:
            _ = stop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stop_task


def _get_base_dir() -> Path:
    """Get base directory for development and frozen execution."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


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
    """肄섏넄 李쎌쓣 ?④린怨?GUI瑜??쒖옉?쒕떎."""
    import ctypes

    hwnd = cast(int, ctypes.windll.kernel32.GetConsoleWindow())
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE

    from agent.gui.app import run_gui

    run_gui()


def main(argv: list[str] | None = None) -> None:
    args = sys.argv if argv is None else argv

    if len(args) == 1:
        # SCM???몄닔 ?놁씠 ?ㅽ뻾 ???쒕퉬???붿뒪?⑥쿂 ?쒕룄
        # ?ㅽ뙣 ???붾툝?대┃ ??鍮?SCM 而⑦뀓?ㅽ듃) ??GUI 紐⑤뱶 ?대갚
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

        # 而ㅻ㎤?쒕씪?? install / start / stop / remove / debug
        win32serviceutil.HandleCommandLine(AILogOpsAgentService)
        if cmd == "install":
            configure_failure_actions(AILogOpsAgentService._svc_name_)


if __name__ == "__main__":
    main()

