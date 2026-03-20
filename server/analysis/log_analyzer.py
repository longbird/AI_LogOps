"""
server/analysis/log_analyzer.py
================================
AirREC 로그 파일 배치 분석 엔진.

에이전트에서 다운로드한 로그 파일들을 일괄 분석하여
통계(통화 건수, DB 실패, 버전 세그먼트 등)를 산출한다.

monitor_state.py(실시간 분석)와 달리, 이 모듈은 저장된 파일 대상의 배치 분석용이다.
monitor_state.py와 의존 관계 없이 독립적으로 동작한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Regex patterns (AirREC log format — batch analysis)
# ---------------------------------------------------------------------------

# Timestamp: [2026-03-19 14:32:05.123456]
RE_TIMESTAMP = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+\]")

# Version/restart: "AirRecorder v2026.3.20.1" or "InitInstance"
RE_VERSION = re.compile(r"AirRecorder\s+v([\d.]+)")
RE_INIT = re.compile(r"InitInstance")
RE_BUILD = re.compile(r"Build:\s*(.+?)(?:\s*\)|\s*$)")

# SMDR events
RE_INBOUND = re.compile(r"\bI:IN_END\b")
RE_OUTBOUND = re.compile(r"\bO:OUT_END\b")
RE_TRUNK_OUTBOUND = re.compile(r"O:OUT_END.*Dnis:850[1-9]")

# DB failures
RE_DB_FAIL = re.compile(r"DB_UPDATE FAIL")
RE_DB_FAIL_3301 = re.compile(r"DB_UPDATE FAIL.*3301")

# Direction filter: DirFilter:N
RE_DIR_FILTER = re.compile(r"DirFilter:(\d)")

# OUTBOUND-RESET
RE_OUTBOUND_RESET = re.compile(r"OUTBOUND-RESET")

# Duration mismatch
RE_DURATION_MISMATCH = re.compile(r"\[DURATION-MISMATCH\]")

# Session invalidation
RE_SESSION_INVALIDATED = re.compile(r"Session invalidated|Aborted.*session")

# SSPP loss (Idx: -1)
RE_SSPP_LOSS = re.compile(r"Idx:\s*-1")

# Normalization failure
RE_NORM_FAILURE = re.compile(r"no match.*candidates")

# SMDR event with details (flag:action Ext: ... Duration:)
RE_SMDR_EVENT = re.compile(
    r"\[SMDR\]\s+\[\d+\]\s+(\w+):(\w+)\s+Ext:(\S+).*?Duration:(\d+)"
)

# FILE CLOSE (recording completion) — Channel, CID, Filename, Size, Time
RE_FILE_CLOSE = re.compile(
    r"\[FILE\]\s+\[C:(\d+)\]\s+CLOSE\s+(\S+)\s+(\S+\.wav)\s+Size:(\d+)\s+Time:(\d+)"
)

# DURATION-MISMATCH detail capture
RE_DURATION_MISMATCH_DETAIL = re.compile(
    r"\[DURATION-MISMATCH\]"
    r".*?SMDR:(\d+)s"
    r".*?Rec:(\d+)s"
    r".*?Diff:([+-]?\d+)s"
    r".*?Ext:(\S+)"
    r".*?Cause:(\S+)"
)

# Extension / number extraction helpers
RE_EXT = re.compile(r"Ext:(\S+)")
RE_CALLER = re.compile(r"Caller:(\S+)")
RE_CALLED = re.compile(r"Called:(\S+)")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class VersionSegment:
    """AirREC 프로세스의 버전 세그먼트 (InitInstance 기준 분할)."""

    version: str
    build_info: str
    start_time: str
    end_time: str
    line_start: int
    line_end: int


@dataclass
class DbFailCause:
    """DB_UPDATE FAIL 분류 결과."""

    timestamp: str
    cause: str  # restart_gap | norm_failure | direction_mismatch | did_3301 | sspp_loss | unknown
    detail: str


@dataclass
class SmdrCall:
    """SMDR 통화 완료 이벤트 (I:IN_END / O:OUT_END)."""
    timestamp: str
    flag: str        # I, O
    action: str      # IN_END, OUT_END
    ext: str
    caller: str
    called: str
    duration: int    # seconds
    seq: int         # SMDR 순번 (1-based)


@dataclass
class RecordingClose:
    """녹취 파일 완료 이벤트 (FILE CLOSE)."""
    timestamp: str
    channel: str
    cid: str
    filename: str
    size: int
    duration: int    # seconds


@dataclass
class DurationMismatch:
    """SMDR-녹취 시간 불일치."""
    timestamp: str
    ext: str
    caller: str
    called: str
    smdr_duration: int
    rec_duration: int
    diff: int
    cause: str


@dataclass
class UnrecordedCall:
    """SMDR에 있지만 녹취되지 않은 통화."""
    timestamp: str
    ext: str
    caller: str
    called: str
    duration: int
    seq: int         # SMDR 순번
    reason: str      # no_file_close | db_fail


@dataclass
class AnalysisResult:
    """배치 로그 분석 결과."""

    agent_id: str
    date: str  # YYYYMMDD
    analysis_period: tuple[str, str]  # (start_time, end_time)
    total_lines: int
    version_segments: list[VersionSegment]
    inbound_count: int
    outbound_count: int
    trunk_outbound_count: int
    db_fail_count: int
    db_fail_causes: list[DbFailCause]
    outbound_reset_count: int
    duration_mismatch_count: int
    dir_filter_dist: dict[int, int]
    session_invalidated_count: int
    sspp_loss_count: int
    norm_failure_count: int
    # SMDR vs Recording matching
    smdr_call_count: int               # SMDR 통화 (Duration>0) 총 건수
    file_close_count: int              # 녹취 완료 (FILE CLOSE, Size>0) 건수
    smdr_calls: list[SmdrCall]         # 전체 SMDR 통화 목록
    duration_mismatches: list[DurationMismatch]  # 시간 차이 건
    unrecorded_calls: list[UnrecordedCall]  # 미녹취건
    crash_count: int
    hourly_dist: dict[str, dict[int, int]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_timestamp(line: str) -> str | None:
    """Extract timestamp string (YYYY-MM-DD HH:MM:SS) from log line."""
    m = RE_TIMESTAMP.match(line)
    if m:
        return m.group(1)
    return None


def _ts_to_hour(ts: str) -> int | None:
    """Extract hour (0-23) from timestamp string."""
    try:
        return int(ts[11:13])
    except (IndexError, ValueError):
        return None


def _ts_to_datetime(ts: str) -> datetime | None:
    """Parse timestamp string to datetime."""
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _empty_hourly() -> dict[int, int]:
    """Create zeroed hourly distribution dict (0-23)."""
    return {h: 0 for h in range(24)}


# ---------------------------------------------------------------------------
# LogAnalyzer
# ---------------------------------------------------------------------------


class LogAnalyzer:
    """AirREC 로그 파일 배치 분석기.

    Usage::

        analyzer = LogAnalyzer(agent_id="PC-DAERIGO", date="20260319")
        result = analyzer.analyze_files([Path("20260319_1.txt"), Path("20260319_2.txt")])
    """

    # DB_UPDATE FAIL 근접 판정 시간 (InitInstance로부터 2분 이내)
    RESTART_GAP_SECONDS = 120

    # SSPP loss / OUTBOUND-RESET 근접 판정 (최근 N 라인 이내)
    CONTEXT_WINDOW_LINES = 50

    def __init__(self, agent_id: str, date: str) -> None:
        self._agent_id = agent_id
        self._date = date

        # --- Counters ---
        self._total_lines: int = 0
        self._inbound_count: int = 0
        self._outbound_count: int = 0
        self._trunk_outbound_count: int = 0
        self._db_fail_count: int = 0
        self._outbound_reset_count: int = 0
        self._duration_mismatch_count: int = 0
        self._session_invalidated_count: int = 0
        self._sspp_loss_count: int = 0
        self._norm_failure_count: int = 0

        # --- SMDR vs Recording matching ---
        self._smdr_calls: list[SmdrCall] = []
        self._file_closes: list[RecordingClose] = []
        self._duration_mismatches_detail: list[DurationMismatch] = []
        self._smdr_seq: int = 0

        # --- Dir filter distribution ---
        self._dir_filter_dist: dict[int, int] = {}

        # --- DB fail causes ---
        self._db_fail_causes: list[DbFailCause] = []

        # --- Hourly distributions ---
        self._hourly_inbound: dict[int, int] = _empty_hourly()
        self._hourly_outbound: dict[int, int] = _empty_hourly()
        self._hourly_db_fail: dict[int, int] = _empty_hourly()

        # --- Version tracking ---
        self._version_segments: list[VersionSegment] = []
        self._current_version: str = ""
        self._current_build: str = ""
        self._current_segment_start_time: str = ""
        self._current_segment_start_line: int = 0

        # --- Timestamp tracking ---
        self._first_ts: str | None = None
        self._last_ts: str | None = None
        self._current_ts: str | None = None

        # --- Context window for DB_FAIL classification ---
        # Recent line flags: list of (line_num, event_type) for context lookup
        self._recent_events: list[tuple[int, str]] = []

        # Last InitInstance timestamp (for restart_gap detection)
        self._last_init_ts: str | None = None

    def analyze_files(self, file_paths: list[Path]) -> AnalysisResult:
        """Analyze multiple log files and return aggregated result.

        Files are sorted by name before processing to ensure correct
        time ordering (YYYYMMDD_N.txt where N is the sequence number).
        """
        sorted_paths = sorted(file_paths, key=lambda p: p.name)

        for fpath in sorted_paths:
            self._process_file(fpath)

        # Finalize the last open version segment
        self._finalize_segment()

        # Detect crashes: unexpected InitInstance gaps
        crash_count = self._detect_crashes()

        # SMDR → FILE CLOSE matching to find unrecorded calls
        unrecorded = self._find_unrecorded_calls()

        # Enrich DURATION-MISMATCH with caller/called from SMDR data
        self._enrich_duration_mismatches()

        return AnalysisResult(
            agent_id=self._agent_id,
            date=self._date,
            analysis_period=(
                self._first_ts or "",
                self._last_ts or "",
            ),
            total_lines=self._total_lines,
            version_segments=self._version_segments,
            inbound_count=self._inbound_count,
            outbound_count=self._outbound_count,
            trunk_outbound_count=self._trunk_outbound_count,
            db_fail_count=self._db_fail_count,
            db_fail_causes=self._db_fail_causes,
            outbound_reset_count=self._outbound_reset_count,
            duration_mismatch_count=self._duration_mismatch_count,
            dir_filter_dist=dict(sorted(self._dir_filter_dist.items())),
            session_invalidated_count=self._session_invalidated_count,
            sspp_loss_count=self._sspp_loss_count,
            norm_failure_count=self._norm_failure_count,
            smdr_call_count=len(self._smdr_calls),
            file_close_count=len(self._file_closes),
            smdr_calls=list(self._smdr_calls),
            duration_mismatches=list(self._duration_mismatches_detail),
            unrecorded_calls=unrecorded,
            crash_count=crash_count,
            hourly_dist={
                "inbound": dict(sorted(self._hourly_inbound.items())),
                "outbound": dict(sorted(self._hourly_outbound.items())),
                "db_fail": dict(sorted(self._hourly_db_fail.items())),
            },
        )

    # ------------------------------------------------------------------
    # File processing
    # ------------------------------------------------------------------

    def _process_file(self, fpath: Path) -> None:
        """Read and process a single log file line by line."""
        try:
            with open(fpath, encoding="cp949", errors="replace") as f:
                for line in f:
                    self._total_lines += 1
                    self._process_line(line.rstrip("\n\r"), self._total_lines)
        except OSError:
            # File not accessible — skip silently
            pass

    # ------------------------------------------------------------------
    # Line processing
    # ------------------------------------------------------------------

    def _process_line(self, line: str, line_num: int) -> None:
        """Process a single log line and update internal state."""
        if not line:
            return

        # --- Timestamp extraction ---
        ts = _parse_timestamp(line)
        if ts:
            self._current_ts = ts
            if self._first_ts is None:
                self._first_ts = ts
            self._last_ts = ts

        # --- Version / InitInstance ---
        if RE_INIT.search(line):
            self._handle_init_instance(line, line_num)
            return

        m = RE_VERSION.search(line)
        if m:
            self._current_version = m.group(1)
            return

        m = RE_BUILD.search(line)
        if m:
            self._current_build = m.group(1)
            return

        # --- SMDR inbound ---
        if RE_INBOUND.search(line):
            self._inbound_count += 1
            self._extract_dir_filter(line)
            hour = _ts_to_hour(self._current_ts) if self._current_ts else None
            if hour is not None:
                self._hourly_inbound[hour] = self._hourly_inbound.get(hour, 0) + 1
            # Collect for SMDR matching
            m_smdr = RE_SMDR_EVENT.search(line)
            if m_smdr and int(m_smdr.group(4)) > 0:
                self._smdr_seq += 1
                m_caller = RE_CALLER.search(line)
                m_called = RE_CALLED.search(line)
                self._smdr_calls.append(SmdrCall(
                    timestamp=self._current_ts or "",
                    flag=m_smdr.group(1),
                    action=m_smdr.group(2),
                    ext=m_smdr.group(3),
                    caller=m_caller.group(1) if m_caller else "",
                    called=m_called.group(1) if m_called else "",
                    duration=int(m_smdr.group(4)),
                    seq=self._smdr_seq,
                ))
            return

        # --- SMDR outbound (check trunk first, since trunk is a subset) ---
        if RE_OUTBOUND.search(line):
            self._outbound_count += 1
            self._extract_dir_filter(line)
            if RE_TRUNK_OUTBOUND.search(line):
                self._trunk_outbound_count += 1
            hour = _ts_to_hour(self._current_ts) if self._current_ts else None
            if hour is not None:
                self._hourly_outbound[hour] = self._hourly_outbound.get(hour, 0) + 1
            # Collect for SMDR matching
            m_smdr = RE_SMDR_EVENT.search(line)
            if m_smdr and int(m_smdr.group(4)) > 0:
                self._smdr_seq += 1
                m_caller = RE_CALLER.search(line)
                m_called = RE_CALLED.search(line)
                self._smdr_calls.append(SmdrCall(
                    timestamp=self._current_ts or "",
                    flag=m_smdr.group(1),
                    action=m_smdr.group(2),
                    ext=m_smdr.group(3),
                    caller=m_caller.group(1) if m_caller else "",
                    called=m_called.group(1) if m_called else "",
                    duration=int(m_smdr.group(4)),
                    seq=self._smdr_seq,
                ))
            return

        # --- DB_UPDATE FAIL ---
        if RE_DB_FAIL.search(line):
            self._db_fail_count += 1
            hour = _ts_to_hour(self._current_ts) if self._current_ts else None
            if hour is not None:
                self._hourly_db_fail[hour] = self._hourly_db_fail.get(hour, 0) + 1
            cause = self._classify_db_fail(line, self._current_ts or "", line_num)
            self._db_fail_causes.append(cause)
            self._push_event(line_num, "db_fail")
            return

        # --- OUTBOUND-RESET ---
        if RE_OUTBOUND_RESET.search(line):
            self._outbound_reset_count += 1
            self._push_event(line_num, "outbound_reset")
            return

        # --- FILE CLOSE (recording complete) ---
        m_fc = RE_FILE_CLOSE.search(line)
        if m_fc:
            size = int(m_fc.group(4))
            if size > 0:
                self._file_closes.append(RecordingClose(
                    timestamp=self._current_ts or "",
                    channel=m_fc.group(1),
                    cid=m_fc.group(2),
                    filename=m_fc.group(3),
                    size=size,
                    duration=int(m_fc.group(5)),
                ))
            return

        # --- Duration mismatch ---
        if RE_DURATION_MISMATCH.search(line):
            self._duration_mismatch_count += 1
            m_detail = RE_DURATION_MISMATCH_DETAIL.search(line)
            if m_detail:
                m_caller = RE_CALLER.search(line)
                m_called = RE_CALLED.search(line)
                self._duration_mismatches_detail.append(DurationMismatch(
                    timestamp=self._current_ts or "",
                    ext=m_detail.group(4),
                    caller=m_caller.group(1) if m_caller else "",
                    called=m_called.group(1) if m_called else "",
                    smdr_duration=int(m_detail.group(1)),
                    rec_duration=int(m_detail.group(2)),
                    diff=int(m_detail.group(3)),
                    cause=m_detail.group(5),
                ))
            return

        # --- Session invalidated ---
        if RE_SESSION_INVALIDATED.search(line):
            self._session_invalidated_count += 1
            return

        # --- SSPP loss ---
        if RE_SSPP_LOSS.search(line):
            self._sspp_loss_count += 1
            self._push_event(line_num, "sspp_loss")
            return

        # --- Normalization failure ---
        if RE_NORM_FAILURE.search(line):
            self._norm_failure_count += 1
            self._push_event(line_num, "norm_failure")
            return

    # ------------------------------------------------------------------
    # Version segment management
    # ------------------------------------------------------------------

    def _handle_init_instance(self, line: str, line_num: int) -> None:
        """Handle InitInstance event — start a new version segment."""
        # Finalize previous segment
        self._finalize_segment()

        # Record InitInstance timestamp for restart_gap detection
        self._last_init_ts = self._current_ts

        # Start new segment
        self._current_segment_start_time = self._current_ts or ""
        self._current_segment_start_line = line_num

        # Push event for context
        self._push_event(line_num, "init_instance")

    def _finalize_segment(self) -> None:
        """Close and store the current version segment (if one is open)."""
        if self._current_segment_start_line == 0:
            return

        segment = VersionSegment(
            version=self._current_version,
            build_info=self._current_build,
            start_time=self._current_segment_start_time,
            end_time=self._last_ts or self._current_segment_start_time,
            line_start=self._current_segment_start_line,
            line_end=self._total_lines,
        )
        self._version_segments.append(segment)

    # ------------------------------------------------------------------
    # DirFilter extraction
    # ------------------------------------------------------------------

    def _extract_dir_filter(self, line: str) -> None:
        """Extract DirFilter value from an SMDR line and update distribution."""
        m = RE_DIR_FILTER.search(line)
        if m:
            val = int(m.group(1))
            self._dir_filter_dist[val] = self._dir_filter_dist.get(val, 0) + 1

    # ------------------------------------------------------------------
    # Crash detection
    # ------------------------------------------------------------------

    def _detect_crashes(self) -> int:
        """Detect crashes by counting unexpected InitInstance restarts.

        The first InitInstance is normal startup. Subsequent ones within
        the same day indicate crashes / unexpected restarts.
        """
        if len(self._version_segments) <= 1:
            return 0

        # Each segment after the first represents a restart.
        # However, a version upgrade (version changes) is intentional — not a crash.
        crash_count = 0
        for i in range(1, len(self._version_segments)):
            prev = self._version_segments[i - 1]
            curr = self._version_segments[i]
            if prev.version == curr.version:
                # Same version restarted — likely a crash
                crash_count += 1
        return crash_count

    # ------------------------------------------------------------------
    # DURATION-MISMATCH enrichment
    # ------------------------------------------------------------------

    def _enrich_duration_mismatches(self) -> None:
        """Fill in missing caller/called on DURATION-MISMATCH from SMDR data.

        DURATION-MISMATCH logs don't contain Caller/Called fields.
        Match by Ext + timestamp (±3 min) against collected SMDR calls.
        """
        for dm in self._duration_mismatches_detail:
            if dm.caller and dm.called:
                continue
            dm_dt = _ts_to_datetime(dm.timestamp)
            if dm_dt is None:
                continue
            for smdr in self._smdr_calls:
                if smdr.ext != dm.ext:
                    continue
                smdr_dt = _ts_to_datetime(smdr.timestamp)
                if smdr_dt is None:
                    continue
                if abs((dm_dt - smdr_dt).total_seconds()) <= self.MATCH_WINDOW_SECONDS:
                    dm.caller = smdr.caller
                    dm.called = smdr.called
                    break

    # ------------------------------------------------------------------
    # SMDR → FILE CLOSE matching
    # ------------------------------------------------------------------

    # Matching time window (seconds)
    MATCH_WINDOW_SECONDS = 180

    def _find_unrecorded_calls(self) -> list[UnrecordedCall]:
        """Find SMDR calls without matching FILE CLOSE.

        Matching strategy:
        - For each SMDR call, look for a FILE CLOSE within ±3 minutes
          where the cid contains the SMDR caller or called number.
        - FILE CLOSE cid format: "caller->called"
        - Matched FILE CLOSEs are consumed (1:1 matching).
        """
        if not self._smdr_calls:
            return []

        # Build list of available FILE CLOSE events (copy to consume)
        available_closes: list[tuple[int, RecordingClose]] = [
            (i, fc) for i, fc in enumerate(self._file_closes)
        ]
        used_indices: set[int] = set()

        # DB_FAIL timestamps for cross-reference
        db_fail_times: set[str] = set()
        for cause in self._db_fail_causes:
            if cause.timestamp:
                db_fail_times.add(cause.timestamp[:16])  # Match to minute

        unrecorded: list[UnrecordedCall] = []

        for smdr in self._smdr_calls:
            smdr_dt = _ts_to_datetime(smdr.timestamp)
            if smdr_dt is None:
                continue

            matched = False
            for idx, fc in available_closes:
                if idx in used_indices:
                    continue
                fc_dt = _ts_to_datetime(fc.timestamp)
                if fc_dt is None:
                    continue

                time_diff = abs((smdr_dt - fc_dt).total_seconds())
                if time_diff > self.MATCH_WINDOW_SECONDS:
                    continue

                # Check if cid matches caller/called
                cid_parts = fc.cid.split("->")
                cid_match = False
                if len(cid_parts) == 2:
                    fc_caller, fc_called = cid_parts
                    if smdr.caller and (smdr.caller in fc_caller or fc_caller in smdr.caller):
                        cid_match = True
                    elif smdr.called and (smdr.called in fc_called or fc_called in smdr.called):
                        cid_match = True
                    elif smdr.ext and (smdr.ext in fc_caller or smdr.ext in fc_called):
                        cid_match = True
                if not cid_match:
                    # Fallback: if ext matches channel or any part of cid
                    if smdr.ext and smdr.ext in fc.cid:
                        cid_match = True

                if cid_match:
                    used_indices.add(idx)
                    matched = True
                    break

            if not matched:
                # Determine reason
                smdr_minute = smdr.timestamp[:16] if smdr.timestamp else ""
                reason = "db_fail" if smdr_minute in db_fail_times else "no_file_close"
                unrecorded.append(UnrecordedCall(
                    timestamp=smdr.timestamp,
                    ext=smdr.ext,
                    caller=smdr.caller,
                    called=smdr.called,
                    duration=smdr.duration,
                    seq=smdr.seq,
                    reason=reason,
                ))

        return unrecorded

    # ------------------------------------------------------------------
    # DB_FAIL classification
    # ------------------------------------------------------------------

    def _classify_db_fail(self, line: str, timestamp: str, line_num: int) -> DbFailCause:
        """Classify a DB_UPDATE FAIL by examining the line and recent context.

        Classification priority:
        1. Contains "3301" → did_3301
        2. Within 2 minutes of InitInstance → restart_gap
        3. Recent Idx:-1 (SSPP loss) → sspp_loss
        4. Contains norm failure pattern → norm_failure
        5. Recent OUTBOUND-RESET → direction_mismatch
        6. Otherwise → unknown
        """
        # Extract detail info
        detail_parts: list[str] = []
        m_ext = RE_EXT.search(line)
        if m_ext:
            detail_parts.append(f"Ext:{m_ext.group(1)}")
        m_caller = RE_CALLER.search(line)
        if m_caller:
            detail_parts.append(f"Caller:{m_caller.group(1)}")
        m_called = RE_CALLED.search(line)
        if m_called:
            detail_parts.append(f"Called:{m_called.group(1)}")
        detail = " ".join(detail_parts)

        # 1. DID 3301
        if RE_DB_FAIL_3301.search(line):
            return DbFailCause(
                timestamp=timestamp,
                cause="did_3301",
                detail=detail or "3301 virtual extension",
            )

        # 2. Restart gap — within 2 minutes of last InitInstance
        if self._last_init_ts and timestamp:
            init_dt = _ts_to_datetime(self._last_init_ts)
            fail_dt = _ts_to_datetime(timestamp)
            if init_dt and fail_dt:
                gap = abs((fail_dt - init_dt).total_seconds())
                if gap <= self.RESTART_GAP_SECONDS:
                    return DbFailCause(
                        timestamp=timestamp,
                        cause="restart_gap",
                        detail=f"InitInstance gap: {int(gap)}s" + (f" {detail}" if detail else ""),
                    )

        # 3. Recent SSPP loss
        if self._has_recent_event(line_num, "sspp_loss"):
            return DbFailCause(
                timestamp=timestamp,
                cause="sspp_loss",
                detail=detail or "Idx:-1 detected nearby",
            )

        # 4. Normalization failure in line
        if RE_NORM_FAILURE.search(line):
            return DbFailCause(
                timestamp=timestamp,
                cause="norm_failure",
                detail=detail or "no match candidates",
            )

        # 5. Recent OUTBOUND-RESET
        if self._has_recent_event(line_num, "outbound_reset"):
            return DbFailCause(
                timestamp=timestamp,
                cause="direction_mismatch",
                detail=detail or "OUTBOUND-RESET nearby",
            )

        # 6. Unknown
        return DbFailCause(
            timestamp=timestamp,
            cause="unknown",
            detail=detail,
        )

    # ------------------------------------------------------------------
    # Context tracking
    # ------------------------------------------------------------------

    def _push_event(self, line_num: int, event_type: str) -> None:
        """Record a recent event for context-based classification.

        Keeps only the last CONTEXT_WINDOW_LINES * 2 events to bound memory.
        """
        self._recent_events.append((line_num, event_type))
        max_keep = self.CONTEXT_WINDOW_LINES * 2
        if len(self._recent_events) > max_keep:
            self._recent_events = self._recent_events[-max_keep:]

    def _has_recent_event(self, current_line: int, event_type: str) -> bool:
        """Check if an event of the given type occurred within CONTEXT_WINDOW_LINES."""
        for evt_line, evt_type in reversed(self._recent_events):
            if current_line - evt_line > self.CONTEXT_WINDOW_LINES:
                break
            if evt_type == event_type:
                return True
        return False
