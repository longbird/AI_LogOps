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
RE_NORM_FAILURE_DETAIL = re.compile(
    r"Normalized callee:\s+(\S+)\s+->\s+(\S+)\s+\(no match,\s+(\d+)\s+candidates"
)

# SMDR event with details (flag:action Ext: ... Duration:)
RE_SMDR_EVENT = re.compile(
    r"\[SMDR\]\s+\[\d+\]\s+(\w+):(\w+)\s+Ext:(\S+).*?Duration:(\d+)"
)

# FILE CLOSE (recording completion) — Channel, CID, Filename, Size, Time, RTP
RE_FILE_CLOSE = re.compile(
    r"\[FILE\]\s+\[C:(\d+)\]\s+CLOSE\s+(\S+)\s+(\S+\.wav)\s+Size:(\d+)\s+Time:(\d+)"
    r"(?:\s+RTP:(\d+),(\d+))?"
)

# FILE CLOSE path-only line (1st of 2-line pair) — has full path but no Size/Time
# Format: [FILE] [C:10] CLOSE 01088943936->02214945 D:\path\filename.wav
RE_FILE_CLOSE_PATH = re.compile(
    r"\[FILE\]\s+\[C:(\d+)\]\s+CLOSE\s+(\S+)\s+\S+[/\\](\S+\.wav)\s*$"
)

# FILE CLOSE with CID-polluted metadata (no .wav filename, but has Size/Time)
# Format: [FILE] [C:10] CLOSE 01022264436->  Size:1213486 Time:37 RTP:1892,1895
RE_FILE_CLOSE_POLLUTED = re.compile(
    r"\[FILE\]\s+\[C:(\d+)\]\s+CLOSE\s+(\S+?->)\S*\s+Size:(\d+)\s+Time:(\d+)"
    r"(?:\s+RTP:(\d+),(\d+))?"
)

# FILE CLOSE corrupted by RTP-INFO-GRACE — empty CID, no filename, RTP:0,0
# Format: [FILE] [C:93] CLOSE ->  Size:1973166 Time:61 RTP:0,0
RE_FILE_CLOSE_GRACE = re.compile(
    r"\[FILE\]\s+\[C:(\d+)\]\s+CLOSE\s+->\s+Size:(\d+)\s+Time:(\d+)"
    r"(?:\s+RTP:(\d+),(\d+))?"
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
    rtp_server: int = 0  # RTP packets (server side)
    rtp_client: int = 0  # RTP packets (client side)


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
class NormFailureDetail:
    """번호 정규화 실패 상세."""
    timestamp: str
    original: str    # 정규화 전 (예: 66101094808301)
    normalized: str  # 정규화 후 (예: 01094808301)
    candidates: int  # 시도한 후보 수
    recovered: bool = False  # True면 ENDED 매칭으로 녹취 회복됨


@dataclass
class GraceClose:
    """RTP-INFO-GRACE로 인한 훼손된 FILE CLOSE (CID/파일명 누락)."""
    timestamp: str
    channel: str
    size: int
    duration: int    # seconds


@dataclass
class PendingClose:
    """채널 경합으로 Size 줄이 분리된 FILE CLOSE 1줄째 (경로만)."""
    timestamp: str
    channel: str
    cid: str
    filename: str


@dataclass
class UnrecordedCall:
    """SMDR에 있지만 녹취되지 않은 통화."""
    timestamp: str
    ext: str
    caller: str
    called: str
    duration: int
    seq: int         # SMDR 순번
    reason: str      # no_file_close | db_fail | grace_close


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
    norm_failure_details: list[NormFailureDetail]
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
        self._norm_failure_details: list[NormFailureDetail] = []

        # --- SMDR vs Recording matching ---
        self._smdr_calls: list[SmdrCall] = []
        self._file_closes: list[RecordingClose] = []
        self._grace_closes: list[GraceClose] = []
        self._pending_closes: dict[str, PendingClose] = {}  # channel -> PendingClose
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

        # Classify normalization failures: recovered (FILE CLOSE exists) vs actual
        self._classify_norm_failures()

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
            norm_failure_details=list(self._norm_failure_details),
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
                channel = m_fc.group(1)
                # Consume pending close for this channel (2-line merge)
                self._pending_closes.pop(channel, None)
                self._file_closes.append(RecordingClose(
                    timestamp=self._current_ts or "",
                    channel=channel,
                    cid=m_fc.group(2),
                    filename=m_fc.group(3),
                    size=size,
                    duration=int(m_fc.group(5)),
                    rtp_server=int(m_fc.group(6)) if m_fc.group(6) else 0,
                    rtp_client=int(m_fc.group(7)) if m_fc.group(7) else 0,
                ))
            return

        # --- FILE CLOSE CID-polluted (channel reuse race: no filename, has Size) ---
        # Format: [FILE] [C:10] CLOSE 01022264436->  Size:1213486 Time:37 RTP:1892,1895
        # Merge with pending close from same channel to recover the real filename/CID.
        m_cp = RE_FILE_CLOSE_POLLUTED.search(line)
        if m_cp:
            channel = m_cp.group(1)
            size = int(m_cp.group(3))
            if size > 0:
                pending = self._pending_closes.pop(channel, None)
                if pending:
                    # Merge: use CID/filename from pending (1st line), Size/Time from this (2nd line)
                    self._file_closes.append(RecordingClose(
                        timestamp=pending.timestamp,
                        channel=channel,
                        cid=pending.cid,
                        filename=pending.filename,
                        size=size,
                        duration=int(m_cp.group(4)),
                        rtp_server=int(m_cp.group(5)) if m_cp.group(5) else 0,
                        rtp_client=int(m_cp.group(6)) if m_cp.group(6) else 0,
                    ))
                else:
                    # No pending — treat as grace close (CID unreliable, no filename)
                    self._grace_closes.append(GraceClose(
                        timestamp=self._current_ts or "",
                        channel=channel,
                        size=size,
                        duration=int(m_cp.group(4)),
                    ))
            return

        # --- FILE CLOSE path-only (1st line of 2-line pair, no Size) ---
        # Format: [FILE] [C:10] CLOSE CID D:\path\filename.wav
        # Store as pending; will be merged when 2nd line (with Size) arrives.
        m_fp = RE_FILE_CLOSE_PATH.search(line)
        if m_fp:
            channel = m_fp.group(1)
            self._pending_closes[channel] = PendingClose(
                timestamp=self._current_ts or "",
                channel=channel,
                cid=m_fp.group(2),
                filename=m_fp.group(3),
            )
            return

        # --- FILE CLOSE corrupted by RTP-INFO-GRACE ---
        m_gc = RE_FILE_CLOSE_GRACE.search(line)
        if m_gc:
            size = int(m_gc.group(2))
            if size > 0:
                self._grace_closes.append(GraceClose(
                    timestamp=self._current_ts or "",
                    channel=m_gc.group(1),
                    size=size,
                    duration=int(m_gc.group(3)),
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
            m_detail = RE_NORM_FAILURE_DETAIL.search(line)
            if m_detail:
                self._norm_failure_details.append(NormFailureDetail(
                    timestamp=self._current_ts or "",
                    original=m_detail.group(1),
                    normalized=m_detail.group(2),
                    candidates=int(m_detail.group(3)),
                ))
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
    # Normalization failure classification
    # ------------------------------------------------------------------

    def _classify_norm_failures(self) -> None:
        """정규화 실패를 FILE CLOSE 존재 여부로 분류.

        normalized 번호가 FILE CLOSE CID 또는 파일명에 포함되면
        ENDED 매칭으로 녹취가 회복된 것이므로 recovered=True.
        """
        for nf in self._norm_failure_details:
            nf_dt = _ts_to_datetime(nf.timestamp)
            if nf_dt is None:
                continue
            for fc in self._file_closes:
                fc_dt = _ts_to_datetime(fc.timestamp)
                if fc_dt is None:
                    continue
                if abs((nf_dt - fc_dt).total_seconds()) > self.MATCH_WINDOW_SECONDS:
                    continue
                if nf.normalized in fc.cid or nf.normalized in fc.filename:
                    nf.recovered = True
                    break

    # ------------------------------------------------------------------
    # SMDR → FILE CLOSE matching
    # ------------------------------------------------------------------

    # Matching time window (seconds)
    MATCH_WINDOW_SECONDS = 180

    @staticmethod
    def _normalize_dtmf(number: str) -> str:
        """DTMF 특수문자(#, *) 제거하여 CID 매칭용 번호 생성.

        SMDR에서 '01037232562#2'로 표기된 번호가
        CID에서는 '010372325622'로 변환되므로 '#' 제거 후 비교.
        """
        return number.replace("#", "").replace("*", "")

    def _detect_did_passthrough(self) -> tuple[list[SmdrCall], list[SmdrCall], set[str]]:
        """DID 패스스루 통화를 자동 감지하여 SMDR을 분리.

        DID 가상내선 경유 통화(예: 3301)는 별도 녹취가 없으므로
        FC 매칭에서 제외해야 실제 통화의 FC를 잘못 소비하지 않는다.

        Returns:
            (normal_smdr, did_smdr, did_trunk_numbers)
        """
        # Step 1: DID 가상내선 번호 감지 (DB_FAIL cause=did_3301에서 추출)
        did_extensions: set[str] = set()
        for cause in self._db_fail_causes:
            if cause.cause == "did_3301":
                # detail에서 DID 번호 추출 (예: "Ext:7056 Caller:...")
                # 또는 called 필드에서 3301 등
                did_extensions.add("3301")
                break

        if not did_extensions:
            return list(self._smdr_calls), [], set()

        # Step 2: DID 가상내선 이벤트 타임스탬프 수집 (분 단위)
        did_timestamps: set[str] = set()
        for smdr in self._smdr_calls:
            if smdr.called in did_extensions and smdr.timestamp:
                did_timestamps.add(smdr.timestamp[:16])

        # Step 3: DID 트렁크 번호 감지
        # 같은 시각(분)에 트렁크(850x)에서 발생한 SMDR의 Called가 DID 트렁크 번호
        did_trunk_numbers: set[str] = set()
        for smdr in self._smdr_calls:
            if (smdr.ext.startswith("850")
                    and smdr.timestamp
                    and smdr.timestamp[:16] in did_timestamps
                    and smdr.called
                    and smdr.called not in did_extensions):
                did_trunk_numbers.add(smdr.called)

        # Step 4: 분류
        normal: list[SmdrCall] = []
        did_related: list[SmdrCall] = []
        for smdr in self._smdr_calls:
            if (smdr.called in did_extensions
                    or smdr.called in did_trunk_numbers
                    or smdr.caller in did_trunk_numbers):
                did_related.append(smdr)
            else:
                normal.append(smdr)

        return normal, did_related, did_trunk_numbers

    def _find_unrecorded_calls(self) -> list[UnrecordedCall]:
        """Find SMDR calls without matching FILE CLOSE.

        Matching strategy (3-pass, index-based):
        - DID 패스스루 통화는 FC 매칭에서 제외 (별도 녹취 없음)
        - Pass 1: 번호 매칭 + duration ≤ 5s (confident)
        - Pass 2: 번호 매칭 + any duration (remaining)
        - Pass 3: ext 매칭 (번호 매칭 실패 고아 SMDR만)
        - DTMF 특수문자(#, *) 정규화 후 비교.
        - FILE CLOSE cid format: "caller->called"
        - Matched FILE CLOSEs are consumed (1:1 matching).
        """
        if not self._smdr_calls:
            return []

        from collections import defaultdict

        # Flush remaining pending closes — 2nd line never arrived (channel race)
        # Convert to RecordingClose with size=0 so filename-based matching can work.
        for pending in self._pending_closes.values():
            self._file_closes.append(RecordingClose(
                timestamp=pending.timestamp,
                channel=pending.channel,
                cid=pending.cid,
                filename=pending.filename,
                size=0,
                duration=0,
            ))
        self._pending_closes.clear()

        # Partition DID pass-through calls
        normal_smdr, did_smdr, _ = self._detect_did_passthrough()

        used_indices: set[int] = set()

        # DB_FAIL timestamps for cross-reference
        db_fail_times: set[str] = set()
        for cause in self._db_fail_causes:
            if cause.timestamp:
                db_fail_times.add(cause.timestamp[:16])  # Match to minute

        unrecorded: list[UnrecordedCall] = []

        # DID pass-through → automatically unrecorded
        for smdr in did_smdr:
            unrecorded.append(UnrecordedCall(
                timestamp=smdr.timestamp,
                ext=smdr.ext,
                caller=smdr.caller,
                called=smdr.called,
                duration=smdr.duration,
                seq=smdr.seq,
                reason="did_passthrough",
            ))

        # Normal SMDR → match against FILE CLOSEs (3-pass)
        _CONFIDENT_DURATION_DIFF = 5
        _SUFFIX_LEN = 8  # phone number suffix length for index key

        # ----------------------------------------------------------
        # Pre-parse all FC entries and build indexes
        # ----------------------------------------------------------
        def _ts_to_seconds(ts: str) -> int | None:
            """Convert 'YYYY-MM-DD HH:MM:SS' to seconds since midnight."""
            try:
                t = ts.split(" ")[1]
                h, m, s = t.split(":")
                return int(h) * 3600 + int(m) * 60 + int(s)
            except (IndexError, ValueError):
                return None

        def _parse_filename_fields(filename: str) -> tuple[str, str, str]:
            parts = filename.rsplit(".wav", 1)[0].split(".")
            if len(parts) >= 5:
                fn_caller = parts[2] if parts[2] != "_" else ""
                fn_called = parts[3] if parts[3] != "_" else ""
                fn_ext = parts[4]
                return fn_caller, fn_called, fn_ext
            return "", "", ""

        def _num_match(a: str, b: str) -> bool:
            if not a or not b:
                return False
            if a == b:
                return True
            short, long = (a, b) if len(a) <= len(b) else (b, a)
            return long.endswith(short)

        # Pre-parsed FC data:
        #   (seconds, duration, caller_nums, called_nums, all_nums, fn_ext)
        # caller_nums/called_nums for direction-aware matching.
        fc_parsed: list[tuple[int, int, list[str], list[str], list[str], str]] = []
        # Indexes: suffix → list of fc indices
        fc_by_suffix: dict[str, list[int]] = defaultdict(list)
        fc_by_ext: dict[str, list[int]] = defaultdict(list)

        for i, fc in enumerate(self._file_closes):
            sec = _ts_to_seconds(fc.timestamp)
            if sec is None:
                fc_parsed.append((-1, fc.duration, [], [], [], ""))
                continue
            cid_parts = fc.cid.split("->")
            fc_caller = cid_parts[0] if len(cid_parts) >= 1 else ""
            fc_called = cid_parts[1] if len(cid_parts) >= 2 else ""
            fn_caller, fn_called, fn_ext = _parse_filename_fields(fc.filename)

            caller_nums = list({fc_caller, fn_caller} - {""})
            called_nums = list({fc_called, fn_called} - {""})
            all_nums = list({fc_caller, fc_called, fn_caller, fn_called} - {""})
            fc_parsed.append((sec, fc.duration, caller_nums, called_nums, all_nums, fn_ext))

            # Index by number suffixes (for fast lookup)
            for n in all_nums:
                clean = n.rstrip("*")
                if clean:
                    key = clean[-_SUFFIX_LEN:] if len(clean) >= _SUFFIX_LEN else clean
                    fc_by_suffix[key].append(i)
            # Index by ext
            if fn_ext:
                fc_by_ext[fn_ext].append(i)

        # ----------------------------------------------------------
        # Candidate lookup (index-based, O(1) per number)
        # ----------------------------------------------------------
        # Direction penalty: when sorting candidates, same-direction
        # matches rank before cross-direction matches at equal duration diff.
        _DIR_PENALTY = 1000  # added to duration diff for cross-direction

        def _get_candidates_by_number(
            smdr_sec: int,
            norm_caller: str,
            norm_called: str,
        ) -> list[tuple[int, int]]:
            """Return [(fc_index, fc_duration)] matching by phone number.

            Direction-aware: prefers FC where the matching number is on the
            same side (caller→caller, called→called). Cross-direction matches
            still work but get a duration penalty so same-direction wins.
            """
            # Collect candidate FC indices from suffix index
            candidate_set: set[int] = set()
            if norm_caller:
                key = norm_caller[-_SUFFIX_LEN:] if len(norm_caller) >= _SUFFIX_LEN else norm_caller
                candidate_set.update(fc_by_suffix.get(key, ()))
            if not norm_caller and norm_called:
                key = norm_called[-_SUFFIX_LEN:] if len(norm_called) >= _SUFFIX_LEN else norm_called
                candidate_set.update(fc_by_suffix.get(key, ()))

            hits: list[tuple[int, int]] = []
            for fi in candidate_set:
                if fi in used_indices:
                    continue
                fc_sec, fc_dur, fc_ca_nums, fc_cd_nums, fc_all, _ = fc_parsed[fi]
                if fc_sec < 0:
                    continue
                if abs(smdr_sec - fc_sec) > self.MATCH_WINDOW_SECONDS:
                    continue
                # Verify full number match + direction check
                if norm_caller:
                    # Inbound SMDR: prefer FC with caller on caller side
                    same_dir = any(_num_match(norm_caller, n) for n in fc_ca_nums)
                    cross_dir = (
                        not same_dir
                        and any(_num_match(norm_caller, n) for n in fc_cd_nums)
                    )
                    if same_dir:
                        hits.append((fi, fc_dur))
                    elif cross_dir:
                        # Penalize duration so same-direction is preferred
                        hits.append((fi, fc_dur + _DIR_PENALTY))
                elif norm_called:
                    # Outbound SMDR: prefer FC with called on called side
                    same_dir = any(_num_match(norm_called, n) for n in fc_cd_nums)
                    cross_dir = (
                        not same_dir
                        and any(_num_match(norm_called, n) for n in fc_ca_nums)
                    )
                    if same_dir:
                        hits.append((fi, fc_dur))
                    elif cross_dir:
                        hits.append((fi, fc_dur + _DIR_PENALTY))
            return hits

        def _get_candidates_by_ext(
            smdr_sec: int,
            ext: str,
        ) -> list[tuple[int, int]]:
            """Return [(fc_index, fc_duration)] matching by extension only."""
            hits: list[tuple[int, int]] = []
            for fi in fc_by_ext.get(ext, ()):
                if fi in used_indices:
                    continue
                fc_sec, fc_dur = fc_parsed[fi][0], fc_parsed[fi][1]
                if fc_sec < 0:
                    continue
                if abs(smdr_sec - fc_sec) > self.MATCH_WINDOW_SECONDS:
                    continue
                hits.append((fi, fc_dur))
            return hits

        # ----------------------------------------------------------
        # 3-pass matching
        # ----------------------------------------------------------
        matched_smdr: set[int] = set()

        # Pre-compute SMDR data
        smdr_data: list[tuple[int, str, str]] = []
        for smdr in normal_smdr:
            sec = _ts_to_seconds(smdr.timestamp)
            nc = self._normalize_dtmf(smdr.caller) if smdr.caller else ""
            nd = self._normalize_dtmf(smdr.called) if smdr.called else ""
            smdr_data.append((sec if sec is not None else -1, nc, nd))

        # Pass 1: number-based confident matches (duration diff ≤ 5s)
        for si, smdr in enumerate(normal_smdr):
            sec, nc, nd = smdr_data[si]
            if sec < 0:
                continue
            candidates = _get_candidates_by_number(sec, nc, nd)
            if not candidates:
                continue
            best_fi, best_dur = min(candidates, key=lambda t: abs(smdr.duration - t[1]))
            if abs(smdr.duration - best_dur) <= _CONFIDENT_DURATION_DIFF:
                used_indices.add(best_fi)
                matched_smdr.add(si)

        # Pass 2: number-based remaining — best duration match
        for si, smdr in enumerate(normal_smdr):
            if si in matched_smdr:
                continue
            sec, nc, nd = smdr_data[si]
            if sec < 0:
                continue
            candidates = _get_candidates_by_number(sec, nc, nd)
            if not candidates:
                continue
            best_fi, _ = min(candidates, key=lambda t: abs(smdr.duration - t[1]))
            used_indices.add(best_fi)
            matched_smdr.add(si)

        # Pass 3: ext-based last resort — only for orphaned SMDRs
        for si, smdr in enumerate(normal_smdr):
            if si in matched_smdr:
                continue
            sec = smdr_data[si][0]
            if sec < 0 or not smdr.ext:
                continue
            candidates = _get_candidates_by_ext(sec, smdr.ext)
            if not candidates:
                continue
            best_fi, _ = min(candidates, key=lambda t: abs(smdr.duration - t[1]))
            used_indices.add(best_fi)
            matched_smdr.add(si)

        # Pass 4: grace-close recovery — match against corrupted CLOSE
        # lines (RTP-INFO-GRACE: empty CID, RTP:0,0) by timestamp + duration.
        # The recording file EXISTS on disk but the log entry lost CID/filename.
        if self._grace_closes:
            gc_by_second: dict[int, list[tuple[int, int]]] = defaultdict(list)
            for gi, gc in enumerate(self._grace_closes):
                gc_sec = _ts_to_seconds(gc.timestamp)
                if gc_sec is not None:
                    gc_by_second[gc_sec].append((gi, gc.duration))

            used_gc: set[int] = set()
            _GC_TIME_WINDOW = 5  # seconds — grace CLOSE is within ~1s of SMDR
            _GC_DURATION_DIFF = 15  # seconds — SMDR vs grace duration tolerance

            for si, smdr in enumerate(normal_smdr):
                if si in matched_smdr:
                    continue
                sec = smdr_data[si][0]
                if sec < 0:
                    continue
                best_gi = -1
                best_diff = _GC_DURATION_DIFF + 1
                for offset in range(-_GC_TIME_WINDOW, _GC_TIME_WINDOW + 1):
                    for gi, gc_dur in gc_by_second.get(sec + offset, ()):
                        if gi in used_gc:
                            continue
                        dur_diff = abs(smdr.duration - gc_dur)
                        if dur_diff < best_diff:
                            best_diff = dur_diff
                            best_gi = gi
                if best_gi >= 0:
                    used_gc.add(best_gi)
                    matched_smdr.add(si)

        # Collect unrecorded
        for si, smdr in enumerate(normal_smdr):
            matched = si in matched_smdr
            if not matched:
                # Determine reason (priority order)
                smdr_minute = smdr.timestamp[:16] if smdr.timestamp else ""
                if self._is_restart_gap(smdr.timestamp):
                    reason = "restart_gap"
                elif smdr_minute in db_fail_times:
                    reason = "db_fail"
                else:
                    reason = "no_file_close"
                unrecorded.append(UnrecordedCall(
                    timestamp=smdr.timestamp,
                    ext=smdr.ext,
                    caller=smdr.caller,
                    called=smdr.called,
                    duration=smdr.duration,
                    seq=smdr.seq,
                    reason=reason,
                ))

        # Post-process: detect silent recordings (FILE CLOSE exists but RTP:0,0)
        self._detect_silent_recordings(unrecorded)

        return unrecorded

    def _is_restart_gap(self, timestamp: str) -> bool:
        """SMDR 이벤트가 재시작 직후 gap 구간에 해당하는지 판별.

        InitInstance 후 RESTART_GAP_SECONDS 이내이면 restart_gap.
        """
        if not self._version_segments:
            return False
        ts_dt = _ts_to_datetime(timestamp)
        if ts_dt is None:
            return False
        for seg in self._version_segments:
            seg_dt = _ts_to_datetime(seg.start_time)
            if seg_dt is None:
                continue
            gap = (ts_dt - seg_dt).total_seconds()
            if 0 <= gap <= self.RESTART_GAP_SECONDS:
                return True
        return False

    def _detect_silent_recordings(self, unrecorded: list[UnrecordedCall]) -> None:
        """미녹취 중 실제로는 FILE CLOSE가 있으나 RTP:0,0인 건을 silent_recording으로 재분류.

        FILE CLOSE는 있지만 RTP 패킷이 0인 경우 = 파일은 존재하나 음성 없음.
        """
        silent_fcs = [
            fc for fc in self._file_closes
            if fc.rtp_server == 0 and fc.rtp_client == 0 and fc.size > 0
        ]
        if not silent_fcs:
            return

        for unrec in unrecorded:
            if unrec.reason != "no_file_close":
                continue
            unrec_dt = _ts_to_datetime(unrec.timestamp)
            if unrec_dt is None:
                continue
            norm_caller = self._normalize_dtmf(unrec.caller) if unrec.caller else ""
            norm_called = self._normalize_dtmf(unrec.called) if unrec.called else ""
            for fc in silent_fcs:
                fc_dt = _ts_to_datetime(fc.timestamp)
                if fc_dt is None:
                    continue
                if abs((unrec_dt - fc_dt).total_seconds()) > self.MATCH_WINDOW_SECONDS:
                    continue
                # Check if this silent FC matches the unrecorded call
                if norm_caller and norm_caller in fc.cid:
                    unrec.reason = "silent_recording"
                    break
                if norm_called and norm_called in fc.cid:
                    unrec.reason = "silent_recording"
                    break

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
