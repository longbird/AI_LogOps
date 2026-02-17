from __future__ import annotations

# pyright: reportMissingImports=false

import asyncio
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from agent.core.log_watcher import LogWatcher
from shared.protocol import LogFileEntry


@pytest.mark.asyncio
async def test_detects_new_lines(tmp_path: Path) -> None:
    log_file = tmp_path / "app.log"
    log_file.write_text("existing\n", encoding="utf-8")

    received: list[tuple[str, str]] = []
    got_lines = asyncio.Event()

    async def on_new_line(filename: str, line: str) -> None:
        received.append((filename, line))
        if len(received) >= 2:
            got_lines.set()

    watcher = LogWatcher(
        watch_dirs=[str(tmp_path)],
        extensions=[".log", ".txt"],
        on_new_line=on_new_line,
    )

    await watcher.start()
    try:
        await asyncio.sleep(0.1)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write("line-a\n")
            handle.write("line-b\n")

        await asyncio.wait_for(got_lines.wait(), timeout=3)
    finally:
        await watcher.stop()

    assert received == [("app.log", "line-a"), ("app.log", "line-b")]


def test_reads_history(tmp_path: Path) -> None:
    log_file = tmp_path / "history.log"
    expected = b"alpha\nbeta\ngamma\n"
    log_file.write_bytes(expected)

    watcher = LogWatcher(
        watch_dirs=[str(tmp_path)],
        extensions=[".log", ".txt"],
        on_new_line=_noop_callback,
    )

    assert watcher.read_history(str(log_file)) == expected


def test_history_max_mb_limit(tmp_path: Path) -> None:
    log_file = tmp_path / "large.log"
    data = b"x" * (2 * 1024 * 1024)
    log_file.write_bytes(data)

    watcher = LogWatcher(
        watch_dirs=[str(tmp_path)],
        extensions=[".log", ".txt"],
        on_new_line=_noop_callback,
    )

    history = watcher.read_history(str(log_file), max_mb=1)
    assert len(history) == 1024 * 1024
    assert history == data[-(1024 * 1024) :]


def test_get_watchable_files(tmp_path: Path) -> None:
    log_file = tmp_path / "agent.log"
    txt_file = tmp_path / "notes.txt"
    dat_file = tmp_path / "binary.dat"

    log_file.write_text("log", encoding="utf-8")
    txt_file.write_text("txt", encoding="utf-8")
    dat_file.write_text("dat", encoding="utf-8")

    watcher = LogWatcher(
        watch_dirs=[str(tmp_path)],
        extensions=[".log", ".txt"],
        on_new_line=_noop_callback,
    )

    assert watcher.get_watchable_files() == sorted([str(log_file), str(txt_file)])


def test_find_files_by_date_returns_matching_files(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "20260217_error.txt").write_text("error1", encoding="utf-8")
    (log_dir / "20260217_info.txt").write_text("info1", encoding="utf-8")
    (log_dir / "20260216_old.txt").write_text("old", encoding="utf-8")
    (log_dir / "20260217_data.csv").write_text("csv", encoding="utf-8")

    watcher = LogWatcher(
        watch_dirs=[str(log_dir)],
        extensions=[".txt"],
        on_new_line=_noop_callback,
    )
    result = watcher.find_files_by_date("20260217")

    assert len(result) == 2
    assert all("20260217" in Path(file_path).name for file_path in result)
    assert not any("csv" in file_path for file_path in result)


def test_find_files_by_date_no_matches(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "20260216_old.txt").write_text("old", encoding="utf-8")

    watcher = LogWatcher(
        watch_dirs=[str(log_dir)],
        extensions=[".txt"],
        on_new_line=_noop_callback,
    )
    result = watcher.find_files_by_date("20260217")

    assert result == []


def test_find_files_by_date_multiple_extensions(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "20260217_app.txt").write_text("t", encoding="utf-8")
    (log_dir / "20260217_app.log").write_text("l", encoding="utf-8")

    watcher = LogWatcher(
        watch_dirs=[str(log_dir)],
        extensions=[".txt", ".log"],
        on_new_line=_noop_callback,
    )
    result = watcher.find_files_by_date("20260217")

    assert len(result) == 2


def test_get_latest_file_returns_newest(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "20260215_a.txt").write_text("a", encoding="utf-8")
    (log_dir / "20260217_b.txt").write_text("b", encoding="utf-8")
    (log_dir / "20260216_c.txt").write_text("c", encoding="utf-8")

    watcher = LogWatcher(
        watch_dirs=[str(log_dir)],
        extensions=[".txt"],
        on_new_line=_noop_callback,
    )
    latest = watcher.get_latest_file()

    assert latest is not None
    assert "20260217" in Path(latest).name


def test_get_latest_file_empty_dir(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    watcher = LogWatcher(
        watch_dirs=[str(log_dir)],
        extensions=[".txt"],
        on_new_line=_noop_callback,
    )

    assert watcher.get_latest_file() is None


@pytest.mark.asyncio
async def test_ignores_non_matching_extensions(tmp_path: Path) -> None:
    dat_file = tmp_path / "ignored.dat"
    dat_file.write_text("seed\n", encoding="utf-8")

    got_event = asyncio.Event()

    async def on_new_line(filename: str, line: str) -> None:
        got_event.set()

    watcher = LogWatcher(
        watch_dirs=[str(tmp_path)],
        extensions=[".log", ".txt"],
        on_new_line=on_new_line,
    )

    await watcher.start()
    try:
        await asyncio.sleep(0.1)
        with dat_file.open("a", encoding="utf-8") as handle:
            handle.write("should-not-trigger\n")

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(got_event.wait(), timeout=0.5)
    finally:
        await watcher.stop()


async def _noop_callback(_filename: str, _line: str) -> None:
    return None


def test_get_files_metadata_returns_entries(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    f1 = log_dir / "20260217_app.txt"
    f1.write_bytes(b"hello world")
    f2 = log_dir / "20260217_error.txt"
    f2.write_bytes(b"error data here")

    watcher = LogWatcher(
        watch_dirs=[str(log_dir)],
        extensions=[".txt"],
        on_new_line=AsyncMock(),
    )

    entries = watcher.get_files_metadata("20260217")

    assert len(entries) == 2
    assert entries[0].filename == "20260217_app.txt"
    assert entries[0].file_size == 11
    assert entries[0].md5 == hashlib.md5(b"hello world").digest()
    assert entries[1].filename == "20260217_error.txt"
    assert entries[1].file_size == 15
    assert entries[1].md5 == hashlib.md5(b"error data here").digest()


def test_get_files_metadata_empty_date(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "20260217_app.txt").write_bytes(b"data")

    watcher = LogWatcher(
        watch_dirs=[str(log_dir)],
        extensions=[".txt"],
        on_new_line=AsyncMock(),
    )

    entries = watcher.get_files_metadata("20250101")
    assert entries == []


def test_get_files_metadata_filters_extensions(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "20260217_app.txt").write_bytes(b"data")
    (log_dir / "20260217_app.csv").write_bytes(b"csv data")

    watcher = LogWatcher(
        watch_dirs=[str(log_dir)],
        extensions=[".txt"],
        on_new_line=AsyncMock(),
    )

    entries = watcher.get_files_metadata("20260217")
    assert len(entries) == 1
    assert entries[0].filename == "20260217_app.txt"
