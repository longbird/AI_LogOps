"""Tests for WAV file uploader."""

from __future__ import annotations

import os
import tempfile
import wave

import pytest

from agent.recording.uploader import RecordingUploader


def _create_test_wav(path: str, duration_sec: float = 0.5) -> None:
    """Create a minimal mono WAV file."""
    sample_rate = 8000
    n_frames = int(sample_rate * duration_sec)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_frames)


class TestRecordingUploader:
    def test_init(self) -> None:
        uploader = RecordingUploader(agent_id="test-agent")
        assert uploader._agent_id == "test-agent"
        assert uploader._timeout == 120.0

    def test_init_custom_timeout(self) -> None:
        uploader = RecordingUploader(agent_id="test", timeout=30.0)
        assert uploader._timeout == 30.0

    @pytest.mark.asyncio
    async def test_upload_file_not_found(self) -> None:
        uploader = RecordingUploader(agent_id="test-agent")
        fname, status, size = await uploader.upload(
            filename="rec_001.wav",
            filepath="/nonexistent/path/recording.wav",
            upload_url="https://example.com/api/upload",
        )
        assert fname == "rec_001.wav"
        assert status == 1  # file_not_found
        assert size == 0

    @pytest.mark.asyncio
    async def test_upload_connection_refused(self) -> None:
        """Upload to unreachable server returns status=2 (upload_failed)."""
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            _create_test_wav(path)
            uploader = RecordingUploader(agent_id="test-agent", timeout=2.0)
            fname, status, size = await uploader.upload(
                filename="rec_042.wav",
                filepath=path,
                upload_url="https://127.0.0.1:19999/api/upload",
            )
            assert fname == "rec_042.wav"
            assert status == 2  # upload_failed (connection refused)
            assert size > 0  # file exists, size was read
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_upload_invalid_url(self) -> None:
        """Upload to invalid URL returns status=2."""
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            _create_test_wav(path)
            uploader = RecordingUploader(agent_id="test-agent", timeout=2.0)
            fname, status, size = await uploader.upload(
                filename="rec_099.wav",
                filepath=path,
                upload_url="not-a-valid-url",
            )
            assert fname == "rec_099.wav"
            assert status == 2
            assert size > 0
        finally:
            os.unlink(path)
