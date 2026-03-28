"""RTP-First Safety Net 효과 분석 모듈.

AirREC v2026.3.28.1 이후 4-Phase RTP-First 변경의 효과를 측정:
  Phase 1: ZOMBIE timer fix (first_packet_time = time(NULL))
  Phase 2: RTP claim release (15s timeout) + orphan timeout extension
  Phase 3: BYE-resume detection (orphan recovery after BYE-grace)
  Phase 4: SMDR orphan match (QUEUE SKIP recovery)

기존 미녹취 태그(SILENT, NO-TYPE5, QUEUE SKIP)도 함께 집계하여
배포 전후 비교 기준선 제공.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


# ──────────────────────────────────────────────────────────────
# Regex patterns
# ──────────────────────────────────────────────────────────────
RE_TS = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+\]")

# Phase 1: ZOMBIE
RE_ZOMBIE = re.compile(
    r"\[SMDR-ZOMBIE\].*\[C:(\d+)\].*Timeout \((\d+)s\).*CID:(\S+)->(\S+).*Ext:(\S+)"
)
# Phase 2: RTP Claim Release
RE_CLAIM_RELEASE = re.compile(
    r"\[RTP-CLAIM-RELEASE\].*\[C:(\d+)\].*CID:(\S+)->(\S+).*Ext:(\S+)"
    r".*rtp:(\S+):(\d+).*SmdrLocked:(\d)"
)
# Phase 3: BYE-Resume
RE_BYE_REMEMBER = re.compile(r"\[BYE-REMEMBER\].*\[C:(\d+)\].*Ext:(\S+)")
RE_BYE_RESUME = re.compile(
    r"\[ORPHAN-RTP\].*\[C:(\d+)\].*BYE-resume detected.*caller:(\S+).*Ext:(\S+)"
)
# Phase 4: SMDR Orphan Match
RE_SMDR_ORPHAN = re.compile(
    r"\[SMDR-ORPHAN-MATCH\].*\[C:(\d+)\].*Key:(\S+).*Ext:(\S+).*Dur:(\d+)"
)

# Existing baseline tags
RE_SILENT = re.compile(r"\[SILENT-RECORDING\].*\[C:(\d+)\].*Size:(\d+).*Time:(\d+)")
RE_NO_TYPE5 = re.compile(r"\[NO-TYPE5\].*\[C:(\d+)\].*Ext:(\S+).*Duration:(\d+)")
RE_QUEUE_SKIP_NR = re.compile(
    r"QUEUE SKIP I Key:(\S+) \(IA/A was applied, no recording\)"
)
RE_ORPHAN_CREATED = re.compile(r"\[ORPHAN-RTP\].*\[C:(\d+)\].*Created.*Ext:(\S+)")
RE_ORPHAN_TIMEOUT = re.compile(
    r"\[ORPHAN-RTP\].*\[C:(\d+)\].*Timeout \((\d+)s\).*Ext:(\S+)"
)
RE_ORPHAN_MATCHED = re.compile(r"\[ORPHAN-MATCH\].*Key:(\S+).*\[C:(\d+)\].*Ext:(\S+)")
RE_PENDING_APPLIED = re.compile(
    r"\[SMDR\].*\[C:(\d+)\].*PENDING APPLIED.*Key:(\S+).*Ext:(\S+).*\(waited:(\d+)sec\)"
)

# Call volume
RE_IN_END = re.compile(r"\bI:IN_END\b.*Duration:(\d+)")
RE_OUT_END = re.compile(r"\bO:OUT_END\b.*Duration:(\d+)")

# Version
RE_VERSION = re.compile(r"AirRecorder\s+v([\d.]+)")


# ──────────────────────────────────────────────────────────────
# Data model
# ──────────────────────────────────────────────────────────────
@dataclass
class RtpFirstStats:
    """RTP-First Safety Net 분석 결과."""

    site: str
    date: str
    version: str = ""
    total_lines: int = 0

    # Call volume
    inbound: int = 0
    outbound: int = 0
    hourly_calls: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    # Phase 1: ZOMBIE
    zombie_releases: list[tuple] = field(default_factory=list)
    # Phase 2: RTP Claim Release
    claim_releases: list[tuple] = field(default_factory=list)
    # Phase 3: BYE-Resume
    bye_remembers: list[tuple] = field(default_factory=list)
    bye_resumes: list[tuple] = field(default_factory=list)
    # Phase 4: SMDR Orphan Match
    smdr_orphan_matches: list[tuple] = field(default_factory=list)

    # Baseline tags
    silent_recordings: list[tuple] = field(default_factory=list)
    no_type5: list[tuple] = field(default_factory=list)
    queue_skip_nr: list[tuple] = field(default_factory=list)
    orphan_created: list[tuple] = field(default_factory=list)
    orphan_timeout: list[tuple] = field(default_factory=list)
    orphan_matched: list[tuple] = field(default_factory=list)
    pending_applied: list[tuple] = field(default_factory=list)

    @property
    def total_calls(self) -> int:
        return self.inbound + self.outbound

    @property
    def missed_recording_count(self) -> int:
        return len(self.silent_recordings) + len(self.no_type5) + len(self.queue_skip_nr)

    @property
    def recovered_count(self) -> int:
        return len(self.smdr_orphan_matches) + len(self.bye_resumes)

    @property
    def has_rtp_first_events(self) -> bool:
        """RTP-First 관련 이벤트가 하나라도 있으면 True."""
        return bool(
            self.zombie_releases
            or self.claim_releases
            or self.bye_remembers
            or self.bye_resumes
            or self.smdr_orphan_matches
        )


# ──────────────────────────────────────────────────────────────
# Parser
# ──────────────────────────────────────────────────────────────
def analyze_rtp_first(
    site: str,
    date: str,
    files: list[Path],
) -> RtpFirstStats:
    """로그 파일 목록을 파싱하여 RTP-First 통계를 반환."""
    stats = RtpFirstStats(site=site, date=date)

    for fpath in files:
        with open(fpath, encoding="cp949", errors="replace") as f:
            for line in f:
                stats.total_lines += 1
                line = line.rstrip("\n\r")
                if not line:
                    continue

                m_ts = RE_TS.match(line)
                ts = m_ts.group(1) if m_ts else ""
                hour = ts[11:13] if ts else ""

                # Version
                m = RE_VERSION.search(line)
                if m:
                    stats.version = m.group(1)
                    continue

                # Call volume
                m = RE_IN_END.search(line)
                if m:
                    stats.inbound += 1
                    if hour:
                        stats.hourly_calls[hour] += 1
                    continue
                m = RE_OUT_END.search(line)
                if m:
                    stats.outbound += 1
                    if hour:
                        stats.hourly_calls[hour] += 1
                    continue

                # Phase 1: ZOMBIE
                m = RE_ZOMBIE.search(line)
                if m:
                    ch, elapsed, caller, callee, ext = m.groups()
                    stats.zombie_releases.append((ts, ch, int(elapsed), caller, callee, ext))
                    continue

                # Phase 2: RTP Claim Release
                m = RE_CLAIM_RELEASE.search(line)
                if m:
                    ch, caller, callee, ext, rtp_addr, rtp_port, locked = m.groups()
                    stats.claim_releases.append(
                        (ts, ch, caller, callee, ext, rtp_addr, rtp_port, locked)
                    )
                    continue

                # Phase 3: BYE-Resume
                m = RE_BYE_REMEMBER.search(line)
                if m:
                    ch, ext = m.groups()
                    stats.bye_remembers.append((ts, ch, ext))
                    continue
                m = RE_BYE_RESUME.search(line)
                if m:
                    ch, caller, ext = m.groups()
                    stats.bye_resumes.append((ts, ch, caller, ext))
                    continue

                # Phase 4: SMDR Orphan Match
                m = RE_SMDR_ORPHAN.search(line)
                if m:
                    ch, key, ext, dur = m.groups()
                    stats.smdr_orphan_matches.append((ts, ch, key, ext, int(dur)))
                    continue

                # Baseline tags
                m = RE_SILENT.search(line)
                if m:
                    ch, size, time_ = m.groups()
                    stats.silent_recordings.append((ts, ch, int(size), int(time_)))
                    continue
                m = RE_NO_TYPE5.search(line)
                if m:
                    ch, ext, dur = m.groups()
                    stats.no_type5.append((ts, ch, ext, int(dur)))
                    continue
                m = RE_QUEUE_SKIP_NR.search(line)
                if m:
                    stats.queue_skip_nr.append((ts, m.group(1)))
                    continue
                m = RE_ORPHAN_CREATED.search(line)
                if m:
                    ch, ext = m.groups()
                    stats.orphan_created.append((ts, ch, ext))
                    continue
                m = RE_ORPHAN_TIMEOUT.search(line)
                if m:
                    ch, elapsed, ext = m.groups()
                    stats.orphan_timeout.append((ts, ch, int(elapsed), ext))
                    continue
                m = RE_ORPHAN_MATCHED.search(line)
                if m:
                    key, ch, ext = m.groups()
                    stats.orphan_matched.append((ts, key, ch, ext))
                    continue
                m = RE_PENDING_APPLIED.search(line)
                if m:
                    ch, key, ext, waited = m.groups()
                    stats.pending_applied.append((ts, ch, key, ext, int(waited)))
                    continue

    return stats


# ──────────────────────────────────────────────────────────────
# Report generation
# ──────────────────────────────────────────────────────────────
def generate_rtp_first_report(stats: RtpFirstStats) -> str:
    """RTP-First 분석 결과를 마크다운 보고서로 생성."""
    lines: list[str] = []

    lines.append(f"## RTP-First Safety Net 분석")
    lines.append(f"")
    lines.append(f"AirRecorder: v{stats.version or 'unknown'}")
    lines.append(f"")

    # ── Baseline: 미녹취 태그 집계 ──
    lines.append("### 미녹취 관련 태그 집계\n")
    lines.append("| 태그 | 건수 | 설명 |")
    lines.append("|------|------|------|")
    lines.append(
        f"| SILENT-RECORDING | {len(stats.silent_recordings)} |"
        f" RTP:0,0 무음 녹취 |"
    )
    lines.append(
        f"| NO-TYPE5 | {len(stats.no_type5)} |"
        f" SSPP Type 5 미수신 |"
    )
    lines.append(
        f"| QUEUE SKIP (no rec) | {len(stats.queue_skip_nr)} |"
        f" PENDING 소진 후 녹취 없음 |"
    )
    lines.append(
        f"| PENDING APPLIED | {len(stats.pending_applied)} |"
        f" 녹취 채널 할당 |"
    )
    lines.append(
        f"| ORPHAN-RTP Created | {len(stats.orphan_created)} |"
        f" 고아 RTP 세션 생성 |"
    )
    lines.append(
        f"| ORPHAN-MATCH | {len(stats.orphan_matched)} |"
        f" 고아 RTP 매칭 성공 |"
    )
    lines.append(
        f"| ORPHAN-RTP Timeout | {len(stats.orphan_timeout)} |"
        f" 고아 RTP 타임아웃 |"
    )
    lines.append("")

    # ── Phase별 분석 (RTP-First 이벤트가 있을 때만) ──
    if stats.has_rtp_first_events:
        lines.append("### RTP-First Phase별 효과\n")

        # Phase 1
        lines.append(f"**Phase 1: ZOMBIE Timer Fix** — {len(stats.zombie_releases)}건")
        if stats.zombie_releases:
            lines.append("")
            lines.append("| 시각 | 채널 | 경과 | CID | 내선 |")
            lines.append("|------|------|------|-----|------|")
            for ts, ch, elapsed, caller, callee, ext in stats.zombie_releases[:20]:
                lines.append(f"| {ts[11:]} | C:{ch} | {elapsed}s | {caller}->{callee} | {ext} |")
            if len(stats.zombie_releases) > 20:
                lines.append(f"| ... | | | {len(stats.zombie_releases) - 20}건 추가 | |")
        lines.append("")

        # Phase 2
        lines.append(f"**Phase 2: RTP Claim Release** — {len(stats.claim_releases)}건")
        if stats.claim_releases:
            locked_cnt = sum(1 for x in stats.claim_releases if x[7] == "1")
            unlocked_cnt = len(stats.claim_releases) - locked_cnt
            lines.append(f"  SmdrLocked=1: {locked_cnt}건 / SmdrLocked=0: {unlocked_cnt}건")
            lines.append("")
            lines.append("| 시각 | 채널 | CID | 내선 | RTP | Locked |")
            lines.append("|------|------|-----|------|-----|--------|")
            for ts, ch, caller, callee, ext, addr, port, locked in stats.claim_releases[:20]:
                lines.append(
                    f"| {ts[11:]} | C:{ch} | {caller}->{callee} | {ext} |"
                    f" {addr}:{port} | {locked} |"
                )
            if len(stats.claim_releases) > 20:
                lines.append(f"| ... | | | {len(stats.claim_releases) - 20}건 추가 | | |")
        lines.append("")

        # Phase 3
        lines.append(
            f"**Phase 3: BYE-Resume Detection** —"
            f" BYE-REMEMBER {len(stats.bye_remembers)}건,"
            f" BYE-RESUME {len(stats.bye_resumes)}건"
        )
        if stats.bye_resumes:
            lines.append("")
            lines.append("| 시각 | 채널 | Caller | 내선 |")
            lines.append("|------|------|--------|------|")
            for ts, ch, caller, ext in stats.bye_resumes[:20]:
                lines.append(f"| {ts[11:]} | C:{ch} | {caller} | {ext} |")
        lines.append("")

        # Phase 4
        lines.append(f"**Phase 4: SMDR Orphan Match** — {len(stats.smdr_orphan_matches)}건")
        if stats.smdr_orphan_matches:
            lines.append("")
            lines.append("| 시각 | 채널 | Key | 내선 | Duration |")
            lines.append("|------|------|-----|------|----------|")
            for ts, ch, key, ext, dur in stats.smdr_orphan_matches[:20]:
                lines.append(f"| {ts[11:]} | C:{ch} | {key} | {ext} | {dur}s |")
        lines.append("")

    # ── Summary ──
    missed = stats.missed_recording_count
    recovered = stats.recovered_count
    net_missed = max(0, missed - recovered)
    total = stats.total_calls

    lines.append("### 요약\n")
    lines.append("| 지표 | 건수 | 비율 |")
    lines.append("|------|------|------|")
    lines.append(f"| 총 통화 | {total:,} | - |")
    pct = f"{missed / total * 100:.2f}%" if total > 0 else "-"
    lines.append(f"| 미녹취 태그 (raw) | {missed:,} | {pct} |")
    lines.append(f"| Phase 2-4 복구 | {recovered:,} | - |")
    net_pct = f"{net_missed / total * 100:.2f}%" if total > 0 else "-"
    lines.append(f"| 미녹취 (net) | {net_missed:,} | {net_pct} |")
    lines.append("")

    if stats.has_rtp_first_events:
        active = []
        if stats.zombie_releases:
            active.append("P1:ZOMBIE")
        if stats.claim_releases:
            active.append("P2:CLAIM-RELEASE")
        if stats.bye_resumes:
            active.append("P3:BYE-RESUME")
        if stats.smdr_orphan_matches:
            active.append("P4:SMDR-ORPHAN")
        lines.append(f"활성 Phase: {', '.join(active)}")
    else:
        lines.append("활성 Phase: 없음 (RTP-First 미배포 또는 해당 이벤트 없음)")
    lines.append("")

    # ── SILENT detail ──
    if stats.silent_recordings:
        lines.append("### SILENT-RECORDING 상세\n")
        lines.append("| 시각 | 채널 | Size | Time |")
        lines.append("|------|------|------|------|")
        for ts, ch, size, time_ in stats.silent_recordings:
            lines.append(f"| {ts[11:]} | C:{ch} | {size:,} | {time_}s |")
        lines.append("")

    # ── NO-TYPE5 detail ──
    if stats.no_type5:
        lines.append("### NO-TYPE5 상세\n")
        lines.append("| 시각 | 채널 | 내선 | Duration |")
        lines.append("|------|------|------|----------|")
        for ts, ch, ext, dur in stats.no_type5:
            lines.append(f"| {ts[11:]} | C:{ch} | {ext} | {dur}s |")
        lines.append("")

    return "\n".join(lines)
