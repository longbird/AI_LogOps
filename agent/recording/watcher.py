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

# ── 파일 완성 대기 상수 ──
# μ-law mono 8kHz = 8,000 bytes/sec → 3초 = 24KB
MIN_FILE_SIZE = 16_000  # 최소 파일 크기 (약 2초 분량)
MIN_DURATION_SEC = 3.0  # 최소 녹취 길이 (초)
STABLE_CHECK_SEC = 2.0  # 크기 안정화 확인 간격 (초)
STABLE_COUNT = 2  # 연속 동일 크기 횟수 (2회 × 2초 = 4초 무변동)
STABLE_MAX_WAIT = 300  # 최대 대기 시간 (초, 5분)


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
        min_file_size: int = MIN_FILE_SIZE,
        min_duration_sec: float = MIN_DURATION_SEC,
    ) -> None:
        self._watch_dir = Path(watch_dir)
        self._extensions = {ext.lower() for ext in extensions}
        self._on_new_recording = on_new_recording
        # YYYYMMDD — 비어있으면 현재일 기준
        self._date_filter = date_filter or datetime.now().strftime("%Y%m%d")
        self._min_file_size = min_file_size
        self._min_duration_sec = min_duration_sec

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
            fname = Path(filepath).name
            try:
                # ── Step 1: 파일 쓰기 완료 대기 ──
                file_size = await self._wait_for_stable(filepath)
                if file_size == 0:
                    self._logger.debug("file removed during stabilization: %s", fname)
                    continue

                # ── Step 2: 최소 파일 크기 필터 ──
                if file_size < self._min_file_size:
                    self._logger.debug(
                        "skip (too small): %s size=%d min=%d",
                        fname,
                        file_size,
                        self._min_file_size,
                    )
                    self._processed.add(filepath)
                    continue

                # ── Step 3: 분석 실행 ──
                rec_no = self._extract_rec_no(filepath)
                self._logger.info(
                    "analyzing: rec_no=%d file=%s size=%d",
                    rec_no,
                    fname,
                    file_size,
                )
                result = await asyncio.to_thread(
                    analyze_recording, rec_no, filepath, 0.0
                )
                self._processed.add(filepath)

                # ── Step 4: 최소 duration 필터 ──
                if result.duration_wav < self._min_duration_sec:
                    self._logger.debug(
                        "skip (too short): %s dur=%.1fs min=%.1fs",
                        fname,
                        result.duration_wav,
                        self._min_duration_sec,
                    )
                    continue

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
                try:
                    sz = Path(filepath).stat().st_size
                except OSError:
                    sz = -1
                self._logger.exception(
                    "Failed to analyze: %s (size=%d)",
                    filepath,
                    sz,
                )

    async def _wait_for_stable(self, filepath: str) -> int:
        """파일 쓰기 완료 대기: 크기가 연속으로 동일하면 안정화로 판단.

        Returns:
            최종 파일 크기 (바이트). 파일이 삭제되었으면 0.
        """
        last_size = -1
        consecutive = 0
        max_checks = int(STABLE_MAX_WAIT / STABLE_CHECK_SEC)
        for _ in range(max_checks):
            try:
                current_size = Path(filepath).stat().st_size
            except OSError:
                return 0
            if current_size == last_size:
                consecutive += 1
                if consecutive >= STABLE_COUNT:
                    return current_size
            else:
                consecutive = 0
                last_size = current_size
            await asyncio.sleep(STABLE_CHECK_SEC)
        self._logger.warning(
            "file stabilization timeout (%ds): %s size=%d",
            STABLE_MAX_WAIT,
            Path(filepath).name,
            last_size,
        )
        return last_size if last_size >= 0 else 0

    @staticmethod
    def _extract_rec_no(filepath: str) -> int:
        """Extract rec_no from filename. E.g. '12345.wav' -> 12345, 'test_001.wav' -> 1."""
        name = Path(filepath).stem
        match = re.search(r"(\d+)", name)
        return int(match.group(1)) if match else 0
