from __future__ import annotations

# pyright: basic, reportMissingImports=false, reportAttributeAccessIssue=false

import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _install_pywin32_stubs(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {
        "event_set_count": 0,
        "report_status_calls": [],
        "event_wait_calls": [],
        "handle_cls": None,
        "subprocess_calls": [],
    }

    class _ServiceFramework:
        def __init__(self, args: object):
            self.args = args

        def ReportServiceStatus(self, status: int) -> None:
            state["report_status_calls"].append(status)

    win32serviceutil = ModuleType("win32serviceutil")
    win32serviceutil.ServiceFramework = _ServiceFramework

    def _handle_command_line(service_cls: type[object]) -> None:
        state["handle_cls"] = service_cls

    win32serviceutil.HandleCommandLine = _handle_command_line

    win32service = ModuleType("win32service")
    win32service.SERVICE_STOP_PENDING = 3
    win32service.SERVICE_AUTO_START = 2

    win32event = ModuleType("win32event")

    def _create_event(
        security_attributes: object,
        manual_reset: bool,
        initial_state: bool,
        name: object,
    ) -> object:
        del security_attributes, manual_reset, initial_state, name
        return object()

    def _set_event(handle: object) -> None:
        del handle
        state["event_set_count"] += 1

    def _wait_for_single_object(handle: object, timeout_ms: int) -> int:
        del handle
        state["event_wait_calls"].append(timeout_ms)
        return 0

    win32event.CreateEvent = _create_event
    win32event.SetEvent = _set_event
    win32event.WaitForSingleObject = _wait_for_single_object

    servicemanager = ModuleType("servicemanager")
    servicemanager.EVENTLOG_INFORMATION_TYPE = 0x0004
    servicemanager.PYS_SERVICE_STARTED = 0x1000
    servicemanager.PYS_SERVICE_STOPPED = 0x1001
    servicemanager.LogMsg = lambda *args, **kwargs: None

    monkeypatch.setitem(sys.modules, "win32serviceutil", win32serviceutil)
    monkeypatch.setitem(sys.modules, "win32service", win32service)
    monkeypatch.setitem(sys.modules, "win32event", win32event)
    monkeypatch.setitem(sys.modules, "servicemanager", servicemanager)

    return state


@pytest.fixture
def win_service_module(monkeypatch: pytest.MonkeyPatch):
    state = _install_pywin32_stubs(monkeypatch)
    monkeypatch.syspath_prepend(str(PROJECT_ROOT))
    sys.modules.pop("agent.service.win_service", None)
    module = importlib.import_module("agent.service.win_service")
    return module, state


def test_service_metadata(
    win_service_module: tuple[ModuleType, dict[str, Any]],
) -> None:
    module, _ = win_service_module
    service_cls = module.AILogOpsAgentService

    assert service_cls._svc_name_ == "AILogOps-Agent"
    assert service_cls._svc_display_name_ == "AI-LogOps Agent Service"
    assert (
        service_cls._svc_description_
        == "AI-LogOps 원격 로그 분석 및 자동 배포 에이전트"
    )
    assert service_cls._svc_start_type_ == module.win32service.SERVICE_AUTO_START


def test_svc_stop_sets_stop_event(
    win_service_module: tuple[ModuleType, dict[str, Any]],
) -> None:
    module, state = win_service_module
    service = module.AILogOpsAgentService(args=[])

    service.SvcStop()

    assert state["report_status_calls"] == [module.win32service.SERVICE_STOP_PENDING]
    assert state["event_set_count"] == 1
    assert service._stop_requested.is_set()


def test_svc_do_run_creates_loop_and_runs_agent(
    win_service_module: tuple[ModuleType, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, _ = win_service_module
    service = module.AILogOpsAgentService(args=[])
    captured: list[str] = []

    async def _fake_run_agent() -> None:
        captured.append("run")

    monkeypatch.setattr(service, "_run_agent", _fake_run_agent)

    service.SvcDoRun()

    assert captured == ["run"]
    assert service._loop is not None
    assert service._loop.is_closed()


def test_configure_failure_actions_executes_sc_command(
    win_service_module: tuple[ModuleType, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, state = win_service_module

    def _fake_run(cmd: list[str], check: bool, capture_output: bool) -> SimpleNamespace:
        state["subprocess_calls"].append((cmd, check, capture_output))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", _fake_run)

    module.configure_failure_actions("AILogOps-Agent")

    assert state["subprocess_calls"] == [
        (
            [
                "sc",
                "failure",
                "AILogOps-Agent",
                "reset=",
                "86400",
                "actions=",
                "restart/60000/restart/60000/restart/60000",
            ],
            True,
            True,
        )
    ]


def test_main_routes_to_handle_command_line_and_recovery(
    win_service_module: tuple[ModuleType, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, state = win_service_module
    recovery_calls: list[str] = []

    monkeypatch.setattr(module, "configure_failure_actions", recovery_calls.append)

    module.main(["win_service.py", "install"])

    assert state["handle_cls"] is module.AILogOpsAgentService
    assert recovery_calls == ["AILogOps-Agent"]


def test_to_bool_helper(win_service_module: tuple[ModuleType, dict[str, Any]]) -> None:
    module, _ = win_service_module

    assert module._to_bool(True, False) is True
    assert module._to_bool(False, True) is False
    assert module._to_bool("true", False) is True
    assert module._to_bool("yes", False) is True
    assert module._to_bool("false", True) is False
    assert module._to_bool(None, True) is True
    assert module._to_bool(42, False) is False
