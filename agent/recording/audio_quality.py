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
DROPOUT_WARN_COUNT: int = 5  # dropout 5건 초과 시 DROPOUT 상태
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
    filename: str,
    filepath: str,
    smdr_duration: float = 0.0,
) -> AnalysisResult:
    """Analyze a single recording file and return an AnalysisResult."""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Recording file not found: {filepath}")

    logger.info(
        "analyze filename=%s file=%s size=%d",
        filename,
        filepath,
        os.path.getsize(filepath),
    )

    wav_duration = get_wav_duration(filepath)
    left_rms_db, right_rms_db, is_stereo = channel_rms(filepath)
    left_silence, right_silence, _ = analyze_file_silence(filepath)

    logger.info(
        "analyze filename=%s duration=%.1fs stereo=%s "
        "L=%.1fdB R=%.1fdB silence_L=%.1f%% silence_R=%.1f%%",
        filename,
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
        logger.info(
            "status=EMPTY: filename=%s L_rms=%.1fdB R_rms=%.1fdB (threshold=%.1fdB)",
            filename,
            left_rms_db,
            right_rms_db,
            EMPTY_THRESHOLD_DB,
        )
    elif (
        left_silence.silence_ratio > MUTED_THRESHOLD_RATIO
        and right_silence.silence_ratio <= MUTED_THRESHOLD_RATIO
    ):
        status = AnalysisStatus.MUTED_L
        logger.info(
            "status=MUTED_L: filename=%s L_silence=%.1f%% R_silence=%.1f%% "
            "(mute_threshold=%.0f%%)",
            filename,
            left_silence.silence_ratio * 100,
            right_silence.silence_ratio * 100,
            MUTED_THRESHOLD_RATIO * 100,
        )
    elif (
        right_silence.silence_ratio > MUTED_THRESHOLD_RATIO
        and left_silence.silence_ratio <= MUTED_THRESHOLD_RATIO
    ):
        status = AnalysisStatus.MUTED_R
        logger.info(
            "status=MUTED_R: filename=%s L_silence=%.1f%% R_silence=%.1f%% "
            "(mute_threshold=%.0f%%)",
            filename,
            left_silence.silence_ratio * 100,
            right_silence.silence_ratio * 100,
            MUTED_THRESHOLD_RATIO * 100,
        )
    elif (
        smdr_duration > 0.0
        and abs(wav_duration - smdr_duration) > DURATION_MISMATCH_SEC
    ):
        status = AnalysisStatus.MISMATCH
        logger.info(
            "status=MISMATCH: filename=%s wav=%.1fs smdr=%.1fs diff=%.1fs "
            "(threshold=%.1fs)",
            filename,
            wav_duration,
            smdr_duration,
            abs(wav_duration - smdr_duration),
            DURATION_MISMATCH_SEC,
        )
    elif dropout_count > DROPOUT_WARN_COUNT:
        status = AnalysisStatus.DROPOUT
        logger.warning(
            "status=DROPOUT: filename=%s dropout_count=%d > threshold=%d "
            "dropout_total=%.1fs L_silence=%.1f%% R_silence=%.1f%% "
            "L_rms=%.1fdB R_rms=%.1fdB duration=%.1fs",
            filename,
            dropout_count,
            DROPOUT_WARN_COUNT,
            dropout_total_sec,
            left_silence.silence_ratio * 100,
            right_silence.silence_ratio * 100,
            left_rms_db,
            right_rms_db,
            wav_duration,
        )
    else:
        status = AnalysisStatus.OK
        logger.debug(
            "status=OK: filename=%s dropout=%d/%d L_silence=%.1f%% R_silence=%.1f%%",
            filename,
            dropout_count,
            DROPOUT_WARN_COUNT,
            left_silence.silence_ratio * 100,
            right_silence.silence_ratio * 100,
        )

    duration_diff = abs(wav_duration - smdr_duration) if smdr_duration > 0.0 else 0.0

    return AnalysisResult(
        filename=filename,
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
        filename: str = str(entry["filename"])
        filepath: str = str(entry["filepath"])
        smdr_duration: float = float(entry.get("smdr_duration", 0.0))

        try:
            result = analyze_recording(filename, filepath, smdr_duration)
        except Exception:
            logger.exception(
                "analyze_batch failed: filename=%s file=%s",
                filename,
                filepath,
            )
            result = AnalysisResult(
                filename=filename,
                status=AnalysisStatus.EMPTY,
                duration_smdr=smdr_duration,
            )

        results.append(result)

    return results
