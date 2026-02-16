from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agent.updater.self_update import SelfUpdater


@pytest.mark.asyncio
async def test_receive_update_writes_temp_file_when_sha_matches(tmp_path: Path) -> None:
    updater = SelfUpdater(backup_dir=str(tmp_path / "backups"), current_version="1.2.3")
    data = b"new-agent-binary"
    expected_sha = hashlib.sha256(data).hexdigest()

    ok = await updater.receive_update(data=data, sha256=expected_sha)

    assert ok is True
    assert updater.new_binary_path.exists()
    assert updater.new_binary_path.read_bytes() == data


@pytest.mark.asyncio
async def test_receive_update_removes_temp_file_when_sha_mismatch(
    tmp_path: Path,
) -> None:
    updater = SelfUpdater(backup_dir=str(tmp_path / "backups"), current_version="1.2.3")

    ok = await updater.receive_update(data=b"invalid", sha256="0" * 64)

    assert ok is False
    assert not updater.new_binary_path.exists()


def test_generate_updater_bat_fills_template_with_paths(tmp_path: Path) -> None:
    updater = SelfUpdater(backup_dir=str(tmp_path / "backups"), current_version="2.0.0")

    bat_path = Path(updater.generate_updater_bat())
    content = bat_path.read_text(encoding="utf-8")

    assert bat_path.exists()
    assert "net stop AILogOps-Agent" in content
    assert "net start AILogOps-Agent" in content
    assert "if errorlevel 1" in content
    assert "agent_2.0.0.exe" in content
    assert str(updater.current_exe_path) in content
    assert str(updater.new_binary_path) in content


def test_verify_version_on_boot_returns_true_when_version_matches(
    tmp_path: Path,
) -> None:
    updater = SelfUpdater(backup_dir=str(tmp_path / "backups"), current_version="3.1.0")
    updater.write_version_file("3.1.0")

    ok = updater.verify_version_on_boot()

    assert ok is True


def test_verify_version_on_boot_rolls_back_when_version_mismatch(
    tmp_path: Path,
) -> None:
    updater = SelfUpdater(backup_dir=str(tmp_path / "backups"), current_version="3.1.0")
    updater.write_version_file("3.0.0")
    _ = updater.current_exe_path.write_bytes(b"broken")

    updater.backup_dir.mkdir(parents=True, exist_ok=True)
    _ = (updater.backup_dir / "agent_3.0.0.exe").write_bytes(b"old")
    _ = (updater.backup_dir / "agent_3.0.5.exe").write_bytes(b"restored")

    ok = updater.verify_version_on_boot()

    assert ok is False
    assert updater.current_exe_path.read_bytes() == b"restored"
    assert updater.version_file_path.read_text(encoding="utf-8").strip() == "3.1.0"


def test_write_version_file_writes_expected_content(tmp_path: Path) -> None:
    updater = SelfUpdater(backup_dir=str(tmp_path / "backups"), current_version="9.9.9")

    updater.write_version_file("9.9.10")

    assert updater.version_file_path.exists()
    assert updater.version_file_path.read_text(encoding="utf-8").strip() == "9.9.10"


def test_execute_update_spawns_updater_and_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater = SelfUpdater(backup_dir=str(tmp_path / "backups"), current_version="1.0.0")
    _ = updater.generate_updater_bat()

    calls: dict[str, object] = {}

    def fake_popen(command: object, **kwargs: object) -> object:
        calls["command"] = command
        calls["kwargs"] = kwargs
        return object()

    class ExitCalled(Exception):
        pass

    def fake_exit(code: int = 0) -> None:
        raise ExitCalled(code)

    monkeypatch.setattr("agent.updater.self_update.subprocess.Popen", fake_popen)
    monkeypatch.setattr("agent.updater.self_update.sys.exit", fake_exit)

    with pytest.raises(ExitCalled) as exc_info:
        updater.execute_update()

    assert exc_info.value.args == (0,)
    command = calls["command"]
    if isinstance(command, list):
        assert command[-1] == str(updater.updater_bat_path)
    else:
        assert str(updater.updater_bat_path) in str(command)
