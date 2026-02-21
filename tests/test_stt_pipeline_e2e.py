"""E2E tests for the STT recording analysis pipeline.

Tests the full flow: RecHandler → upload decision → STT pipeline → quality analysis
→ SttResultPayload, with only faster-whisper mocked (external dependency).
"""

from __future__ import annotations

import json
import sys
import types
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from server.airec.rec_handler import RecHandler
from server.airec.storage import RecordingStorage
from server.airec.analyzer.call_quality import (
    CallQualityResult,
    analyze_call_quality,
    compute_first_response,
    compute_talk_ratios,
    compute_scores,
)
from shared.protocol import RecAnalysisPayload, SttResultPayload

# faster_whisper is an optional dependency not installed in test env.
# We stub the module so server.airec.analyzer.stt can be imported.
if "faster_whisper" not in sys.modules:
    _fw_stub = types.ModuleType("faster_whisper")
    setattr(_fw_stub, "WhisperModel", MagicMock)
    sys.modules["faster_whisper"] = _fw_stub

from server.airec.analyzer.pipeline import PipelineResult, run_pipeline  # noqa: E402
from server.airec.analyzer.stt import extract_channel_wav, transcribe_file  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_stereo_wav(path: str, duration_sec: float = 5.0) -> None:
    """Create a minimal stereo WAV file (silence)."""
    sample_rate = 8000
    n_frames = int(sample_rate * duration_sec)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_frames * 2)


def _create_mono_wav(path: str, duration_sec: float = 5.0) -> None:
    """Create a minimal mono WAV file (silence)."""
    sample_rate = 8000
    n_frames = int(sample_rate * duration_sec)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_frames)


def _create_mulaw_stereo_wav(path: str, duration_sec: float = 5.0) -> None:
    """Create a minimal stereo μ-law WAV file (silence).

    μ-law format (format code 7) is used in telephone recordings.
    This creates a raw RIFF WAV with μ-law encoding.
    """
    import struct

    sample_rate = 8000
    n_channels = 2
    n_frames = int(sample_rate * duration_sec)

    # μ-law silence is 0xFF (all bits set)
    mulaw_silence = b"\xff" * (n_frames * n_channels)

    with open(path, "wb") as f:
        # RIFF header
        f.write(b"RIFF")
        # File size (placeholder, will update)
        file_size_pos = f.tell()
        f.write(b"\x00\x00\x00\x00")
        f.write(b"WAVE")

        # fmt chunk
        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))  # chunk size
        f.write(struct.pack("<H", 7))  # format code (7 = μ-law)
        f.write(struct.pack("<H", n_channels))
        f.write(struct.pack("<I", sample_rate))
        f.write(struct.pack("<I", sample_rate * n_channels))  # byte rate
        f.write(struct.pack("<H", n_channels))  # block align
        f.write(struct.pack("<H", 8))  # bits per sample

        # data chunk
        f.write(b"data")
        f.write(struct.pack("<I", len(mulaw_silence)))
        f.write(mulaw_silence)

        # Update file size
        file_size = f.tell() - 8
        f.seek(file_size_pos)
        f.write(struct.pack("<I", file_size))


@dataclass
class FakeSegment:
    """Fake STT segment for mocking."""

    start: float
    end: float
    text: str


@dataclass
class FakeSpeakerSegment:
    """Fake speaker-labeled segment."""

    time: float
    end: float
    speaker: str
    text: str


@dataclass
class FakeTranscriptResult:
    """Fake transcript result matching TranscriptResult interface."""

    segments: list[FakeSpeakerSegment]
    agent_segments: list[FakeSegment]
    customer_segments: list[FakeSegment]
    agent_text: str
    customer_text: str
    full_text: str
    duration_sec: float
    word_count: int


def _make_fake_transcript() -> FakeTranscriptResult:
    """Create a realistic fake transcript with agent and customer segments."""
    agent_segs = [
        FakeSegment(start=0.0, end=2.5, text="안녕하세요 감사합니다"),
        FakeSegment(start=5.0, end=8.0, text="확인해 드리겠습니다 잠시만요"),
        FakeSegment(start=12.0, end=15.0, text="좋은 하루 되세요"),
    ]
    customer_segs = [
        FakeSegment(start=3.0, end=4.5, text="네 주문 확인 부탁드려요"),
        FakeSegment(start=9.0, end=11.0, text="네 알겠습니다"),
    ]
    speaker_segs = [
        FakeSpeakerSegment(time=s.start, end=s.end, speaker="agent", text=s.text)
        for s in agent_segs
    ] + [
        FakeSpeakerSegment(time=s.start, end=s.end, speaker="customer", text=s.text)
        for s in customer_segs
    ]
    speaker_segs.sort(key=lambda x: x.time)

    agent_text = " ".join(s.text for s in agent_segs)
    customer_text = " ".join(s.text for s in customer_segs)
    full_text = " ".join(s.text for s in speaker_segs)

    return FakeTranscriptResult(
        segments=speaker_segs,
        agent_segments=agent_segs,
        customer_segments=customer_segs,
        agent_text=agent_text,
        customer_text=customer_text,
        full_text=full_text,
        duration_sec=15.0,
        word_count=len(full_text.split()),
    )


def _make_analysis_payload(
    filename: str = "rec_1001.wav",
    *,
    status: str = "OK",
    is_stereo: bool = True,
    duration_wav: float = 30.0,
) -> RecAnalysisPayload:
    """Create a RecAnalysisPayload for testing."""
    return RecAnalysisPayload(
        filename=filename,
        status=status,
        left_rms_db=-20.0,
        right_rms_db=-22.0,
        left_silence_ratio=0.1,
        right_silence_ratio=0.15,
        dropout_count=0,
        duration_wav=duration_wav,
        duration_smdr=duration_wav,
        is_stereo=is_stereo,
    )


# ---------------------------------------------------------------------------
# RecHandler: analysis result handling + upload decision
# ---------------------------------------------------------------------------


class TestRecHandlerAnalysis:
    """Test RecHandler.handle_analysis_result and upload decision logic."""

    def test_ok_stereo_recording_requests_upload(self) -> None:
        handler = RecHandler(upload_base_url="http://localhost:8080")
        payload = _make_analysis_payload(
            filename="rec_100.wav", status="OK", is_stereo=True
        )

        result = handler.handle_analysis_result("agent-1", payload)

        assert result is not None
        assert result.filename == "rec_100.wav"
        assert "upload" in result.upload_url

    def test_mono_recording_triggers_upload(self) -> None:
        """Mono recordings are now eligible for upload (STT handles mono)."""
        handler = RecHandler()
        payload = _make_analysis_payload(is_stereo=False)

        result = handler.handle_analysis_result("agent-1", payload)

        assert result is not None
        assert result.filename == payload.filename

    def test_short_recording_skips_upload(self) -> None:
        handler = RecHandler()
        payload = _make_analysis_payload(duration_wav=2.0)

        result = handler.handle_analysis_result("agent-1", payload)

        assert result is None

    def test_non_ok_status_skips_upload(self) -> None:
        handler = RecHandler()
        for status in ("EMPTY", "MUTED", "ERROR"):
            payload = _make_analysis_payload(status=status)
            result = handler.handle_analysis_result("agent-1", payload)
            assert result is None, f"status={status} should not request upload"

    def test_analysis_record_stored(self) -> None:
        handler = RecHandler()
        payload = _make_analysis_payload(filename="rec_200.wav")

        handler.handle_analysis_result("agent-1", payload)

        assert ("agent-1", "rec_200.wav") in handler.records
        record = handler.records[("agent-1", "rec_200.wav")]
        assert record.status == "OK"
        assert record.is_stereo is True
        assert record.uploaded is False

    def test_upload_ack_marks_uploaded(self) -> None:
        handler = RecHandler()
        payload = _make_analysis_payload(filename="rec_300.wav")
        handler.handle_analysis_result("agent-1", payload)

        handler.handle_upload_ack(
            "agent-1", filename="rec_300.wav", status=0, file_size=1024
        )

        assert handler.records[("agent-1", "rec_300.wav")].uploaded is True

    def test_already_uploaded_skips_re_upload(self) -> None:
        handler = RecHandler()
        payload = _make_analysis_payload(filename="rec_400.wav")
        handler.handle_analysis_result("agent-1", payload)
        handler.handle_upload_ack(
            "agent-1", filename="rec_400.wav", status=0, file_size=1024
        )

        # Verify the record is marked uploaded
        assert handler.records[("agent-1", "rec_400.wav")].uploaded is True

    def test_agent_stats(self) -> None:
        handler = RecHandler()
        handler.handle_analysis_result(
            "a1", _make_analysis_payload(filename="rec_001.wav", status="OK")
        )
        handler.handle_analysis_result(
            "a1", _make_analysis_payload(filename="rec_002.wav", status="MUTED")
        )
        handler.handle_upload_ack("a1", filename="rec_001.wav", status=0, file_size=100)

        stats = handler.get_agent_stats("a1")
        assert stats["total"] == 2
        assert stats["ok"] == 1
        assert stats["uploaded"] == 1


# ---------------------------------------------------------------------------
# RecordingStorage: store and retrieve WAV files
# ---------------------------------------------------------------------------


class TestRecordingStorage:
    def test_store_and_find(self, tmp_path: Path) -> None:
        storage = RecordingStorage(base_dir=str(tmp_path / "recordings"))
        data = b"RIFF" + b"\x00" * 100

        saved = storage.store("agent-1", data, "rec_1001.wav")

        assert saved.exists()
        assert saved.read_bytes() == data

    def test_find_by_filename(self, tmp_path: Path) -> None:
        storage = RecordingStorage(base_dir=str(tmp_path / "recordings"))
        data = b"RIFF" + b"\x00" * 50
        storage.store("agent-1", data, "rec_2001.wav")

        found = storage.find_by_filename("rec_2001.wav")

        assert found is not None
        assert found.name == "rec_2001.wav"

    def test_find_nonexistent_returns_none(self, tmp_path: Path) -> None:
        storage = RecordingStorage(base_dir=str(tmp_path / "recordings"))
        assert storage.find_by_filename("rec_9999.wav") is None

    def test_list_recordings(self, tmp_path: Path) -> None:
        storage = RecordingStorage(base_dir=str(tmp_path / "recordings"))
        storage.store("agent-1", b"\x00" * 10, "rec_1.wav")
        storage.store("agent-1", b"\x00" * 20, "rec_2.wav")

        listings = storage.list_recordings(agent_id="agent-1")
        assert len(listings) == 2
        fnames = {entry["filename"] for entry in listings}
        assert fnames == {"rec_1.wav", "rec_2.wav"}


# ---------------------------------------------------------------------------
# Call quality analysis (no external deps)
# ---------------------------------------------------------------------------


class TestCallQuality:
    def test_first_response_basic(self) -> None:
        agent = [FakeSegment(start=5.0, end=7.0, text="응")]
        customer = [FakeSegment(start=3.0, end=4.0, text="질문")]

        resp = compute_first_response(agent, customer)
        assert resp == 2.0  # agent spoke 2s after customer

    def test_first_response_no_agent(self) -> None:
        assert compute_first_response([], [FakeSegment(0, 1, "hi")]) == 0.0

    def test_talk_ratios(self) -> None:
        agent = [FakeSegment(start=0.0, end=5.0, text="talk")]
        customer = [FakeSegment(start=5.0, end=8.0, text="talk")]

        a_ratio, c_ratio, s_ratio = compute_talk_ratios(agent, customer, 10.0)
        assert a_ratio == 0.5
        assert c_ratio == 0.3
        assert s_ratio == 0.2

    def test_scores_fast_response_no_forbidden(self) -> None:
        total, resp, phrase, silence = compute_scores(
            first_response=2.0,
            silence_ratio=0.15,
            required_hit=True,
            forbidden_hit=False,
        )
        assert resp == 100.0  # ≤3s
        assert phrase == 100.0  # required hit, no forbidden
        assert silence == 100.0  # ≤20%
        assert total == 100.0

    def test_scores_slow_response_forbidden(self) -> None:
        total, resp, phrase, silence = compute_scores(
            first_response=15.0,
            silence_ratio=0.60,
            required_hit=True,
            forbidden_hit=True,
        )
        assert resp == 0.0  # ≥15s
        assert phrase == 0.0  # forbidden
        assert silence == 0.0  # ≥60%
        assert total == 0.0

    def test_analyze_call_quality_with_transcript(self) -> None:
        transcript = _make_fake_transcript()
        result = analyze_call_quality(transcript)

        assert isinstance(result, CallQualityResult)
        assert result.score_total > 0
        assert result.agent_talk_ratio > 0
        assert result.customer_talk_ratio > 0
        # "감사합니다", "안녕하세요" in agent text → required phrases hit
        assert result.required_phrase_hit is True


# ---------------------------------------------------------------------------
# Full pipeline E2E (mock STT, test everything else)
# ---------------------------------------------------------------------------


class TestPipelineE2E:
    """Test run_pipeline with mocked STT — exercises quality analysis + scoring."""

    def test_pipeline_returns_complete_result(self, tmp_path: Path) -> None:
        wav_path = str(tmp_path / "test_1001.wav")
        _create_stereo_wav(wav_path, duration_sec=5.0)

        fake_transcript = _make_fake_transcript()

        with patch(
            "server.airec.analyzer.stt.transcribe_file",
            return_value=fake_transcript,
        ):
            result = run_pipeline("test_1001.wav", wav_path)

        assert isinstance(result, PipelineResult)
        assert result.filename == "test_1001.wav"
        assert result.full_text != ""
        assert result.agent_text != ""
        assert result.customer_text != ""
        assert result.duration_sec == 15.0
        assert result.word_count > 0
        assert result.score_total > 0

    def test_pipeline_segments_json_valid(self, tmp_path: Path) -> None:
        wav_path = str(tmp_path / "test_1002.wav")
        _create_stereo_wav(wav_path)

        with patch(
            "server.airec.analyzer.stt.transcribe_file",
            return_value=_make_fake_transcript(),
        ):
            result = run_pipeline("test_1002.wav", wav_path)

        segments = json.loads(result.segments_json)
        assert isinstance(segments, list)
        assert len(segments) == 5  # 3 agent + 2 customer
        for seg in segments:
            assert "time" in seg
            assert "end" in seg
            assert "speaker" in seg
            assert "text" in seg

    def test_pipeline_keyword_detection(self, tmp_path: Path) -> None:
        """Required phrases detected → high phrase score."""
        wav_path = str(tmp_path / "test_1003.wav")
        _create_stereo_wav(wav_path)

        with patch(
            "server.airec.analyzer.stt.transcribe_file",
            return_value=_make_fake_transcript(),
        ):
            result = run_pipeline("test_1003.wav", wav_path)

        # "감사합니다", "안녕하세요" in agent text
        assert result.required_phrase_hit is True
        assert result.score_phrase == 100.0
        assert result.forbidden_word_hit is False

    def test_pipeline_talk_ratios_computed(self, tmp_path: Path) -> None:
        wav_path = str(tmp_path / "test_1004.wav")
        _create_stereo_wav(wav_path)

        with patch(
            "server.airec.analyzer.stt.transcribe_file",
            return_value=_make_fake_transcript(),
        ):
            result = run_pipeline("test_1004.wav", wav_path)

        assert result.agent_talk_ratio > 0
        assert result.customer_talk_ratio > 0
        assert result.silence_ratio >= 0
        # ratios should sum to ~1.0
        total = (
            result.agent_talk_ratio + result.customer_talk_ratio + result.silence_ratio
        )
        assert 0.9 <= total <= 1.1


# ---------------------------------------------------------------------------
# RecHandler.run_stt_pipeline E2E (mock only STT)
# ---------------------------------------------------------------------------


class TestRecHandlerSTTPipeline:
    """Test RecHandler.run_stt_pipeline → SttResultPayload conversion."""

    def test_run_stt_pipeline_returns_payload(self, tmp_path: Path) -> None:
        wav_path = str(tmp_path / "rec_5001.wav")
        _create_stereo_wav(wav_path)
        handler = RecHandler()

        with patch(
            "server.airec.analyzer.stt.transcribe_file",
            return_value=_make_fake_transcript(),
        ):
            result = handler.run_stt_pipeline("agent-1", "rec_5001.wav", wav_path)

        assert result is not None
        assert isinstance(result, SttResultPayload)
        assert result.filename == "rec_5001.wav"
        assert result.agent_id == "agent-1"
        assert result.full_text != ""
        assert result.agent_text != ""
        assert result.customer_text != ""
        assert result.score_total > 0
        assert result.word_count > 0
        assert result.required_phrase_hit is True

    def test_run_stt_pipeline_failure_returns_none(self, tmp_path: Path) -> None:
        handler = RecHandler()

        with patch(
            "server.airec.analyzer.stt.transcribe_file",
            side_effect=RuntimeError("STT model not found"),
        ):
            result = handler.run_stt_pipeline(
                "agent-1", "nonexistent.wav", "/nonexistent.wav"
            )

        assert result is None

    def test_full_flow_analysis_to_stt_result(self, tmp_path: Path) -> None:
        """Full E2E: analysis → upload decision → STT → result payload."""
        handler = RecHandler(upload_base_url="http://localhost:8080")
        storage = RecordingStorage(base_dir=str(tmp_path / "recordings"))

        # Step 1: Agent sends analysis result
        payload = _make_analysis_payload(
            filename="rec_6001.wav", status="OK", is_stereo=True
        )
        upload_req = handler.handle_analysis_result("agent-1", payload)
        assert upload_req is not None, "Should request upload for OK stereo recording"

        # Step 2: Agent uploads WAV → server stores it
        wav_path = str(tmp_path / "rec_6001.wav")
        _create_stereo_wav(wav_path)
        wav_data = Path(wav_path).read_bytes()
        stored_path = storage.store("agent-1", wav_data, "rec_6001.wav")

        # Step 3: Upload ack → mark as uploaded
        handler.handle_upload_ack(
            "agent-1", filename="rec_6001.wav", status=0, file_size=len(wav_data)
        )
        assert handler.records[("agent-1", "rec_6001.wav")].uploaded is True

        # Step 4: Server runs STT pipeline on stored WAV
        with patch(
            "server.airec.analyzer.stt.transcribe_file",
            return_value=_make_fake_transcript(),
        ):
            stt_result = handler.run_stt_pipeline(
                "agent-1", "rec_6001.wav", str(stored_path)
            )

        # Step 5: Verify result payload
        assert stt_result is not None
        assert stt_result.filename == "rec_6001.wav"
        assert stt_result.agent_id == "agent-1"
        assert stt_result.score_total > 0
        assert stt_result.required_phrase_hit is True
        assert stt_result.forbidden_word_hit is False

        # Verify segments JSON is valid
        segments = json.loads(stt_result.segments_json)
        assert len(segments) == 5

        # Verify stats
        stats = handler.get_agent_stats("agent-1")
        assert stats == {"total": 1, "ok": 1, "uploaded": 1}


# ---------------------------------------------------------------------------
# μ-law WAV handling (format code 7)
# ---------------------------------------------------------------------------


class TestMulawWavHandling:
    """Test μ-law WAV file support (telephone recordings)."""

    def test_extract_channel_mulaw_stereo(self, tmp_path: Path) -> None:
        """Test extracting a channel from a μ-law stereo WAV."""
        wav_path = str(tmp_path / "mulaw_stereo.wav")
        _create_mulaw_stereo_wav(wav_path, duration_sec=2.0)

        # Extract left channel
        left_wav = extract_channel_wav(wav_path, channel=0)
        assert Path(left_wav).exists()

        # Verify it's a valid PCM WAV (not μ-law)
        with wave.open(left_wav, "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2  # Converted to 16-bit PCM
            assert wf.getframerate() == 8000
            frames = wf.readframes(wf.getnframes())
            assert len(frames) > 0

        # Clean up
        Path(left_wav).unlink()

    def test_extract_channel_mulaw_right(self, tmp_path: Path) -> None:
        """Test extracting right channel from μ-law stereo WAV."""
        wav_path = str(tmp_path / "mulaw_stereo.wav")
        _create_mulaw_stereo_wav(wav_path, duration_sec=2.0)

        # Extract right channel
        right_wav = extract_channel_wav(wav_path, channel=1)
        assert Path(right_wav).exists()

        # Verify it's a valid PCM WAV
        with wave.open(right_wav, "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 8000

        # Clean up
        Path(right_wav).unlink()

    def test_transcribe_file_mulaw_stereo(self, tmp_path: Path) -> None:
        """Test transcribe_file detects μ-law stereo and routes to stereo handler."""
        wav_path = str(tmp_path / "mulaw_stereo.wav")
        _create_mulaw_stereo_wav(wav_path, duration_sec=2.0)

        # Mock transcribe_file to verify it's called with stereo routing
        with patch(
            "server.airec.analyzer.stt.transcribe_stereo",
            return_value=_make_fake_transcript(),
        ) as mock_stereo:
            result = transcribe_file(wav_path)

        # Verify stereo handler was called (positional args)
        mock_stereo.assert_called_once_with(wav_path, "ko")
        assert result is not None

    def test_mulaw_mono_fallback(self, tmp_path: Path) -> None:
        """Test that mono μ-law WAV is handled correctly."""
        import struct

        wav_path = str(tmp_path / "mulaw_mono.wav")
        sample_rate = 8000
        n_frames = int(sample_rate * 2.0)

        # Create mono μ-law WAV
        with open(wav_path, "wb") as f:
            f.write(b"RIFF")
            file_size_pos = f.tell()
            f.write(b"\x00\x00\x00\x00")
            f.write(b"WAVE")

            # fmt chunk
            f.write(b"fmt ")
            f.write(struct.pack("<I", 16))
            f.write(struct.pack("<H", 7))  # μ-law
            f.write(struct.pack("<H", 1))  # mono
            f.write(struct.pack("<I", sample_rate))
            f.write(struct.pack("<I", sample_rate))
            f.write(struct.pack("<H", 1))
            f.write(struct.pack("<H", 8))

            # data chunk
            f.write(b"data")
            f.write(struct.pack("<I", n_frames))
            f.write(b"\xff" * n_frames)

            # Update file size
            file_size = f.tell() - 8
            f.seek(file_size_pos)
            f.write(struct.pack("<I", file_size))

        # extract_channel_wav should copy mono file as-is
        result = extract_channel_wav(wav_path, channel=0)
        assert Path(result).exists()
        Path(result).unlink()
