from __future__ import annotations

# pyright: reportMissingImports=false

import asyncio
from pathlib import Path

import pytest

from agent.core.log_watcher import LogWatcher


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
