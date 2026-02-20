"""Audio quality analysis orchestrator.

Ties together energy and silence sub-modules to produce a single
AnalysisResult per recording file.
"""

import logging
import os
import wave

from .energy import load_wav, rms_dbfs, channel_rms
from .silence import analyze_file_silence, SilenceResult
from .models import AnalysisResult, AnalysisStatus, ChannelStats

logger = logging.getLogger(__name__)

EMPTY_THRESHOLD_DB: float = -60.0
MUTED_THRESHOLD_RATIO: float = 0.95
DROPOUT_WARN_COUNT: int = 3
DURATION_MISMATCH_SEC: float = 5.0


def get_wav_duration(filepath: str) -> float:
    """Return duration of a WAV file in seconds (supports μ-law)."""
    try:
        with wave.open(filepath, "rb") as wf:
            n_frames = wf.getnframes()
            frame_rate = wf.getframerate()
            if frame_rate == 0:
                return 0.0
            return n_frames / float(frame_rate)
    except wave.Error:
        # μ-law 등 비표준 포맷: load_wav fallback
        try:
            samples, sr, n_ch = load_wav(filepath)
            n_samples = samples.shape[0]
            return n_samples / float(sr) if sr > 0 else 0.0
        except Exception:
            return 0.0
    except Exception:
        return 0.0


def analyze_recording(
    rec_no: int,
    filepath: str,
    smdr_duration: float = 0.0,
) -> AnalysisResult:
    """Analyze a single recording file and return an AnalysisResult."""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Recording file not found: {filepath}")

    logger.info(
        "analyze rec_no=%d file=%s size=%d",
        rec_no,
        filepath,
        os.path.getsize(filepath),
    )

    wav_duration = get_wav_duration(filepath)
    left_rms_db, right_rms_db, is_stereo = channel_rms(filepath)
    left_silence, right_silence, _ = analyze_file_silence(filepath)

    logger.info(
        "analyze rec_no=%d duration=%.1fs stereo=%s "
        "L=%.1fdB R=%.1fdB silence_L=%.1f%% silence_R=%.1f%%",
        rec_no,
        wav_duration,
        is_stereo,
        left_rms_db,
        right_rms_db,
        left_silence.silence_ratio * 100,
        right_silence.silence_ratio * 100,
    )

    left_stats = ChannelStats(
        rms_db=left_rms_db,
        silence_ratio=left_silence.silence_ratio,
    )
    right_stats = ChannelStats(
        rms_db=right_rms_db,
        silence_ratio=right_silence.silence_ratio,
    )

    dropout_count = max(left_silence.dropout_count, right_silence.dropout_count)
    dropout_total_sec = max(
        left_silence.dropout_total_sec, right_silence.dropout_total_sec
    )

    if left_rms_db < EMPTY_THRESHOLD_DB and right_rms_db < EMPTY_THRESHOLD_DB:
        status = AnalysisStatus.EMPTY
    elif (
        left_silence.silence_ratio > MUTED_THRESHOLD_RATIO
        and right_silence.silence_ratio <= MUTED_THRESHOLD_RATIO
    ):
        status = AnalysisStatus.MUTED_L
    elif (
        right_silence.silence_ratio > MUTED_THRESHOLD_RATIO
        and left_silence.silence_ratio <= MUTED_THRESHOLD_RATIO
    ):
        status = AnalysisStatus.MUTED_R
    elif (
        smdr_duration > 0.0
        and abs(wav_duration - smdr_duration) > DURATION_MISMATCH_SEC
    ):
        status = AnalysisStatus.MISMATCH
    elif dropout_count > DROPOUT_WARN_COUNT:
        status = AnalysisStatus.DROPOUT
    else:
        status = AnalysisStatus.OK

    duration_diff = abs(wav_duration - smdr_duration) if smdr_duration > 0.0 else 0.0

    return AnalysisResult(
        rec_no=rec_no,
        status=status,
        left=left_stats,
        right=right_stats,
        dropout_count=dropout_count,
        dropout_total_sec=dropout_total_sec,
        duration_wav=wav_duration,
        duration_smdr=smdr_duration,
        duration_diff=duration_diff,
        is_stereo=is_stereo,
    )


def analyze_batch(
    recordings: list[dict[str, int | float | str]],
) -> list[AnalysisResult]:
    """Analyze a list of recordings and return one AnalysisResult per entry."""
    results: list[AnalysisResult] = []

    for entry in recordings:
        rec_no: int = int(entry["rec_no"])
        filepath: str = str(entry["filepath"])
        smdr_duration: float = float(entry.get("smdr_duration", 0.0))

        try:
            result = analyze_recording(rec_no, filepath, smdr_duration)
        except Exception:
            logger.exception(
                "analyze_batch failed: rec_no=%d file=%s",
                rec_no,
                filepath,
            )
            result = AnalysisResult(
                rec_no=rec_no,
                status=AnalysisStatus.EMPTY,
                duration_smdr=smdr_duration,
            )

        results.append(result)

    return results
