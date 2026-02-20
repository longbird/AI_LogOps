"""Recording folder watcher - detects new WAV files, runs audio quality analysis.

Based on agent/core/log_watcher.py pattern (watchdog Observer + asyncio queue).
Analysis engine from AirREC: agent/recording/audio_quality.py.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from os import fsdecode
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from agent.recording.audio_quality import analyze_recording
from agent.recording.models import AnalysisResult

RecordingCallback = Callable[[int, str, AnalysisResult], Awaitable[None]]


class _RecEventHandler(FileSystemEventHandler):
    def __init__(self, watcher: RecordingWatcher) -> None:
        self._watcher = watcher

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._watcher.enqueue_file(fsdecode(event.src_path))


class RecordingWatcher:
    """Watch a recording directory for new WAV files, analyze on arrival."""

    def __init__(
        self,
        watch_dir: str,
        extensions: list[str],
        on_new_recording: RecordingCallback,
        date_filter: str = "",
    ) -> None:
        self._watch_dir = Path(watch_dir)
        self._extensions = {ext.lower() for ext in extensions}
        self._on_new_recording = on_new_recording
        # YYYYMMDD — 비어있으면 현재일 기준
        self._date_filter = date_filter or datetime.now().strftime("%Y%m%d")

        self._observer = Observer()
        self._processed: set[str] = set()
        self._event_queue: asyncio.Queue[str] = asyncio.Queue()
        self._consumer_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = False
        self._logger = logging.getLogger(self.__class__.__name__)

    @property
    def processed_count(self) -> int:
        """Return the number of WAV files processed so far."""
        return len(self._processed)

    async def start(self) -> None:
        if self._started:
            return
        self._loop = asyncio.get_running_loop()
        handler = _RecEventHandler(self)
        if self._watch_dir.is_dir():
            self._observer.schedule(handler, str(self._watch_dir), recursive=True)
        self._observer.start()
        self._consumer_task = asyncio.create_task(self._consume())
        self._started = True
        self._logger.info("RecordingWatcher started: %s", self._watch_dir)

    async def stop(self) -> None:
        if not self._started:
            return
        self._observer.stop()
        await asyncio.to_thread(self._observer.join, timeout=5)
        if self._consumer_task is not None:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
            self._consumer_task = None
        self._started = False
        self._logger.info("RecordingWatcher stopped")

    def enqueue_file(self, filepath: str) -> None:
        p = Path(filepath)
        if p.suffix.lower() not in self._extensions:
            return
        if filepath in self._processed:
            return
        # 날짜 필터: watch_dir/YYYYMMDD/ 하위 파일만 허용
        if self._date_filter:
            if p.parent.name != self._date_filter:
                return
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._event_queue.put_nowait, filepath)

    async def _consume(self) -> None:
        while True:
            filepath = await self._event_queue.get()
            if filepath in self._processed:
                continue
            # Brief delay for file write to complete
            await asyncio.sleep(0.5)
            try:
                rec_no = self._extract_rec_no(filepath)
                file_size = (
                    Path(filepath).stat().st_size if Path(filepath).exists() else 0
                )
                self._logger.info(
                    "analyzing: rec_no=%d file=%s size=%d",
                    rec_no,
                    Path(filepath).name,
                    file_size,
                )
                result = await asyncio.to_thread(
                    analyze_recording, rec_no, filepath, 0.0
                )
                self._processed.add(filepath)
                self._logger.info(
                    "analyzed: rec_no=%d status=%s L=%.1fdB R=%.1fdB dur=%.1fs",
                    rec_no,
                    result.status.value,
                    result.left.rms_db,
                    result.right.rms_db,
                    result.duration_wav,
                )
                await self._on_new_recording(rec_no, filepath, result)
            except Exception:
                file_size = (
                    Path(filepath).stat().st_size if Path(filepath).exists() else -1
                )
                self._logger.exception(
                    "Failed to analyze: %s (size=%d)",
                    filepath,
                    file_size,
                )

    @staticmethod
    def _extract_rec_no(filepath: str) -> int:
        """Extract rec_no from filename. E.g. '12345.wav' -> 12345, 'test_001.wav' -> 1."""
        name = Path(filepath).stem
        match = re.search(r"(\d+)", name)
        return int(match.group(1)) if match else 0
