"""STT + quality analysis pipeline for a single recording.

Orchestrates: WAV -> STT (multi-engine) -> call quality scoring.
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

    filename: str
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
    stt_model: str = "faster-whisper-medium"
    forbidden_word_list: str = ""


# Engine key → actual model name mapping for DB storage
# local 엔진은 config.yaml에서 동적으로 결정됨 (get_stt_config 참조)
def _get_engine_model_name(engine: str) -> str:
    """STT 엔진별 DB 저장용 모델명 반환."""
    static = {
        "openai-whisper": "whisper-1",
        "openai-gpt4o": "gpt-4o-mini-transcribe",
        "openai-diarize": "gpt-4o-transcribe-diarize",
        "rtzr": "rtzr-sommers",
    }
    if engine in static:
        return static[engine]
    # local: config에서 모델명 읽기
    try:
        from .stt import get_stt_config
        cfg = get_stt_config()
        model_name = cfg.get("whisper", {}).get("model", "faster-whisper-medium")
        if "/" in model_name:
            model_name = model_name.split("/")[-1]
        return model_name
    except Exception:
        return "faster-whisper-medium"

def run_pipeline(
    filename: str,
    wav_path: str,
    language: str = "ko",
    in_out: int = 0,
    *,
    stt_engine: str = "local",
    openai_prompt: str = "",
) -> PipelineResult:
    """Run full STT + quality analysis pipeline on a WAV file.
    Args:
        filename: Recording filename identifier.
        wav_path: Path to the WAV file.
        language: BCP-47 language code for STT.
        in_out: 1=수신, 2=발신, 0=알수없음 — 스테레오 채널 매핑에 사용.
        stt_engine: 'local', 'openai-whisper', or 'openai-gpt4o'.
        openai_prompt: Domain-specific prompt hint for OpenAI models.
    Returns:
        PipelineResult with transcript and quality data.
        ImportError: If faster-whisper is not installed (local engine).
        FileNotFoundError: If wav_path does not exist.
    """
    from .stt import transcribe_file
    from .call_quality import CallQualityResult, analyze_call_quality
    _logger.info(
        "Pipeline start: filename=%s path=%s in_out=%d engine=%s",
        filename, wav_path, in_out, stt_engine,
    )
    # Step 1: STT
    transcript = transcribe_file(
        wav_path, language, in_out,
        engine=stt_engine, openai_prompt=openai_prompt,
    )
    _logger.info(
        "STT done: filename=%s segments=%d words=%d",
        filename,
        len(transcript.segments),
        transcript.word_count,
    )

    # Step 2: Call quality
    quality: CallQualityResult = analyze_call_quality(transcript)
    is_mono = quality.customer_talk_ratio == 0.0 and quality.agent_talk_ratio > 0.0
    _logger.info(
        "Quality done: filename=%s mode=%s score=%.1f "
        "resp=%.1f phrase=%.1f silence=%.1f "
        "talk=%.0f%% silence_r=%.0f%% pace=%.0fWPM",
        filename,
        "mono" if is_mono else "stereo",
        quality.score_total,
        quality.score_response,
        quality.score_phrase,
        quality.score_silence,
        quality.agent_talk_ratio * 100,
        quality.silence_ratio * 100,
        quality.first_response_sec if is_mono else 0.0,
    )

    # Build segments JSON (order 필드로 생성 시점의 정렬 순서를 확정)
    segments_json = json.dumps(
        [
            {
                "order": idx,
                "time": s.time,
                "end": s.end,
                "speaker": s.speaker,
                "text": s.text,
            }
            for idx, s in enumerate(transcript.segments)
        ],
        ensure_ascii=False,
    )

    return PipelineResult(
        filename=filename,
        stt_model=_get_engine_model_name(stt_engine),
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
