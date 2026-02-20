# AirREC Agent + Command Server Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Split AirREC analysis features between the AI-LogOps agent (local audio quality analysis + WAV upload) and a new command server (STT + quality scoring + scheduler + web dashboard).

**Architecture:** Agent watches recording folder locally, runs lightweight audio quality analysis (numpy), reports results and uploads WAV files on demand via HTTPS. Command server manages STT backlog, runs faster-whisper, computes call quality scores, and serves the web dashboard. Control messages flow over the existing TCP protocol (new PacketTypes). Binary file transfers use HTTPS multipart upload (agent-initiated, outbound only).

**Tech Stack:** Python 3.13, numpy, watchdog, faster-whisper, FastAPI, Bootstrap 5, wavesurfer.js 7, Chart.js 4, MySQL (pymysql), existing AI-LogOps TCP protocol

**Reference Source:** `D:\Work\AirTech\PBXServer\AirRec\airec_api\` (existing AirREC implementation to copy/adapt)

---

## Overview

```
Agent (Factory PC)                     Command Server (Central)
========================              ========================
[RecordingWatcher]                    [FastAPI Server]
  - watchdog: new WAV detected          - POST /api/upload (receive WAV)
  - audio quality analysis (numpy)      - GET /api/stream/{rec_no} (Range)
  - report result via TCP               - GET /api/recordings (list)
  - upload WAV on server request        - POST /api/transcribe (STT)
                                        - GET /api/transcript/{rec_no}
[TCP Client] ←────TCP────→ [TCP Server] - POST /api/quality (scoring)
  - REC_ANALYSIS_RESULT (agent→server)  - GET /api/quality/{rec_no}
  - REC_UPLOAD_REQ (server→agent)       - GET /api/quality/summary/{date}
  - REC_UPLOAD_ACK (agent→server)       - GET /api/scheduler/status
  - Telegram alerts on anomalies
                                      [AnalysisScheduler]
                                        - poll unanalyzed recordings
                                        - request WAV upload from agent
                                        - run STT + quality pipeline
                                        - store results in DB

                                      [Web Dashboard]
                                        - recordings list + filters
                                        - waveform player + transcript
                                        - quality scores + charts
```

---

## Task 1: Protocol Extension — New PacketTypes for Recording Analysis

**Files:**
- Modify: `shared/protocol.py` — Add 3 new PacketTypes + payload classes
- Test: `tests/test_protocol_rec.py`

### Step 1: Write failing tests for new protocol payloads

```python
# tests/test_protocol_rec.py
"""Tests for recording analysis protocol extensions."""
from __future__ import annotations
import pytest
from shared.protocol import (
    PacketType,
    RecAnalysisPayload,
    RecUploadReqPayload,
    RecUploadAckPayload,
    Packet,
)


class TestRecAnalysisPayload:
    def test_roundtrip(self):
        original = RecAnalysisPayload(
            rec_no=12345,
            status="EMPTY",
            left_rms_db=-45.2,
            right_rms_db=-60.1,
            left_silence_ratio=0.95,
            right_silence_ratio=0.30,
            dropout_count=2,
            duration_wav=120.5,
            duration_smdr=121.0,
            is_stereo=True,
        )
        packed = original.pack()
        restored = RecAnalysisPayload.unpack(packed)
        assert restored.rec_no == 12345
        assert restored.status == "EMPTY"
        assert abs(restored.left_rms_db - (-45.2)) < 0.1
        assert abs(restored.right_rms_db - (-60.1)) < 0.1
        assert abs(restored.left_silence_ratio - 0.95) < 0.01
        assert restored.dropout_count == 2
        assert restored.is_stereo is True

    def test_packet_build(self):
        payload = RecAnalysisPayload(
            rec_no=1, status="OK",
            left_rms_db=-20.0, right_rms_db=-20.0,
            left_silence_ratio=0.1, right_silence_ratio=0.1,
            dropout_count=0, duration_wav=60.0, duration_smdr=60.0,
            is_stereo=False,
        )
        pkt = Packet.build(PacketType.REC_ANALYSIS_RESULT, payload.pack())
        assert len(pkt) > 5  # header + payload


class TestRecUploadReqPayload:
    def test_roundtrip(self):
        original = RecUploadReqPayload(rec_no=99999, upload_url="https://server:8000/api/upload?token=abc123")
        packed = original.pack()
        restored = RecUploadReqPayload.unpack(packed)
        assert restored.rec_no == 99999
        assert restored.upload_url == "https://server:8000/api/upload?token=abc123"


class TestRecUploadAckPayload:
    def test_roundtrip(self):
        original = RecUploadAckPayload(rec_no=99999, status=0, file_size=1024000)
        packed = original.pack()
        restored = RecUploadAckPayload.unpack(packed)
        assert restored.rec_no == 99999
        assert restored.status == 0
        assert restored.file_size == 1024000
```

### Step 2: Run tests — expect ImportError

```bash
pytest tests/test_protocol_rec.py -v
```
Expected: FAIL — `ImportError: cannot import name 'RecAnalysisPayload'`

### Step 3: Implement protocol extensions

Add to `shared/protocol.py`:

1. New PacketType values:
```python
class PacketType(IntEnum):
    # ... existing ...
    REC_ANALYSIS_RESULT = 0x30   # Agent → Server: audio quality result
    REC_UPLOAD_REQ = 0x31        # Server → Agent: request WAV upload
    REC_UPLOAD_ACK = 0x32        # Agent → Server: upload completed/failed
```

2. New payload classes (JSON-based for flexibility, unlike fixed-struct existing payloads):

```python
import json

@dataclass(slots=True)
class RecAnalysisPayload:
    """REC_ANALYSIS_RESULT: JSON payload with audio quality analysis."""
    rec_no: int
    status: str              # AnalysisStatus value: OK, EMPTY, MUTED_L, etc.
    left_rms_db: float
    right_rms_db: float
    left_silence_ratio: float
    right_silence_ratio: float
    dropout_count: int
    duration_wav: float
    duration_smdr: float
    is_stereo: bool

    def pack(self) -> bytes:
        data = {
            "rec_no": self.rec_no,
            "status": self.status,
            "left_rms_db": round(self.left_rms_db, 2),
            "right_rms_db": round(self.right_rms_db, 2),
            "left_silence_ratio": round(self.left_silence_ratio, 4),
            "right_silence_ratio": round(self.right_silence_ratio, 4),
            "dropout_count": self.dropout_count,
            "duration_wav": round(self.duration_wav, 3),
            "duration_smdr": round(self.duration_smdr, 3),
            "is_stereo": self.is_stereo,
        }
        return json.dumps(data, separators=(",", ":")).encode("utf-8")

    @classmethod
    def unpack(cls, data: bytes) -> RecAnalysisPayload:
        obj = json.loads(data.decode("utf-8"))
        return cls(**obj)


@dataclass(slots=True)
class RecUploadReqPayload:
    """REC_UPLOAD_REQ: Server requests agent to upload a WAV file."""
    rec_no: int
    upload_url: str          # HTTPS URL for multipart upload

    def pack(self) -> bytes:
        data = {"rec_no": self.rec_no, "upload_url": self.upload_url}
        return json.dumps(data, separators=(",", ":")).encode("utf-8")

    @classmethod
    def unpack(cls, data: bytes) -> RecUploadReqPayload:
        obj = json.loads(data.decode("utf-8"))
        return cls(**obj)


@dataclass(slots=True)
class RecUploadAckPayload:
    """REC_UPLOAD_ACK: Agent confirms WAV upload result."""
    rec_no: int
    status: int              # 0=success, 1=file_not_found, 2=upload_failed
    file_size: int

    _STRUCT: ClassVar[struct.Struct] = struct.Struct("!IBQ")
    _SIZE: ClassVar[int] = 13

    def pack(self) -> bytes:
        return self._STRUCT.pack(self.rec_no, self.status, self.file_size)

    @classmethod
    def unpack(cls, data: bytes) -> RecUploadAckPayload:
        if len(data) != cls._SIZE:
            raise ValueError("rec upload ack payload must be exactly 13 bytes")
        rec_no, status, file_size = cast(
            tuple[int, int, int], cls._STRUCT.unpack(data)
        )
        return cls(rec_no=rec_no, status=status, file_size=file_size)
```

### Step 4: Run tests — expect PASS

```bash
pytest tests/test_protocol_rec.py -v
```

### Step 5: Commit

```bash
git add shared/protocol.py tests/test_protocol_rec.py
git commit -m "feat: add recording analysis protocol extensions (REC_ANALYSIS_RESULT, REC_UPLOAD_REQ, REC_UPLOAD_ACK)"
```

---

## Task 2: Agent — Recording Watcher + Audio Quality Analyzer

**Files:**
- Copy from AirREC: `analyzer/models.py`, `energy.py`, `silence.py`, `audio_quality.py` → `agent/recording/`
- Create: `agent/recording/__init__.py`
- Create: `agent/recording/watcher.py` — watchdog-based new WAV detection
- Test: `tests/test_recording_watcher.py`

### Step 1: Copy AirREC analyzer modules into agent

Copy these files from `D:\Work\AirTech\PBXServer\AirRec\airec_api\analyzer\` to `agent/recording/`:
- `models.py` → as-is (AnalysisStatus, ChannelStats, AnalysisResult)
- `energy.py` → as-is (load_wav, rms_dbfs, channel_rms)
- `silence.py` → as-is (analyze_silence, analyze_file_silence)
- `audio_quality.py` → as-is (analyze_recording, analyze_batch)

Create `agent/recording/__init__.py`:
```python
"""AirREC recording analysis module for AI-LogOps Agent."""
```

### Step 2: Write failing test for RecordingWatcher

```python
# tests/test_recording_watcher.py
"""Tests for recording folder watcher."""
from __future__ import annotations
import asyncio
import os
import tempfile
import wave
import struct
import pytest

from agent.recording.watcher import RecordingWatcher


def _create_test_wav(path: str, duration_sec: float = 1.0, channels: int = 1):
    """Create a minimal test WAV file."""
    sample_rate = 8000
    n_frames = int(sample_rate * duration_sec)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        # Write silence
        wf.writeframes(b"\x00\x00" * n_frames * channels)


@pytest.mark.asyncio
async def test_watcher_detects_new_wav():
    """New WAV file in watched dir triggers on_new_recording callback."""
    detected: list[tuple[int, str]] = []

    async def on_new(rec_no: int, filepath: str, result) -> None:
        detected.append((rec_no, filepath))

    with tempfile.TemporaryDirectory() as tmpdir:
        watcher = RecordingWatcher(
            watch_dir=tmpdir,
            extensions=[".wav"],
            on_new_recording=on_new,
        )
        await watcher.start()
        try:
            # Create a WAV file
            wav_path = os.path.join(tmpdir, "test_001.wav")
            _create_test_wav(wav_path, duration_sec=0.5)

            # Wait for detection
            await asyncio.sleep(1.5)
        finally:
            await watcher.stop()

        assert len(detected) >= 1
        assert detected[0][1] == wav_path


@pytest.mark.asyncio
async def test_watcher_ignores_non_wav():
    """Non-WAV files should be ignored."""
    detected: list[str] = []

    async def on_new(rec_no: int, filepath: str, result) -> None:
        detected.append(filepath)

    with tempfile.TemporaryDirectory() as tmpdir:
        watcher = RecordingWatcher(
            watch_dir=tmpdir,
            extensions=[".wav"],
            on_new_recording=on_new,
        )
        await watcher.start()
        try:
            # Create a non-WAV file
            with open(os.path.join(tmpdir, "readme.txt"), "w") as f:
                f.write("not a wav")
            await asyncio.sleep(1.0)
        finally:
            await watcher.stop()

        assert len(detected) == 0
```

### Step 3: Run test — expect ImportError

```bash
pytest tests/test_recording_watcher.py -v
```

### Step 4: Implement RecordingWatcher

Create `agent/recording/watcher.py`:

```python
"""Recording folder watcher — detects new WAV files, runs audio quality analysis.

Based on agent/core/log_watcher.py pattern (watchdog Observer + asyncio queue).
Analysis engine from AirREC: agent/recording/audio_quality.py.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
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
    ) -> None:
        self._watch_dir = Path(watch_dir)
        self._extensions = {ext.lower() for ext in extensions}
        self._on_new_recording = on_new_recording

        self._observer = Observer()
        self._processed: set[str] = set()
        self._event_queue: asyncio.Queue[str] = asyncio.Queue()
        self._consumer_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = False
        self._logger = logging.getLogger(self.__class__.__name__)

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
        self._observer.join(timeout=5)
        if self._consumer_task is not None:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
        self._started = False

    def enqueue_file(self, filepath: str) -> None:
        p = Path(filepath)
        if p.suffix.lower() not in self._extensions:
            return
        if filepath in self._processed:
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
                result = await asyncio.to_thread(
                    analyze_recording, rec_no, filepath, 0.0
                )
                self._processed.add(filepath)
                await self._on_new_recording(rec_no, filepath, result)
            except Exception:
                self._logger.exception("Failed to analyze: %s", filepath)

    @staticmethod
    def _extract_rec_no(filepath: str) -> int:
        """Extract rec_no from filename. E.g. '12345.wav' → 12345."""
        name = Path(filepath).stem
        match = re.search(r"(\d+)", name)
        return int(match.group(1)) if match else 0
```

### Step 5: Run tests — expect PASS

```bash
pytest tests/test_recording_watcher.py -v
```

### Step 6: Commit

```bash
git add agent/recording/ tests/test_recording_watcher.py
git commit -m "feat: add recording watcher + audio quality analysis module (from AirREC)"
```

---

## Task 3: Agent — Integrate RecordingWatcher into Agent Lifecycle

**Files:**
- Modify: `agent/config.yaml` — Add `recording:` section
- Modify: `agent/service/win_service.py` — Wire RecordingWatcher into `_run_agent()`
- Modify: `agent/gui/app.py` — Wire RecordingWatcher into `_run_agent_async()`
- Modify: `agent/telegram/poller.py` — Add `/rec_status` command + anomaly alerts

### Step 1: Add recording config to `agent/config.yaml`

```yaml
recording:
  enabled: false
  watch_dir: "D:/AirSoft/Server2/Record"
  extensions: [".wav"]
  # 명령서버 업로드 URL (HTTPS)
  upload_base_url: ""
  # 이상 감지 시 텔레그램 알림
  alert_on_anomaly: true
```

### Step 2: Wire RecordingWatcher in `win_service.py` `_run_agent()` method

After existing watcher/poller setup (around line 287), add:

```python
# ── 녹취 폴더 감시 (옵션) ──
recording_cfg = _as_mapping(config.get("recording"))
rec_watcher = None
if _to_bool(recording_cfg.get("enabled"), False):
    from agent.recording.watcher import RecordingWatcher

    async def _on_new_recording(rec_no, filepath, result):
        """녹취 분석 완료 콜백: TCP로 결과 전송 + 이상 시 텔레그램 알림."""
        logger.info("rec_no=%s status=%s path=%s", rec_no, result.status.value, filepath)
        # TCP로 분석 결과 전송
        if tcp_client.is_connected:
            from shared.protocol import PacketType, RecAnalysisPayload
            payload = RecAnalysisPayload(
                rec_no=rec_no, status=result.status.value,
                left_rms_db=result.left.rms_db, right_rms_db=result.right.rms_db,
                left_silence_ratio=result.left.silence_ratio,
                right_silence_ratio=result.right.silence_ratio,
                dropout_count=result.dropout_count,
                duration_wav=result.duration_wav, duration_smdr=result.duration_smdr,
                is_stereo=result.is_stereo,
            )
            with contextlib.suppress(ConnectionError, OSError):
                await tcp_client.send_packet(PacketType.REC_ANALYSIS_RESULT, payload.pack())
        # 이상 감지 시 텔레그램 알림
        if _to_bool(recording_cfg.get("alert_on_anomaly"), True):
            if result.status.value not in ("OK",):
                with contextlib.suppress(Exception):
                    await poller.send_message(
                        f"[녹취 이상] rec_no={rec_no}\n"
                        f"상태: {result.status.value}\n"
                        f"L: {result.left.rms_db:.1f}dB / R: {result.right.rms_db:.1f}dB"
                    )

    rec_watcher = RecordingWatcher(
        watch_dir=_to_str(recording_cfg.get("watch_dir"), ""),
        extensions=_to_list_str(recording_cfg.get("extensions"), [".wav"]),
        on_new_recording=_on_new_recording,
    )
    await rec_watcher.start()
    logger.info(">>> RecordingWatcher started")
```

Add cleanup in the `finally` block:
```python
if rec_watcher is not None:
    with contextlib.suppress(Exception):
        await rec_watcher.stop()
```

### Step 3: Same wiring in `gui/app.py` `_run_agent_async()`

Same pattern as win_service.py, with GUI log count update in callback.

### Step 4: Add `send_packet()` method to TCPClient

In `agent/core/tcp_client.py`, add a generic method for sending new packet types:

```python
async def send_packet(self, packet_type: PacketType, payload: bytes) -> None:
    """Send an arbitrary packet."""
    if not self._connected or self._writer is None:
        return
    pkt = Packet.build(packet_type, payload)
    self._writer.write(pkt)
    await self._writer.drain()
```

### Step 5: Add `/rec_status` Telegram command

In `agent/telegram/poller.py`, add handler:

```python
async def _handle_rec_status(self, chat_id: int) -> None:
    """녹취 감시 상태를 보여준다."""
    # RecordingWatcher 참조로 processed 카운트 등 표시
    await self._send(chat_id, "녹취 감시 상태: ...")
```

### Step 6: Commit

```bash
git add agent/config.yaml agent/service/win_service.py agent/gui/app.py agent/core/tcp_client.py agent/telegram/poller.py
git commit -m "feat: integrate RecordingWatcher into agent lifecycle with TCP reporting + Telegram alerts"
```

---

## Task 4: Agent — WAV Upload Client (HTTPS Multipart)

**Files:**
- Create: `agent/recording/uploader.py` — HTTPS multipart WAV uploader
- Modify: `agent/core/tcp_client.py` — Handle REC_UPLOAD_REQ → trigger upload
- Test: `tests/test_rec_uploader.py`

### Step 1: Write failing test

```python
# tests/test_rec_uploader.py
"""Tests for WAV file uploader."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, patch
from agent.recording.uploader import RecordingUploader


@pytest.mark.asyncio
async def test_upload_file_not_found():
    uploader = RecordingUploader(agent_id="test-agent")
    rec_no, status, size = await uploader.upload(
        rec_no=1, filepath="/nonexistent.wav", upload_url="https://example.com/upload"
    )
    assert status == 1  # file_not_found
    assert size == 0
```

### Step 2: Implement uploader

```python
# agent/recording/uploader.py
"""HTTPS multipart WAV file uploader."""
from __future__ import annotations

import logging
from pathlib import Path

import httpx

_logger = logging.getLogger(__name__)


class RecordingUploader:
    """Upload WAV files to command server via HTTPS multipart."""

    def __init__(self, agent_id: str, timeout: float = 120.0) -> None:
        self._agent_id = agent_id
        self._timeout = timeout

    async def upload(
        self, rec_no: int, filepath: str, upload_url: str
    ) -> tuple[int, int, int]:
        """Upload a WAV file.

        Returns: (rec_no, status, file_size)
          status: 0=success, 1=file_not_found, 2=upload_failed
        """
        path = Path(filepath)
        if not path.is_file():
            _logger.warning("rec_no=%s file not found: %s", rec_no, filepath)
            return rec_no, 1, 0

        file_size = path.stat().st_size
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                with path.open("rb") as f:
                    resp = await client.post(
                        upload_url,
                        files={"file": (path.name, f, "audio/wav")},
                        data={"rec_no": str(rec_no), "agent_id": self._agent_id},
                    )
                if resp.status_code == 200:
                    _logger.info("rec_no=%s uploaded (%d bytes)", rec_no, file_size)
                    return rec_no, 0, file_size
                else:
                    _logger.error("rec_no=%s upload HTTP %d", rec_no, resp.status_code)
                    return rec_no, 2, file_size
        except Exception:
            _logger.exception("rec_no=%s upload failed", rec_no)
            return rec_no, 2, file_size
```

### Step 3: Wire upload request handling in TCPClient

Add `REC_UPLOAD_REQ` handler in `tcp_client.py` `_dispatch()`:

```python
elif ptype == PacketType.REC_UPLOAD_REQ:
    req = RecUploadReqPayload.unpack(payload)
    if self.on_rec_upload_req is not None:
        await self.on_rec_upload_req(req)
```

### Step 4: Commit

```bash
git add agent/recording/uploader.py tests/test_rec_uploader.py agent/core/tcp_client.py
git commit -m "feat: add WAV upload client (HTTPS multipart) + TCP upload request handler"
```

---

## Task 5: Command Server — FastAPI + WAV Storage + Upload Endpoint

**Files:**
- Create: `server/airec/__init__.py`
- Create: `server/airec/config.py` — AirREC-specific config
- Create: `server/airec/storage.py` — WAV file storage manager
- Create: `server/airec/routers/upload.py` — POST /api/upload
- Create: `server/airec/routers/recordings.py` — GET /api/recordings, /api/stream/{rec_no}
- Adapt from: `airec_api/routers/recordings.py`, `airec_api/routers/stream.py`

### Key points:
- WAV files stored under `server/storage/recordings/{agent_id}/{date}/`
- Upload endpoint validates agent_id, stores file, returns 200
- Stream endpoint supports HTTP Range (206 Partial Content) for wavesurfer.js
- Recordings list queries MySQL `rec_his` + local storage index

### Step 1: Implement storage manager

```python
# server/airec/storage.py
"""WAV file storage manager."""
from pathlib import Path
from datetime import date


class RecordingStorage:
    def __init__(self, base_dir: str) -> None:
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)

    def store(self, agent_id: str, rec_no: int, data: bytes, filename: str) -> Path:
        today = date.today().strftime("%Y%m%d")
        target_dir = self._base / agent_id / today
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        target.write_bytes(data)
        return target

    def get_path(self, agent_id: str, rec_no: int, filename: str) -> Path | None:
        # Search in date-based dirs
        agent_dir = self._base / agent_id
        if not agent_dir.is_dir():
            return None
        for date_dir in sorted(agent_dir.iterdir(), reverse=True):
            candidate = date_dir / filename
            if candidate.is_file():
                return candidate
        return None
```

### Step 2: Implement upload router

Adapt pattern from `airec_api/routers/recordings.py`.

### Step 3: Implement stream router

Copy `airec_api/routers/stream.py` — change file lookup to use RecordingStorage.

### Step 4: Commit

```bash
git add server/airec/
git commit -m "feat: add command server WAV storage + upload/stream endpoints"
```

---

## Task 6: Command Server — STT + Quality Analysis Pipeline

**Files:**
- Copy from AirREC: `analyzer/stt.py`, `call_quality.py`, `keywords.py`, `db_helpers.py` → `server/airec/analyzer/`
- Copy: `analyzer/required_phrases.json`, `forbidden_words.json` → `server/airec/analyzer/`
- Create: `server/airec/analyzer/pipeline.py` — orchestrates STT + quality for a single recording
- Adapt: `airec_api/scheduler/` → `server/airec/scheduler/`

### Key points:
- STT runs on server (where GPU/CPU power is available)
- Pipeline: receive uploaded WAV → STT → call quality → save to DB → update status
- Scheduler polls DB for recordings with analysis result but no transcript
- Sends REC_UPLOAD_REQ to agent for WAV files not yet uploaded

### Step 1: Copy analyzer modules

These are copied as-is from AirREC:
- `stt.py` — faster-whisper wrapper (no changes needed)
- `call_quality.py` — quality scoring (no changes needed)
- `keywords.py` — keyword matching (adjust path to JSON files)
- `db_helpers.py` — adapt imports from `server.airec.database` instead of `database`

### Step 2: Create pipeline orchestrator

```python
# server/airec/analyzer/pipeline.py
"""STT + quality analysis pipeline for a single recording."""
from __future__ import annotations
import logging
from pathlib import Path

_logger = logging.getLogger(__name__)


def process_recording(rec_no: int, wav_path: str) -> dict:
    """Run full analysis pipeline: STT → quality → DB save.

    Returns dict with transcript_id, quality result, etc.
    """
    from .stt import transcribe_file
    from .call_quality import analyze_call_quality
    from .db_helpers import save_transcript, save_call_quality

    # STT
    transcript = transcribe_file(wav_path)
    transcript_id = save_transcript(rec_no, transcript)
    _logger.info("rec_no=%s STT done: %d segments", rec_no, len(transcript.segments))

    # Quality
    quality = analyze_call_quality(transcript)
    save_call_quality(rec_no, transcript_id, quality)
    _logger.info("rec_no=%s quality=%.1f", rec_no, quality.score_total)

    return {
        "rec_no": rec_no,
        "transcript_id": transcript_id,
        "score_total": quality.score_total,
    }
```

### Step 3: Adapt scheduler

Copy `airec_api/scheduler/scheduler.py` and `jobs.py`. Key changes:
- Instead of reading local WAV files, scheduler queries DB for recordings that have `REC_ANALYSIS_RESULT` but no transcript
- For missing WAV files, send `REC_UPLOAD_REQ` via TCP to the agent
- When WAV upload arrives, trigger `process_recording()`

### Step 4: Commit

```bash
git add server/airec/analyzer/ server/airec/scheduler/
git commit -m "feat: add STT + quality pipeline on command server (adapted from AirREC)"
```

---

## Task 7: Command Server — Web Dashboard

**Files:**
- Copy from AirREC: `templates/`, `static/`, `routers/views.py` → `server/airec/`
- Adapt: Template URLs to match new API paths
- Adapt: WAV streaming URL to use server's `/api/stream/{rec_no}`

### Key points:
- Copy templates as-is from `airec_api/templates/` (base.html, recordings.html, detail.html, dashboard.html)
- Copy static files from `airec_api/static/` (css/style.css, js/app.js)
- Copy `routers/views.py` — adapt DB query imports
- wavesurfer.js points to server's stream endpoint (not agent)
- Chart.js dashboard works unchanged (queries server DB)

### Step 1: Copy and adapt templates

Minimal changes:
- `detail.html`: wavesurfer URL → `/api/stream/{rec_no}` (same as before, now served from server storage)
- `recordings.html`: STT trigger button → `POST /api/transcribe` (same)

### Step 2: Wire into FastAPI app

In `server/airec/main.py` or existing server app:
```python
app.include_router(views.router)
app.mount("/static", StaticFiles(directory="server/airec/static"), name="static")
```

### Step 3: Commit

```bash
git add server/airec/templates/ server/airec/static/ server/airec/routers/views.py
git commit -m "feat: add web dashboard (recordings, detail, charts) from AirREC"
```

---

## Task 8: Command Server — TCP Handler for Recording Messages

**Files:**
- Modify: `server/core/tcp_server.py` — Handle REC_ANALYSIS_RESULT, send REC_UPLOAD_REQ
- Create: `server/airec/rec_handler.py` — Recording message handler (server-side)

### Key points:
- When server receives `REC_ANALYSIS_RESULT`, store in DB, check if STT needed
- If STT needed and WAV not uploaded yet, send `REC_UPLOAD_REQ` to agent
- When `REC_UPLOAD_ACK` received with status=0, trigger STT pipeline

### Commit

```bash
git add server/core/tcp_server.py server/airec/rec_handler.py
git commit -m "feat: handle recording analysis results + upload orchestration on server"
```

---

## Task 9: DB Schema + Config Updates

**Files:**
- Copy: `airec_api/schema/create_tables.sql` → `server/airec/schema/`
- Add: `rec_audio_quality` table for storing agent-reported analysis results
- Update: `agent/config.yaml` template with recording section
- Update: `agent.spec` — add `numpy` hidden imports
- Update: `requirements.txt` — add `numpy` to agent deps

### New table: `rec_audio_quality`

```sql
CREATE TABLE IF NOT EXISTS rec_audio_quality (
    id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    rec_no          INT UNSIGNED NOT NULL,
    agent_id        VARCHAR(32) NOT NULL,
    analyzed_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status          VARCHAR(20) NOT NULL COMMENT 'OK, EMPTY, MUTED_L, MUTED_R, DROPOUT, MISMATCH',
    left_rms_db     FLOAT DEFAULT NULL,
    right_rms_db    FLOAT DEFAULT NULL,
    left_silence    FLOAT DEFAULT NULL,
    right_silence   FLOAT DEFAULT NULL,
    dropout_count   INT DEFAULT 0,
    duration_wav    FLOAT DEFAULT NULL,
    duration_smdr   FLOAT DEFAULT NULL,
    is_stereo       TINYINT(1) DEFAULT 0,
    INDEX idx_rec_no (rec_no),
    INDEX idx_status (status),
    INDEX idx_analyzed_at (analyzed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='Agent-reported audio quality analysis';
```

### Commit

```bash
git add server/airec/schema/ agent/config.yaml agent.spec requirements.txt
git commit -m "feat: add DB schema + config updates for recording analysis"
```

---

## Task 10: Integration Test + Build

**Files:**
- Test: Full flow — agent detects WAV → analyzes → reports → server requests upload → STT → dashboard shows result
- Build: PyInstaller with numpy added
- Send: Telegram deploy

### Step 1: Run all tests

```bash
pytest tests/ -v --tb=short
```

### Step 2: Build agent (with numpy)

```bash
python deploy.py --skip-send
```

### Step 3: Verify agent size increase is acceptable

Expected: ~30-35MB (was 27.5MB, numpy adds ~5-8MB)

### Step 4: Send to Telegram

```bash
python deploy.py --skip-build --bot-token "..." --chat-id ...
```

### Step 5: Final commit

```bash
git add -A
git commit -m "feat: complete AirREC agent + command server integration"
```
