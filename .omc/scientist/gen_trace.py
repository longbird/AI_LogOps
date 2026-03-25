import os, re, csv
from collections import defaultdict

LOG_DIR = r"D:/Work/AI_Projects/AI-LogOps/storage/logs/PC-DAERIGO/20260317"
OUT_TSV = os.path.join(LOG_DIR, "PC-DAERIGO_20260317_Unrecorded_LogTrace.tsv")
OUT_MD  = os.path.join(LOG_DIR, "PC-DAERIGO_20260317_Unrecorded_LogTrace.md")

CALLS = [
    {"no":1, "caller":"01048164519", "ext":"3356", "did":"4720", "time":"07:02", "dur":"28"},
    {"no":2, "caller":"01024178617", "ext":"3381", "did":"4720", "time":"07:03", "dur":"10"},
    {"no":3, "caller":"01033426727", "ext":"3394", "did":"4720", "time":"14:18", "dur":"8"},
    {"no":4, "caller":"01029064352", "ext":"3394", "did":"4720", "time":"14:18", "dur":"43"},
    {"no":5, "caller":"**", "ext":"3368", "did":"4720", "time":"15:37", "dur":"53"},
    {"no":6, "caller":"01037343669", "ext":"3312", "did":"4720", "time":"20:18", "dur":"218"},
    {"no":7, "caller":"01099942648", "ext":"3356", "did":"4720", "time":"21:10", "dur":"267"},
    {"no":8, "caller":"01071943282", "ext":"3391", "did":"4720", "time":"21:20", "dur":"78"},
    {"no":9, "caller":"01098985173", "ext":"3374", "did":"4720", "time":"21:23", "dur":"35"},
    {"no":10, "caller":"01038267480", "ext":"3358", "did":"4720", "time":"21:40", "dur":"252"},
    {"no":11, "caller":"01034786009", "ext":"3312", "did":"4720", "time":"22:30", "dur":"259"},
    {"no":12, "caller":"01064716714", "ext":"3357", "did":"4720", "time":"22:42", "dur":"62"},
]
CAUSES = {
    1: "AirRecorder 재시작 (07:03:14) - SMDR IR 수신 전 프로세스 종료",
    2: "AirRecorder 재시작 (07:03:14) - SMDR IR 수신 전 프로세스 종료",
    3: "AirRecorder 재시작 (14:18:05) - SMDR IR 수신 전 프로세스 종료",
    4: "AirRecorder 재시작 (14:18:05) - SMDR IA 이후 RTP 미시작",
    5: "발신자 번호 마스킹 (**) - PENDING 큐 매칭 불가",
    6: "RTCP BYE (46s) + SSPP-GRACE 조기 종료 - 통화 중 RTP 스트림 중단",
    7: "선내 재사용 경쟁 상태 - FILE CLOSE 통계줄 RTP:0,0 / DB 파일경로 미업데이트",
    8: "선내 재사용 경쟁 상태 - FILE CLOSE 통계줄 RTP:0,0 / DB 파일경로 미업데이트",
    9: "선내 재사용 경쟁 상태 - FILE CLOSE 통계줄 RTP:0,0 / DB 파일경로 미업데이트",
    10: "선내 재사용 경쟁 상태 - FILE CLOSE 통계줄 RTP:0,0 / DB 파일경로 미업데이트",
    11: "선내 재사용 경쟁 상태 - FILE CLOSE 통계줄 RTP:0,0 / DB 파일경로 미업데이트",
    12: "SMDR-REDIRECT (C:6->C:4) 후 7초 만에 RTCP STOP - 2번째 구간 RTP 미시작",
}

CAUSE_SHORT = {
    1: "A: AirRecorder Restart",
    2: "A: AirRecorder Restart",
    3: "A: AirRecorder Restart",
    4: "A: AirRecorder Restart",
    5: "B: Masked Caller",
    6: "C: RTCP BYE + SSPP-GRACE",
    7: "D: Channel Reuse Race",
    8: "D: Channel Reuse Race",
    9: "D: Channel Reuse Race",
    10: "D: Channel Reuse Race",
    11: "D: Channel Reuse Race",
    12: "E: SMDR-REDIRECT Early Stop",
}

DETAIL = {
    1: "07:03:14 AirRecorder InitInstance 재시작. 07:02:34 SMDR RI 수신 후 선내 미할당 상태에서 프로세스 재시작으로 모든 세션 소실.",
    2: "07:03:14 AirRecorder InitInstance 재시작. 07:03:08 SMDR RI 수신 직후 6초 만에 재시작으로 RTP 세션 미생성.",
    3: "14:18:05 AirRecorder InitInstance 재시작. SMDR IR 이전 재시작으로 RING 이벤트 미처리.",
    4: "14:18:05 재시작 직후 SMDR IA(14:18:09) 수신. PACKET_LOSS? 경고 발생. RTP 선내 미할당으로 녹취 파일 미생성.",
    5: "발신자 번호 ** (마스킹됨). SMDR IR:Ext=3591, IA:Ext=3368 (PU=픽업). SSPP Type:5에 ** 번호 미포함으로 PENDING 매칭 실패. C:141 선내 아웃바운드 통화와 충돌.",
    6: "C:10 선내. 20:14:31 RTP START. 20:15:17 RTCP BYE 수신 (46s). SSPP-GRACE 34s 대기 후 20:15:51 FILE CLOSE. 에: 통화 218s 중 46s만 녹취.",
    7: "C:16 선내. 21:10:xx RTP START + FILE OPEN. RTCP STOP (실제 패킷 수신). FILE CLOSE line1(파일명) 기록 후 즉시 신규 통화 선내 재사용. FILE CLOSE line2 RTP:0,0 / callee 공백. QUEUE SKIP I로 DB 파일경로 미업데이트. WAV 파일 존재.",
    8: "C:8 선내. 21:20:xx RTP START + FILE OPEN. 동일 선내 재사용 경쟁 상태. FILE CLOSE RTP:0,0. DB 파일경로 미업데이트. WAV 파일 존재.",
    9: "C:11 선내. 21:23:xx RTP START + FILE OPEN. 동일 선내 재사용 경쟁 상태. FILE CLOSE RTP:0,0. DB 파일경로 미업데이트. WAV 파일 존재.",
    10: "C:2 선내. 21:40:xx RTP START + FILE OPEN. 동일 선내 재사용 경쟁 상태. FILE CLOSE RTP:0,0. DB 파일경로 미업데이트. WAV 파일 존재.",
    11: "C:10 선내. 22:30:xx RTP START + FILE OPEN. 동일 선내 재사용 경쟁 상태. FILE CLOSE RTP:0,0. DB 파일경로 미업데이트. WAV 파일 존재.",
    12: "C:6->C:4 SMDR-REDIRECT (22:41:14). C:4 RTCP STOP 22:41:19 (7s). 2번째 구간 Ext:3357 (55s)는 신규 RTP 선내 미할당. 에: 62s 중 초기 7s만 녹취 시도.",
}


print('Loading log files...')
all_lines = []
for i in range(1, 10):
    fpath = os.path.join(LOG_DIR, f'20260317_{i}.txt')
    try:
        with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
            all_lines.extend([(i, ln, line.rstrip()) for ln, line in enumerate(lines, 1)])
        print(f'  File {i}: {len(lines):,} lines')
    except FileNotFoundError:
        print(f'  File {i}: NOT FOUND')

print(f'Total: {len(all_lines):,} lines loaded')

def categorize(line):
    cats = []
    if '[SMDR]' in line:
        if ' RI ' in line or ',RI,' in line or 'RI:' in line:
            cats.append('SMDR_RI')
        elif ' RS ' in line or ',RS,' in line or 'RS:' in line:
            cats.append('SMDR_RS')
        elif ' IR ' in line or ',IR,' in line or 'IR:' in line:
            cats.append('SMDR_IR')
        elif ' IA ' in line or ',IA,' in line or 'IA:' in line:
            cats.append('SMDR_IA')
        elif 'IN_END' in line or ',I,' in line:
            cats.append('SMDR_I')
        else:
            cats.append('SMDR_RI')
    if '[SSPP]' in line and 'Type:5' in line:
        cats.append('SSPP_Type5')
    if '[RTP]' in line or '[RTCP' in line:
        cats.append('RTP_START')
    if '[FILE]' in line and 'OPEN' in line:
        cats.append('FILE_OPEN')
    if '[FILE]' in line and 'CLOSE' in line:
        cats.append('FILE_CLOSE')
    if 'PENDING' in line and 'ADD' in line:
        cats.append('PENDING_ADD')
    if 'PENDING' in line and 'APPLIED' in line:
        cats.append('PENDING_APPLIED')
    if 'PENDING' in line and 'EXPIR' in line:
        cats.append('PENDING_EXPIRED')
    if any(w in line for w in ['WARNING', 'ERROR', 'FAIL', 'PACKET_LOSS', 'DB_UPDATE', 'QUEUE SKIP', 'GRACE', 'no matching']):
        cats.append('WAR_ERR')
    return cats

def search_caller(caller, ext, did, time_hint):
    results = defaultdict(list)
    th, tm = int(time_hint.split(':')[0]), int(time_hint.split(':')[1])
    for fno, lno, line in all_lines:
        match = False
        ts_m = re.search(r'(\d{2}):(\d{2}):\d{2}', line)
        lh = int(ts_m.group(1)) if ts_m else -1
        lm = int(ts_m.group(2)) if ts_m else -1
        in_window = (lh == th and abs(lm - tm) <= 8)
        if caller == '**':
            if in_window and (ext in line or did in line):
                match = True
        else:
            if caller in line:
                match = True
            elif in_window and (f'Ext:{ext}' in line or f'DID:{did}' in line):
                match = True
        if not match:
            continue
        cats = categorize(line)
        if cats:
            for c in cats:
                results[c].append(line)
        else:
            results['OTHER'].append(line)
    return results

def fmt(items, limit=3):
    if not items:
        return '-'
    sel = items[:limit]
    out = []
    for s in sel:
        ts = re.search(r'\d{2}:\d{2}:\d{2}\.\d{3}', s)
        if ts:
            rest = s[s.find(ts.group(0)):].strip()
            short = rest[:100]
        else:
            short = s.strip()[:100]
        out.append(short)
    result = ' || '.join(out)
    if len(items) > limit:
        result += f' (+{len(items)-limit}more)'
    return result

print('Analyzing calls...')
rows = []
for call in CALLS:
    n = call['no']
    caller = call['caller']
    ext = call['ext']
    did = call['did']
    time_hint = call['time']
    dur = call['dur']
    found = search_caller(caller, ext, did, time_hint)
    row = {
        'no': n, 'caller': caller, 'ext': ext, 'did': did, 'dur': dur,
        'SMDR_RI': fmt(found.get('SMDR_RI', [])),
        'SMDR_RS': fmt(found.get('SMDR_RS', [])),
        'SMDR_IR': fmt(found.get('SMDR_IR', [])),
        'SMDR_IA': fmt(found.get('SMDR_IA', [])),
        'SMDR_I':  fmt(found.get('SMDR_I', [])),
        'SSPP_Type5': fmt(found.get('SSPP_Type5', [])),
        'RTP_START': fmt(found.get('RTP_START', [])),
        'FILE_OPEN': fmt(found.get('FILE_OPEN', [])),
        'FILE_CLOSE': fmt(found.get('FILE_CLOSE', [])),
        'PENDING_ADD': fmt(found.get('PENDING_ADD', [])),
        'PENDING_APPLIED': fmt(found.get('PENDING_APPLIED', [])),
        'PENDING_EXPIRED': fmt(found.get('PENDING_EXPIRED', [])),
        'WAR_ERR': fmt(found.get('WAR_ERR', []), limit=5),
        'cause': CAUSE_SHORT[n],
    }
    rows.append(row)
    total = sum(len(v) for v in found.values())
    print(f'  Call {n}: {caller} ({ext}) -> {total} matching lines')


HEADERS = ['순번','발신자','내선','DID','통화시간','SMDR_RI','SMDR_RS','SMDR_IR','SMDR_IA','SMDR_I','SSPP_Type5','RTP_START','FILE_OPEN','FILE_CLOSE','PENDING_ADD','PENDING_APPLIED','PENDING_EXPIRED','WAR_ERR','원인분석']

with open(OUT_TSV, 'w', newline='', encoding='utf-8-sig') as f:
    writer = csv.DictWriter(f, fieldnames=HEADERS, delimiter='\t')
    writer.writeheader()
    for r in rows:
        writer.writerow({
            '순번': r['no'],
            '발신자': r['caller'],
            '내선': r['ext'],
            'DID': r['did'],
            '통화시간': r['dur'] + 's',
            'SMDR_RI': r['SMDR_RI'],
            'SMDR_RS': r['SMDR_RS'],
            'SMDR_IR': r['SMDR_IR'],
            'SMDR_IA': r['SMDR_IA'],
            'SMDR_I':  r['SMDR_I'],
            'SSPP_Type5': r['SSPP_Type5'],
            'RTP_START': r['RTP_START'],
            'FILE_OPEN': r['FILE_OPEN'],
            'FILE_CLOSE': r['FILE_CLOSE'],
            'PENDING_ADD': r['PENDING_ADD'],
            'PENDING_APPLIED': r['PENDING_APPLIED'],
            'PENDING_EXPIRED': r['PENDING_EXPIRED'],
            'WAR_ERR': r['WAR_ERR'],
            '원인분석': r['cause'],
        })
print(f'TSV saved: {OUT_TSV}')




import pickle as _pkl
_S = _pkl.load(open(".omc/scientist/md_strings.pkl", "rb"))

with open(OUT_MD, "w", encoding="utf-8") as f:
    f.write(_S["title"] + chr(10) + chr(10))
    f.write(_S["date"] + chr(10) + chr(10))
    f.write(_S["overview_hdr"] + chr(10) + chr(10))
    f.write(_S["tbl_header"] + chr(10))
    f.write(_S["tbl_sep"] + chr(10))
    for call in CALLS:
        n = call["no"]
        parts = ["| " + str(n), call["caller"], call["ext"], call["did"], call["dur"]+"s", CAUSE_SHORT[n], CAUSES[n] + " |"]
        f.write(" | ".join(parts) + chr(10))
    f.write(chr(10))
    for i, call in enumerate(CALLS):
        n = call["no"]
        r = rows[i]
        hdr = "## Call #" + str(n) + ": " + call["caller"] + " (Ext:" + call["ext"] + ", DID:" + call["did"] + ", Duration:" + call["dur"] + "s)"
        f.write(hdr + chr(10) + chr(10))
        f.write(_S["cause_prefix"] + CAUSE_SHORT[n] + chr(10) + chr(10))
        f.write(CAUSES[n] + chr(10) + chr(10))
        f.write(_S["log_trace"] + chr(10) + chr(10))
        f.write(_S["log_tbl_h"] + chr(10))
        f.write(_S["log_tbl_s"] + chr(10))
        for col in ["SMDR_RI","SMDR_RS","SMDR_IR","SMDR_IA","SMDR_I","SSPP_Type5","RTP_START","FILE_OPEN","FILE_CLOSE","PENDING_ADD","PENDING_APPLIED","PENDING_EXPIRED","WAR_ERR"]:
            val = r[col].replace("|", "&#124;")
            f.write("| " + col + " | " + val + " |" + chr(10))
        f.write(chr(10))
        f.write(_S["analysis"] + chr(10) + chr(10))
        f.write(DETAIL[n] + chr(10) + chr(10))
        f.write("---" + chr(10) + chr(10))

print("Markdown saved: " + OUT_MD)
print("DONE")
