#!/usr/bin/env python3
"""
v2026.3.28.1 RTP-First Safety Net - Effect Analysis Script

Analyzes AirREC logs to measure the impact of the 4-phase RTP-First changes:
  Phase 1: ZOMBIE timer fix (first_packet_time = time(NULL))
  Phase 2: RTP claim release (15s timeout) + orphan timeout extension
  Phase 3: BYE-resume detection (orphan recovery after BYE-grace)
  Phase 4: SMDR orphan match (QUEUE SKIP recovery)

Usage:
  python analyze_rtp_first.py <site> <date>
  python analyze_rtp_first.py PC-DAERIGO 20260328
  python analyze_rtp_first.py PC-DAERIGO 20260327 20260328   # compare two dates
"""
import re, os, sys
from pathlib import Path
from collections import defaultdict, OrderedDict
from datetime import datetime

STORAGE = Path(r"D:\Work\AI_Projects\AI-LogOps\storage\logs")

# ──────────────────────────────────────────────────────────────
# Regex patterns
# ──────────────────────────────────────────────────────────────
RE_TS = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+\]")

# --- Phase 1: ZOMBIE ---
RE_ZOMBIE = re.compile(r"\[SMDR-ZOMBIE\].*\[C:(\d+)\].*Timeout \((\d+)s\).*CID:(\S+)->(\S+).*Ext:(\S+)")

# --- Phase 2: RTP Claim Release ---
RE_CLAIM_RELEASE = re.compile(r"\[RTP-CLAIM-RELEASE\].*\[C:(\d+)\].*CID:(\S+)->(\S+).*Ext:(\S+).*rtp:(\S+):(\d+).*SmdrLocked:(\d)")

# --- Phase 3: BYE-Resume ---
RE_BYE_REMEMBER = re.compile(r"\[BYE-REMEMBER\].*\[C:(\d+)\].*Ext:(\S+)")
RE_BYE_RESUME = re.compile(r"\[ORPHAN-RTP\].*\[C:(\d+)\].*BYE-resume detected.*caller:(\S+).*Ext:(\S+)")

# --- Phase 4: SMDR Orphan Match ---
RE_SMDR_ORPHAN = re.compile(r"\[SMDR-ORPHAN-MATCH\].*\[C:(\d+)\].*Key:(\S+).*Ext:(\S+).*Dur:(\d+)")

# --- Existing tags (comparison baseline) ---
RE_SILENT = re.compile(r"\[SILENT-RECORDING\].*\[C:(\d+)\].*Size:(\d+).*Time:(\d+)")
RE_NO_TYPE5 = re.compile(r"\[NO-TYPE5\].*\[C:(\d+)\].*Ext:(\S+).*Duration:(\d+)")
RE_QUEUE_SKIP_NR = re.compile(r"QUEUE SKIP I Key:(\S+) \(IA/A was applied, no recording\)")
RE_ORPHAN_CREATED = re.compile(r"\[ORPHAN-RTP\].*\[C:(\d+)\].*Created.*Ext:(\S+)")
RE_ORPHAN_TIMEOUT = re.compile(r"\[ORPHAN-RTP\].*\[C:(\d+)\].*Timeout \((\d+)s\).*Ext:(\S+)")
RE_ORPHAN_MATCHED = re.compile(r"\[ORPHAN-MATCH\].*Key:(\S+).*\[C:(\d+)\].*Ext:(\S+)")
RE_PENDING_APPLIED = re.compile(r"\[SMDR\].*\[C:(\d+)\].*PENDING APPLIED.*Key:(\S+).*Ext:(\S+).*\(waited:(\d+)sec\)")

# --- Call volume (for ratio calculation) ---
RE_IN_END = re.compile(r"\bI:IN_END\b.*Duration:(\d+)")
RE_OUT_END = re.compile(r"\bO:OUT_END\b.*Duration:(\d+)")

# --- Version detection ---
RE_VERSION = re.compile(r"AirRecorder\s+v([\d.]+)")


class DayStats:
    """Stats for one day of logs."""

    def __init__(self, site: str, date: str):
        self.site = site
        self.date = date
        self.version = ""
        self.total_lines = 0

        # Call volume
        self.inbound = 0
        self.outbound = 0
        self.hourly_calls = defaultdict(int)

        # Phase 1: ZOMBIE
        self.zombie_releases = []       # (ts, ch, elapsed, caller, callee, ext)

        # Phase 2: RTP Claim Release
        self.claim_releases = []        # (ts, ch, caller, callee, ext, rtp_addr, rtp_port, locked)

        # Phase 3: BYE-Resume
        self.bye_remembers = []         # (ts, ch, ext)
        self.bye_resumes = []           # (ts, ch, caller, ext)

        # Phase 4: SMDR Orphan Match
        self.smdr_orphan_matches = []   # (ts, ch, key, ext, dur)

        # Existing tags (comparison)
        self.silent_recordings = []     # (ts, ch, size, time)
        self.no_type5 = []              # (ts, ch, ext, dur)
        self.queue_skip_nr = []         # (ts, key)
        self.orphan_created = []        # (ts, ch, ext)
        self.orphan_timeout = []        # (ts, ch, elapsed, ext)
        self.orphan_matched = []        # (ts, key, ch, ext)
        self.pending_applied = []       # (ts, ch, key, ext, waited)

    @property
    def total_calls(self):
        return self.inbound + self.outbound

    @property
    def missed_recording_count(self):
        """Estimated missed recordings = SILENT + NO-TYPE5 + QUEUE_SKIP_NR (excluding recovered)."""
        return len(self.silent_recordings) + len(self.no_type5) + len(self.queue_skip_nr)

    @property
    def recovered_count(self):
        """Recordings recovered by Phase 2-4 mechanisms."""
        return len(self.smdr_orphan_matches) + len(self.bye_resumes)


def parse_day(site: str, date: str) -> DayStats:
    """Parse all log files for a given site/date."""
    log_dir = STORAGE / site / date
    if not log_dir.exists():
        print(f"  ERROR: {log_dir} does not exist")
        sys.exit(1)

    files = sorted(log_dir.glob("*.txt"))
    if not files:
        print(f"  ERROR: No .txt files in {log_dir}")
        sys.exit(1)

    stats = DayStats(site, date)
    print(f"  Parsing {len(files)} files from {log_dir} ...")

    for fpath in files:
        with open(fpath, encoding="cp949", errors="replace") as f:
            for line in f:
                stats.total_lines += 1
                line = line.rstrip("\n\r")
                if not line:
                    continue

                # Timestamp extraction
                m_ts = RE_TS.match(line)
                ts = m_ts.group(1) if m_ts else ""

                # Hour extraction for hourly distribution
                if ts:
                    hour = ts[11:13]

                # Version detection
                m = RE_VERSION.search(line)
                if m:
                    stats.version = m.group(1)
                    continue

                # --- Call volume ---
                m = RE_IN_END.search(line)
                if m:
                    stats.inbound += 1
                    if ts:
                        stats.hourly_calls[hour] += 1
                    continue
                m = RE_OUT_END.search(line)
                if m:
                    stats.outbound += 1
                    if ts:
                        stats.hourly_calls[hour] += 1
                    continue

                # --- Phase 1: ZOMBIE ---
                m = RE_ZOMBIE.search(line)
                if m:
                    ch, elapsed, caller, callee, ext = m.groups()
                    stats.zombie_releases.append((ts, ch, int(elapsed), caller, callee, ext))
                    continue

                # --- Phase 2: RTP Claim Release ---
                m = RE_CLAIM_RELEASE.search(line)
                if m:
                    ch, caller, callee, ext, rtp_addr, rtp_port, locked = m.groups()
                    stats.claim_releases.append((ts, ch, caller, callee, ext, rtp_addr, rtp_port, locked))
                    continue

                # --- Phase 3: BYE-Resume ---
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

                # --- Phase 4: SMDR Orphan Match ---
                m = RE_SMDR_ORPHAN.search(line)
                if m:
                    ch, key, ext, dur = m.groups()
                    stats.smdr_orphan_matches.append((ts, ch, key, ext, int(dur)))
                    continue

                # --- Existing tags ---
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
                    key = m.group(1)
                    stats.queue_skip_nr.append((ts, key))
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


def print_report(stats: DayStats):
    """Print analysis report for a single day."""
    print(f"\n{'='*72}")
    print(f"  RTP-First Effect Analysis: {stats.site} / {stats.date}")
    print(f"  Version: {stats.version or 'unknown'}")
    print(f"  Lines: {stats.total_lines:,}  |  Calls: {stats.total_calls:,} "
          f"(IN:{stats.inbound} OUT:{stats.outbound})")
    print(f"{'='*72}")

    # ── Phase 1: ZOMBIE ──
    print(f"\n[Phase 1] ZOMBIE Timer Fix (first_packet_time=time(NULL))")
    print(f"  [SMDR-ZOMBIE] channel releases: {len(stats.zombie_releases)}")
    if stats.zombie_releases:
        for ts, ch, elapsed, caller, callee, ext in stats.zombie_releases[:10]:
            print(f"    {ts} C:{ch} {elapsed}s {caller}->{callee} Ext:{ext}")
        if len(stats.zombie_releases) > 10:
            print(f"    ... and {len(stats.zombie_releases)-10} more")

    # ── Phase 2: RTP Claim Release ──
    print(f"\n[Phase 2] RTP Claim Release (15s SSPP timeout)")
    print(f"  [RTP-CLAIM-RELEASE] events: {len(stats.claim_releases)}")
    if stats.claim_releases:
        locked_cnt = sum(1 for x in stats.claim_releases if x[7] == "1")
        unlocked_cnt = len(stats.claim_releases) - locked_cnt
        print(f"    SmdrLocked=1: {locked_cnt}  |  SmdrLocked=0: {unlocked_cnt}")
        for ts, ch, caller, callee, ext, rtp_addr, rtp_port, locked in stats.claim_releases[:10]:
            print(f"    {ts} C:{ch} {caller}->{callee} Ext:{ext} rtp:{rtp_addr}:{rtp_port} locked:{locked}")
        if len(stats.claim_releases) > 10:
            print(f"    ... and {len(stats.claim_releases)-10} more")

    # ── Phase 3: BYE-Resume ──
    print(f"\n[Phase 3] BYE-Grace Resume Detection")
    print(f"  [BYE-REMEMBER] sessions stored: {len(stats.bye_remembers)}")
    print(f"  [ORPHAN-RTP BYE-resume] detections: {len(stats.bye_resumes)}")
    if stats.bye_resumes:
        for ts, ch, caller, ext in stats.bye_resumes[:10]:
            print(f"    {ts} C:{ch} caller:{caller} Ext:{ext}")

    # ── Phase 4: SMDR Orphan Match ──
    print(f"\n[Phase 4] SMDR Orphan Match (QUEUE SKIP recovery)")
    print(f"  [SMDR-ORPHAN-MATCH] recoveries: {len(stats.smdr_orphan_matches)}")
    if stats.smdr_orphan_matches:
        for ts, ch, key, ext, dur in stats.smdr_orphan_matches[:10]:
            print(f"    {ts} C:{ch} Key:{key} Ext:{ext} Dur:{dur}s")

    # ── Existing tags (comparison baseline) ──
    print(f"\n{'─'*72}")
    print(f"  Comparison Baseline (existing tags)")
    print(f"{'─'*72}")
    print(f"  [SILENT-RECORDING]     : {len(stats.silent_recordings):>5}")
    print(f"  [NO-TYPE5]             : {len(stats.no_type5):>5}")
    print(f"  QUEUE SKIP (no rec)    : {len(stats.queue_skip_nr):>5}")
    print(f"  PENDING APPLIED        : {len(stats.pending_applied):>5}")
    print(f"  [ORPHAN-RTP] Created   : {len(stats.orphan_created):>5}")
    print(f"  [ORPHAN-RTP] Timeout   : {len(stats.orphan_timeout):>5}")
    print(f"  [ORPHAN-MATCH]         : {len(stats.orphan_matched):>5}")

    # ── Summary ──
    print(f"\n{'='*72}")
    print(f"  SUMMARY")
    print(f"{'='*72}")
    missed = stats.missed_recording_count
    recovered = stats.recovered_count
    net_missed = max(0, missed - recovered)
    pct = (missed / stats.total_calls * 100) if stats.total_calls > 0 else 0
    net_pct = (net_missed / stats.total_calls * 100) if stats.total_calls > 0 else 0

    print(f"  Total calls           : {stats.total_calls:>5}")
    print(f"  Missed (raw)          : {missed:>5}  ({pct:.2f}%)")
    print(f"  Recovered by Phase2-4 : {recovered:>5}")
    print(f"  Missed (net)          : {net_missed:>5}  ({net_pct:.2f}%)")
    print()

    # Phase activation summary
    active = []
    if stats.zombie_releases:
        active.append("P1:ZOMBIE")
    if stats.claim_releases:
        active.append("P2:CLAIM-RELEASE")
    if stats.bye_resumes:
        active.append("P3:BYE-RESUME")
    if stats.smdr_orphan_matches:
        active.append("P4:SMDR-ORPHAN")
    if active:
        print(f"  Active phases: {', '.join(active)}")
    else:
        print(f"  Active phases: NONE (v2026.3.28.1 not deployed or no trigger events)")


def print_comparison(before: DayStats, after: DayStats):
    """Print side-by-side comparison of two days."""
    print(f"\n{'='*72}")
    print(f"  BEFORE vs AFTER Comparison")
    print(f"  Before: {before.site}/{before.date} (v{before.version or '?'})")
    print(f"  After : {after.site}/{after.date} (v{after.version or '?'})")
    print(f"{'='*72}")

    def delta(a, b):
        d = b - a
        if d == 0:
            return "  ="
        return f" {d:+d}"

    rows = [
        ("Total calls",           before.total_calls,           after.total_calls),
        ("",                      None,                         None),
        ("SILENT-RECORDING",      len(before.silent_recordings),len(after.silent_recordings)),
        ("NO-TYPE5",              len(before.no_type5),         len(after.no_type5)),
        ("QUEUE SKIP (no rec)",   len(before.queue_skip_nr),    len(after.queue_skip_nr)),
        ("Missed total",          before.missed_recording_count,after.missed_recording_count),
        ("",                      None,                         None),
        ("ORPHAN-RTP Created",    len(before.orphan_created),   len(after.orphan_created)),
        ("ORPHAN-MATCH",          len(before.orphan_matched),   len(after.orphan_matched)),
        ("ORPHAN-RTP Timeout",    len(before.orphan_timeout),   len(after.orphan_timeout)),
        ("PENDING APPLIED",       len(before.pending_applied),  len(after.pending_applied)),
        ("",                      None,                         None),
        ("P1: ZOMBIE releases",   len(before.zombie_releases),  len(after.zombie_releases)),
        ("P2: RTP-CLAIM-RELEASE", len(before.claim_releases),   len(after.claim_releases)),
        ("P3: BYE-REMEMBER",      len(before.bye_remembers),    len(after.bye_remembers)),
        ("P3: BYE-RESUME",        len(before.bye_resumes),      len(after.bye_resumes)),
        ("P4: SMDR-ORPHAN-MATCH", len(before.smdr_orphan_matches), len(after.smdr_orphan_matches)),
        ("",                      None,                         None),
        ("Recovered (P2-4)",      before.recovered_count,       after.recovered_count),
        ("Net missed",            before.missed_recording_count - before.recovered_count,
                                  after.missed_recording_count - after.recovered_count),
    ]

    print(f"\n  {'Metric':<25} {'Before':>8} {'After':>8} {'Delta':>8}")
    print(f"  {'─'*25} {'─'*8:>8} {'─'*8:>8} {'─'*8:>8}")
    for label, bv, av in rows:
        if bv is None:
            print()
            continue
        d = delta(bv, av)
        print(f"  {label:<25} {bv:>8} {av:>8} {d:>8}")

    # Hourly comparison
    print(f"\n  Hourly Call Distribution:")
    print(f"  {'Hour':<6} {'Before':>8} {'After':>8}")
    print(f"  {'─'*6} {'─'*8:>8} {'─'*8:>8}")
    all_hours = sorted(set(list(before.hourly_calls.keys()) + list(after.hourly_calls.keys())))
    for h in all_hours:
        bv = before.hourly_calls.get(h, 0)
        av = after.hourly_calls.get(h, 0)
        print(f"  {h}:00  {bv:>8} {av:>8}")


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        print("\nAvailable sites:")
        if STORAGE.exists():
            for d in sorted(STORAGE.iterdir()):
                if d.is_dir() and not d.name.startswith("."):
                    dates = sorted([x.name for x in d.iterdir() if x.is_dir()])
                    if dates:
                        print(f"  {d.name}: {dates[0]} ~ {dates[-1]} ({len(dates)} days)")
        sys.exit(1)

    site = sys.argv[1]
    date1 = sys.argv[2]
    date2 = sys.argv[3] if len(sys.argv) > 3 else None

    print(f"RTP-First Safety Net Effect Analysis")
    print(f"Site: {site}")

    stats1 = parse_day(site, date1)
    print_report(stats1)

    if date2:
        stats2 = parse_day(site, date2)
        print_report(stats2)
        print_comparison(stats1, stats2)

    # SILENT detail (always useful for diagnosis)
    if stats1.silent_recordings:
        print(f"\n{'─'*72}")
        print(f"  SILENT-RECORDING Detail ({stats1.date})")
        print(f"{'─'*72}")
        for ts, ch, size, time_ in stats1.silent_recordings:
            print(f"  {ts} C:{ch} Size:{size} Time:{time_}s")

    if date2 and stats2.silent_recordings:
        print(f"\n{'─'*72}")
        print(f"  SILENT-RECORDING Detail ({stats2.date})")
        print(f"{'─'*72}")
        for ts, ch, size, time_ in stats2.silent_recordings:
            print(f"  {ts} C:{ch} Size:{size} Time:{time_}s")


if __name__ == "__main__":
    main()
