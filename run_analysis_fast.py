"""Fast analysis: basic stats only, skip heavy SMDR-FC matching."""
import os, sys, json, re, traceback
from pathlib import Path
from collections import defaultdict

os.chdir(r"D:\Work\AI_Projects\AI-LogOps")

result_file = r"D:\Work\AI_Projects\AI-LogOps\analysis_result.json"
error_file = r"D:\Work\AI_Projects\AI-LogOps\analysis_error.txt"

# Patterns
RE_TS = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+\]")
RE_VERSION = re.compile(r"AirRecorder\s+v([\d.]+)")
RE_INIT = re.compile(r"InitInstance")
RE_BUILD = re.compile(r"Build:\s*(.+?)(?:\s*\)|\s*$)")
RE_INBOUND = re.compile(r"\bI:IN_END\b")
RE_OUTBOUND = re.compile(r"\bO:OUT_END\b")
RE_TRUNK_OB = re.compile(r"O:OUT_END.*Dnis:850[1-9]")
RE_DB_FAIL = re.compile(r"DB_UPDATE FAIL")
RE_DB_FAIL_3301 = re.compile(r"DB_UPDATE FAIL.*3301")
RE_DIR_FILTER = re.compile(r"DirFilter:(\d)")
RE_OB_RESET = re.compile(r"OUTBOUND-RESET")
RE_DUR_MISMATCH = re.compile(r"\[DURATION-MISMATCH\]")
RE_SESS_INVAL = re.compile(r"Session invalidated|Aborted.*session")
RE_SSPP_LOSS = re.compile(r"Idx:\s*-1")
RE_NORM_FAIL = re.compile(r"no match.*candidates")
RE_SMDR_EVENT = re.compile(r"\[SMDR\]\s+\[\d+\]\s+(\w+):(\w+)\s+Ext:(\S+).*?Duration:(\d+)")
RE_FILE_CLOSE = re.compile(r"\[FILE\]\s+\[C:(\d+)\]\s+CLOSE\s+\S+\s+\S+\.wav\s+Size:(\d+)\s+Time:(\d+)")

try:
    log_path = Path(r"D:\Work\AI_Projects\AI-LogOps\storage\logs\PC-DAERIGO\20260324")
    files = sorted(log_path.glob("*.txt"))

    total_lines = 0
    first_ts = last_ts = None
    inbound = outbound = trunk_ob = 0
    db_fail = ob_reset = dur_mismatch = sess_inval = sspp_loss = norm_fail = 0
    db_fail_3301 = 0
    smdr_calls = 0
    file_close_count = 0
    hourly_in = defaultdict(int)
    hourly_out = defaultdict(int)
    hourly_dbf = defaultdict(int)
    dir_filter = defaultdict(int)
    versions = []
    cur_version = ""
    cur_build = ""
    cur_seg_start = ""
    init_count = 0

    for fpath in files:
        with open(fpath, encoding="cp949", errors="replace") as f:
            for line in f:
                total_lines += 1
                line = line.rstrip("\n\r")
                if not line:
                    continue

                m_ts = RE_TS.match(line)
                ts = m_ts.group(1) if m_ts else None
                if ts:
                    if first_ts is None:
                        first_ts = ts
                    last_ts = ts

                if RE_INIT.search(line):
                    init_count += 1
                    if cur_seg_start and cur_version:
                        versions.append({"version": cur_version, "build": cur_build, "start": cur_seg_start, "end": last_ts or ""})
                    cur_seg_start = ts or ""
                    cur_version = ""
                    cur_build = ""
                    continue

                m = RE_VERSION.search(line)
                if m:
                    cur_version = m.group(1)
                    continue

                m = RE_BUILD.search(line)
                if m:
                    cur_build = m.group(1)
                    continue

                if RE_INBOUND.search(line):
                    inbound += 1
                    m = RE_DIR_FILTER.search(line)
                    if m: dir_filter[int(m.group(1))] += 1
                    if ts: hourly_in[int(ts[11:13])] += 1
                    m = RE_SMDR_EVENT.search(line)
                    if m and int(m.group(4)) > 0:
                        smdr_calls += 1
                    continue

                if RE_OUTBOUND.search(line):
                    outbound += 1
                    if RE_TRUNK_OB.search(line):
                        trunk_ob += 1
                    m = RE_DIR_FILTER.search(line)
                    if m: dir_filter[int(m.group(1))] += 1
                    if ts: hourly_out[int(ts[11:13])] += 1
                    m = RE_SMDR_EVENT.search(line)
                    if m and int(m.group(4)) > 0:
                        smdr_calls += 1
                    continue

                if RE_DB_FAIL.search(line):
                    db_fail += 1
                    if RE_DB_FAIL_3301.search(line):
                        db_fail_3301 += 1
                    if ts: hourly_dbf[int(ts[11:13])] += 1
                    continue

                if RE_OB_RESET.search(line):
                    ob_reset += 1
                    continue

                m_fc = RE_FILE_CLOSE.search(line)
                if m_fc and int(m_fc.group(2)) > 0:
                    file_close_count += 1
                    continue

                if RE_DUR_MISMATCH.search(line):
                    dur_mismatch += 1
                    continue

                if RE_SESS_INVAL.search(line):
                    sess_inval += 1
                    continue

                if RE_SSPP_LOSS.search(line):
                    sspp_loss += 1
                    continue

                if RE_NORM_FAIL.search(line):
                    norm_fail += 1
                    continue

    # Finalize last version segment
    if cur_seg_start and cur_version:
        versions.append({"version": cur_version, "build": cur_build, "start": cur_seg_start, "end": last_ts or ""})

    crash_count = 0
    for i in range(1, len(versions)):
        if versions[i-1]["version"] == versions[i]["version"]:
            crash_count += 1

    output = {
        "total_lines": total_lines,
        "analysis_period": [first_ts or "", last_ts or ""],
        "version_segments": versions,
        "inbound_count": inbound,
        "outbound_count": outbound,
        "trunk_outbound_count": trunk_ob,
        "smdr_call_count": smdr_calls,
        "file_close_count": file_close_count,
        "db_fail_count": db_fail,
        "db_fail_3301_count": db_fail_3301,
        "outbound_reset_count": ob_reset,
        "duration_mismatch_count": dur_mismatch,
        "session_invalidated_count": sess_inval,
        "sspp_loss_count": sspp_loss,
        "norm_failure_count": norm_fail,
        "crash_count": crash_count,
        "init_count": init_count,
        "recording_rate": f"{file_close_count}/{smdr_calls}" if smdr_calls else "N/A",
        "dir_filter_dist": dict(sorted(dir_filter.items())),
        "hourly_inbound": {str(h): hourly_in[h] for h in range(24)},
        "hourly_outbound": {str(h): hourly_out[h] for h in range(24)},
        "hourly_db_fail": {str(h): hourly_dbf[h] for h in range(24)},
    }

    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # Signal completion
    with open(r"D:\Work\AI_Projects\AI-LogOps\analysis_done.flag", "w") as f:
        f.write("DONE")

except Exception:
    with open(error_file, "w", encoding="utf-8") as f:
        f.write(traceback.format_exc())
