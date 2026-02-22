"""Energy-threshold based silence detection for 8kHz telephone WAV files.

Uses frame-based RMS analysis with configurable threshold (default -48 dBFS).
No external dependencies beyond numpy.
"""

import numpy as np
from dataclasses import dataclass

from .energy import load_wav, rms_dbfs

SILENCE_THRESHOLD_DB = -48.0
FRAME_MS = 20
MIN_DROPOUT_SEC = 2.0  # 2초 이상 연속 침묵만 dropout으로 간주


@dataclass
class SilenceResult:
    silence_ratio: float
    dropout_count: int
    dropout_total_sec: float


def analyze_silence(
    samples: np.ndarray, sample_rate: int, threshold_db: float = SILENCE_THRESHOLD_DB
) -> SilenceResult:
    """Analyze silence in a single-channel signal."""
    frame_size = int(sample_rate * FRAME_MS / 1000)
    n_frames = len(samples) // frame_size

    if n_frames == 0:
        return SilenceResult(silence_ratio=1.0, dropout_count=0, dropout_total_sec=0.0)

    is_silent = []
    for i in range(n_frames):
        frame = samples[i * frame_size : (i + 1) * frame_size]
        db = rms_dbfs(frame)
        is_silent.append(db < threshold_db)

    silence_ratio = sum(is_silent) / len(is_silent)

    min_frames = int(MIN_DROPOUT_SEC * 1000 / FRAME_MS)
    dropout_count = 0
    dropout_total_frames = 0
    run = 0

    for s in is_silent:
        if s:
            run += 1
        else:
            if run >= min_frames:
                dropout_count += 1
                dropout_total_frames += run
            run = 0

    dropout_total_sec = dropout_total_frames * FRAME_MS / 1000.0

    return SilenceResult(
        silence_ratio=silence_ratio,
        dropout_count=dropout_count,
        dropout_total_sec=dropout_total_sec,
    )


def analyze_file_silence(filepath: str) -> tuple[SilenceResult, SilenceResult, bool]:
    """Analyze silence for a WAV file.

    Returns (left_result, right_result, is_stereo).
    For mono, both results are the same.
    """
    samples, sr, n_ch = load_wav(filepath)

    if n_ch >= 2:
        left = analyze_silence(samples[:, 0], sr)
        right = analyze_silence(samples[:, 1], sr)
        return left, right, True
    else:
        result = analyze_silence(samples, sr)
        return result, result, False
