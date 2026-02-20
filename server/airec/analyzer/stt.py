"""
analyzer/stt.py - faster-whisper STT wrapper module.

Provides lazy model loading, channel extraction (no FFmpeg),
and transcription for mono and stereo WAV files.
"""

from __future__ import annotations

import logging
import os
import shutil
import struct
import tempfile
import wave
from dataclasses import dataclass

import numpy as np
from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)

_model: WhisperModel | None = None
_model_size: str | None = None


# μ-law decoding lookup table and helpers
def _decode_mulaw_sample(byte_val: int) -> int:
    """Decode a single μ-law byte to int16 PCM sample."""
    byte_val = ~byte_val & 0xFF
    sign = byte_val & 0x80
    exponent = (byte_val >> 4) & 0x07
    mantissa = byte_val & 0x0F
    sample = ((mantissa << 3) + 0x84) << exponent
    sample -= 0x84
    return -sample if sign else sample


_MULAW_TABLE = np.array([_decode_mulaw_sample(i) for i in range(256)], dtype=np.int16)


def _decode_mulaw(data: bytes) -> np.ndarray:
    """Decode μ-law bytes to int16 PCM samples using vectorized lookup table."""
    indices = np.frombuffer(data, dtype=np.uint8)
    return _MULAW_TABLE[indices]


def _read_wav_raw(wav_path: str) -> tuple[bytes, int, int, int, int]:
    """
    Manually parse RIFF WAV header to extract raw audio data and metadata.

    Returns:
        (raw_data, sample_rate, n_channels, sample_width, format_code)
        - raw_data: bytes of audio samples
        - sample_rate: samples per second
        - n_channels: number of channels
        - sample_width: bytes per sample
        - format_code: 1=PCM, 7=μ-law, etc.
    """
    with open(wav_path, "rb") as f:
        # Read RIFF header
        riff_header = f.read(4)
        if riff_header != b"RIFF":
            raise ValueError(f"Not a RIFF file: {wav_path}")

        _ = struct.unpack("<I", f.read(4))[0]  # file_size (not used)
        wave_header = f.read(4)
        if wave_header != b"WAVE":
            raise ValueError(f"Not a WAVE file: {wav_path}")

        # Find 'fmt ' chunk
        fmt_data = None
        while True:
            chunk_id = f.read(4)
            if not chunk_id:
                break
            chunk_size = struct.unpack("<I", f.read(4))[0]

            if chunk_id == b"fmt ":
                fmt_data = f.read(chunk_size)
                break
            else:
                _ = f.seek(chunk_size, 1)  # Skip this chunk

        if fmt_data is None:
            raise ValueError("No 'fmt ' chunk found in WAV file")

        # Parse fmt chunk (at least 16 bytes)
        if len(fmt_data) < 16:
            raise ValueError("fmt chunk too small")

        (
            format_code,
            n_channels,
            sample_rate,
            _,  # byte_rate (not used)
            _,  # block_align (not used)
            bits_per_sample,
        ) = struct.unpack("<HHIIHH", fmt_data[:16])
        sample_width = bits_per_sample // 8

        # Find 'data' chunk
        _ = f.seek(0)
        _ = f.read(12)  # Skip RIFF header
        raw_data = None
        while True:
            chunk_id = f.read(4)
            if not chunk_id:
                break
            chunk_size = struct.unpack("<I", f.read(4))[0]

            if chunk_id == b"data":
                raw_data = f.read(chunk_size)
                break
            else:
                _ = f.seek(chunk_size, 1)

        if raw_data is None:
            raise ValueError("No 'data' chunk found in WAV file")

        return raw_data, sample_rate, n_channels, sample_width, format_code


def get_model(
    model_size: str = "medium",
    device: str = "cpu",
    compute_type: str = "int8",
) -> WhisperModel:
    global _model, _model_size
    if _model is None or _model_size != model_size:
        logger.info(
            "Loading faster-whisper model: %s (device=%s, compute=%s)",
            model_size,
            device,
            compute_type,
        )
        _model = WhisperModel(model_size, device=device, compute_type=compute_type)
        _model_size = model_size
    return _model


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class SpeakerSegment:
    time: float
    end: float
    speaker: str
    text: str


@dataclass
class TranscriptResult:
    segments: list[SpeakerSegment]
    agent_segments: list[Segment]
    customer_segments: list[Segment]
    agent_text: str
    customer_text: str
    full_text: str
    duration_sec: float
    word_count: int


def extract_channel_wav(wav_path: str, channel: int) -> str:
    # Try standard wave.open() first (for PCM files)
    try:
        with wave.open(wav_path, "rb") as wf:
            n_channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            sample_rate = wf.getframerate()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
        format_code = 1  # PCM
    except wave.Error:
        # Fall back to manual parsing for μ-law or other formats
        raw, sample_rate, n_channels, sample_width, format_code = _read_wav_raw(
            wav_path
        )
        n_frames = (
            len(raw) // (n_channels * sample_width)
            if format_code == 1
            else len(raw) // n_channels
        )

    # PCM 모노는 그대로 복사
    if n_channels < 2 and format_code == 1:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        _ = shutil.copy2(wav_path, tmp.name)
        return tmp.name

    # μ-law → int16 PCM 변환 (모노/스테레오 공통)
    if format_code == 7:
        samples = _decode_mulaw(raw)
        sample_width = 2
    elif sample_width == 2:
        fmt = f"<{n_frames * n_channels}h"
        samples = np.array(struct.unpack(fmt, raw), dtype=np.int16)
    elif sample_width == 1:
        samples = np.frombuffer(raw, dtype=np.uint8).astype(np.int16) - 128
        samples = (samples * 256).astype(np.int16)
        sample_width = 2
    else:
        raise ValueError(f"Unsupported sample width: {sample_width} bytes")

    channel_data = samples if n_channels < 2 else samples[channel::n_channels]

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    with wave.open(tmp.name, "wb") as wf_out:
        wf_out.setnchannels(1)
        wf_out.setsampwidth(sample_width)
        wf_out.setframerate(sample_rate)
        wf_out.writeframes(channel_data.tobytes())

    return tmp.name


def transcribe(
    wav_path: str,
    language: str = "ko",
    beam_size: int = 5,
) -> list[Segment]:
    model = get_model()
    segments_iter, info = model.transcribe(
        wav_path, language=language, beam_size=beam_size
    )

    results: list[Segment] = []
    for seg in segments_iter:
        text = seg.text.strip()
        if text:
            results.append(
                Segment(
                    start=round(seg.start, 3),
                    end=round(seg.end, 3),
                    text=text,
                )
            )

    logger.info(
        "Transcribed %s: %d segment(s), lang=%s (prob=%.2f)",
        wav_path,
        len(results),
        info.language,
        info.language_probability,
    )
    return results


def transcribe_stereo(wav_path: str, language: str = "ko") -> TranscriptResult:
    l_wav = extract_channel_wav(wav_path, 0)
    r_wav = extract_channel_wav(wav_path, 1)
    try:
        agent_segs = transcribe(l_wav, language)
        cust_segs = transcribe(r_wav, language)
    finally:
        os.unlink(l_wav)
        os.unlink(r_wav)

    combined: list[SpeakerSegment] = []
    for s in agent_segs:
        combined.append(
            SpeakerSegment(time=s.start, end=s.end, speaker="agent", text=s.text)
        )
    for s in cust_segs:
        combined.append(
            SpeakerSegment(time=s.start, end=s.end, speaker="customer", text=s.text)
        )
    combined.sort(key=lambda x: x.time)

    agent_text = " ".join(s.text for s in agent_segs)
    customer_text = " ".join(s.text for s in cust_segs)
    full_text = " ".join(s.text for s in combined)

    all_ends = [s.end for s in agent_segs] + [s.end for s in cust_segs]
    duration = max(all_ends) if all_ends else 0.0
    word_count = len(full_text.split()) if full_text else 0

    return TranscriptResult(
        segments=combined,
        agent_segments=agent_segs,
        customer_segments=cust_segs,
        agent_text=agent_text,
        customer_text=customer_text,
        full_text=full_text,
        duration_sec=round(duration, 3),
        word_count=word_count,
    )


def transcribe_mono(wav_path: str, language: str = "ko") -> TranscriptResult:
    segs = transcribe(wav_path, language)

    combined = [
        SpeakerSegment(time=s.start, end=s.end, speaker="unknown", text=s.text)
        for s in segs
    ]
    full_text = " ".join(s.text for s in segs)
    duration = max(s.end for s in segs) if segs else 0.0
    word_count = len(full_text.split()) if full_text else 0

    return TranscriptResult(
        segments=combined,
        agent_segments=[],
        customer_segments=[],
        agent_text="",
        customer_text="",
        full_text=full_text,
        duration_sec=round(duration, 3),
        word_count=word_count,
    )


def transcribe_file(wav_path: str, language: str = "ko") -> TranscriptResult:
    # Try standard wave.open() first
    try:
        with wave.open(wav_path, "rb") as wf:
            n_channels = wf.getnchannels()
    except wave.Error:
        # Fall back to manual parsing for μ-law or other formats
        _, _, n_channels, _, _ = _read_wav_raw(wav_path)

    if n_channels >= 2:
        return transcribe_stereo(wav_path, language)
    else:
        return transcribe_mono(wav_path, language)
