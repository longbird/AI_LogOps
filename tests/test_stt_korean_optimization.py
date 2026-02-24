"""
Korean STT Optimization A/B Test (v2)

기존 Whisper medium vs large-v3-turbo + 최적화 파라미터 비교.
WAV 파일로 실제 STT 실행 후 결과 비교.

Tests:
  A) Baseline: medium model, no VAD, no preprocessing, no prompt
  B) Optimized: large-v3-turbo + VAD + hallucination prevention + domain prompt + preprocessing
  C) Optimized (no preprocessing): same as B but preprocessing disabled — to evaluate trade-off

Covers:
  - 10 WAV files across all date folders
  - Both mono (.1.) and stereo (.2.) files
  - Summary statistics at end
"""

import sys
import os
import time
import json
import logging
import wave
from pathlib import Path
from typing import Any

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
)
logger = logging.getLogger("stt_test")


def get_test_wavs(
    base_dir: str, max_per_date: int = 3, include_stereo: int = 2
) -> list[dict]:
    """Pick test WAV files from the recordings directory.

    Returns list of dicts with 'path', 'date', 'is_stereo' keys.
    Ensures coverage across dates and includes stereo files.
    """
    base = Path(base_dir)
    mono_files: list[dict] = []
    stereo_files: list[dict] = []

    for date_dir in sorted(base.iterdir()):
        if not date_dir.is_dir():
            continue
        date_name = date_dir.name
        mono_count = 0
        for wav in sorted(date_dir.glob("*.wav")):
            fname = wav.name
            # .2. in filename = stereo/outgoing channel
            is_stereo = ".2." in fname
            entry = {"path": str(wav), "date": date_name, "is_stereo": is_stereo}
            if is_stereo:
                if len(stereo_files) < include_stereo:
                    stereo_files.append(entry)
            else:
                if mono_count < max_per_date:
                    mono_files.append(entry)
                    mono_count += 1

    # Combine: take 2 mono per date + all collected stereo
    result = []
    dates_seen: dict[str, int] = {}
    for f in mono_files:
        d = f["date"]
        if dates_seen.get(d, 0) < 2:
            result.append(f)
            dates_seen[d] = dates_seen.get(d, 0) + 1
    result.extend(stereo_files)
    return result[:12]  # cap at 12


def get_wav_info(wav_path: str) -> dict:
    """Get basic WAV file info."""
    try:
        with wave.open(wav_path, "rb") as wf:
            return {
                "channels": wf.getnchannels(),
                "sample_rate": wf.getframerate(),
                "sample_width": wf.getsampwidth(),
                "duration": wf.getnframes() / wf.getframerate(),
                "format": 1,
            }
    except wave.Error:
        from airec.analyzer.stt import _read_wav_raw

        raw, sr, ch, sw, fmt = _read_wav_raw(wav_path)
        n_frames = len(raw) // (ch * sw) if fmt == 1 else len(raw) // ch
        return {
            "channels": ch,
            "sample_rate": sr,
            "sample_width": sw,
            "duration": n_frames / sr,
            "format": fmt,
        }


def _transcribe_with_model(model, wav_path: str, **kwargs) -> dict:
    """Run transcription and collect results."""
    import contextlib

    start = time.time()
    segments_iter, stt_info = model.transcribe(wav_path, **kwargs)
    segments = []
    for seg in segments_iter:
        text = seg.text.strip()
        if text:
            segments.append(
                {
                    "start": round(seg.start, 3),
                    "end": round(seg.end, 3),
                    "text": text,
                }
            )
    elapsed = time.time() - start
    full_text = " ".join(s["text"] for s in segments)
    return {
        "segments": len(segments),
        "text": full_text,
        "elapsed_sec": round(elapsed, 2),
        "lang_prob": round(stt_info.language_probability, 3),
        "segment_details": segments,
    }


def run_baseline(wav_path: str, info: dict) -> dict | None:
    """Run baseline: medium model, no VAD, no preprocessing, no prompt."""
    from airec.analyzer.stt import extract_channel_wav
    from faster_whisper import WhisperModel
    import contextlib

    model = WhisperModel("medium", device="cpu", compute_type="int8")

    mono_path = wav_path
    cleanup = False
    if info["channels"] >= 2:
        mono_path = extract_channel_wav(wav_path, 0)
        cleanup = True

    try:
        result = _transcribe_with_model(model, mono_path, language="ko", beam_size=5)
        result["model"] = "medium"
        result["config"] = "baseline (no VAD, no preprocess, no prompt)"
        return result
    except Exception as e:
        logger.error("Baseline failed for %s: %s", wav_path, e)
        return None
    finally:
        if cleanup:
            with contextlib.suppress(OSError):
                os.unlink(mono_path)


def run_optimized(
    wav_path: str, info: dict, *, use_preprocessing: bool = True
) -> dict | None:
    """Run optimized: large-v3-turbo + VAD + hallucination prevention + prompt.

    Args:
        use_preprocessing: If True, apply audio preprocessing pipeline.
    """
    from airec.analyzer.stt import (
        get_model,
        _preprocess_audio,
        _get_domain_prompt,
        _load_stt_config,
        extract_channel_wav,
    )
    import contextlib

    cfg = _load_stt_config()
    whisper_cfg = cfg.get("whisper", {})
    hp = whisper_cfg.get("hallucination_prevention", {})

    mono_path = wav_path
    cleanup_mono = False
    if info["channels"] >= 2:
        mono_path = extract_channel_wav(wav_path, 0)
        cleanup_mono = True

    # Preprocess (conditional)
    preprocessed_path = mono_path
    preprocessed = False
    if use_preprocessing:
        preprocessed_path = _preprocess_audio(mono_path, cfg.get("preprocessing"))
        preprocessed = preprocessed_path != mono_path

    try:
        model = get_model()
        domain_prompt = _get_domain_prompt()

        transcribe_kwargs: dict[str, Any] = {
            "language": whisper_cfg.get("language", "ko"),
            "beam_size": whisper_cfg.get("beam_size", 5),
            "initial_prompt": domain_prompt,
        }

        if whisper_cfg.get("vad_filter", True):
            transcribe_kwargs["vad_filter"] = True
            vad_params = whisper_cfg.get("vad_parameters", {})
            if vad_params:
                transcribe_kwargs["vad_parameters"] = vad_params

        if hp:
            for key in (
                "compression_ratio_threshold",
                "log_prob_threshold",
                "no_speech_threshold",
                "condition_on_previous_text",
            ):
                if key in hp:
                    transcribe_kwargs[key] = hp[key]
            temps = hp.get("temperature")
            if isinstance(temps, list) and temps:
                transcribe_kwargs["temperature"] = temps

        result = _transcribe_with_model(model, preprocessed_path, **transcribe_kwargs)
        result["model"] = whisper_cfg.get("model", "unknown")
        preproc_label = (
            "with preprocessing" if use_preprocessing else "NO preprocessing"
        )
        result["config"] = f"optimized ({preproc_label})"
        result["vad"] = whisper_cfg.get("vad_filter", True)
        result["preprocessing"] = use_preprocessing
        result["prompt_len"] = len(domain_prompt)
        return result
    except Exception as e:
        logger.error("Optimized failed for %s: %s", wav_path, e, exc_info=True)
        return None
    finally:
        if preprocessed:
            with contextlib.suppress(OSError):
                os.unlink(preprocessed_path)
        if cleanup_mono:
            with contextlib.suppress(OSError):
                os.unlink(mono_path)


def print_result(label: str, result: dict | None):
    """Pretty-print a single test result."""
    if result is None:
        print(f"    {label}: ERROR (see logs)")
        return
    print(f"    {label}:")
    print(
        f"      Model: {result['model']} | Segments: {result['segments']} | Time: {result['elapsed_sec']}s | Lang: {result['lang_prob']}"
    )
    text = result["text"]
    if len(text) > 300:
        text = text[:300] + "..."
    print(f"      Text ({len(result['text'])} chars): {text}")


def main():
    recordings_dir = str(
        Path(__file__).resolve().parents[1] / "storage" / "recordings" / "PC-DAERIGO"
    )
    test_files = get_test_wavs(recordings_dir)

    if not test_files:
        print("No WAV files found for testing!")
        return

    mono_count = sum(1 for f in test_files if not f["is_stereo"])
    stereo_count = sum(1 for f in test_files if f["is_stereo"])
    dates = sorted(set(f["date"] for f in test_files))

    print(f"\n{'=' * 90}")
    print(f"  Korean STT Optimization A/B Test (v2)")
    print(
        f"  Files: {len(test_files)} total ({mono_count} mono, {stereo_count} stereo)"
    )
    print(f"  Dates: {', '.join(dates)}")
    print(
        f"  Configs: A=medium(baseline), B=large-v3-turbo+optimized, C=optimized(no preprocess)"
    )
    print(f"{'=' * 90}")

    all_results: list[dict] = []

    for i, finfo in enumerate(test_files, 1):
        wav_path = finfo["path"]
        filename = os.path.basename(wav_path)
        info = get_wav_info(wav_path)
        stereo_label = "STEREO" if finfo["is_stereo"] else "MONO"
        fmt_label = "u-law" if info.get("format") == 7 else "PCM"

        print(f"\n{'─' * 90}")
        print(f"  [{i}/{len(test_files)}] {filename}")
        print(
            f"  Date: {finfo['date']} | {stereo_label} | {info['channels']}ch | {info['sample_rate']}Hz | {fmt_label} | {info.get('duration', 0):.1f}s"
        )
        print()

        # A) Baseline
        baseline = run_baseline(wav_path, info)
        print_result("A) Baseline", baseline)

        # B) Optimized with preprocessing
        optimized_pp = run_optimized(wav_path, info, use_preprocessing=True)
        print_result("B) Optimized+Preprocess", optimized_pp)

        # C) Optimized without preprocessing
        optimized_raw = run_optimized(wav_path, info, use_preprocessing=False)
        print_result("C) Optimized(raw)", optimized_raw)

        # Compare
        entry = {
            "file": filename,
            "date": finfo["date"],
            "stereo": finfo["is_stereo"],
            "duration": info.get("duration", 0),
            "format": info.get("format", 1),
        }
        for key, res in [
            ("baseline", baseline),
            ("optimized_pp", optimized_pp),
            ("optimized_raw", optimized_raw),
        ]:
            if res:
                entry[f"{key}_segments"] = res["segments"]
                entry[f"{key}_textlen"] = len(res["text"])
                entry[f"{key}_time"] = res["elapsed_sec"]
                entry[f"{key}_lang"] = res["lang_prob"]
            else:
                entry[f"{key}_segments"] = 0
                entry[f"{key}_textlen"] = 0
                entry[f"{key}_time"] = 0
                entry[f"{key}_lang"] = 0
        all_results.append(entry)

    # ── Summary ──
    print(f"\n{'=' * 90}")
    print(f"  SUMMARY")
    print(f"{'=' * 90}")
    n = len(all_results)

    def avg(key):
        vals = [r[key] for r in all_results if r[key] > 0]
        return sum(vals) / len(vals) if vals else 0

    print(f"\n  {'Metric':<30} {'Baseline':>12} {'Opt+Preproc':>12} {'Opt(raw)':>12}")
    print(f"  {'─' * 66}")
    print(
        f"  {'Avg segments':<30} {avg('baseline_segments'):>12.1f} {avg('optimized_pp_segments'):>12.1f} {avg('optimized_raw_segments'):>12.1f}"
    )
    print(
        f"  {'Avg text length (chars)':<30} {avg('baseline_textlen'):>12.1f} {avg('optimized_pp_textlen'):>12.1f} {avg('optimized_raw_textlen'):>12.1f}"
    )
    print(
        f"  {'Avg time (sec)':<30} {avg('baseline_time'):>12.1f} {avg('optimized_pp_time'):>12.1f} {avg('optimized_raw_time'):>12.1f}"
    )
    print(
        f"  {'Avg lang probability':<30} {avg('baseline_lang'):>12.3f} {avg('optimized_pp_lang'):>12.3f} {avg('optimized_raw_lang'):>12.3f}"
    )

    # Text length improvement
    improvements = []
    for r in all_results:
        if r["baseline_textlen"] > 0 and r["optimized_pp_textlen"] > 0:
            improvements.append(
                (r["optimized_pp_textlen"] - r["baseline_textlen"])
                / r["baseline_textlen"]
                * 100
            )
    if improvements:
        avg_improvement = sum(improvements) / len(improvements)
        print(
            f"\n  Avg text length change (optimized+preproc vs baseline): {avg_improvement:+.1f}%"
        )

    # Preprocessing impact
    pp_vs_raw = []
    for r in all_results:
        if r["optimized_pp_textlen"] > 0 and r["optimized_raw_textlen"] > 0:
            pp_vs_raw.append(r["optimized_pp_segments"] - r["optimized_raw_segments"])
    if pp_vs_raw:
        avg_seg_diff = sum(pp_vs_raw) / len(pp_vs_raw)
        print(f"  Avg segment count change (preproc vs raw): {avg_seg_diff:+.1f}")

    # Save results
    results_path = Path(__file__).resolve().parent / "stt_test_results.json"
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)
    print(f"\n  Results saved: {results_path}")
    print()


if __name__ == "__main__":
    main()
