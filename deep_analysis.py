#!/usr/bin/env python3
import re, os, sys
from collections import defaultdict, Counter
from datetime import datetime, timedelta

LOG_DIR = "D:/Work/AI_Projects/AI-LogOps/storage/logs/PC-1577/20260317"
LOG_FILES = [os.path.join(LOG_DIR, f"20260317_{i}.txt") for i in range(1, 6)]

def parse_ts(s):
    try: return datetime.strptime(s[:23], "%Y-%m-%d %H:%M:%S.%f")
    except: return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")

def ext_from_fname(fn):
    p = fn.replace(".wav","").split(".")
    return p[4] if len(p) >= 5 else ""

def bkt(d):
    if d<=5: return "0-5s"
    if d<=10: return "6-10s"
    if d<=30: return "11-30s"
    if d<=60: return "31-60s"
    if d<=120: return "61-120s"
    return "121s+"

# Build patterns at runtime to avoid escaping hell
TS = r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)\]"
PAT_IN = re.compile(TS + r".*\[SMDR\].*I:IN_END Ext:(\d+) Dnis:(\d+) DID:(\w*) Caller:(\S+) Called:(\S*) Duration:(\d+)")
PAT_OUT = re.compile(TS + r".*\[SMDR\].*O:OUT_END Ext:(\d+) Dnis:(\d+) DID:(\w*) Caller:(\S*) Called:(\S+) Duration:(\d+)")
PAT_FC = re.compile(TS + r".*\[FILE\].*CLOSE (\S+)->(\S+) (\d{6}\.\d+\.\S+\.wav) Size:(\d+) Time:(\d+)")
PAT_PE = re.compile(TS + r".*PENDING EXPIRED Key:(\S+) Status:(\d+) Caller:(\S+) Called:(\S*) Ext:(\d+) \(waited:(\d+)sec\)")
PAT_SKIP = re.compile(TS + r".*\[REC-CHECK\] \[C:(\d+)\] rtp_start:(\d+) IsOpen:(\d+) iRemainSec:(\d+) caller:'([^']*)' callee:'([^']*)' skip:1")
PAT_PCAP = re.compile(TS + r".*\[PCAP-STATS\].*queue_drop=(\d+)")
PAT_DM = re.compile(TS + r".*\[DURATION-MISMATCH\].*Ext:(\d+).*Class:(\S+)")

smdr_calls = []
file_closes = []
pending_expireds = []
rec_check_skips = []
pcap_stats_list = []
duration_mismatches = []

print("=== PHASE 1: Parsing ===")
total_lines = 0

print("=== Parsing log files ===")
total_lines = 0
for fpath in LOG_FILES:
    if not os.path.exists(fpath):
        print(f"  SKIP: {fpath}")
        continue
    lc = 0
    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            lc += 1
            m = PAT_IN.search(line)
            if m:
                ts, ext, dnis, did, caller, called, dur = m.groups()
                dur = int(dur)
                if dur > 0:
                    smdr_calls.append((parse_ts(ts), "I", ext, dnis, did, caller, called, dur))
                continue
            m = PAT_OUT.search(line)
            if m:
                ts, ext, dnis, did, caller, called, dur = m.groups()
                dur = int(dur)
                if dur > 0:
                    smdr_calls.append((parse_ts(ts), "O", ext, dnis, did, caller, called, dur))
                continue
            m = PAT_FC.search(line)
            if m:
                ts, fn, tn, fname, size, tv = m.groups()
                efn = ext_from_fname(fname)
                file_closes.append((parse_ts(ts), fn, tn, fname, int(size), int(tv), efn))
                continue
            m = PAT_PE.search(line)
            if m:
                ts, key, status, caller, called, ext, waited = m.groups()
                pending_expireds.append((parse_ts(ts), key, int(status), caller, called, ext, int(waited)))
                continue
            m = PAT_SKIP.search(line)
            if m:
                ts, ch, rtp, iso, rem, caller, callee = m.groups()
                rec_check_skips.append((parse_ts(ts), ch, int(rtp), int(iso), int(rem), caller, callee))
                continue
            m = PAT_PCAP.search(line)
            if m:
                ts, qd = m.groups()
                pcap_stats_list.append((parse_ts(ts), int(qd)))
                continue
            m = PAT_DM.search(line)
            if m:
                ts, ext, cls = m.groups()
                duration_mismatches.append((parse_ts(ts), ext, cls))
                continue
    total_lines += lc
    print(f"  {os.path.basename(fpath)}: {lc:,} lines")

ni = sum(1 for c in smdr_calls if c[1]=="I")
no = sum(1 for c in smdr_calls if c[1]=="O")
print(f"  Total: {total_lines:,} lines")
print(f"  SMDR dur>0: {len(smdr_calls):,} (I:{ni:,}, O:{no:,})")
print(f"  FILE CLOSE: {len(file_closes):,}")
print(f"  PENDING EXPIRED: {len(pending_expireds):,}")
print(f"  REC-CHECK skip:1: {len(rec_check_skips):,}")
print(f"  DURATION-MISMATCH: {len(duration_mismatches):,}")

# Phase 2: Matching
print("\n=== PHASE 2: SMDR <-> FILE CLOSE matching ===")
fc_by_num = defaultdict(list)
for fc in file_closes:
    ts, fn, tn, fname, size, tv, efn = fc
    if fn and fn != "_": fc_by_num[fn].append(fc)
    if tn and tn != "_": fc_by_num[tn].append(fc)

matched = []
unmatched = []
for call in smdr_calls:
    ts, ct, ext, dnis, did, caller, called, dur = call
    mn = caller if ct == "I" else called
    best = None; best_dt = 999999
    for fc in fc_by_num.get(mn, []):
        dt = abs((fc[0] - ts).total_seconds())
        if dt <= 120 and dt < best_dt:
            best_dt = dt; best = fc
    if not best:
        sn = called if ct == "I" else caller
        if sn:
            for fc in fc_by_num.get(sn, []):
                dt = abs((fc[0] - ts).total_seconds())
                if dt <= 120 and dt < best_dt:
                    best_dt = dt; best = fc
    if best:
        matched.append((call, best, best_dt))
    else:
        unmatched.append(call)

print(f"  MATCHED: {len(matched):,} ({len(matched)/len(smdr_calls)*100:.1f}%)")
print(f"  UNMATCHED: {len(unmatched):,} ({len(unmatched)/len(smdr_calls)*100:.1f}%)")

# SECTION A: All unmatched
print("\n" + "="*100)
print(f"=== SECTION A: ALL {len(unmatched)} UNMATCHED CALLS ===")
print("="*100)
print("Time         T  Ext    Caller           Called           DID    Dur")
print("-"*75)
for c in sorted(unmatched, key=lambda x: x[0]):
    ts, ct, ext, dnis, did, caller, called, dur = c
    print(f"{ts.strftime('%H:%M:%S'):<12} {ct}  {ext:<6} {caller:<16} {called:<16} {did:<6} {dur:>4}s")

# SECTION B: Profile
print("\n" + "="*100)
print("=== SECTION B: UNMATCHED CALL PROFILE ===")
print("="*100)

print("\n--- B1: By hour ---")
h_um = Counter(c[0].hour for c in unmatched)
h_ma = Counter(m[0][0].hour for m in matched)
print("Hr    UnM   Mat   Tot   UnM%")
for h in range(24):
    u, m2 = h_um.get(h,0), h_ma.get(h,0)
    t = u+m2
    if t: print(f"{h:02d}    {u:>4}  {m2:>4}  {t:>4}  {u/t*100:>5.1f}%")

print("\n--- B2: By extension (top 30) ---")
e_um = Counter(c[2] for c in unmatched)
e_ma = Counter(m[0][2] for m in matched)
print("Ext     UnM   Mat   Tot   UnM%")
for ext, cnt in e_um.most_common(30):
    m2 = e_ma.get(ext,0)
    t = cnt+m2
    print(f"{ext:<7} {cnt:>4}  {m2:>4}  {t:>4}  {cnt/t*100:>5.1f}%")

print("\n--- B3: By DID ---")
d_um = Counter(c[4] or "(none)" for c in unmatched)
for d, cnt in d_um.most_common(15): print(f"  {d}: {cnt}")

print("\n--- B4: By duration bucket ---")
b_um = Counter(bkt(c[7]) for c in unmatched)
b_ma = Counter(bkt(m[0][7]) for m in matched)
for b in ["0-5s","6-10s","11-30s","31-60s","61-120s","121s+"]:
    u, m2 = b_um.get(b,0), b_ma.get(b,0)
    t = u+m2
    pct = u/t*100 if t else 0
    print(f"  {b:<10} UnM:{u:>4} Mat:{m2:>5} UnM%:{pct:.1f}%")

print("\n--- B5: By call type ---")
for ct in ["I","O"]:
    u = sum(1 for c in unmatched if c[1]==ct)
    m2 = sum(1 for m in matched if m[0][1]==ct)
    t = u+m2
    if t: print(f"  {ct}: UnM={u}, Mat={m2}, UnM%={u/t*100:.1f}%")

print("\n--- B6: Avg duration ---")
if unmatched:
    du = sorted(c[7] for c in unmatched)
    print(f"  Unmatched: mean={sum(du)/len(du):.1f}s, median={du[len(du)//2]}s, min={du[0]}s, max={du[-1]}s")
if matched:
    dm = sorted(m[0][7] for m in matched)
    print(f"  Matched:   mean={sum(dm)/len(dm):.1f}s, median={dm[len(dm)//2]}s")

# SECTION C: Problem extensions
print("\n" + "="*100)
print("=== SECTION C: PROBLEM EXTENSION DEEP DIVE ===")
print("="*100)
prob_exts = ["3279","3216","3446","3340","3243","3272","3311","3225"]
um_by_ext = defaultdict(list)
for c in unmatched: um_by_ext[c[2]].append(c)
ma_by_ext = defaultdict(list)
for m in matched: ma_by_ext[m[0][2]].append(m)
skip_nums = set()
for s in rec_check_skips:
    if s[5]: skip_nums.add(s[5])
    if s[6]: skip_nums.add(s[6])
for ext in prob_exts:
    uc = um_by_ext.get(ext,[])
    mc = ma_by_ext.get(ext,[])
    t = len(uc)+len(mc)
    print(f"\n{'='*70}")
    if t == 0: print(f"  Ext {ext}: No calls"); continue
    print(f"  Ext {ext}: Total={t}, Matched={len(mc)}, UNMATCHED={len(uc)} ({len(uc)/t*100:.1f}%)")
    if not uc: print("  All recorded."); continue
    hc = Counter(c[0].hour for c in uc)
    print("  Hourly: " + ", ".join(f"{h:02d}h:{hc[h]}" for h in sorted(hc)))
    cc = Counter((c[5] if c[1]=="I" else c[6]) for c in uc)
    print("  Top callers: " + ", ".join(f"{n}({cnt})" for n,cnt in cc.most_common(5)))
    dc = Counter(c[4] or "(none)" for c in uc)
    print("  DIDs: " + ", ".join(f"{d}({cnt})" for d,cnt in dc.most_common(5)))
    sk = sum(1 for c in uc if (c[5] if c[1]=="I" else c[6]) in skip_nums)
    print(f"  REC-CHECK skip overlap: {sk}/{len(uc)}")
    durs = [c[7] for c in uc]
    print(f"  Duration: min={min(durs)}s max={max(durs)}s mean={sum(durs)/len(durs):.1f}s")
    print("  Calls:")
    for c in sorted(uc, key=lambda x: x[0])[:20]:
        ts, ct2, _, dnis, did, caller, called, dur = c
        print(f"    {ts.strftime('%H:%M:%S')} {ct2} Caller:{caller} Called:{called} DID:{did} Dur:{dur}s")
    if len(uc)>20: print(f"    ...+{len(uc)-20} more")

# SECTION D: 3648 PENDING EXPIRED
print("\n" + "="*100)
print("=== SECTION D: EXT 3648 PENDING EXPIRED ===")
print("="*100)
pe_3648 = [p for p in pending_expireds if p[5]=="3648"]
print(f"  Total PE: {len(pending_expireds)}, 3648: {len(pe_3648)} ({len(pe_3648)/len(pending_expireds)*100:.1f}%)")
sc = Counter(p[2] for p in pe_3648)
print("  Status: " + ", ".join(f"S{s}:{cnt}" for s,cnt in sc.most_common()))
hpe = Counter(p[0].hour for p in pe_3648)
print("  Hourly: " + ", ".join(f"{h:02d}h:{hpe[h]}" for h in sorted(hpe)))
print(f"  Status:1 (ring pending)={sum(1 for p in pe_3648 if p[2]==1)} -> Queue cleanup")
print(f"  Status:3 (IA/A applied)={sum(1 for p in pe_3648 if p[2]==3)} -> Recording likely exists")
print(f"  Status:5 (completed)={sum(1 for p in pe_3648 if p[2]==5)}")
fc_nums = set()
for fc in file_closes:
    if fc[1] and fc[1]!="_": fc_nums.add(fc[1])
    if fc[2] and fc[2]!="_": fc_nums.add(fc[2])
pe_c3648 = set(p[3] for p in pe_3648)
wr = pe_c3648 & fc_nums
print(f"  Unique callers: {len(pe_c3648)}, with rec: {len(wr)} ({len(wr)/len(pe_c3648)*100:.1f}%), without: {len(pe_c3648-fc_nums)}")

# SECTION E: 22-23h
print("\n" + "="*100)
print("=== SECTION E: 22:00-23:59 DEEP DIVE ===")
print("="*100)
lu = [c for c in unmatched if c[0].hour >= 22]
lm = [m for m in matched if m[0][0].hour >= 22]
lt = len(lu)+len(lm)
if lt: print(f"  22-23h: UnM={len(lu)}, Mat={len(lm)}, Rate={len(lu)/lt*100:.1f}%")
ec = Counter(c[2] for c in lu)
print("  By ext: " + ", ".join(f"{e}({cnt})" for e,cnt in ec.most_common(15)))
print("  10-min buckets:")
bu = Counter(f"{c[0].hour:02d}:{(c[0].minute//10)*10:02d}" for c in lu)
bm = Counter(f"{m[0][0].hour:02d}:{(m[0][0].minute//10)*10:02d}" for m in lm)
for b in sorted(set(list(bu.keys())+list(bm.keys()))):
    u, m2 = bu.get(b,0), bm.get(b,0)
    t = u+m2
    if t: print(f"    {b} UnM:{u:>3} Mat:{m2:>3} Rate:{u/t*100:.1f}%")
print(f"\n  All {len(lu)} unmatched 22-23h:")
for c in sorted(lu, key=lambda x: x[0]):
    ts, ct, ext, dnis, did, caller, called, dur = c
    print(f"    {ts.strftime('%H:%M:%S')} {ct} Ext:{ext} Caller:{caller} Called:{called} DID:{did} Dur:{dur}s")

# SECTION F: REC-CHECK skip:1
print("\n" + "="*100)
print(f"=== SECTION F: REC-CHECK skip:1 ({len(rec_check_skips)} events) ===")
print("="*100)
for s in sorted(rec_check_skips, key=lambda x: x[0]):
    ts, ch, rtp, iso, rem, caller, callee = s
    print(f"  {ts.strftime('%H:%M:%S')} C:{ch} IsOpen:{iso} Remain:{rem}s caller:{caller or '-'} callee:{callee or '-'}")
print("  Callee dist: " + ", ".join(f"{c}({n})" for c,n in Counter(s[6] or "-" for s in rec_check_skips).most_common()))
print("  iRemainSec: " + ", ".join(f"{r}s:{n}" for r,n in sorted(Counter(s[4] for s in rec_check_skips).items())))
print("  IsOpen: " + ", ".join(f"{o}:{n}" for o,n in Counter(s[3] for s in rec_check_skips).items()))

# SECTION G: PE vs Unmatched
print("\n" + "="*100)
print("=== SECTION G: PENDING EXPIRED <-> UNMATCHED ===")
print("="*100)
pe_all_c = set(p[3] for p in pending_expireds)
um_w_pe = sum(1 for c in unmatched if (c[5] if c[1]=="I" else c[6]) in pe_all_c)
print(f"  Unmatched with PE for same caller: {um_w_pe}/{len(unmatched)}")
print(f"  Unmatched without PE: {len(unmatched)-um_w_pe}")
pe_w_r = sum(1 for p in pending_expireds if p[3] in fc_nums)
print(f"  PE with recording: {pe_w_r}/{len(pending_expireds)}")
print(f"  PE without recording: {len(pending_expireds)-pe_w_r}")
pe_no_ext = Counter(p[5] for p in pending_expireds if p[3] not in fc_nums)
print("  PE w/o rec by ext: " + ", ".join(f"{e}({n})" for e,n in pe_no_ext.most_common(10)))

# SECTION H: System Events
print("\n" + "="*100)
print("=== SECTION H: SYSTEM EVENTS ===")
print("="*100)
if pcap_stats_list:
    sp = sorted(pcap_stats_list, key=lambda x: x[0])
    print(f"  PCAP queue drops: {sp[0][1]:,} -> {sp[-1][1]:,} (delta={sp[-1][1]-sp[0][1]:,})")
    prev = sp[0]; bj = []
    for p in sp[1:]:
        d = p[1]-prev[1]; e = (p[0]-prev[0]).total_seconds()
        if e>0 and d/e>50: bj.append((p[0],d,e,d/e))
        prev = p
    if bj:
        print(f"  High drop rate ({len(bj)} periods):")
        for ts,d,e,r in sorted(bj,key=lambda x:-x[3])[:15]:
            print(f"    {ts.strftime('%H:%M:%S')} delta={d:,} in {e:.0f}s ({r:.0f}/sec)")
print(f"\n  DURATION-MISMATCH: {len(duration_mismatches)}")
if duration_mismatches:
    cc2 = Counter(d[2] for d in duration_mismatches)
    print("  Classes: " + ", ".join(f"{c}({n})" for c,n in cc2.most_common()))
    ec2 = Counter(d[1] for d in duration_mismatches)
    print("  Top exts: " + ", ".join(f"{e}({n})" for e,n in ec2.most_common(10)))

# FINAL SUMMARY
print("\n" + "="*100)
print("=== FINAL FORENSIC SUMMARY ===")
print("="*100)
print(f"SMDR dur>0: {len(smdr_calls):,} (I:{ni:,} O:{no:,})")
print(f"FILE CLOSE: {len(file_closes):,}")
print(f"MATCHED: {len(matched):,} ({len(matched)/len(smdr_calls)*100:.1f}%)")
print(f"UNMATCHED: {len(unmatched):,} ({len(unmatched)/len(smdr_calls)*100:.1f}%)")
print("\nTop unmatched exts:")
for ext, cnt in e_um.most_common(10):
    m2 = e_ma.get(ext,0); t=cnt+m2
    print(f"  {ext}: {cnt}/{t} ({cnt/t*100:.1f}%)")
print(f"\nREC-CHECK skip:1: {len(rec_check_skips)}")
print(f"PENDING EXPIRED: {len(pending_expireds)} (3648: {len(pe_3648)})")
if lt: print(f"22-23h unmatch: {len(lu)}/{lt} ({len(lu)/lt*100:.1f}%)")
if pcap_stats_list: print(f"PCAP queue drops: {pcap_stats_list[-1][1]:,}")
print(f"DURATION-MISMATCH: {len(duration_mismatches)}")
print("\nDone.")

