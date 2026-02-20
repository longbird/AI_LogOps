"""Channel-wise RMS/dBFS energy analysis for 8kHz telephone WAV files."""

import numpy as np
import wave
import struct


def load_wav(filepath: str) -> tuple[np.ndarray, int, int]:
    """Load WAV file and return (samples_array, sample_rate, n_channels).

    Returns float64 array normalized to [-1.0, 1.0].
    For stereo: shape (n_samples, 2), for mono: shape (n_samples,).
    """
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
