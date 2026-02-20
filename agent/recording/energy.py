"""Channel-wise RMS/dBFS energy analysis for 8kHz telephone WAV files."""

from __future__ import annotations

import numpy as np
import wave
import struct


def _decode_mulaw_sample(byte_val: int) -> int:
    """Decode a single μ-law byte to PCM int16 sample."""
    byte_val = ~byte_val & 0xFF
    sign = byte_val & 0x80
    exponent = (byte_val >> 4) & 0x07
    mantissa = byte_val & 0x0F
    sample = ((mantissa << 3) + 0x84) << exponent
    sample -= 0x84
    return -sample if sign else sample


# μ-law decoding lookup table (ITU-T G.711)
_MULAW_TABLE = np.array([_decode_mulaw_sample(i) for i in range(256)], dtype=np.int16)


def _decode_mulaw(data: bytes) -> np.ndarray:
    """Decode μ-law encoded bytes to float64 PCM samples [-1.0, 1.0]."""
    indices = np.frombuffer(data, dtype=np.uint8)
    return _MULAW_TABLE[indices].astype(np.float64) / 32768.0


def _read_wav_raw(filepath: str) -> tuple[bytes, int, int, int]:
    """Parse WAV file manually to extract raw audio data and metadata.

    Returns (raw_data, sample_rate, n_channels, format_code).
    Handles formats that wave.open() doesn't support (e.g., μ-law).
    """
    with open(filepath, "rb") as f:
        # Read RIFF header
        riff_tag = f.read(4)
        if riff_tag != b"RIFF":
            raise ValueError("Not a valid WAV file (missing RIFF tag)")

        file_size = struct.unpack("<I", f.read(4))[0]
        wave_tag = f.read(4)
        if wave_tag != b"WAVE":
            raise ValueError("Not a valid WAV file (missing WAVE tag)")

        # Find fmt chunk
        fmt_data = None
        data_data = None

        while True:
            chunk_id = f.read(4)
            if not chunk_id or len(chunk_id) < 4:
                break

            chunk_size = struct.unpack("<I", f.read(4))[0]

            if chunk_id == b"fmt ":
                fmt_data = f.read(chunk_size)
            elif chunk_id == b"data":
                data_data = f.read(chunk_size)
                break
            else:
                f.seek(chunk_size, 1)  # Skip unknown chunk

        if fmt_data is None or data_data is None:
            raise ValueError("WAV file missing fmt or data chunk")

        # Parse fmt chunk
        format_code = struct.unpack("<H", fmt_data[0:2])[0]
        n_channels = struct.unpack("<H", fmt_data[2:4])[0]
        sample_rate = struct.unpack("<I", fmt_data[4:8])[0]

        return data_data, sample_rate, n_channels, format_code


def load_wav(filepath: str) -> tuple[np.ndarray, int, int]:
    """Load WAV file and return (samples_array, sample_rate, n_channels).

    Returns float64 array normalized to [-1.0, 1.0].
    For stereo: shape (n_samples, 2), for mono: shape (n_samples,).
    Supports PCM (format 1) and μ-law (format 7).
    """
    # Try standard wave.open() first (PCM files)
    try:
        with wave.open(filepath, "rb") as wf:
            n_channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            sample_rate = wf.getframerate()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)

        if sample_width == 1:
            fmt = f"<{n_frames * n_channels}B"
            samples = np.array(struct.unpack(fmt, raw), dtype=np.float64) / 128.0 - 1.0
        elif sample_width == 2:
            fmt = f"<{n_frames * n_channels}h"
            samples = np.array(struct.unpack(fmt, raw), dtype=np.float64) / 32768.0
        else:
            raise ValueError(f"Unsupported sample width: {sample_width}")

        if n_channels > 1:
            samples = samples.reshape(-1, n_channels)

        return samples, sample_rate, n_channels

    except wave.Error:
        # Fallback: parse WAV manually for unsupported formats (μ-law, A-law)
        raw_data, sample_rate, n_channels, format_code = _read_wav_raw(filepath)

        if format_code == 7:  # μ-law
            samples = _decode_mulaw(raw_data)
            n_frames = len(samples) // n_channels
        elif format_code == 6:  # A-law
            raise ValueError(
                "A-law (format 6) WAV files are not yet supported. "
                "Please convert to PCM or μ-law."
            )
        else:
            raise ValueError(
                f"Unsupported WAV format code: {format_code}. "
                f"Supported: 1 (PCM), 7 (μ-law)"
            )

        if n_channels > 1:
            samples = samples.reshape(-1, n_channels)

        return samples, sample_rate, n_channels


def rms_dbfs(samples: np.ndarray) -> float:
    """Calculate RMS level in dBFS. Returns -96.0 for silence."""
    if len(samples) == 0:
        return -96.0
    rms = np.sqrt(np.mean(samples**2))
    if rms < 1e-10:
        return -96.0
    return 20.0 * np.log10(rms)


def channel_rms(filepath: str) -> tuple[float, float, bool]:
    """Return (left_rms_db, right_rms_db, is_stereo) for a WAV file.

    For mono files, both values are the same.
    """
    samples, sr, n_ch = load_wav(filepath)

    if n_ch >= 2:
        left_db = rms_dbfs(samples[:, 0])
        right_db = rms_dbfs(samples[:, 1])
        return left_db, right_db, True
    else:
        db = rms_dbfs(samples)
        return db, db, False
