"""
server/analysis/monitor_state.py
=================================
AirREC 실시간 로그 분석 상태 관리.

realtime_monitor.py (D:\\Work\\AirTech\\PBXServer\\AirRec\\scripts\\realtime_monitor.py)
의 MonitorState 로직을 서버 in-process로 포팅.
각 에이전트별 인스턴스가 유지되며, LOG_REAL 수신 시 라인을 직접 처리한다.
"""

from __future__ import annotations

import re
from collections import Counter, deque
from datetime import datetime, timedelta
from typing import Any

# ---------------------------------------------------------------------------
# Regex patterns (AirREC log format)
# ---------------------------------------------------------------------------

RE_TIMESTAMP = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)\]")

RE_RTP_THREAD_START = re.compile(
    r"RtpProcessThread\s+index:(-?\d+)\s+Address:(\S*)\s*>>\s*Start"
)
RE_RTP_THREAD_TERM = re.compile(
    r"RtpProcessThread\s+index:(-?\d+)\s+Address:(\S*)\s*>>\s*terminated"
)
RE_PKT_THREAD_START = re.compile(
    r"PacketProcessThread\s+index:(-?\d+)\s+Address:(\S*)\s*>>\s*Start"
)
RE_PKT_THREAD_TERM = re.compile(r"PacketProcessThread\s+idx:(-?\d+)\s*>>\s*terminated")

RE_DURATION_MISMATCH = re.compile(
    r"\[DURATION-MISMATCH\]"
    r".*?SMDR:(\d+)s"
    r".*?Rec:(\d+)s"
    r".*?Diff:([+-]?\d+)s"
    r".*?Ext:(\S+)"
    r".*?Cause:(\S+)"
    r"(?:.*?Lag:([+-]?\d+)s)?"
)

RE_RTP_START = re.compile(r"\[RTP\]\s+\[C:(\d+)\]\s+START\s+(\S+)")
RE_RTP_MATCH = re.compile(r"\[RTP\]\s+\[C:(\d+)\]\s+MATCH\s+(\S+)")
RE_RTP_BYE = re.compile(r"\[RTP\]\s+\[C:(\d+)\]\s+BYE\s+(\S+)")

RE_FILE_OPEN = re.compile(r"\[FILE\]\s+\[C:(\d+)\]\s+OPEN\s+(\S+)\s+(\S+\.wav)")
RE_FILE_CLOSE = re.compile(
    r"\[FILE\]\s+\[C:(\d+)\]\s+CLOSE\s+(\S+)\s+(\S+\.wav)\s+Size:(\d+)\s+Time:(\d+)"
)

RE_SMDR_EVENT = re.compile(
    r"\[SMDR\]\s+\[\d+\]\s+(\w+):(\w+)\s+Ext:(\S+).*?Duration:(\d+)"
)
RE_SMDR_PENDING = re.compile(
    r"\[SMDR\]\s+\[C:(\d+)\]\s+PENDING APPLIED.*?waited:(\d+)sec"
)

RE_GRACE_STARTED = re.compile(r"\[RTP-GRACE\]\s+\[C:(\d+)\]\s+BYE grace period started")
RE_GRACE_EXPIRED = re.compile(r"\[RTP-GRACE\]\s+\[C:(\d+)\]\s+Expired")
RE_GRACE_RESUMED = re.compile(r"\[RTP-GRACE\]\s+\[C:(\d+)\]\s+Resumed")
RE_GRACE_TERMINATED = re.compile(r"\[RTP-GRACE\]\s+\[C:(\d+)\]\s+Terminated")

RE_SSPP = re.compile(r"Idx:\s*\d+,(\d+)\s+\S+\s+.*?Type:(\d+)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_timestamp(line: str) -> datetime | None:
    m = RE_TIMESTAMP.match(line)
    if not m:
        return None
    ts_str = m.group(1)
    dot_idx = ts_str.rfind(".")
    if dot_idx != -1:
        frac = ts_str[dot_idx + 1 :]
        ts_str = ts_str[: dot_idx + 1] + frac[:6].ljust(6, "0")
    try:
        return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return None


def _fmt_uptime(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}h {m:02d}m"
    if m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"


# ---------------------------------------------------------------------------
# MonitorState
# ---------------------------------------------------------------------------

_EventDeque = deque[tuple[datetime, dict[str, Any]]]


class MonitorState:
    """에이전트별 AirREC 실시간 로그 분석 상태.

    ``process_line()`` 으로 라인을 처리하고 ``to_dict()`` 로 JSON-직렬화 가능한
    현재 통계를 얻는다.
    """

    WINDOW_SECONDS = 3600  # 1h for mismatch stats
    ACTIVITY_SECONDS = 300  # 5min for session activity stats

    def __init__(self) -> None:
        self.start_time = datetime.now()

        # Thread state: index -> address
        self.rtp_threads: dict[int, str] = {}
        self.pkt_threads: dict[int, str] = {}

        # Rolling event deques: (datetime, data_dict)
        self.mismatch_events: _EventDeque = deque()
        self.rtp_start_events: _EventDeque = deque()
        self.rtp_match_events: _EventDeque = deque()
        self.rtp_bye_events: _EventDeque = deque()
        self.file_open_events: _EventDeque = deque()
        self.file_close_events: _EventDeque = deque()
        self.smdr_events: _EventDeque = deque()
        self.smdr_pending_events: _EventDeque = deque()
        self.grace_started_events: _EventDeque = deque()
        self.grace_expired_events: _EventDeque = deque()
        self.grace_resumed_events: _EventDeque = deque()
        self.grace_terminated_events: _EventDeque = deque()
        self.sspp_events: _EventDeque = deque()

        # Active sessions: chan -> {cid, file, open_time}
        self.active_sessions: dict[str, dict[str, Any]] = {}

        # Recent items for display
        self.recent_mismatches: deque[dict[str, Any]] = deque(maxlen=10)
        self.recent_thread_events: deque[tuple[datetime, str]] = deque(maxlen=20)

        self.total_lines: int = 0
        self.last_ts: datetime | None = None
        self.current_log: str = ""  # most recent filename seen

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _prune(self, dq: _EventDeque, cutoff: datetime) -> None:
        while dq and dq[0][0] < cutoff:
            dq.popleft()

    def prune_all(self) -> None:
        now = datetime.now()
        hour_ago = now - timedelta(seconds=self.WINDOW_SECONDS)
        five_min_ago = now - timedelta(seconds=self.ACTIVITY_SECONDS)

        self._prune(self.mismatch_events, hour_ago)
        for dq in (
            self.rtp_start_events,
            self.rtp_match_events,
            self.rtp_bye_events,
            self.file_open_events,
            self.file_close_events,
            self.smdr_events,
            self.smdr_pending_events,
            self.grace_started_events,
            self.grace_expired_events,
            self.grace_resumed_events,
            self.grace_terminated_events,
            self.sspp_events,
        ):
            self._prune(dq, five_min_ago)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_line(self, line: str, filename: str = "") -> None:
        """Parse a single log line and update state.

        ``line`` may have the ``[F{N}] `` prefix added by the server buffer
        — it is stripped before analysis.
        ``filename`` is the source filename (for current_log tracking).
        """
        line = line.strip()
        if not line:
            return

        # Strip [FN] prefix added by _handle_log_real buffering
        if line.startswith("[F") and "] " in line[:8]:
            bracket_end = line.index("] ")
            line = line[bracket_end + 2 :]

        if filename:
            self.current_log = filename

        self.total_lines += 1
        ts = _parse_timestamp(line)
        event_time = ts if ts else datetime.now()
        if ts:
            self.last_ts = ts

        # Thread events
        m = RE_RTP_THREAD_START.search(line)
        if m:
            self.rtp_threads[int(m.group(1))] = m.group(2)
            self.recent_thread_events.append(
                (event_time, f"RtpProcessThread #{m.group(1)} START ({m.group(2)})")
            )
            return
        m = RE_RTP_THREAD_TERM.search(line)
        if m:
            self.rtp_threads.pop(int(m.group(1)), None)
            self.recent_thread_events.append(
                (event_time, f"RtpProcessThread #{m.group(1)} STOP")
            )
            return
        m = RE_PKT_THREAD_START.search(line)
        if m:
            self.pkt_threads[int(m.group(1))] = m.group(2)
            self.recent_thread_events.append(
                (
                    event_time,
                    f"PacketProcessThread #{m.group(1)} START ({m.group(2)})",
                )
            )
            return
        m = RE_PKT_THREAD_TERM.search(line)
        if m:
            self.pkt_threads.pop(int(m.group(1)), None)
            return

        # Duration mismatch
        m = RE_DURATION_MISMATCH.search(line)
        if m:
            data: dict[str, Any] = {
                "smdr": int(m.group(1)),
                "rec": int(m.group(2)),
                "diff": int(m.group(3)),
                "ext": m.group(4),
                "cause": m.group(5),
                "lag": int(m.group(6)) if m.group(6) else int(m.group(3)),
                "ts": event_time.strftime("%H:%M:%S"),
            }
            self.mismatch_events.append((event_time, data))
            self.recent_mismatches.append(data)
            return

        # RTP events
        m = RE_RTP_START.search(line)
        if m:
            self.rtp_start_events.append(
                (event_time, {"chan": m.group(1), "cid": m.group(2)})
            )
            return
        m = RE_RTP_MATCH.search(line)
        if m:
            self.rtp_match_events.append((event_time, {"chan": m.group(1)}))
            return
        m = RE_RTP_BYE.search(line)
        if m:
            chan = m.group(1)
            self.rtp_bye_events.append((event_time, {"chan": chan, "cid": m.group(2)}))
            self.active_sessions.pop(chan, None)
            return

        # File events
        m = RE_FILE_OPEN.search(line)
        if m:
            chan, cid, fname = m.group(1), m.group(2), m.group(3)
            self.file_open_events.append(
                (event_time, {"chan": chan, "cid": cid, "file": fname})
            )
            self.active_sessions[chan] = {
                "cid": cid,
                "file": fname,
                "open_time": event_time.strftime("%H:%M:%S"),
            }
            return
        m = RE_FILE_CLOSE.search(line)
        if m:
            chan = m.group(1)
            size, dur = int(m.group(4)), int(m.group(5))
            self.file_close_events.append(
                (event_time, {"chan": chan, "size": size, "duration": dur})
            )
            self.active_sessions.pop(chan, None)
            return

        # SMDR events
        m = RE_SMDR_EVENT.search(line)
        if m:
            self.smdr_events.append(
                (
                    event_time,
                    {
                        "flag": m.group(1),
                        "action": m.group(2),
                        "ext": m.group(3),
                        "duration": int(m.group(4)),
                    },
                )
            )
            return
        m = RE_SMDR_PENDING.search(line)
        if m:
            self.smdr_pending_events.append(
                (event_time, {"chan": m.group(1), "wait": int(m.group(2))})
            )
            return

        # Grace period events
        m = RE_GRACE_STARTED.search(line)
        if m:
            self.grace_started_events.append((event_time, {"chan": m.group(1)}))
            return
        m = RE_GRACE_EXPIRED.search(line)
        if m:
            self.grace_expired_events.append((event_time, {"chan": m.group(1)}))
            return
        m = RE_GRACE_RESUMED.search(line)
        if m:
            self.grace_resumed_events.append((event_time, {"chan": m.group(1)}))
            return
        m = RE_GRACE_TERMINATED.search(line)
        if m:
            self.grace_terminated_events.append((event_time, {"chan": m.group(1)}))
            return

        # SSPP events
        m = RE_SSPP.search(line)
        if m:
            self.sspp_events.append(
                (event_time, {"chan": m.group(1), "type": m.group(2)})
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialize current analysis state to JSON-serializable dict."""
        self.prune_all()

        now = datetime.now()
        uptime_s = int((now - self.start_time).total_seconds())

        # --- Thread ---
        rtp_idxs = sorted(self.rtp_threads.keys())
        pkt_idxs = sorted(self.pkt_threads.keys())

        # --- Duration mismatch (1h) ---
        mm_events = list(self.mismatch_events)
        mm_count = len(mm_events)
        if mm_count > 0:
            lags = [e[1]["lag"] for e in mm_events]
            cause_ctr: Counter[str] = Counter(e[1]["cause"] for e in mm_events)
            mismatch: dict[str, Any] = {
                "count": mm_count,
                "avg_lag": round(sum(lags) / len(lags), 1),
                "max_lag": max(lags),
                "min_lag": min(lags),
                "causes": cause_ctr.most_common(),
            }
        else:
            mismatch = {
                "count": 0,
                "avg_lag": 0.0,
                "max_lag": 0,
                "min_lag": 0,
                "causes": [],
            }

        # --- Activity (5min) ---
        fc_events = list(self.file_close_events)
        fc_n = len(fc_events)
        fo_n = len(self.file_open_events)
        smdr_evs = list(self.smdr_events)
        sspp_evs = list(self.sspp_events)
        pend_evs = list(self.smdr_pending_events)

        file_avg_size_kb = 0.0
        file_avg_dur = 0.0
        if fc_n > 0:
            file_avg_size_kb = round(
                sum(e[1]["size"] for e in fc_events) / fc_n / 1024, 1
            )
            file_avg_dur = round(sum(e[1]["duration"] for e in fc_events) / fc_n, 0)

        smdr_n = len(smdr_evs)
        sspp_n = len(sspp_evs)
        pend_n = len(pend_evs)
        type_ctr: Counter[str] = Counter(e[1]["type"] for e in sspp_evs)

        pend_avg_wait = 0.0
        pend_max_wait = 0
        if pend_n > 0:
            waits = [e[1]["wait"] for e in pend_evs]
            pend_avg_wait = round(sum(waits) / len(waits), 1)
            pend_max_wait = max(waits)

        activity: dict[str, Any] = {
            "rtp_start": len(self.rtp_start_events),
            "rtp_match": len(self.rtp_match_events),
            "rtp_bye": len(self.rtp_bye_events),
            "file_open": fo_n,
            "file_close": fc_n,
            "file_avg_size_kb": file_avg_size_kb,
            "file_avg_duration": int(file_avg_dur),
            "smdr_total": smdr_n,
            "smdr_in_end": sum(1 for e in smdr_evs if e[1]["action"] == "IN_END"),
            "smdr_out_end": sum(1 for e in smdr_evs if e[1]["action"] == "OUT_END"),
            "smdr_answer": sum(1 for e in smdr_evs if e[1]["action"] == "ANSWER"),
            "smdr_ring": sum(1 for e in smdr_evs if e[1]["action"] == "RING"),
            "sspp_total": sspp_n,
            "sspp_types": dict(type_ctr.most_common()),
            "pending_count": pend_n,
            "pending_avg_wait": pend_avg_wait,
            "pending_max_wait": pend_max_wait,
        }

        # --- Grace period ---
        grace: dict[str, int] = {
            "started": len(self.grace_started_events),
            "expired": len(self.grace_expired_events),
            "resumed": len(self.grace_resumed_events),
            "terminated": len(self.grace_terminated_events),
        }

        # --- Active sessions ---
        active_sessions = list(self.active_sessions.values())

        # --- Recent mismatches (last 5) ---
        recent_mm = list(self.recent_mismatches)[-5:]

        # --- Recent thread events (last 5) ---
        recent_ev = [
            {"ts": ts.strftime("%H:%M:%S"), "msg": msg}
            for ts, msg in list(self.recent_thread_events)[-5:]
        ]

        return {
            "updated_at": now.strftime("%H:%M:%S"),
            "uptime": _fmt_uptime(uptime_s),
            "total_lines": self.total_lines,
            "last_log_ts": (
                self.last_ts.strftime("%H:%M:%S") if self.last_ts else "--:--:--"
            ),
            "current_log": self.current_log,
            "thread": {
                "rtp_count": len(self.rtp_threads),
                "rtp_indices": rtp_idxs,
                "pkt_count": len(self.pkt_threads),
                "pkt_indices": pkt_idxs,
                "active_sessions": len(self.active_sessions),
            },
            "mismatch_1h": mismatch,
            "activity_5min": activity,
            "grace": grace,
            "active_sessions": active_sessions,
            "recent_mismatches": recent_mm,
            "recent_thread_events": recent_ev,
        }
