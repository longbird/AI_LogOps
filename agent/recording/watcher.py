"""DB-driven recording analyzer.

rec_his / rec_his_YYYYMM 테이블에서 미분석 녹취를 조회하여
음질 분석(audio quality)을 실행한다.
Analysis engine: agent/recording/audio_quality.py.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.recording.audio_quality import get_wav_duration

# callback: (rec_no, filename, filepath, duration, in_out)
RecordingCallback = Callable[[int, str, str, float, int], Awaitable[None]]

# ── 상수 ──
MIN_FILE_SIZE = 16_000  # 최소 파일 크기 (약 2초 분량)
MIN_DURATION_SEC = 5.0  # 최소 녹취 길이 (초) — 5초 이상만 서버 분석
DB_POLL_INTERVAL = 30.0  # DB 재조회 간격 (초)


class RecordingWatcher:
    """DB 기반 녹취 분석. rec_his에서 미분석 건을 조회하여 분석 진행."""

    def __init__(
        self,
        watch_dir: str,
        extensions: list[str],
        on_new_recording: RecordingCallback,
        date_filter: str = "",
        db_config: Mapping[str, Any] | None = None,
        min_file_size: int = MIN_FILE_SIZE,
        min_duration_sec: float = MIN_DURATION_SEC,
        poll_interval: float = DB_POLL_INTERVAL,
    ) -> None:
        self._watch_dir = Path(watch_dir)
        self._extensions = {ext.lower() for ext in extensions}
        self._on_new_recording = on_new_recording
        self._date_filter = date_filter or datetime.now().strftime("%Y%m%d")
        self._db_config = db_config
        self._min_file_size = min_file_size
        self._min_duration_sec = min_duration_sec
        self._poll_interval = poll_interval

        self._processed: set[int] = set()  # 처리된 rec_no 집합
        self._event_queue: asyncio.Queue[tuple[int, str, int]] = asyncio.Queue()
        self._consumer_task: asyncio.Task[None] | None = None
        self._poll_task: asyncio.Task[None] | None = None
        self._started = False
        self._logger = logging.getLogger(self.__class__.__name__)

        # ── Flow control: 서버 제어 기반 1건씩 처리 ──
        self._next_event = asyncio.Event()
        self._first_done = False  # 첫 건은 자동 처리
        self._next_timeout: float = 90.0  # NEXT 대기 타임아웃 (초)

    @property
    def processed_count(self) -> int:
        """분석 완료된 녹취 건수."""
        return len(self._processed)

    # ------------------------------------------------------------------
    # start / stop
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._started:
            return
        self._consumer_task = asyncio.create_task(self._consume())
        self._poll_task = asyncio.create_task(self._poll_db())
        self._started = True

        # 초기 DB 조회
        await self._fetch_and_enqueue()

        self._logger.info(
            "RecordingWatcher started (DB mode): watch_dir=%s date=%s",
            self._watch_dir,
            self._date_filter,
        )

    def resume_next(self) -> None:
        """서버 NEXT 명령 수신 시 호출 — 다음 1건 분석 재개."""
        self._next_event.set()

    async def stop(self) -> None:
        if not self._started:
            return
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
        self._logger.info("RecordingWatcher stopped")

    # ------------------------------------------------------------------
    # DB 폴링 — 미분석 녹취 조회
    # ------------------------------------------------------------------

    async def _poll_db(self) -> None:
        """주기적으로 DB에서 미분석 녹취를 재조회."""
        while True:
            await asyncio.sleep(self._poll_interval)
            try:
                await self._fetch_and_enqueue()
            except Exception:
                self._logger.exception("DB poll failed")

    async def _fetch_and_enqueue(self) -> None:
        """DB에서 미분석 녹취 조회 후 큐에 추가."""
        if not self._db_config:
            self._logger.warning("DB config not set — skipping fetch")
            return
        try:
            from agent.db.connection import get_connection
            from agent.db.helpers import fetch_unanalyzed_recordings

            db_cfg: dict[str, object] = dict(self._db_config)
            conn = await asyncio.to_thread(get_connection, db_cfg)
            recordings = await asyncio.to_thread(
                fetch_unanalyzed_recordings, conn, self._date_filter
            )

            new_count = 0
            skipped = 0
            for rec_no, filename, in_out in recordings:
                if rec_no in self._processed:
                    continue
                # 실제 파일이 존재하는 경우에만 분석 대상으로 추가
                filepath = self._locate_file(filename)
                if not filepath:
                    skipped += 1
                    continue
                self._event_queue.put_nowait((rec_no, filename, in_out))
                new_count += 1

            if new_count or skipped:
                self._logger.info(
                    "DB poll: %d enqueued, %d skipped (file not found) — date=%s",
                    new_count,
                    skipped,
                    self._date_filter,
                )
        except Exception:
            self._logger.exception("Failed to fetch unanalyzed recordings")

    # ------------------------------------------------------------------
    # 파일 위치 탐색
    # ------------------------------------------------------------------

    def _locate_file(self, filename: str) -> str | None:
        """watch_dir 에서 녹취 파일을 찾는다.

        1) watch_dir/YYYYMMDD/filename (가장 일반적)
        2) watch_dir 하위 재귀 검색
        """
        target = self._watch_dir / self._date_filter / filename
        if target.is_file():
            return str(target)
        for f in self._watch_dir.rglob(filename):
            if f.is_file():
                return str(f)
        return None

    # ------------------------------------------------------------------
    # 분석 실행
    # ------------------------------------------------------------------

    async def _consume(self) -> None:
        while True:
            rec_no, filename, in_out = await self._event_queue.get()
            if rec_no in self._processed:
                continue

            # ── Flow control: 첫 건 이후에는 서버 NEXT 명령 대기 ──
            if self._first_done:
                self._logger.info(
                    "waiting for server NEXT: rec_no=%d filename=%s",
                    rec_no,
                    filename,
                )
                self._next_event.clear()
                try:
                    await asyncio.wait_for(
                        self._next_event.wait(), timeout=self._next_timeout
                    )
                except asyncio.TimeoutError:
                    self._logger.warning(
                        "NEXT timeout (%.0fs), resuming automatically: "
                        "rec_no=%d filename=%s",
                        self._next_timeout,
                        rec_no,
                        filename,
                    )
                self._logger.info(
                    "server NEXT received, resuming: rec_no=%d filename=%s",
                    rec_no,
                    filename,
                )

            try:
                # ── Step 1: 파일 위치 확인 ──
                filepath = await asyncio.to_thread(self._locate_file, filename)
                if not filepath:
                    self._logger.debug(
                        "file not found: rec_no=%d filename=%s", rec_no, filename
                    )
                    continue

                # ── Step 2: 최소 파일 크기 필터 ──
                try:
                    file_size = Path(filepath).stat().st_size
                except OSError:
                    continue
                if file_size < self._min_file_size:
                    self._logger.debug(
                        "skip (too small): rec_no=%d filename=%s size=%d",
                        rec_no,
                        filename,
                        file_size,
                    )
                    self._processed.add(rec_no)
                    continue

                # ── Step 3: WAV 길이 확인 (음질 분석은 서버에서 수행) ──
                self._logger.info(
                    "checking duration: rec_no=%d filename=%s size=%d",
                    rec_no,
                    filename,
                    file_size,
                )
                duration = await asyncio.to_thread(get_wav_duration, filepath)
                self._processed.add(rec_no)

                # ── Step 4: 최소 duration 필터 ──
                if duration < self._min_duration_sec:
                    self._logger.debug(
                        "skip (too short): rec_no=%d filename=%s dur=%.1fs",
                        rec_no,
                        filename,
                        duration,
                    )
                    continue

                self._logger.info(
                    "qualified: rec_no=%d filename=%s dur=%.1fs → send to server",
                    rec_no,
                    filename,
                    duration,
                )
                await self._on_new_recording(
                    rec_no, filename, filepath, duration, in_out
                )
                self._first_done = True  # 이후부터는 NEXT 대기
            except Exception:
                self._logger.exception(
                    "Failed to analyze: rec_no=%d filename=%s",
                    rec_no,
                    filename,
                )
