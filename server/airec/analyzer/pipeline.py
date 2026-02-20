"""STT + quality analysis pipeline for a single recording.

Orchestrates: WAV -> STT (faster-whisper) -> call quality scoring.
DB operations are optional and passed as callbacks.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable, Protocol, cast

_logger = logging.getLogger(__name__)


class _SpeakerSegmentLike(Protocol):
    time: float
    end: float
    speaker: str
    text: str


class _TranscriptLike(Protocol):
    segments: list[_SpeakerSegmentLike]
    agent_text: str
    customer_text: str
    full_text: str
    duration_sec: float
    word_count: int


@dataclass
class PipelineResult:
    """Result of the full analysis pipeline."""

    rec_no: int
    full_text: str
    agent_text: str
    customer_text: str
    segments_json: str
    duration_sec: float
    word_count: int
    score_total: float
    score_response: float
    score_phrase: float
    score_silence: float
    # Call quality detail
    first_response_sec: float = 0.0
    agent_talk_ratio: float = 0.0
    customer_talk_ratio: float = 0.0
    silence_ratio: float = 0.0
    required_phrase_hit: bool = False
    required_phrase_list: str = ""
    forbidden_word_hit: bool = False
    forbidden_word_list: str = ""


def run_pipeline(rec_no: int, wav_path: str, language: str = "ko") -> PipelineResult:
    """Run full STT + quality analysis pipeline on a WAV file.

    Args:
        rec_no: Recording number identifier.
        wav_path: Path to the WAV file.
        language: BCP-47 language code for STT.

    Returns:
        PipelineResult with transcript and quality data.

    Raises:
        ImportError: If faster-whisper is not installed.
        FileNotFoundError: If wav_path does not exist.
    """
    from .stt import transcribe_file
    from .call_quality import CallQualityResult, analyze_call_quality

    transcribe = cast(Callable[[str, str], _TranscriptLike], transcribe_file)

    _logger.info("Pipeline start: rec_no=%s path=%s", rec_no, wav_path)

    # Step 1: STT
    transcript = transcribe(wav_path, language)
    _logger.info(
        "STT done: rec_no=%s segments=%d words=%d",
        rec_no,
        len(transcript.segments),
        transcript.word_count,
    )

    # Step 2: Call quality
    quality: CallQualityResult = analyze_call_quality(transcript)
    _logger.info(
        "Quality done: rec_no=%s score=%.1f",
        rec_no,
        quality.score_total,
    )

    # Build segments JSON
    segments_json = json.dumps(
        [
            {
                "time": s.time,
                "end": s.end,
                "speaker": s.speaker,
                "text": s.text,
            }
            for s in transcript.segments
        ],
        ensure_ascii=False,
    )

    return PipelineResult(
        rec_no=rec_no,
        full_text=transcript.full_text,
        agent_text=transcript.agent_text,
        customer_text=transcript.customer_text,
        segments_json=segments_json,
        duration_sec=transcript.duration_sec,
        word_count=transcript.word_count,
        score_total=quality.score_total,
        score_response=quality.score_response,
        score_phrase=quality.score_phrase,
        score_silence=quality.score_silence,
        first_response_sec=quality.first_response_sec,
        agent_talk_ratio=quality.agent_talk_ratio,
        customer_talk_ratio=quality.customer_talk_ratio,
        silence_ratio=quality.silence_ratio,
        required_phrase_hit=quality.required_phrase_hit,
        required_phrase_list=",".join(quality.required_phrase_list),
        forbidden_word_hit=quality.forbidden_word_hit,
        forbidden_word_list=",".join(quality.forbidden_word_list),
    )
