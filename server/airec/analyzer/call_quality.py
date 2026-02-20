"""Call quality metrics computation from STT transcript data."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, cast, runtime_checkable

from .keywords import check_forbidden_words, check_required_phrases


@runtime_checkable
class _HasStart(Protocol):
    start: float


@runtime_checkable
class _HasTime(Protocol):
    time: float


@runtime_checkable
class _HasEnd(Protocol):
    end: float


@runtime_checkable
class _TranscriptLike(Protocol):
    agent_segments: Sequence[object]
    customer_segments: Sequence[object]
    duration_sec: float
    agent_text: str
    full_text: str


@dataclass
class CallQualityResult:
    first_response_sec: float = 0.0
    agent_talk_ratio: float = 0.0
    customer_talk_ratio: float = 0.0
    silence_ratio: float = 0.0
    required_phrase_hit: bool = False
    required_phrase_list: list[str] = field(default_factory=list)
    forbidden_word_hit: bool = False
    forbidden_word_list: list[str] = field(default_factory=list)
    score_total: float = 0.0
    score_response: float = 0.0
    score_phrase: float = 0.0
    score_silence: float = 0.0


def _mapping_number(seg: Mapping[str, object], key: str, default: float) -> float:
    value = seg.get(key, default)
    if isinstance(value, int | float):
        return float(value)
    return default


def _get_start(seg: object) -> float:
    if isinstance(seg, _HasStart):
        return seg.start
    if isinstance(seg, _HasTime):
        return seg.time
    if isinstance(seg, Mapping):
        mapping_seg = cast(Mapping[str, object], seg)
        return _mapping_number(
            mapping_seg,
            "start",
            _mapping_number(mapping_seg, "time", 0.0),
        )
    return 0.0


def _get_end(seg: object, default: float) -> float:
    if isinstance(seg, _HasEnd):
        return seg.end
    if isinstance(seg, Mapping):
        return _mapping_number(cast(Mapping[str, object], seg), "end", default)
    return default


def compute_first_response(
    agent_segments: Sequence[object], customer_segments: Sequence[object]
) -> float:
    if not customer_segments or not agent_segments:
        return 0.0

    cust_first = min(_get_start(s) for s in customer_segments)
    agent_after = [s for s in agent_segments if _get_start(s) > cust_first]
    if not agent_after:
        return 0.0
    agent_first = min(_get_start(s) for s in agent_after)
    return round(agent_first - cust_first, 3)


def compute_talk_ratios(
    agent_segments: Sequence[object],
    customer_segments: Sequence[object],
    total_duration: float,
) -> tuple[float, float, float]:
    if total_duration <= 0:
        return 0.0, 0.0, 1.0

    def _sum_duration(segments: Sequence[object]) -> float:
        total = 0.0
        for s in segments:
            start = _get_start(s)
            end = _get_end(s, start)
            total += end - start
        return total

    agent_time = _sum_duration(agent_segments)
    cust_time = _sum_duration(customer_segments)
    talk_time = agent_time + cust_time
    silence_time = max(0.0, total_duration - talk_time)

    return (
        round(min(agent_time / total_duration, 1.0), 4),
        round(min(cust_time / total_duration, 1.0), 4),
        round(min(silence_time / total_duration, 1.0), 4),
    )


def compute_scores(
    first_response: float,
    silence_ratio: float,
    required_hit: bool,
    forbidden_hit: bool,
) -> tuple[float, float, float, float]:
    if first_response <= 0:
        response_score = 50.0
    elif first_response <= 3.0:
        response_score = 100.0
    elif first_response >= 15.0:
        response_score = 0.0
    else:
        response_score = 100.0 * (15.0 - first_response) / 12.0

    if forbidden_hit:
        phrase_score = 0.0
    elif required_hit:
        phrase_score = 100.0
    else:
        phrase_score = 50.0

    if silence_ratio <= 0.20:
        silence_score = 100.0
    elif silence_ratio >= 0.60:
        silence_score = 0.0
    else:
        silence_score = 100.0 * (0.60 - silence_ratio) / 0.40

    total = round(response_score * 0.3 + phrase_score * 0.4 + silence_score * 0.3, 1)

    return (
        total,
        round(response_score, 1),
        round(phrase_score, 1),
        round(silence_score, 1),
    )


def analyze_call_quality(
    transcript_result: object,
    agent_text: str = "",
    full_text: str = "",
) -> CallQualityResult:
    if isinstance(transcript_result, _TranscriptLike):
        agent_segs: Sequence[object] = transcript_result.agent_segments
        cust_segs: Sequence[object] = transcript_result.customer_segments
        duration = transcript_result.duration_sec
        transcript_agent_text = transcript_result.agent_text
        transcript_full_text = transcript_result.full_text
    else:
        agent_segs = []
        cust_segs = []
        duration = 0.0
        transcript_agent_text = ""
        transcript_full_text = ""

    check_text = (
        agent_text or transcript_agent_text or full_text or transcript_full_text
    )

    first_resp = compute_first_response(agent_segs, cust_segs)
    agent_ratio, cust_ratio, silence_ratio = compute_talk_ratios(
        agent_segs, cust_segs, duration
    )
    req_hit, req_list = check_required_phrases(check_text)
    forb_hit, forb_list = check_forbidden_words(check_text)
    total, resp_score, phrase_score, sil_score = compute_scores(
        first_resp, silence_ratio, req_hit, forb_hit
    )

    return CallQualityResult(
        first_response_sec=first_resp,
        agent_talk_ratio=agent_ratio,
        customer_talk_ratio=cust_ratio,
        silence_ratio=silence_ratio,
        required_phrase_hit=req_hit,
        required_phrase_list=req_list,
        forbidden_word_hit=forb_hit,
        forbidden_word_list=forb_list,
        score_total=total,
        score_response=resp_score,
        score_phrase=phrase_score,
        score_silence=sil_score,
    )
