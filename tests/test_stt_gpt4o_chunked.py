from __future__ import annotations

import sys
import types
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

if "faster_whisper" not in sys.modules:
    _fw_stub = types.ModuleType("faster_whisper")
    setattr(_fw_stub, "WhisperModel", MagicMock)
    sys.modules["faster_whisper"] = _fw_stub

from server.airec.analyzer.stt import Segment, transcribe  # noqa: E402


def _create_mono_wav(path: str, duration_sec: float = 30.0) -> None:
    sample_rate = 8000
    n_frames = int(sample_rate * duration_sec)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_frames)


def test_gpt4o_uses_chunked_fallback_for_single_block(tmp_path: Path) -> None:
    wav_path = str(tmp_path / "mono_30s.wav")
    _create_mono_wav(wav_path, duration_sec=30.0)

    coarse = [Segment(start=0.0, end=30.0, text="한 덩어리")]
    chunked = [
        Segment(start=0.0, end=10.0, text="첫 구간"),
        Segment(start=10.0, end=20.0, text="둘째 구간"),
    ]

    with (
        patch("server.airec.analyzer.stt._transcribe_openai", return_value=coarse),
        patch(
            "server.airec.analyzer.stt._transcribe_openai_gpt4o_chunked",
            return_value=chunked,
        ) as mock_chunked,
    ):
        result = transcribe(wav_path, engine="openai-gpt4o")

    mock_chunked.assert_called_once()
    assert len(result) == 2
    assert result[0].text == "첫 구간"


def test_gpt4o_keeps_original_when_chunked_not_better(tmp_path: Path) -> None:
    wav_path = str(tmp_path / "mono_30s.wav")
    _create_mono_wav(wav_path, duration_sec=30.0)

    coarse = [Segment(start=0.0, end=30.0, text="한 덩어리")]
    not_better = [Segment(start=0.0, end=30.0, text="한 덩어리")]

    with (
        patch("server.airec.analyzer.stt._transcribe_openai", return_value=coarse),
        patch(
            "server.airec.analyzer.stt._transcribe_openai_gpt4o_chunked",
            return_value=not_better,
        ),
    ):
        result = transcribe(wav_path, engine="openai-gpt4o")

    assert len(result) == 1
    assert result[0].text == "한 덩어리"
