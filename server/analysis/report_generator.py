"""
server/analysis/report_generator.py
=====================================
AirREC 로그 분석 결과를 마크다운 보고서로 변환한다.

Usage::

    from server.analysis.log_analyzer import LogAnalyzer
    from server.analysis.report_generator import generate_report

    result = LogAnalyzer("PC-DAERIGO", "20260319").analyze_files(paths)
    md = generate_report(result)
"""

from __future__ import annotations

from datetime import datetime

from server.analysis.log_analyzer import AnalysisResult, DbFailCause, DurationMismatch, NormFailureDetail, UnrecordedCall, VersionSegment


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CAUSE_LABELS: dict[str, tuple[str, str]] = {
    "restart_gap": ("재시작 gap", "InitInstance 후 SSPP 재구축 중"),
    "did_3301": ("DID 3301", "가상 내선 매칭 실패"),
    "norm_failure": ("정규화 실패", "번호 정규화 후보 없음"),
    "direction_mismatch": ("방향 오매칭", "OUTBOUND-RESET 기반"),
    "sspp_loss": ("SSPP 유실", "Idx:-1 상태"),
    "unknown": ("기타", "미분류"),
}

# Ordered for table display
_CAUSE_ORDER = [
    "restart_gap",
    "did_3301",
    "norm_failure",
    "direction_mismatch",
    "sspp_loss",
    "unknown",
]

_DIR_FILTER_LABELS = {
    0: "없음",
    1: "수신",
    2: "발신",
}

_UNRECORDED_REASON_LABELS = {
    "no_file_close": "FILE CLOSE 없음",
    "db_fail": "DB 실패",
    "did_passthrough": "DID 패스스루",
    "ivr_system": "IVR/시스템",
    "restart_gap": "재시작 gap",
    "silent_recording": "무음 녹취",
    "partial_recording": "부분 녹취",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fmt_date(yyyymmdd: str) -> str:
    """Convert YYYYMMDD to YYYY-MM-DD."""
    if len(yyyymmdd) == 8 and yyyymmdd.isdigit():
        return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"
    return yyyymmdd


def _pct(count: int, total: int) -> str:
    """Return percentage string, e.g. '42.1%'. Returns '0.0%' when total is 0."""
    if total == 0:
        return "0.0%"
    return f"{count / total * 100:.1f}%"


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _section_header(result: AnalysisResult) -> str:
    start, end = result.analysis_period
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return (
        "# AirREC 로그 분석 보고서\n\n"
        f"- **에이전트**: {result.agent_id}\n"
        f"- **분석 날짜**: {_fmt_date(result.date)}\n"
        f"- **분석 기간**: {start} ~ {end}\n"
        f"- **총 라인 수**: {result.total_lines:,}\n"
        f"- **생성 시각**: {now}\n"
    )


def _section_version_timeline(segments: list[VersionSegment]) -> str:
    lines = [
        "## 1. 버전 타임라인\n",
        "| 구간 | 버전 | 빌드 |",
        "|------|------|------|",
    ]
    for seg in segments:
        lines.append(
            f"| {seg.start_time} ~ {seg.end_time} | {seg.version} | {seg.build_info} |"
        )
    return "\n".join(lines) + "\n"


def _section_key_metrics(result: AnalysisResult) -> str:
    lines = [
        "## 2. 핵심 지표\n",
        "| 지표 | 건수 |",
        "|------|------|",
        f"| 수신 (I:IN_END) | {result.inbound_count:,} |",
        f"| 발신 (O:OUT_END) | {result.outbound_count:,} |",
        f"| 발신 8501~ (트렁크) | {result.trunk_outbound_count:,} |",
        f"| DB_UPDATE FAIL | {result.db_fail_count} |",
        f"| OUTBOUND-RESET | {result.outbound_reset_count} |",
        f"| DURATION-MISMATCH | {result.duration_mismatch_count} |",
        f"| Session invalidated | {result.session_invalidated_count} |",
        f"| SSPP 유실 (Idx:-1) | {result.sspp_loss_count} |",
        f"| 정규화 실패 | {result.norm_failure_count} (실제 {sum(1 for d in result.norm_failure_details if not d.recovered)}, 회복 {sum(1 for d in result.norm_failure_details if d.recovered)}) |",
        f"| 크래시 (재시작) | {result.crash_count} |",
    ]
    return "\n".join(lines) + "\n"


def _section_db_fail_causes(causes: list[DbFailCause], total: int) -> str:
    # Count by cause code
    counts: dict[str, int] = {k: 0 for k in _CAUSE_ORDER}
    for c in causes:
        key = c.cause if c.cause in counts else "unknown"
        counts[key] += 1

    lines = [
        f"## 3. DB_UPDATE FAIL 원인 분류 ({total}건)\n",
        "| 원인 | 건수 | 비율 | 상세 |",
        "|------|------|------|------|",
    ]
    for key in _CAUSE_ORDER:
        n = counts[key]
        if n == 0:
            continue
        label, detail = _CAUSE_LABELS[key]
        lines.append(f"| {label} | {n} | {_pct(n, total)} | {detail} |")

    return "\n".join(lines) + "\n"


def _section_dir_filter(dist: dict[int, int]) -> str | None:
    if not any(v for v in dist.values()):
        return None

    lines = ["## 4. 방향 필터 분포\n", "```"]
    for key in sorted(dist.keys()):
        count = dist[key]
        label = _DIR_FILTER_LABELS.get(key, str(key))
        lines.append(f"DirFilter:{key} ({label}): {count}건")
    lines.append("```")
    return "\n".join(lines) + "\n"


def _section_hourly(hourly_dist: dict[str, dict[int, int]]) -> str:
    inbound = hourly_dist.get("inbound", {})
    outbound = hourly_dist.get("outbound", {})
    db_fail = hourly_dist.get("db_fail", {})

    # Only include hours that have at least one event across all categories
    active_hours = sorted(
        h for h in range(24)
        if inbound.get(h, 0) > 0 or outbound.get(h, 0) > 0 or db_fail.get(h, 0) > 0
    )

    lines = [
        "## 5. 시간대별 분포\n",
        "| 시간 | 수신 | 발신 | DB실패 |",
        "|------|------|------|--------|",
    ]
    for h in active_hours:
        lines.append(
            f"| {h:02d}시 | {inbound.get(h, 0)} | {outbound.get(h, 0)} | {db_fail.get(h, 0)} |"
        )

    return "\n".join(lines) + "\n"


def _section_version_comparison(segments: list[VersionSegment]) -> str | None:
    if len(segments) < 2:
        return None

    lines = [
        "## 6. 버전별 비교\n",
        "| 구간 | 버전 | 빌드 | 시작 | 종료 |",
        "|------|------|------|------|------|",
    ]
    for i, seg in enumerate(segments, start=1):
        lines.append(
            f"| #{i} | {seg.version} | {seg.build_info} | {seg.start_time} | {seg.end_time} |"
        )

    return "\n".join(lines) + "\n"


def _section_smdr_matching(result: AnalysisResult) -> str:
    """SMDR vs 녹취 매칭 요약."""
    smdr_total = result.smdr_call_count
    file_total = result.file_close_count
    unrecorded_count = len(result.unrecorded_calls)
    mismatch_count = len(result.duration_mismatches)

    # Split mismatches by direction
    smdr_longer = [m for m in result.duration_mismatches if m.diff > 0]
    rec_longer = [m for m in result.duration_mismatches if m.diff < 0]

    if smdr_total == 0:
        rate = "N/A"
    else:
        rate = f"{file_total / smdr_total * 100:.1f}%"

    lines = [
        "## 7. SMDR vs 녹취 매칭\n",
        "| 지표 | 건수 |",
        "|------|------|",
        f"| SMDR 통화 (Duration>0) | {smdr_total:,} |",
        f"| 녹취 완료 (FILE CLOSE) | {file_total:,} |",
        f"| 녹취율 | {rate} |",
        f"| 미녹취 (실제) | {sum(1 for u in result.unrecorded_calls if u.reason not in ('did_passthrough', 'partial_recording', 'ivr_system')):,} |",
        f"| 미녹취 (DID패스스루) | {sum(1 for u in result.unrecorded_calls if u.reason == 'did_passthrough'):,} |",
        f"| 미녹취 (IVR/시스템) | {sum(1 for u in result.unrecorded_calls if u.reason == 'ivr_system'):,} |",
        f"| 부분 녹취 | {sum(1 for u in result.unrecorded_calls if u.reason == 'partial_recording'):,} |",
        f"| 시간 불일치 (녹취 부족) | {len(smdr_longer):,} |",
        f"| 시간 불일치 (녹취 초과) | {len(rec_longer):,} |",
    ]
    return "\n".join(lines) + "\n"


def _section_duration_mismatches(mismatches: list[DurationMismatch]) -> str | None:
    """녹취시간 불일치 목록 — 방향별 분리."""
    if not mismatches:
        return None

    smdr_longer = [m for m in mismatches if m.diff > 0]
    rec_longer = [m for m in mismatches if m.diff < 0]

    sections: list[str] = []

    if smdr_longer:
        lines = [
            f"## 8a. 녹취 부족 — SMDR > 녹취 ({len(smdr_longer)}건)\n",
            "| 시각 | 내선 | 발신 | 착신 | SMDR | 녹취 | 차이 | 원인 |",
            "|------|------|------|------|------|------|------|------|",
        ]
        for m in smdr_longer:
            ts_short = m.timestamp[11:16] if len(m.timestamp) >= 16 else m.timestamp
            lines.append(
                f"| {ts_short} | {m.ext} | {m.caller} | {m.called} "
                f"| {m.smdr_duration}s | {m.rec_duration}s | {m.diff:+d}s | {m.cause} |"
            )
        sections.append("\n".join(lines) + "\n")

    if rec_longer:
        lines = [
            f"## 8b. 녹취 초과 — SMDR < 녹취 ({len(rec_longer)}건)\n",
            "| 시각 | 내선 | 발신 | 착신 | SMDR | 녹취 | 차이 | 원인 |",
            "|------|------|------|------|------|------|------|------|",
        ]
        for m in rec_longer:
            ts_short = m.timestamp[11:16] if len(m.timestamp) >= 16 else m.timestamp
            lines.append(
                f"| {ts_short} | {m.ext} | {m.caller} | {m.called} "
                f"| {m.smdr_duration}s | {m.rec_duration}s | {m.diff:+d}s | {m.cause} |"
            )
        sections.append("\n".join(lines) + "\n")

    return ("\n---\n\n".join(sections)) if sections else None


def _section_unrecorded_calls(calls: list[UnrecordedCall]) -> str | None:
    """미녹취건 목록 (있을 경우만). DID 패스스루는 요약만 표시."""
    if not calls:
        return None

    did_calls = [c for c in calls if c.reason == "did_passthrough"]
    ivr_calls = [c for c in calls if c.reason == "ivr_system"]
    real_calls = [c for c in calls if c.reason not in ("did_passthrough", "ivr_system")]

    lines = [
        f"## 9. 미녹취건 (실제 {len(real_calls)}건, DID패스스루 {len(did_calls)}건, IVR/시스템 {len(ivr_calls)}건)\n",
    ]

    if real_calls:
        lines.extend([
            "| # | 시각 | 내선 | 발신 | 착신 | 통화시간 | 원인 |",
            "|---|------|------|------|------|----------|------|",
        ])
        for c in real_calls:
            ts_short = c.timestamp[11:16] if len(c.timestamp) >= 16 else c.timestamp
            reason_label = _UNRECORDED_REASON_LABELS.get(c.reason, c.reason)
            lines.append(
                f"| {c.seq} | {ts_short} | {c.ext} | {c.caller} | {c.called} "
                f"| {c.duration}s | {reason_label} |"
            )

    if did_calls:
        lines.append(f"\n*DID 패스스루 {len(did_calls)}건은 가상내선 경유 통화로 별도 녹취 대상 아님 (생략)*")

    return "\n".join(lines) + "\n"


def _section_norm_failures(details: list[NormFailureDetail]) -> str | None:
    """정규화 실패 상세 (있을 경우만)."""
    if not details:
        return None

    actual = [d for d in details if not d.recovered]
    recovered = [d for d in details if d.recovered]

    lines = [
        f"## 10. 정규화 실패 ({len(details)}건: 실제 {len(actual)}, ENDED회복 {len(recovered)})\n",
    ]

    if actual:
        lines.append(f"### 실제 미녹취 ({len(actual)}건)\n")
        lines.extend([
            "| 시각 | 원본 | 정규화 | 후보수 |",
            "|------|------|--------|--------|",
        ])
        for d in actual:
            ts_short = d.timestamp[11:16] if len(d.timestamp) >= 16 else d.timestamp
            lines.append(f"| {ts_short} | {d.original} | {d.normalized} | {d.candidates} |")
        lines.append("")

    if recovered:
        lines.append(f"### ENDED 매칭 회복 ({len(recovered)}건)\n")
        lines.extend([
            "| 시각 | 원본 | 정규화 | 후보수 |",
            "|------|------|--------|--------|",
        ])
        for d in recovered:
            ts_short = d.timestamp[11:16] if len(d.timestamp) >= 16 else d.timestamp
            lines.append(f"| {ts_short} | {d.original} | {d.normalized} | {d.candidates} |")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_report(result: AnalysisResult) -> str:
    """Generate a markdown report from an AnalysisResult.

    Returns:
        Markdown-formatted string ready for display or file writing.
    """
    sections: list[str] = []

    sections.append(_section_header(result))
    sections.append("---\n")

    sections.append(_section_version_timeline(result.version_segments))
    sections.append("---\n")

    sections.append(_section_key_metrics(result))
    sections.append("---\n")

    sections.append(_section_db_fail_causes(result.db_fail_causes, result.db_fail_count))
    sections.append("---\n")

    dir_filter_section = _section_dir_filter(result.dir_filter_dist)
    if dir_filter_section is not None:
        sections.append(dir_filter_section)
        sections.append("---\n")

    sections.append(_section_hourly(result.hourly_dist))
    sections.append("---\n")

    version_cmp = _section_version_comparison(result.version_segments)
    if version_cmp is not None:
        sections.append(version_cmp)

    sections.append("---\n")
    sections.append(_section_smdr_matching(result))

    dm_section = _section_duration_mismatches(result.duration_mismatches)
    if dm_section is not None:
        sections.append("---\n")
        sections.append(dm_section)

    unrec_section = _section_unrecorded_calls(result.unrecorded_calls)
    if unrec_section is not None:
        sections.append("---\n")
        sections.append(unrec_section)

    norm_section = _section_norm_failures(result.norm_failure_details)
    if norm_section is not None:
        sections.append("---\n")
        sections.append(norm_section)

    return "\n".join(sections)
