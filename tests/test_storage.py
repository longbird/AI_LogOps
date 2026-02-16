from __future__ import annotations

import os
import time
from pathlib import Path

from server.storage.manager import StorageManager


def test_save_log_history(tmp_path: Path) -> None:
    manager = StorageManager(base_dir=str(tmp_path))

    saved_path = manager.save_log_history(
        agent_id="agent-a",
        filename="app.log",
        data="line1\nline2\n".encode("utf-8"),
    )

    path = Path(saved_path)
    assert path.exists()
    assert path.read_bytes() == b"line1\nline2\n"


def test_append_realtime_log(tmp_path: Path) -> None:
    manager = StorageManager(base_dir=str(tmp_path))

    manager.append_realtime_log("agent-a", "realtime.log", "first")
    manager.append_realtime_log("agent-a", "realtime.log", "second")
    manager.append_realtime_log("agent-a", "realtime.log", "third")

    realtime_path = tmp_path / "logs" / "agent-a" / "realtime" / "realtime.log"
    assert realtime_path.exists()
    assert realtime_path.read_text(encoding="utf-8") == "first\nsecond\nthird\n"


def test_get_agent_logs(tmp_path: Path) -> None:
    manager = StorageManager(base_dir=str(tmp_path))

    _ = manager.save_log_history("agent-a", "a.log", b"a")
    _ = manager.save_log_history("agent-a", "b.log", b"b")
    manager.append_realtime_log("agent-a", "realtime.log", "line")

    logs = manager.get_agent_logs("agent-a")

    expected = {
        str(tmp_path / "logs" / "agent-a" / "a.log"),
        str(tmp_path / "logs" / "agent-a" / "b.log"),
        str(tmp_path / "logs" / "agent-a" / "realtime" / "realtime.log"),
    }
    assert set(logs) == expected


def test_save_report(tmp_path: Path) -> None:
    manager = StorageManager(base_dir=str(tmp_path))

    saved_path = manager.save_report(
        agent_id="agent-a",
        report_name="daily.md",
        content="# Daily\n\nOK",
    )

    path = Path(saved_path)
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "# Daily\n\nOK"


def test_cleanup_old_logs(tmp_path: Path) -> None:
    manager = StorageManager(base_dir=str(tmp_path), max_retention_days=1)
    old_path = Path(manager.save_log_history("agent-a", "old.log", b"old"))

    old_ts = time.time() - (2 * 24 * 60 * 60)
    os.utime(old_path, (old_ts, old_ts))

    removed = manager.cleanup_old_logs()

    assert removed == 1
    assert not old_path.exists()


def test_cleanup_preserves_recent(tmp_path: Path) -> None:
    manager = StorageManager(base_dir=str(tmp_path), max_retention_days=1)
    recent_path = Path(manager.save_log_history("agent-a", "recent.log", b"recent"))

    removed = manager.cleanup_old_logs()

    assert removed == 0
    assert recent_path.exists()
