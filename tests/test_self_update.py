from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from agent.updater.self_update import SelfUpdater


@pytest.mark.asyncio
async def test_receive_zip_extracts_to_update_dir(tmp_path: Path) -> None:
    """Test that receive_zip extracts zip data to update_dir."""
    updater = SelfUpdater(install_dir=tmp_path)

    # Create a simple zip file in memory
    import io

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr("test.txt", "test content")
    zip_data = zip_buffer.getvalue()

    ok = await updater.receive_zip(zip_data)

    assert ok is True
    assert updater.update_dir.exists()
    assert (updater.update_dir / "test.txt").exists()
    assert (updater.update_dir / "test.txt").read_text() == "test content"


@pytest.mark.asyncio
async def test_receive_zip_returns_false_on_bad_zip(tmp_path: Path) -> None:
    """Test that receive_zip returns False for invalid zip data."""
    updater = SelfUpdater(install_dir=tmp_path)
    bad_zip_data = b"not a valid zip file"

    ok = await updater.receive_zip(bad_zip_data)

    assert ok is False


@pytest.mark.asyncio
async def test_receive_file_saves_to_update_dir(tmp_path: Path) -> None:
    """Test that receive_file saves a single file to update_dir."""
    updater = SelfUpdater(install_dir=tmp_path)
    file_data = b"binary file content"

    ok = await updater.receive_file(file_data, "agent.exe")

    assert ok is True
    assert updater.update_dir.exists()
    assert (updater.update_dir / "agent.exe").exists()
    assert (updater.update_dir / "agent.exe").read_bytes() == file_data


def test_generate_updater_bat_creates_bat_file(tmp_path: Path) -> None:
    """Test that generate_updater_bat creates updater.bat with correct paths."""
    updater = SelfUpdater(install_dir=tmp_path, is_service_mode=False)

    bat_path_str = updater.generate_updater_bat()
    bat_path = Path(bat_path_str)
    content = bat_path.read_text(encoding="utf-8")

    assert bat_path.exists()
    assert bat_path == updater.updater_bat_path
    assert str(updater.install_dir) in content
    assert str(updater.backup_dir) in content
    assert str(updater.update_dir) in content
    assert "taskkill" in content  # debug mode template


def test_generate_updater_bat_service_mode(tmp_path: Path) -> None:
    """Test that generate_updater_bat uses service template when is_service_mode=True."""
    updater = SelfUpdater(install_dir=tmp_path, is_service_mode=True)

    bat_path_str = updater.generate_updater_bat()
    content = Path(bat_path_str).read_text(encoding="utf-8")

    assert "net stop AILogOps-Agent" in content
    assert "net start AILogOps-Agent" in content


def test_backup_dir_created_from_install_dir(tmp_path: Path) -> None:
    """Test that backup_dir is correctly derived from install_dir."""
    updater = SelfUpdater(install_dir=tmp_path)

    assert updater.backup_dir == tmp_path / "backups"


def test_paths_derived_from_install_dir(tmp_path: Path) -> None:
    """Test that all paths are correctly derived from install_dir."""
    updater = SelfUpdater(install_dir=tmp_path)

    assert updater.install_dir == tmp_path
    assert updater.temp_dir == tmp_path / "temp"
    assert updater.parts_dir == tmp_path / "temp" / "parts"
    assert updater.update_dir == tmp_path / "temp" / "update"
    assert updater.backup_dir == tmp_path / "backups"
    assert updater.updater_bat_path == tmp_path / "updater.bat"


def test_execute_update_spawns_updater_and_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that execute_update generates bat and spawns subprocess."""
    updater = SelfUpdater(install_dir=tmp_path)

    # Create update_dir with dummy files
    updater.update_dir.mkdir(parents=True, exist_ok=True)
    _ = (updater.update_dir / "test.txt").write_text("test")

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
