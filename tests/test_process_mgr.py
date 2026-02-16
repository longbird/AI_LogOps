from __future__ import annotations

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest

from agent.core.process_mgr import ProcessManager


def test_find_pid_returns_pid_when_found(monkeypatch: pytest.MonkeyPatch) -> None:
    process = SimpleNamespace(info={"name": "target_app.exe", "pid": 4242})
    monkeypatch.setattr(psutil, "process_iter", lambda _attrs: [process])
    mgr = ProcessManager("target_app.exe", "C:/Apps/target_app.exe", "C:/Apps/backups")

    assert mgr.find_pid() == 4242


def test_find_pid_returns_none_when_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    process = SimpleNamespace(info={"name": "other.exe", "pid": 1111})
    monkeypatch.setattr(psutil, "process_iter", lambda _attrs: [process])
    mgr = ProcessManager("target_app.exe", "C:/Apps/target_app.exe", "C:/Apps/backups")

    assert mgr.find_pid() is None


def test_kill_terminates_process(monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = ProcessManager("target_app.exe", "C:/Apps/target_app.exe", "C:/Apps/backups")
    monkeypatch.setattr(mgr, "find_pid", lambda: 1234)

    class DummyProc:
        def __init__(self) -> None:
            self.terminated: bool = False
            self.wait_timeout: int | None = None

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, timeout: int) -> int:
            self.wait_timeout = timeout
            return 0

    proc = DummyProc()
    monkeypatch.setattr(psutil, "Process", lambda _pid: proc)

    assert mgr.kill() is True
    assert proc.terminated is True
    assert proc.wait_timeout == 10


def test_kill_returns_true_when_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = ProcessManager("target_app.exe", "C:/Apps/target_app.exe", "C:/Apps/backups")
    monkeypatch.setattr(mgr, "find_pid", lambda: None)

    assert mgr.kill() is True


def test_start_returns_pid(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "target_app.exe"
    _ = target.write_bytes(b"binary")

    popen_mock = Mock(return_value=SimpleNamespace(pid=9999))
    monkeypatch.setattr("subprocess.Popen", popen_mock)

    mgr = ProcessManager("target_app.exe", str(target), str(tmp_path / "backups"))

    assert mgr.start() == 9999


@pytest.mark.asyncio
async def test_health_check_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mgr = ProcessManager(
        "target_app.exe",
        str(tmp_path / "target_app.exe"),
        str(tmp_path / "backups"),
    )

    call_count = 0

    def mock_find_pid() -> int | None:
        nonlocal call_count
        call_count += 1
        return 12345 if call_count >= 2 else None

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(mgr, "find_pid", mock_find_pid)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    assert await mgr.health_check(timeout=1) is True


@pytest.mark.asyncio
async def test_health_check_timeout(tmp_path: Path) -> None:
    mgr = ProcessManager(
        "target_app.exe",
        str(tmp_path / "target_app.exe"),
        str(tmp_path / "backups"),
    )

    assert await mgr.health_check(timeout=0) is False


def test_backup_current_creates_copy(tmp_path: Path) -> None:
    target = tmp_path / "target_app.exe"
    _ = target.write_bytes(b"current-build")
    backup_dir = tmp_path / "backups"

    mgr = ProcessManager("target_app.exe", str(target), str(backup_dir))
    backup_path = Path(mgr.backup_current())

    assert backup_path.exists()
    assert backup_path.read_bytes() == b"current-build"
    assert backup_path.parent == backup_dir


def test_backup_current_raises_if_missing(tmp_path: Path) -> None:
    target = tmp_path / "missing.exe"
    mgr = ProcessManager("missing.exe", str(target), str(tmp_path / "backups"))

    with pytest.raises(FileNotFoundError):
        _ = mgr.backup_current()


def test_rollback_restores_latest_backup(tmp_path: Path) -> None:
    target = tmp_path / "target_app.exe"
    _ = target.write_bytes(b"broken-current")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    oldest = backup_dir / "target_app_20240101_010101.exe"
    latest = backup_dir / "target_app_20240101_020202.exe"
    _ = oldest.write_bytes(b"old-version")
    _ = latest.write_bytes(b"new-version")

    mgr = ProcessManager("target_app.exe", str(target), str(backup_dir))

    assert mgr.rollback() is True
    assert target.read_bytes() == b"new-version"


def test_rollback_returns_false_if_no_backups(tmp_path: Path) -> None:
    target = tmp_path / "target_app.exe"
    _ = target.write_bytes(b"current")
    backup_dir = tmp_path / "backups"

    mgr = ProcessManager("target_app.exe", str(target), str(backup_dir))

    assert mgr.rollback() is False


def test_cleanup_old_backups(tmp_path: Path) -> None:
    target = tmp_path / "target_app.exe"
    _ = target.write_bytes(b"current")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    for idx in range(1, 8):
        backup = backup_dir / f"target_app_20240101_00000{idx}.exe"
        _ = backup.write_bytes(f"backup-{idx}".encode("utf-8"))

    mgr = ProcessManager("target_app.exe", str(target), str(backup_dir))
    removed = mgr.cleanup_old_backups(max_backups=5)

    assert removed == 2
    assert mgr.get_backup_count() == 5
