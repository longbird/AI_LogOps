"""Tests for recording folder watcher and audio quality analyzer."""

from __future__ import annotations

import asyncio
import os
import tempfile
import wave

import pytest

from agent.recording.models import AnalysisStatus, AnalysisResult, ChannelStats
from agent.recording.energy import load_wav, rms_dbfs, channel_rms
from agent.recording.silence import analyze_silence, analyze_file_silence
from agent.recording.audio_quality import analyze_recording, get_wav_duration
from agent.recording.watcher import RecordingWatcher


def _create_test_wav(
    path: str,
    duration_sec: float = 1.0,
    channels: int = 1,
    sample_rate: int = 8000,
    silence: bool = True,
) -> None:
    """Create a minimal test WAV file (silence or tone)."""
    n_frames = int(sample_rate * duration_sec)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        if silence:
            wf.writeframes(b"\x00\x00" * n_frames * channels)
        else:
            # Generate a simple tone (1kHz sine) for non-silence tests
            import struct as st
            import math

            samples = []
            for i in range(n_frames * channels):
                val = int(
                    16000 * math.sin(2 * math.pi * 1000 * (i // channels) / sample_rate)
                )
                samples.append(st.pack("<h", val))
            wf.writeframes(b"".join(samples))


class TestModels:
    def test_analysis_status_values(self):
        assert AnalysisStatus.OK.value == "OK"
        assert AnalysisStatus.EMPTY.value == "EMPTY"
        assert AnalysisStatus.MUTED_L.value == "MUTED_L"

    def test_channel_stats_defaults(self):
        stats = ChannelStats()
        assert stats.rms_db == -96.0
        assert stats.silence_ratio == 1.0

    def test_analysis_result_defaults(self):
        r = AnalysisResult(rec_no=1)
        assert r.status == AnalysisStatus.OK
        assert r.dropout_count == 0


class TestEnergy:
    def test_load_wav_mono(self):
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            _create_test_wav(path, duration_sec=0.5, channels=1)
            samples, sr, n_ch = load_wav(path)
            assert sr == 8000
            assert n_ch == 1
            assert len(samples) == 4000
        finally:
            os.unlink(path)

    def test_load_wav_stereo(self):
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            _create_test_wav(path, duration_sec=0.5, channels=2)
            samples, sr, n_ch = load_wav(path)
            assert n_ch == 2
            assert samples.shape == (4000, 2)
        finally:
            os.unlink(path)

    def test_rms_dbfs_silence(self):
        import numpy as np

        silence = np.zeros(1000, dtype=np.float64)
        assert rms_dbfs(silence) == -96.0

    def test_rms_dbfs_nonzero(self):
        import numpy as np

        tone = np.ones(1000, dtype=np.float64) * 0.5
        db = rms_dbfs(tone)
        assert -7.0 < db < -5.0  # ~-6.02 dBFS

    def test_channel_rms_mono(self):
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            _create_test_wav(path, channels=1, silence=True)
            l_db, r_db, is_stereo = channel_rms(path)
            assert not is_stereo
            assert l_db == r_db
            assert l_db == -96.0
        finally:
            os.unlink(path)


class TestSilence:
    def test_silence_detection_on_silent_wav(self):
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            _create_test_wav(path, duration_sec=1.0, channels=1, silence=True)
            left, right, is_stereo = analyze_file_silence(path)
            assert not is_stereo
            assert left.silence_ratio == 1.0
        finally:
            os.unlink(path)

    def test_silence_detection_on_tone_wav(self):
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            _create_test_wav(path, duration_sec=1.0, channels=1, silence=False)
            left, right, is_stereo = analyze_file_silence(path)
            assert left.silence_ratio < 0.5  # tone should not be mostly silent
        finally:
            os.unlink(path)


class TestAudioQuality:
    def test_analyze_silent_mono(self):
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            _create_test_wav(path, duration_sec=1.0, channels=1, silence=True)
            result = analyze_recording(rec_no=1, filepath=path)
            assert result.status == AnalysisStatus.EMPTY
            assert result.rec_no == 1
        finally:
            os.unlink(path)

    def test_analyze_tone_mono(self):
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            _create_test_wav(path, duration_sec=1.0, channels=1, silence=False)
            result = analyze_recording(rec_no=2, filepath=path)
            assert result.status == AnalysisStatus.OK
            assert result.rec_no == 2
        finally:
            os.unlink(path)

    def test_get_wav_duration(self):
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            _create_test_wav(path, duration_sec=2.0, channels=1)
            dur = get_wav_duration(path)
            assert abs(dur - 2.0) < 0.01
        finally:
            os.unlink(path)

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            analyze_recording(rec_no=0, filepath="/nonexistent.wav")


@pytest.mark.asyncio
async def test_watcher_detects_new_wav():
    """New WAV file in watched dir triggers on_new_recording callback."""
    from datetime import datetime as _dt

    detected: list[tuple[int, str, AnalysisResult]] = []

    async def on_new(rec_no: int, filepath: str, result: AnalysisResult) -> None:
        detected.append((rec_no, filepath, result))

    with tempfile.TemporaryDirectory() as tmpdir:
        # 날짜 서브디렉토리 생성 (실환경 구조: watch_dir/YYYYMMDD/)
        date_dir = os.path.join(tmpdir, _dt.now().strftime("%Y%m%d"))
        os.makedirs(date_dir, exist_ok=True)

        watcher = RecordingWatcher(
            watch_dir=tmpdir,
            extensions=[".wav"],
            on_new_recording=on_new,
            min_file_size=0,
            min_duration_sec=0.0,
        )
        await watcher.start()
        try:
            wav_path = os.path.join(date_dir, "test_001.wav")
            _create_test_wav(wav_path, duration_sec=0.5)
            # 안정화 대기: STABLE_CHECK_SEC(2) × (STABLE_COUNT(2)+1) + 여유
            await asyncio.sleep(8.0)
        finally:
            await watcher.stop()

        assert len(detected) >= 1
        assert detected[0][0] == 1  # rec_no extracted from "test_001"
        assert detected[0][1] == wav_path
        assert isinstance(detected[0][2], AnalysisResult)


@pytest.mark.asyncio
async def test_watcher_ignores_non_wav():
    """Non-WAV files should be ignored."""
    detected: list[str] = []

    async def on_new(rec_no: int, filepath: str, result: AnalysisResult) -> None:
        detected.append(filepath)

    with tempfile.TemporaryDirectory() as tmpdir:
        watcher = RecordingWatcher(
            watch_dir=tmpdir,
            extensions=[".wav"],
            on_new_recording=on_new,
        )
        await watcher.start()
        try:
            with open(os.path.join(tmpdir, "readme.txt"), "w") as f:
                f.write("not a wav")
            await asyncio.sleep(1.5)
        finally:
            await watcher.stop()

        assert len(detected) == 0


@pytest.mark.asyncio
async def test_watcher_processed_count():
    """processed_count property tracks number of analyzed files."""
    from datetime import datetime as _dt

    async def on_new(rec_no: int, filepath: str, result: AnalysisResult) -> None:
        pass

    with tempfile.TemporaryDirectory() as tmpdir:
        date_dir = os.path.join(tmpdir, _dt.now().strftime("%Y%m%d"))
        os.makedirs(date_dir, exist_ok=True)

        watcher = RecordingWatcher(
            watch_dir=tmpdir,
            extensions=[".wav"],
            on_new_recording=on_new,
            min_file_size=0,
            min_duration_sec=0.0,
        )
        assert watcher.processed_count == 0
        await watcher.start()
        try:
            _create_test_wav(os.path.join(date_dir, "rec_100.wav"), duration_sec=0.5)
            await asyncio.sleep(8.0)
        finally:
            await watcher.stop()

        assert watcher.processed_count == 1
