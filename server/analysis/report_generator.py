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

from server.analysis.log_analyzer import AnalysisResult, DbFailCause, VersionSegment


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
        f"| 정규화 실패 | {result.norm_failure_count} |",
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

    return "\n".join(sections)
