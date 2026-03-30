"""
analyzer/stt.py - Multi-engine STT wrapper module.
  - local         : faster-whisper (offline, free)
  - openai-whisper: OpenAI whisper-1 API (segments with timestamps)
  - openai-gpt4o  : OpenAI gpt-4o-mini-transcribe API (highest accuracy)
  - openai-diarize: OpenAI gpt-4o-transcribe-diarize (speaker diarization)
  - rtzr          : RTZR sommers model (Korean CALL domain, speaker diarization)
Provides lazy model loading, channel extraction (no FFmpeg),
and transcription for mono and stereo WAV files.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import struct
import tempfile
import time as _time
import wave
import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)
_model: WhisperModel | None = None
_model_size: str | None = None
_stt_config: dict[str, Any] | None = None
# ── Engine constants ──
VALID_ENGINES = ("local", "openai-whisper", "openai-gpt4o", "openai-diarize", "rtzr")
# ── Default STT config (대리운전 콜센터 도메인) ──
_DEFAULT_STT_CONFIG: dict[str, Any] = {
    "whisper": {
        "model": "large-v3-turbo",
        "device": "cpu",
        "compute_type": "int8",
        "beam_size": 5,
        "language": "ko",
        "vad_filter": True,
        "vad_parameters": {
            "threshold": 0.35,
            "min_speech_duration_ms": 150,
            "min_silence_duration_ms": 300,
            "speech_pad_ms": 200,
        },
        "hallucination_prevention": {
            "compression_ratio_threshold": 2.4,
            "log_prob_threshold": -1.0,
            "no_speech_threshold": 0.8,
            "condition_on_previous_text": True,
            "temperature": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        },
        "initial_prompt": (
            "안녕하세요, 대리운전입니다. 어디로 모실까요? "
            "출발지와 도착지를 말씀해 주세요. "
            "강남역, 홍대입구역, 서울역, 잠실역, 신림역, 구로디지털단지역, "
            "수원역, 인천역, 판교역, 부천역, 일산, 분당, "
            "아파트, 주민센터, 사거리, 삼거리, 대로, "
            "대리 요금, 만 원, 이만 원, 삼만 원, 할인, 추가 요금, "
            "기사님, 손님, 고객님, 예약, 배차, 도착 예정"
        ),
    },
    "preprocessing": {
        "enabled": True,
        "resample_rate": 16000,
        "bandpass": {"enabled": True, "low_freq": 300, "high_freq": 3400},
        "noise_reduction": {"enabled": True, "prop_decrease": 0.8},
        "normalize": {"enabled": True, "target_dbfs": -20.0},
    },
    "prompt_auto_update": {
        "enabled": True,
        "prompt_file": "stt/domain_prompt.txt",
        "vocabulary_file": "stt/domain_vocabulary.json",
        "update_interval": 50,
        "max_prompt_tokens": 200,
    },
    "learning": {
        "enabled": True,
        "data_dir": "stt/training_data",
        "location_extraction": {
            "enabled": True,
            "patterns": [
                r"(에서|부터)\s*출발",
                r"(으로|까지)\s*(가|와|오)",
                r"(역|동|구|시|읍|면|리|아파트|빌딩|타워|센터|병원|학교|대학|공원|마트)",
            ],
        },
        "vocab_update_interval": 100,
    },
}


def _load_stt_config() -> dict[str, Any]:
    """server/config.yaml에서 stt 섹션을 로드. 없으면 기본값 사용."""
    global _stt_config
    if _stt_config is not None:
        return _stt_config

    import yaml

    config_paths = [
        Path(__file__).resolve().parents[2] / "config.yaml",  # server/config.yaml
        Path("server/config.yaml"),
        Path("config.yaml"),
    ]
    for p in config_paths:
        if p.exists():
            try:
                with open(p, encoding="utf-8") as f:
                    full = yaml.safe_load(f) or {}
                raw = full.get("stt", {})
                if raw:
                    import copy
                    merged = copy.deepcopy(_DEFAULT_STT_CONFIG)
                    _deep_merge(merged, raw)
                    _stt_config = merged
                    logger.info("STT config loaded from %s", p)
                    return _stt_config
            except Exception as e:
                logger.warning("Failed to load STT config from %s: %s", p, e)

    _stt_config = _DEFAULT_STT_CONFIG.copy()
    logger.info("Using default STT config (no config.yaml found)")
    return _stt_config


def _deep_merge(base: dict, override: dict) -> None:
    """딥 머지: override 값으로 base를 재귀적으로 업데이트."""
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def get_stt_config() -> dict[str, Any]:
    """STT 설정을 반환. 외부에서 참조 가능."""
    return _load_stt_config()


def reload_stt_config() -> dict[str, Any]:
    """STT 설정 강제 리로드."""
    global _stt_config
    _stt_config = None
    return _load_stt_config()


# ── Audio preprocessing ──


def _preprocess_audio(wav_path: str, cfg: dict[str, Any] | None = None) -> str:
    """오디오 전처리 파이프라인.

    1. WAV 로드 및 float32 변환
    2. 리샘플링 (→ 16kHz)
    3. 대역 필터 (300~3400Hz, 전화 음성)
    4. 노이즈 감소
    5. 음량 정규화

    Returns: 전처리된 임시 WAV 경로 (또는 비활성화 시 원본 경로).
    """
    if cfg is None:
        cfg = _load_stt_config().get("preprocessing", {})
    if not cfg.get("enabled", False):
        return wav_path

    try:
        # WAV 로드
        raw, sample_rate, n_channels, sample_width, fmt_code = _read_wav_raw(wav_path)

        # μ-law → PCM 변환
        if fmt_code == 7:
            samples = _decode_mulaw(raw).astype(np.float32) / 32768.0
        elif sample_width == 2:
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        elif sample_width == 1:
            samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        else:
            logger.warning("Unsupported sample_width=%d for preprocessing, skipping", sample_width)
            return wav_path

        sample_rate_orig = sample_rate

        # 모노 변환 (필요시)
        if n_channels >= 2:
            samples = samples.reshape(-1, n_channels).mean(axis=1)

        # 1. 리샘플링
        target_sr = cfg.get("resample_rate", 16000)
        if sample_rate_orig != target_sr:
            from scipy.signal import resample_poly
            from math import gcd
            g = gcd(sample_rate_orig, target_sr)
            up = target_sr // g
            down = sample_rate_orig // g
            samples = resample_poly(samples, up, down).astype(np.float32)
            sample_rate = target_sr
        else:
            sample_rate = sample_rate_orig

        # 2. 대역 필터 (300~3400Hz)
        bp_cfg = cfg.get("bandpass", {})
        if bp_cfg.get("enabled", False):
            from scipy.signal import butter, sosfilt
            low = bp_cfg.get("low_freq", 300)
            high = bp_cfg.get("high_freq", 3400)
            nyq = sample_rate / 2.0
            if high < nyq:
                sos = butter(5, [low / nyq, high / nyq], btype="band", output="sos")
                samples = sosfilt(sos, samples).astype(np.float32)

        # 3. 노이즈 감소
        nr_cfg = cfg.get("noise_reduction", {})
        if nr_cfg.get("enabled", False):
            try:
                import noisereduce as nr
                prop = nr_cfg.get("prop_decrease", 0.8)
                samples = nr.reduce_noise(
                    y=samples, sr=sample_rate, prop_decrease=prop,
                    n_fft=512, hop_length=128,
                ).astype(np.float32)
            except ImportError:
                logger.debug("noisereduce not installed, skipping noise reduction")

        # 4. 음량 정규화
        norm_cfg = cfg.get("normalize", {})
        if norm_cfg.get("enabled", False):
            target_dbfs = norm_cfg.get("target_dbfs", -20.0)
            rms = np.sqrt(np.mean(samples ** 2))
            if rms > 0:
                current_dbfs = 20 * np.log10(rms + 1e-10)
                gain = 10 ** ((target_dbfs - current_dbfs) / 20)
                samples = (samples * gain).astype(np.float32)
                peak = np.max(np.abs(samples))
                if peak > 1.0:
                    samples = (samples / peak * 0.99).astype(np.float32)

        # 임시 파일로 저장
        tmp = tempfile.NamedTemporaryFile(suffix="_preprocessed.wav", delete=False)
        tmp.close()
        int16_data = (samples * 32767).clip(-32768, 32767).astype(np.int16)
        with wave.open(tmp.name, "wb") as wf_out:
            wf_out.setnchannels(1)
            wf_out.setsampwidth(2)
            wf_out.setframerate(sample_rate)
            wf_out.writeframes(int16_data.tobytes())

        logger.debug(
            "Audio preprocessed: %s → %s (sr=%d, dur=%.1fs)",
            wav_path, tmp.name, sample_rate, len(samples) / sample_rate,
        )
        return tmp.name

    except Exception as e:
        logger.warning("Audio preprocessing failed for %s: %s, using original", wav_path, e)
        return wav_path


# ── Domain prompt management ──


def _get_domain_prompt() -> str:
    """도메인 프롬프트를 반환.

    우선순위:
    1. 자동 생성된 프롬프트 파일 (prompt_auto_update.prompt_file)
    2. config.yaml의 initial_prompt
    3. 기본 프롬프트
    """
    cfg = _load_stt_config()
    update_cfg = cfg.get("prompt_auto_update", {})

    # 자동 생성 프롬프트 파일 확인
    if update_cfg.get("enabled", False):
        prompt_file = update_cfg.get("prompt_file", "stt/domain_prompt.txt")
        storage_base = Path(__file__).resolve().parents[2] / "storage"
        prompt_path = storage_base / prompt_file
        if prompt_path.exists():
            try:
                text = prompt_path.read_text(encoding="utf-8").strip()
                if text:
                    logger.debug("Using auto-generated domain prompt from %s", prompt_path)
                    return text
            except Exception:
                pass

    # config 프롬프트
    whisper_cfg = cfg.get("whisper", {})
    prompt = whisper_cfg.get("initial_prompt", "")
    if isinstance(prompt, str) and prompt.strip():
        return prompt.strip()

    # 기본값
    return _DEFAULT_STT_CONFIG["whisper"]["initial_prompt"]


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


def _get_wav_duration(wav_path: str) -> float:
    """Get WAV file duration in seconds."""
    try:
        with wave.open(wav_path, "rb") as wf:
            rate = wf.getframerate()
            return wf.getnframes() / rate if rate > 0 else 0.0
    except wave.Error:
        raw, sample_rate, n_channels, sample_width, fmt = _read_wav_raw(wav_path)
        if fmt == 1:
            n_frames = len(raw) // (n_channels * sample_width)
        else:
            n_frames = len(raw) // n_channels
        return n_frames / sample_rate if sample_rate > 0 else 0.0


def _split_mono_wav_chunks(
    wav_path: str,
    *,
    chunk_sec: float = 12.0,
) -> list[tuple[str, float, float]]:
    """Split mono PCM WAV into temporary chunk files.

    Returns list of ``(chunk_path, start_sec, end_sec)``.
    If file cannot be parsed by ``wave`` (e.g. non-PCM), returns empty list.
    """
    try:
        with wave.open(wav_path, "rb") as wf:
            n_channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            sample_rate = wf.getframerate()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
    except wave.Error:
        return []

    if n_channels != 1 or sample_rate <= 0:
        return []

    frames_per_chunk = max(1, int(sample_rate * chunk_sec))
    chunks: list[tuple[str, float, float]] = []
    frame = 0
    bytes_per_frame = sample_width

    while frame < n_frames:
        end_frame = min(frame + frames_per_chunk, n_frames)
        start_byte = frame * bytes_per_frame
        end_byte = end_frame * bytes_per_frame
        chunk_raw = raw[start_byte:end_byte]

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        with wave.open(tmp.name, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(sample_width)
            out.setframerate(sample_rate)
            out.writeframes(chunk_raw)

        start_sec = frame / sample_rate
        end_sec = end_frame / sample_rate
        chunks.append((tmp.name, start_sec, end_sec))
        frame = end_frame

    return chunks


def get_model(
    model_size: str | None = None,
    device: str | None = None,
    compute_type: str | None = None,
) -> WhisperModel:
    """Whisper 모델 로드 (레이지 로딩, 캐싱).

    파라미터 생략 시 config.yaml 설정 사용.
    HuggingFace 모델 ID (e.g. ghost613/faster-whisper-large-v3-turbo-korean) 지원.
    """
    global _model, _model_size
    cfg = _load_stt_config().get("whisper", {})
    model_size = model_size or cfg.get("model", "medium")
    device = device or cfg.get("device", "cpu")
    compute_type = compute_type or cfg.get("compute_type", "int8")
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




# ── 긴 세그먼트 후처리 분할 ──
# 한국어 문장 종결 패턴
_KO_SENTENCE_END = re.compile(
    r'(?<=[.?!。])'  # 일반 문장부호
    r'|(?<=합니다)'
    r'|(?<=습니다)'
    r'|(?<=세요)'
    r'|(?<=거든요)'
    r'|(?<=잖아요)'
    r'|(?<=는데요)'
    r'|(?<=이에요)'
    r'|(?<=예요)'
    r'|(?<=이요)'
    r'|(?<=해요)'
    r'|(?<=하죠)'
    r'|(?<=구요)'
    r'|(?<=네요)'
    r'|(?<=군요)'
    r'|(?<=인데)'
    r'|(?<=는데)'
    r'|(?<=지요)'
    r'|(?<=죠)'
    r'|(?<=요)\s'  # 종결어미 '요' 뒤 공백
)


def _map_words_to_segments(
    segments: list[Segment],
    word_segments: list[list],
) -> list[list]:
    """word_segments를 실제 result segments에 매핑.

    Whisper가 1개 raw segment를 생성하고 VAD가 이를 N개로 분할하면,
    word_segments[0]에 모든 단어가 들어있고 word_segments[1:]는 비어있거나 없다.
    이 함수는 모든 단어를 시간 범위 기준으로 올바른 segment에 재배치한다.
    """
    if not word_segments:
        return [[] for _ in segments]

    # 모든 word timestamps를 하나의 리스트로 평탄화
    all_words: list[tuple] = []
    for ws in word_segments:
        if ws:
            all_words.extend(ws)

    if not all_words:
        return [[] for _ in segments]

    # 시간순 정렬
    all_words.sort(key=lambda w: w[1])

    # 각 segment의 시간 범위에 속하는 단어들을 할당
    mapped: list[list] = [[] for _ in segments]
    word_idx = 0
    for seg_idx, seg in enumerate(segments):
        while word_idx < len(all_words):
            _, w_start, w_end = all_words[word_idx]
            w_mid = (w_start + w_end) / 2  # 단어 중간 시점 기준
            # 단어의 중간 시점이 세그먼트 범위에 포함되면 할당
            # 마지막 세그먼트는 남은 단어 모두 수용 (tolerance)
            if w_mid <= seg.end + 0.1 or seg_idx == len(segments) - 1:
                mapped[seg_idx].append(all_words[word_idx])
                word_idx += 1
            else:
                break

    return mapped


def _split_long_segments(
    segments: list[Segment],
    max_duration: float = 8.0,
    word_segments: list[list] | None = None,
) -> list[Segment]:
    """긴 세그먼트를 한국어 문장 경계에서 분할.

    Args:
        segments: 원본 세그먼트 리스트.
        max_duration: 이 초 이상인 세그먼트를 분할 시도.
        word_segments: 세그먼트별 word-level 타임스탬프 리스트.
            word_segments[i] = [(word, start, end), ...] for segments[i].

    Returns:
        분할된 세그먼트 리스트 (짧은 세그먼트는 그대로 유지).
    """
    result: list[Segment] = []

    for seg_idx, seg in enumerate(segments):
        duration = seg.end - seg.start
        if duration <= max_duration:
            result.append(seg)
            continue

        # word timestamps가 있으면 단어 단위 분할
        if word_segments and seg_idx < len(word_segments) and word_segments[seg_idx]:
            words = word_segments[seg_idx]
            result.extend(_split_by_words(seg, words, max_duration))
        else:
            # word timestamps 없으면 텍스트 기반 균등 분할
            result.extend(_split_by_text(seg, max_duration))

    return result


def _split_by_words(
    seg: Segment, words: list, max_duration: float,
) -> list[Segment]:
    """단어 타임스탬프를 이용한 문장 경계 분할."""
    if not words:
        return [seg]

    # 문장 종결 위치 찾기
    split_points: list[int] = []  # word index after which to split
    accumulated_text = ""
    last_split_time = words[0][1]  # 마지막 분할 시점 (초기: 첫 단어 시작)
    for i, (word, w_start, w_end) in enumerate(words):
        accumulated_text += word
        # 문장 종결 패턴 매칭
        if _KO_SENTENCE_END.search(word):
            # 마지막 분할점 이후 누적 시간이 max_duration의 40% 이상이면 분할
            if i > 0:
                elapsed = w_end - last_split_time
                if elapsed >= max_duration * 0.4:
                    split_points.append(i)
                    accumulated_text = ""
                    last_split_time = w_end  # 분할 시점 갱신

    if not split_points:
        # 문장 경계 없으면 시간 기반 균등 분할
        return _split_by_time(seg, words, max_duration)

    # 분할 실행
    result: list[Segment] = []
    prev_idx = 0
    for sp in split_points:
        chunk_words = words[prev_idx:sp + 1]
        if chunk_words:
            text = "".join(w for w, _, _ in chunk_words).strip()
            if text:
                result.append(Segment(
                    start=round(chunk_words[0][1], 3),
                    end=round(chunk_words[-1][2], 3),
                    text=text,
                ))
        prev_idx = sp + 1

    # 나머지
    if prev_idx < len(words):
        chunk_words = words[prev_idx:]
        text = "".join(w for w, _, _ in chunk_words).strip()
        if text:
            result.append(Segment(
                start=round(chunk_words[0][1], 3),
                end=round(chunk_words[-1][2], 3),
                text=text,
            ))

    # 분할 후에도 max_duration 초과 세그먼트가 있으면 시간 기반 재분할
    final: list[Segment] = []
    for s in result:
        if (s.end - s.start) > max_duration:
            # 이 청크에 해당하는 word timestamps 찾기
            chunk_ws = [(w, ws, we) for w, ws, we in words
                        if ws >= s.start - 0.05 and we <= s.end + 0.05]
            if chunk_ws:
                final.extend(_split_by_time(s, chunk_ws, max_duration))
            else:
                final.append(s)
        else:
            final.append(s)

    return final if final else [seg]


def _split_by_time(
    seg: Segment, words: list, max_duration: float,
) -> list[Segment]:
    """단어 타임스탬프 기반 시간 균등 분할 (문장 경계 없을 때).

    단어 간 큰 간격(gap)이 있으면 해당 지점에서 우선 분할하고,
    그렇지 않으면 시간 균등 분할한다.
    """
    if not words:
        return [seg]
    # 전략 1: 단어 간 큰 간격(gap) 기준 분할 (무음 구간 > 2초)
    gap_threshold = 2.0  # 단어 사이 이 이상 무음이면 분할
    gap_splits: list[int] = []  # gap 이전 단어 인덱스
    for i in range(len(words) - 1):
        _, _, prev_end = words[i]
        _, next_start, _ = words[i + 1]
        if next_start - prev_end >= gap_threshold:
            gap_splits.append(i)

    if gap_splits:
        # gap 기준 분할
        result: list[Segment] = []
        prev_idx = 0
        for gi in gap_splits:
            chunk_words = words[prev_idx:gi + 1]
            if chunk_words:
                text = "".join(w for w, _, _ in chunk_words).strip()
                if text:
                    result.append(Segment(
                        start=round(chunk_words[0][1], 3),
                        end=round(chunk_words[-1][2], 3),
                        text=text,
                    ))
            prev_idx = gi + 1
        # 나머지
        if prev_idx < len(words):
            chunk_words = words[prev_idx:]
            text = "".join(w for w, _, _ in chunk_words).strip()
            if text:
                result.append(Segment(
                    start=round(chunk_words[0][1], 3),
                    end=round(chunk_words[-1][2], 3),
                    text=text,
                ))
        return result if result else [seg]

    # 전략 2: 시간 균등 분할 (gap이 없을 때)
    total_dur = seg.end - seg.start
    n_chunks = max(2, int(total_dur / max_duration + 0.5))
    chunk_dur = total_dur / n_chunks
    result = []
    current_words: list = []
    chunk_start_time = words[0][1]
    current_target = chunk_start_time + chunk_dur

    for word, w_start, w_end in words:
        current_words.append((word, w_start, w_end))
        if w_end >= current_target and len(result) < n_chunks - 1:
            text = "".join(w for w, _, _ in current_words).strip()
            if text:
                result.append(Segment(
                    start=round(current_words[0][1], 3),
                    end=round(current_words[-1][2], 3),
                    text=text,
                ))
            current_words = []
            current_target = w_end + chunk_dur
    # 나머지
    if current_words:
        text = "".join(w for w, _, _ in current_words).strip()
        if text:
            result.append(Segment(
                start=round(current_words[0][1], 3),
                end=round(current_words[-1][2], 3),
                text=text,
            ))

    return result if result else [seg]

def _split_by_text(seg: Segment, max_duration: float) -> list[Segment]:
    """word timestamps 없을 때 텍스트 기반 문장 경계 분할 (fallback).

    _KO_SENTENCE_END lookbehind로 문장 경계 위치를 찾고,
    해당 위치에서 텍스트를 분할한 뒤 시간을 비율 배분.
    """
    text = seg.text
    # 문장 경계 위치 찾기
    split_positions = [m.start() for m in _KO_SENTENCE_END.finditer(text)]

    if not split_positions:
        return [seg]

    # 텍스트를 분할 위치에서 자르기
    parts: list[str] = []
    prev = 0
    for pos in split_positions:
        chunk = text[prev:pos].strip()
        if chunk:
            parts.append(chunk)
        prev = pos
    # 나머지
    remainder = text[prev:].strip()
    if remainder:
        parts.append(remainder)
    if len(parts) <= 1:
        return [seg]

    # 인접한 짧은 파트 병합 (최소 3글자 이상)
    merged: list[str] = []
    for p in parts:
        if merged and len(merged[-1]) < 3:
            merged[-1] += " " + p
        else:
            merged.append(p)
    if len(merged) <= 1:
        return [seg]
    # 시간을 텍스트 길이 비율로 배분
    total_len = sum(len(p) for p in merged)
    total_dur = seg.end - seg.start
    result: list[Segment] = []
    current_start = seg.start
    for p in merged:
        ratio = len(p) / total_len if total_len > 0 else 1.0 / len(merged)
        chunk_dur = total_dur * ratio
        result.append(Segment(
            start=round(current_start, 3),
            end=round(current_start + chunk_dur, 3),
            text=p,
        ))
        current_start += chunk_dur


    return result if result else [seg]

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


# ── OpenAI API transcription ──


def _transcribe_openai(
    wav_path: str,
    language: str = "ko",
    model: str = "whisper-1",
    prompt: str = "",
) -> list[Segment]:
    """Transcribe using OpenAI Audio Transcription API.

    - whisper-1: verbose_json 지원 → 세그먼트별 타임스탬프 반환
    - gpt-4o-*-transcribe: verbose_json 세그먼트 타임스탬프 우선 사용,
      미지원/빈 응답 시 text 기반 단일 세그먼트 폴백
    """
    import openai as _openai

    client = _openai.OpenAI()

    try:
        if model == "whisper-1":
            return _transcribe_openai_whisper(client, wav_path, language, prompt)
        else:
            return _transcribe_openai_gpt4o(client, wav_path, language, model, prompt)
    except _openai.AuthenticationError:
        logger.error("OpenAI API key not configured or invalid")
        raise
    except _openai.RateLimitError:
        logger.error("OpenAI API rate limit exceeded")
        raise
    except _openai.BadRequestError as e:
        logger.error("OpenAI API bad request: %s", e)
        raise
    except _openai.APIConnectionError:
        logger.error("OpenAI API connection failed (network issue)")
        raise


def _transcribe_openai_whisper(
    client: object,
    wav_path: str,
    language: str,
    prompt: str,
) -> list[Segment]:
    """whisper-1 모델: verbose_json으로 세그먼트별 타임스탬프 획득."""
    import openai as _openai

    _client = _openai.OpenAI() if not isinstance(client, _openai.OpenAI) else client

    with open(wav_path, "rb") as f:
        common = dict(
            model="whisper-1",
            file=f,
            language=language,
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )
        if prompt:
            common["prompt"] = prompt
        response = _client.audio.transcriptions.create(**common)  # type: ignore[arg-type]

    results: list[Segment] = []
    segments = getattr(response, "segments", None) or []
    for seg in segments:
        # SDK 객체 또는 dict 양쪽 지원
        if isinstance(seg, dict):
            text = str(seg.get("text", "")).strip()
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", 0.0))
        else:
            text = str(getattr(seg, "text", "")).strip()
            start = float(getattr(seg, "start", 0.0))
            end = float(getattr(seg, "end", 0.0))

        if text:
            results.append(Segment(start=round(start, 3), end=round(end, 3), text=text))

    logger.info(
        "OpenAI (whisper-1) transcribed %s: %d segment(s), lang=%s",
        wav_path,
        len(results),
        language,
    )
    return results


def _transcribe_openai_gpt4o(
    client: object,
    wav_path: str,
    language: str,
    model: str,
    prompt: str,
) -> list[Segment]:
    """gpt-4o-*-transcribe 모델 전사.

    모델이 세그먼트 타임스탬프를 제공하면 사용하고,
    일반적으로는 text 응답을 단일 세그먼트로 폴백한다.
    """
    import openai as _openai

    _client = _openai.OpenAI() if not isinstance(client, _openai.OpenAI) else client

    with open(wav_path, "rb") as f:
        common: dict[str, object] = dict(
            model=model,
            file=f,
            language=language,
            response_format="json",
        )
        if prompt:
            common["prompt"] = prompt
        response = _client.audio.transcriptions.create(**common)  # type: ignore[arg-type]

    results: list[Segment] = []
    raw_segments = getattr(response, "segments", None) or []
    for seg in raw_segments:
        if isinstance(seg, dict):
            text = str(seg.get("text", "")).strip()
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", 0.0))
        else:
            text = str(getattr(seg, "text", "")).strip()
            start = float(getattr(seg, "start", 0.0))
            end = float(getattr(seg, "end", 0.0))
        if text:
            results.append(Segment(start=round(start, 3), end=round(end, 3), text=text))

    if results:
        logger.info(
            "OpenAI (%s) transcribed %s: %d segment(s)",
            model,
            wav_path,
            len(results),
        )
        return results

    text = str(getattr(response, "text", "")).strip()
    if not text:
        logger.info("OpenAI (%s) transcribed %s: empty result", model, wav_path)
        return []

    duration = _get_wav_duration(wav_path)
    logger.info(
        "OpenAI (%s) transcribed %s: no segments, fallback text_len=%d duration=%.1fs",
        model,
        wav_path,
        len(text),
        duration,
    )
    return [Segment(start=0.0, end=round(duration, 3), text=text)]


def _transcribe_openai_gpt4o_chunked(
    wav_path: str,
    language: str,
    model: str,
    prompt: str,
    *,
    chunk_sec: float = 12.0,
) -> list[Segment]:
    """Chunked fallback for gpt-4o transcription when per-file output is coarse.

    The model may return a single long text block per channel. Splitting a mono
    channel WAV into time windows restores turn granularity for downstream merge.
    """
    import openai as _openai

    chunks = _split_mono_wav_chunks(wav_path, chunk_sec=chunk_sec)
    if len(chunks) <= 1:
        return []

    client = _openai.OpenAI()
    merged: list[Segment] = []

    for chunk_path, chunk_start, chunk_end in chunks:
        try:
            chunk_segs = _transcribe_openai_gpt4o(
                client,
                chunk_path,
                language,
                model,
                prompt,
            )
        finally:
            with contextlib.suppress(OSError):
                os.unlink(chunk_path)

        for seg in chunk_segs:
            text = seg.text.strip()
            if not text:
                continue
            abs_start = round(chunk_start + seg.start, 3)
            abs_end = round(min(chunk_end, chunk_start + seg.end), 3)
            if abs_end <= abs_start:
                abs_end = round(chunk_end, 3)
            merged.append(Segment(start=abs_start, end=abs_end, text=text))

    merged.sort(key=lambda x: x.start)
    return merged


def _transcribe_openai_diarize(
    wav_path: str,
    language: str = "ko",
) -> TranscriptResult:
    """gpt-4o-transcribe-diarize: 화자 분리 + 타임스탬프 네이티브 지원.

    채널 분리 없이 전체 WAV 파일을 보내면 화자별 세그먼트와 타임스탬프를 반환.
    30초 이상 오디오는 chunking_strategy='auto' 필수.
    """
    import openai as _openai

    client = _openai.OpenAI()

    try:
        with open(wav_path, "rb") as f:
            response = client.audio.transcriptions.create(
                model="gpt-4o-transcribe-diarize",
                file=f,
                response_format="diarized_json",  # type: ignore[arg-type]
                chunking_strategy="auto",  # type: ignore[arg-type]
                language=language,
            )
    except _openai.AuthenticationError:
        logger.error("OpenAI API key not configured or invalid")
        raise
    except _openai.RateLimitError:
        logger.error("OpenAI API rate limit exceeded")
        raise
    except _openai.BadRequestError as e:
        logger.error("OpenAI API bad request: %s", e)
        raise
    except _openai.APIConnectionError:
        logger.error("OpenAI API connection failed (network issue)")
        raise

    # Parse diarized segments
    raw_segments = getattr(response, "segments", None) or []
    logger.info(
        "OpenAI (diarize) raw response: %d segment(s), first 3: %s",
        len(raw_segments),
        [
            {
                "speaker": getattr(
                    s, "speaker", s.get("speaker", "?") if isinstance(s, dict) else "?"
                ),
                "start": getattr(
                    s, "start", s.get("start", 0) if isinstance(s, dict) else 0
                ),
                "end": getattr(s, "end", s.get("end", 0) if isinstance(s, dict) else 0),
            }
            for s in raw_segments[:3]
        ],
    )
    combined: list[SpeakerSegment] = []
    agent_segs: list[Segment] = []
    customer_segs: list[Segment] = []
    # 화자 매핑: diarize 모델이 반환하는 speaker 레이블 기준
    # 일반적으로 상담원이 먼저 응대하므로 첫 화자 = agent
    first_speaker: str | None = None
    for seg in raw_segments:
        if isinstance(seg, dict):
            speaker_raw = str(seg.get("speaker", "unknown"))
            text = str(seg.get("text", "")).strip()
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", 0.0))
        else:
            speaker_raw = str(getattr(seg, "speaker", "unknown"))
            text = str(getattr(seg, "text", "")).strip()
            start = float(getattr(seg, "start", 0.0))
            end = float(getattr(seg, "end", 0.0))
        if not text:
            continue
        # 첫 화자를 agent로 매핑 (상담원 응대 패턴)
        if first_speaker is None:
            first_speaker = speaker_raw
        speaker = "agent" if speaker_raw == first_speaker else "customer"
        combined.append(
            SpeakerSegment(
                time=round(start, 3),
                end=round(end, 3),
                speaker=speaker,
                text=text,
            )
        )
        seg_obj = Segment(start=round(start, 3), end=round(end, 3), text=text)
        if speaker == "agent":
            agent_segs.append(seg_obj)
        else:
            customer_segs.append(seg_obj)
    # diarize API는 이미 시간순 정렬하여 반환하므로 재정렬하지 않음
    # (재정렬하면 동일 시간에서 API 원본 순서가 깨질 수 있음)

    agent_text = " ".join(s.text for s in agent_segs)
    customer_text = " ".join(s.text for s in customer_segs)
    full_text = " ".join(s.text for s in combined)

    all_ends = [s.end for s in agent_segs] + [s.end for s in customer_segs]
    duration = max(all_ends) if all_ends else _get_wav_duration(wav_path)
    word_count = len(full_text.split()) if full_text else 0

    logger.info(
        "OpenAI (diarize) transcribed %s: %d segment(s), speakers=%d",
        wav_path,
        len(combined),
        len({s.speaker for s in combined}),
    )

    return TranscriptResult(
        segments=combined,
        agent_segments=agent_segs,
        customer_segments=customer_segs,
        agent_text=agent_text,
        customer_text=customer_text,
        full_text=full_text,
        duration_sec=round(duration, 3),
        word_count=word_count,
    )


# ── RTZR (리턴제로) API transcription ──

_rtzr_token: str | None = None
_rtzr_token_expire: float = 0.0


def _rtzr_authenticate() -> str:
    """RTZR JWT 토큰 발급/캐싱. 만료 5분 전 자동 갱신."""
    import requests as _requests

    global _rtzr_token, _rtzr_token_expire
    if _rtzr_token and _time.time() < _rtzr_token_expire - 300:
        return _rtzr_token

    client_id = os.environ.get("RTZR_CLIENT_ID", "")
    client_secret = os.environ.get("RTZR_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise RuntimeError(
            "RTZR credentials not configured. "
            "Set RTZR_CLIENT_ID and RTZR_CLIENT_SECRET environment variables."
        )

    resp = _requests.post(
        "https://openapi.vito.ai/v1/authenticate",
        data={"client_id": client_id, "client_secret": client_secret},
    )
    resp.raise_for_status()
    data = resp.json()
    token = str(data["access_token"])
    _rtzr_token = token
    _rtzr_token_expire = float(data["expire_at"])
    logger.info("RTZR authenticated, token expires at %s", _rtzr_token_expire)
    return token


def _transcribe_rtzr(
    wav_path: str,
    language: str = "ko",
    in_out: int = 0,
) -> TranscriptResult:
    """RTZR sommers 모델: CALL 도메인 + 화자 분리 네이티브 지원.
    스테레오: use_multi_channel(채널별 분리 전사) / 모노: use_diarization(AI 화자 분리).
    """
    import requests as _requests

    token = _rtzr_authenticate()
    headers = {"Authorization": f"Bearer {token}"}

    # 채널 수 확인 → 스테레오이면 multi_channel, 모노이면 diarization
    try:
        with wave.open(wav_path, "rb") as wf:
            n_channels = wf.getnchannels()
    except wave.Error:
        _, _, n_channels, _, _ = _read_wav_raw(wav_path)

    is_stereo = n_channels >= 2

    config: dict[str, Any] = {
        "model_name": "sommers",
        "domain": "CALL",
        "use_itn": True,
        "use_disfluency_filter": True,
        "use_paragraph_splitter": True,
        "paragraph_splitter": {"max": 50},
        "language": language,
    }

    if is_stereo:
        # 스테레오: 채널별 독립 전사 (spk = 채널 인덱스)
        config["use_multi_channel"] = True
    else:
        # 모노: AI 기반 화자 분리
        config["use_diarization"] = True
        config["diarization"] = {"spk_count": 2}
    # 1) 전사 요청 제출
    with open(wav_path, "rb") as f:
        resp = _requests.post(
            "https://openapi.vito.ai/v1/transcribe",
            headers=headers,
            data={"config": json.dumps(config)},
            files={"file": (os.path.basename(wav_path), f)},
        )
    resp.raise_for_status()
    transcribe_id = resp.json()["id"]
    logger.info(
        "RTZR submitted %s: transcribe_id=%s, stereo=%s",
        wav_path, transcribe_id, is_stereo,
    )

    # 2) 결과 폴링 (최대 10분)
    deadline = _time.time() + 600
    result: dict[str, Any] = {}
    while _time.time() < deadline:
        _time.sleep(5)
        poll_resp = _requests.get(
            f"https://openapi.vito.ai/v1/transcribe/{transcribe_id}",
            headers={"Authorization": f"Bearer {_rtzr_authenticate()}"},
        )
        poll_resp.raise_for_status()
        result = poll_resp.json()
        status = result.get("status")

        if status == "completed":
            break
        elif status == "failed":
            error = result.get("error", {})
            raise RuntimeError(
                f"RTZR transcription failed: {error.get('code')} - {error.get('message')}"
            )
        # status == "transcribing" → 계속 폴링
    else:
        raise TimeoutError(f"RTZR transcription timed out after 600s: {transcribe_id}")

    # 3) 결과 파싱
    utterances = result.get("results", {}).get("utterances", [])
    logger.info(
        "RTZR transcribed %s: %d utterance(s), first 3: %s",
        wav_path,
        len(utterances),
        [
            {
                "spk": u.get("spk"),
                "start_at": u.get("start_at"),
                "msg": u.get("msg", "")[:30],
            }
            for u in utterances[:3]
        ],
    )

    combined: list[SpeakerSegment] = []
    agent_segs: list[Segment] = []
    customer_segs: list[Segment] = []

    if is_stereo:
        # 멀티채널: spk = 채널 인덱스 → PBX 채널 매핑 적용
        # PBX 고정 배치: Left(ch0) = 고객, Right(ch1) = 상담원
        # 발신(in_out==2): PBX가 채널을 반대로 기록
        if in_out == 2:
            ch_agent, ch_customer = 0, 1
        else:
            ch_agent, ch_customer = 1, 0

        for u in utterances:
            start_ms = u.get("start_at", 0)
            duration_ms = u.get("duration", 0)
            start = round(start_ms / 1000, 3)
            end = round((start_ms + duration_ms) / 1000, 3)
            text = u.get("msg", "").strip()
            spk = u.get("spk", 0)

            if not text:
                continue

            speaker = "agent" if spk == ch_agent else "customer"

            combined.append(SpeakerSegment(time=start, end=end, speaker=speaker, text=text))
            seg = Segment(start=start, end=end, text=text)
            if speaker == "agent":
                agent_segs.append(seg)
            else:
                customer_segs.append(seg)
    else:
        # 모노: diarization spk → 첫 화자 기반 매핑
        # - 수신/기타(in_out!=2): 첫 화자=agent
        # - 발신(in_out==2): 첫 화자=customer
        first_spk: int | None = None
        first_role = "customer" if in_out == 2 else "agent"
        other_role = "agent" if first_role == "customer" else "customer"

        for u in utterances:
            start_ms = u.get("start_at", 0)
            duration_ms = u.get("duration", 0)
            start = round(start_ms / 1000, 3)
            end = round((start_ms + duration_ms) / 1000, 3)
            text = u.get("msg", "").strip()
            spk = u.get("spk", 0)

            if not text:
                continue

            if first_spk is None:
                first_spk = spk

            speaker = first_role if spk == first_spk else other_role

            combined.append(SpeakerSegment(time=start, end=end, speaker=speaker, text=text))
            seg = Segment(start=start, end=end, text=text)
            if speaker == "agent":
                agent_segs.append(seg)
            else:
                customer_segs.append(seg)

    agent_text = " ".join(s.text for s in agent_segs)
    customer_text = " ".join(s.text for s in customer_segs)
    full_text = " ".join(s.text for s in combined)

    all_ends = [s.end for s in agent_segs] + [s.end for s in customer_segs]
    duration = max(all_ends) if all_ends else _get_wav_duration(wav_path)
    word_count = len(full_text.split()) if full_text else 0

    logger.info(
        "RTZR completed %s: %d segment(s), speakers=%d, duration=%.1fs, in_out=%d, stereo=%s",
        wav_path,
        len(combined),
        len({s.speaker for s in combined}),
        duration,
        in_out,
        is_stereo,
    )

    return TranscriptResult(
        segments=combined,
        agent_segments=agent_segs,
        customer_segments=customer_segs,
        agent_text=agent_text,
        customer_text=customer_text,
        full_text=full_text,
        duration_sec=round(duration, 3),
        word_count=word_count,
    )


# ── Core transcribe functions ──


def transcribe(
    wav_path: str,
    language: str = "ko",
    beam_size: int = 5,
    *,
    engine: str = "local",
    openai_prompt: str = "",
) -> list[Segment]:
    """Transcribe a mono WAV file using the specified engine.
    Args:
        wav_path: Path to mono WAV file.
        language: BCP-47 language code.
        beam_size: Beam size for local engine.
        engine: 'local', 'openai-whisper', or 'openai-gpt4o'.
        openai_prompt: Domain-specific prompt hint for OpenAI models.
    """
    if engine == "openai-whisper":
        return _transcribe_openai(
            wav_path, language, model="whisper-1", prompt=openai_prompt
        )
    elif engine == "openai-gpt4o":
        segs = _transcribe_openai(
            wav_path,
            language,
            model="gpt-4o-mini-transcribe",
            prompt=openai_prompt,
        )
        duration = _get_wav_duration(wav_path)
        if len(segs) <= 1 and duration >= 20.0:
            chunked = _transcribe_openai_gpt4o_chunked(
                wav_path,
                language,
                "gpt-4o-mini-transcribe",
                openai_prompt,
            )
            if len(chunked) > len(segs):
                logger.info(
                    "OpenAI (gpt4o) chunked fallback applied: %d -> %d segment(s)",
                    len(segs),
                    len(chunked),
                )
                return chunked
        return segs
    else:
        # local (faster-whisper) — 한국어 최적화 적용
        cfg = _load_stt_config()
        whisper_cfg = cfg.get("whisper", {})
        hp = whisper_cfg.get("hallucination_prevention", {})

        # 오디오 전처리
        preprocessed_path = _preprocess_audio(wav_path, cfg.get("preprocessing"))
        preprocessed = preprocessed_path != wav_path

        try:
            model = get_model()
            # 도메인 프롬프트
            domain_prompt = _get_domain_prompt()

            # transcribe 파라미터 구성
            transcribe_kwargs: dict[str, Any] = {
                "language": whisper_cfg.get("language", language),
                "beam_size": whisper_cfg.get("beam_size", beam_size),
                "initial_prompt": domain_prompt,
                "word_timestamps": True,  # 단어별 타임스탬프 (세그먼트 후처리 분할용)
            }
            # VAD 활성화
            if whisper_cfg.get("vad_filter", True):
                transcribe_kwargs["vad_filter"] = True
                vad_params = whisper_cfg.get("vad_parameters", {})
                if vad_params:
                    transcribe_kwargs["vad_parameters"] = vad_params
            if hp:
                for key in ("compression_ratio_threshold", "log_prob_threshold",
                            "no_speech_threshold", "condition_on_previous_text",
                            "repetition_penalty"):
                    if key in hp:
                        transcribe_kwargs[key] = hp[key]
                temps = hp.get("temperature")
                if isinstance(temps, list) and temps:
                    transcribe_kwargs["temperature"] = temps

            segments_iter, info = model.transcribe(preprocessed_path, **transcribe_kwargs)

            results: list[Segment] = []
            word_segments: list[list] = []  # 세그먼트별 word timestamps
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
                    # word-level timestamps 수집
                    seg_words = []
                    if seg.words:
                        for w in seg.words:
                            seg_words.append((w.word, w.start, w.end))
                    word_segments.append(seg_words)

            # word timestamps를 VAD 분할 후 실제 segments에 재매핑
            mapped_words = _map_words_to_segments(results, word_segments)
            # 긴 세그먼트 후처리 분할 (8초 초과 세그먼트를 문장 경계에서 분할)
            original_count = len(results)
            results = _split_long_segments(results, max_duration=8.0, word_segments=mapped_words)
            if len(results) != original_count:
                logger.info(
                    "Segment post-split: %d -> %d segment(s) for %s",
                    original_count, len(results), wav_path,
                )
            logger.info(
                "Transcribed %s: %d segment(s), lang=%s (prob=%.2f), model=%s, vad=%s, prompt_len=%d",
                wav_path,
                len(results),
                info.language,
                info.language_probability,
                whisper_cfg.get("model", "medium"),
                whisper_cfg.get("vad_filter", True),
                len(domain_prompt),
            )
            # 데이터 축적
            _accumulate_stt_data(wav_path, results, cfg)


            return results
        finally:
            if preprocessed:
                with contextlib.suppress(OSError):
                    os.unlink(preprocessed_path)


def transcribe_stereo(
    wav_path: str,
    language: str = "ko",
    in_out: int = 0,
    *,
    engine: str = "local",
    openai_prompt: str = "",
) -> TranscriptResult:
    """스테레오 WAV를 채널별 분리 후 STT.

    PBX 녹음은 통화 방향에 관계없이 채널 배치가 고정:
    - Left(ch0) = 고객(외부), Right(ch1) = 상담원(내선)
    *in_out* 은 로깅 목적으로만 사용.
    """
    l_wav = extract_channel_wav(wav_path, 0)
    r_wav = extract_channel_wav(wav_path, 1)
    try:
        l_segs = transcribe(l_wav, language, engine=engine, openai_prompt=openai_prompt)
        r_segs = transcribe(r_wav, language, engine=engine, openai_prompt=openai_prompt)
    finally:
        os.unlink(l_wav)
        os.unlink(r_wav)

    # PBX 채널 매핑: 수신=Left:고객/Right:상담원, 발신=반대
    if in_out == 2:  # 발신: PBX가 채널을 반대로 기록
        agent_segs = l_segs
        cust_segs = r_segs
        logger.info(
            "channel mapping: in_out=%d (outbound) \u2192 Left=Agent, Right=Customer",
            in_out,
        )
    else:  # 수신 또는 미분류
        agent_segs = r_segs
        cust_segs = l_segs
        logger.info(
            "channel mapping: in_out=%d (inbound) \u2192 Right=Agent, Left=Customer",
            in_out,
        )

    combined: list[SpeakerSegment] = []
    for s in agent_segs:
        combined.append(
            SpeakerSegment(time=s.start, end=s.end, speaker="agent", text=s.text)
        )
    for s in cust_segs:
        combined.append(
            SpeakerSegment(time=s.start, end=s.end, speaker="customer", text=s.text)
        )
    # 시간순 정렬, 동일 시작 시간이면 고객(수신측) 우선
    combined.sort(key=lambda x: (x.time, 0 if x.speaker == "customer" else 1))

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


def transcribe_mono(
    wav_path: str,
    language: str = "ko",
    *,
    engine: str = "local",
    openai_prompt: str = "",
) -> TranscriptResult:
    segs = transcribe(wav_path, language, engine=engine, openai_prompt=openai_prompt)

    combined = [
        SpeakerSegment(time=s.start, end=s.end, speaker="unknown", text=s.text)
        for s in segs
    ]
    full_text = " ".join(s.text for s in segs)
    duration = max(s.end for s in segs) if segs else 0.0
    word_count = len(full_text.split()) if full_text else 0

    # 모노: agent/customer 구분 불가 → 전체 세그먼트를 agent_segments에
    # 넣어 talk_ratio / silence_ratio 산출이 가능하도록 함.
    return TranscriptResult(
        segments=combined,
        agent_segments=list(segs),
        customer_segments=[],
        agent_text=full_text,
        customer_text="",
        full_text=full_text,
        duration_sec=round(duration, 3),
        word_count=word_count,
    )


def transcribe_file(
    wav_path: str,
    language: str = "ko",
    in_out: int = 0,
    *,
    engine: str = "local",
    openai_prompt: str = "",
) -> TranscriptResult:
    """WAV 파일을 채널 수에 따라 모노/스테레오 STT 수행.

    *in_out* 1=수신, 2=발신 — 스테레오 채널 매핑에 사용.
    *engine* 'local', 'openai-whisper', 'openai-gpt4o', 'openai-diarize', 'rtzr'.
    *openai_prompt* OpenAI 모델에 전달할 도메인 힌트 텍스트.
    """
    # diarize 엔진: 채널 분리 없이 전체 파일을 API에 전달 (화자 분리 + 타임스탬프 네이티브)
    if engine == "openai-diarize":
        return _transcribe_openai_diarize(wav_path, language)
    # rtzr 엔진: 채널 분리 없이 전체 파일을 RTZR API에 전달 (화자 분리 + 타임스탬프 네이티브)
    if engine == "rtzr":
        return _transcribe_rtzr(wav_path, language, in_out=in_out)
    # Try standard wave.open() first
    try:
        with wave.open(wav_path, "rb") as wf:
            n_channels = wf.getnchannels()
    except wave.Error:
        # Fall back to manual parsing for μ-law or other formats
        _, _, n_channels, _, _ = _read_wav_raw(wav_path)
    if n_channels >= 2:
        return transcribe_stereo(
            wav_path,
            language,
            in_out=in_out,
            engine=engine,
            openai_prompt=openai_prompt,
        )
    else:
        return transcribe_mono(
            wav_path,
            language,
            engine=engine,
            openai_prompt=openai_prompt,
        )


# ── Data accumulation & auto-learning ──

_accumulation_counter: int = 0


def _accumulate_stt_data(
    wav_path: str, segments: list[Segment], cfg: dict[str, Any],
) -> None:
    """데이터 축적 및 자동 학습 파이프라인.

    1. STT 결과에서 위치/도착지 키워드 추출
    2. 도메인 어휘 사전에 축적
    3. N건마다 도메인 프롬프트 자동 갱신
    """
    global _accumulation_counter
    learning_cfg = cfg.get("learning", {})
    if not learning_cfg.get("enabled", False):
        return

    try:
        full_text = " ".join(s.text for s in segments)
        if not full_text.strip():
            return

        storage_base = Path(__file__).resolve().parents[2] / "storage"

        # 위치/지명 추출
        loc_cfg = learning_cfg.get("location_extraction", {})
        if loc_cfg.get("enabled", False):
            _extract_and_save_locations(full_text, loc_cfg, storage_base, cfg)

        # 축적 카운터 증가 및 프롬프트 업데이트 체크
        _accumulation_counter += 1
        update_cfg = cfg.get("prompt_auto_update", {})
        interval = update_cfg.get("update_interval", 50)
        if update_cfg.get("enabled", False) and _accumulation_counter % interval == 0:
            _update_domain_prompt(storage_base, cfg)
            logger.info(
                "Domain prompt auto-updated after %d transcriptions",
                _accumulation_counter,
            )

    except Exception as e:
        logger.debug("Data accumulation error (non-fatal): %s", e)


def _extract_and_save_locations(
    text: str, loc_cfg: dict, storage_base: Path, cfg: dict[str, Any],
) -> None:
    """텍스트에서 위치/지명 키워드를 추출하여 vocabulary 파일에 축적."""
    patterns = loc_cfg.get("patterns", [])
    found_locations: list[str] = []

    for pattern in patterns:
        try:
            for sub_m in re.finditer(pattern, text):
                context = text[max(0, sub_m.start() - 10):sub_m.end() + 5]
                found_locations.append(context.strip())
        except re.error:
            continue

    # 일반적인 지명 패턴도 추출
    general_pattern = r'[\uac00-\ud7a3]{1,10}(?:역|동|리|로|길|대로|아파트|빌딩|타워|센터|병원|학교|대학|공원|마트|시장)'
    for m in re.finditer(general_pattern, text):
        word = m.group().strip()
        if len(word) >= 2:
            found_locations.append(word)

    if not found_locations:
        return

    # vocabulary 파일에 축적
    vocab_path_str = cfg.get("prompt_auto_update", {}).get(
        "vocabulary_file", "stt/domain_vocabulary.json"
    )
    vocab_path = storage_base / vocab_path_str
    vocab_path.parent.mkdir(parents=True, exist_ok=True)

    vocab: dict[str, int] = {}
    if vocab_path.exists():
        try:
            vocab = json.loads(vocab_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            vocab = {}

    for loc in found_locations:
        loc = loc.strip()
        if loc and len(loc) >= 2:
            vocab[loc] = vocab.get(loc, 0) + 1

    vocab_path.write_text(
        json.dumps(vocab, ensure_ascii=False, indent=2, sort_keys=False),
        encoding="utf-8",
    )


def _update_domain_prompt(storage_base: Path, cfg: dict[str, Any]) -> None:
    """축적된 vocabulary를 기반으로 도메인 프롬프트를 자동 갱신."""
    update_cfg = cfg.get("prompt_auto_update", {})
    vocab_file = update_cfg.get("vocabulary_file", "stt/domain_vocabulary.json")
    prompt_file = update_cfg.get("prompt_file", "stt/domain_prompt.txt")
    max_tokens = update_cfg.get("max_prompt_tokens", 200)

    vocab_path = storage_base / vocab_file
    prompt_path = storage_base / prompt_file
    prompt_path.parent.mkdir(parents=True, exist_ok=True)

    if not vocab_path.exists():
        return

    try:
        vocab: dict[str, int] = json.loads(vocab_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    sorted_words = sorted(vocab.items(), key=lambda x: -x[1])

    base_prompt = (
        "안녕하세요, 대리운전입니다. 어디로 모실까요? "
        "출발지와 도착지를 말씀해 주세요. "
    )

    learned_words: list[str] = []
    budget = max_tokens - len(base_prompt) // 2
    used = 0
    for word, _count in sorted_words:
        est_tokens = len(word)
        if used + est_tokens + 2 > budget:
            break
        learned_words.append(word)
        used += est_tokens + 2

    if learned_words:
        location_part = ", ".join(learned_words)
        full_prompt = f"{base_prompt}{location_part}, "
        full_prompt += (
            "대리 요금, 만 원, 이만 원, 삼만 원, 할인, 추가 요금, "
            "기사님, 손님, 고객님, 예약, 배차, 도착 예정"
        )
    else:
        full_prompt = _DEFAULT_STT_CONFIG["whisper"]["initial_prompt"]

    prompt_path.write_text(full_prompt, encoding="utf-8")
    logger.info(
        "Domain prompt updated: %d learned words, prompt_len=%d",
        len(learned_words), len(full_prompt),
    )
