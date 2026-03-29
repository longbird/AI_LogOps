from __future__ import annotations

# pyright: reportMissingImports=false

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from os import fsdecode
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from shared.protocol import LogFileEntry

LineCallback = Callable[[str, str, int], Awaitable[None]]


class _LogEventHandler(FileSystemEventHandler):
    def __init__(self, watcher: LogWatcher) -> None:
        self._watcher = watcher

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._watcher.enqueue_file(fsdecode(event.src_path))

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._watcher.enqueue_file(fsdecode(event.src_path))


class LogWatcher:
    """watchdog 기반 파일 변경 감지 + 테일링. 스펙 섹션 2.1 참조."""

    def __init__(
        self,
        watch_dirs: list[str],
        extensions: list[str],
        on_new_line: LineCallback,
    ) -> None:
        self._watch_dirs = [Path(path) for path in watch_dirs]
        self._extensions = {ext.lower() for ext in extensions}
        self._on_new_line = on_new_line

        self._observer = Observer()
        self._positions: dict[str, int] = {}
        self._event_queue: asyncio.Queue[str] = asyncio.Queue()
        self._consumer_task: asyncio.Task[None] | None = None
        self._poll_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = False

    async def start(self) -> None:
        """watchdog Observer 시작. 파일 변경 감지 시작."""

        if self._started:
            return

        self._loop = asyncio.get_running_loop()
        self._seed_file_positions()

        handler = _LogEventHandler(self)
        for watch_dir in self._watch_dirs:
            if watch_dir.exists() and watch_dir.is_dir():
                self._observer.schedule(handler, str(watch_dir), recursive=True)

        self._observer.start()
        self._consumer_task = asyncio.create_task(self._consume_events())
        self._poll_task = asyncio.create_task(self._poll_files())
        self._started = True

    async def stop(self) -> None:
        """Observer 정지."""

        if not self._started:
            return

        self._observer.stop()
        await asyncio.to_thread(self._observer.join)

        for task in (self._consumer_task, self._poll_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._consumer_task = None
        self._poll_task = None

        self._started = False

    async def update_config(
        self, watch_dirs: list[str], extensions: list[str],
    ) -> None:
        """핫리로드: 감시 폴더/확장자 변경. Observer를 재시작합니다."""
        from shared.utils import setup_logging
        logger = setup_logging("log_watcher")

        new_dirs = {str(Path(p)) for p in watch_dirs}
        old_dirs = {str(d) for d in self._watch_dirs}
        new_exts = {e.lower() for e in extensions}

        if new_dirs == old_dirs and new_exts == self._extensions:
            return  # 변경 없음

        logger.info(
            "hot-reload: watch_dirs %s -> %s, extensions %s -> %s",
            old_dirs, new_dirs, self._extensions, new_exts,
        )

        await self.stop()
        self._watch_dirs = [Path(p) for p in watch_dirs]
        self._extensions = new_exts
        self._observer = Observer()
        await self.start()

    def read_history(self, filepath: str, max_mb: int = 10) -> bytes:
        """파일 전체 또는 max_mb 제한만큼 읽기. 파일 끝에서 역방향."""

        max_bytes = max_mb * 1024 * 1024
        file_path = Path(filepath)
        file_size = file_path.stat().st_size
        start_pos = max(0, file_size - max_bytes)
        with file_path.open("rb") as handle:
            handle.seek(start_pos)
            return handle.read()

    def get_watchable_files(self) -> list[str]:
        """감시 대상 파일 목록 반환 (extensions 필터링)."""

        files: list[str] = []
        for watch_dir in self._watch_dirs:
            if not watch_dir.exists() or not watch_dir.is_dir():
                continue
            for file_path in watch_dir.rglob("*"):
                if file_path.is_file() and file_path.suffix.lower() in self._extensions:
                    files.append(str(file_path))
        return sorted(files)

    def find_files_by_date(self, date_str: str) -> list[str]:
        """감시 폴더에서 YYYYMMDD_* 패턴 + extensions 매칭 파일 검색."""

        files: list[str] = []
        pattern = f"{date_str}_*"
        for watch_dir in self._watch_dirs:
            if not watch_dir.exists() or not watch_dir.is_dir():
                continue
            for file_path in watch_dir.glob(pattern):
                if file_path.is_file() and self._is_watchable(str(file_path)):
                    files.append(str(file_path.resolve()))
        return sorted(files)

    def get_files_metadata(self, date_str: str) -> list[LogFileEntry]:
        """감시 폴더에서 YYYYMMDD_* 파일의 메타데이터(filename, size, MD5) 수집."""

        files = self.find_files_by_date(date_str)
        entries: list[LogFileEntry] = []
        for filepath in files:
            path = Path(filepath)
            data = path.read_bytes()
            entry = LogFileEntry(
                filename=path.name,
                file_size=len(data),
                md5=hashlib.md5(data).digest(),
            )
            entries.append(entry)
        return entries

    @property
    def watch_dir_count(self) -> int:
        """감시 폴더 수 반환."""
        return len(self._watch_dirs)

    def find_files_by_date_and_folder(
        self, date_str: str, folder_index: int
    ) -> list[str]:
        """특정 감시 폴더에서 YYYYMMDD_* 패턴 파일 검색.
        folder_index < 0 이면 전체 폴더 검색 (find_files_by_date 동작)."""
        if folder_index < 0:
            return self.find_files_by_date(date_str)

        if folder_index >= len(self._watch_dirs):
            return []

        watch_dir = self._watch_dirs[folder_index]
        if not watch_dir.exists() or not watch_dir.is_dir():
            return []

        files: list[str] = []
        pattern = f"{date_str}_*"
        for file_path in watch_dir.glob(pattern):
            if file_path.is_file() and self._is_watchable(str(file_path)):
                files.append(str(file_path.resolve()))
        return sorted(files)

    def get_files_metadata_by_folder(
        self, date_str: str, folder_index: int
    ) -> list[LogFileEntry]:
        """특정 폴더의 YYYYMMDD_* 파일 메타데이터 수집.
        folder_index < 0 이면 전체 폴더 (get_files_metadata 동작)."""
        files = self.find_files_by_date_and_folder(date_str, folder_index)
        entries: list[LogFileEntry] = []
        for filepath in files:
            path = Path(filepath)
            data = path.read_bytes()
            entry = LogFileEntry(
                filename=path.name,
                file_size=len(data),
                md5=hashlib.md5(data).digest(),
            )
            entries.append(entry)
        return entries

    def get_latest_file(self) -> str | None:
        """감시 폴더의 최신 파일 경로 반환 (이름 기준 정렬 마지막)."""

        files = self.get_watchable_files()
        if not files:
            return None
        return files[-1]

    def get_latest_files_by_dir(self) -> list[tuple[str, str]]:
        """각 감시 디렉토리별 최신 파일 반환. [(dir_path, file_path), ...]"""
        result: list[tuple[str, str]] = []
        for watch_dir in self._watch_dirs:
            if not watch_dir.exists() or not watch_dir.is_dir():
                continue
            files = sorted(
                f
                for f in watch_dir.rglob("*")
                if f.is_file() and f.suffix.lower() in self._extensions
            )
            if files:
                result.append((str(watch_dir), str(files[-1])))
        return result

    @staticmethod
    def read_last_n_lines(filepath: str, n: int) -> list[str]:
        """파일에서 마지막 N줄을 읽는다."""
        try:
            p = Path(filepath)
            with p.open("r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                return [line.rstrip("\n\r") for line in lines[-n:]]
        except OSError:
            return ["(read error)"]

    # ------------------------------------------------------------------
    # 폴링 폴백 — watchdog 이벤트 누락 시 주기적으로 파일 변경 확인
    # ------------------------------------------------------------------

    async def _poll_files(self) -> None:
        """3초 간격으로 감시 파일 크기를 확인하여 watchdog 누락분을 보완한다."""
        while True:
            await asyncio.sleep(3)
            try:
                for filepath in self.get_watchable_files():
                    try:
                        file_size = Path(filepath).stat().st_size
                    except OSError:
                        continue
                    last_pos = self._positions.get(filepath, 0)
                    if file_size > last_pos:
                        self._event_queue.put_nowait(filepath)
            except Exception:
                pass  # 폴링 실패해도 watchdog은 계속 동작

    def enqueue_file(self, filepath: str) -> None:
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._event_queue.put_nowait, filepath)

    async def _consume_events(self) -> None:
        while True:
            filepath = await self._event_queue.get()
            if not self._is_watchable(filepath):
                continue
            folder_index = self._get_folder_index(filepath)
            lines = await asyncio.to_thread(self._read_new_lines, filepath)
            filename = Path(filepath).name
            for line in lines:
                await self._on_new_line(filename, line, folder_index)

    def _seed_file_positions(self) -> None:
        for filepath in self.get_watchable_files():
            file_path = Path(filepath)
            self._positions[filepath] = file_path.stat().st_size

    def _is_watchable(self, filepath: str) -> bool:
        return Path(filepath).suffix.lower() in self._extensions

    def _get_folder_index(self, filepath: str) -> int:
        """파일 경로가 속한 watch_dir의 0-based 인덱스 반환. 미매칭 시 0."""
        resolved = Path(filepath).resolve()
        for idx, watch_dir in enumerate(self._watch_dirs):
            try:
                resolved.relative_to(watch_dir.resolve())
                return idx
            except ValueError:
                continue
        return 0

    def _read_new_lines(self, filepath: str) -> list[str]:
        file_path = Path(filepath)
        if not file_path.exists() or not file_path.is_file():
            self._positions.pop(filepath, None)
            return []

        file_size = file_path.stat().st_size
        last_pos = self._positions.get(filepath, 0)
        if file_size < last_pos:
            last_pos = 0

        with file_path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(last_pos)
            data = handle.read()
            self._positions[filepath] = handle.tell()

        if not data:
            return []
        return data.splitlines()
