# PC-DAERIGO SMDR/PBX Log Analysis - 2026-03-16

**Files:** 20260316_1..8.txt | **Lines:** 583,112 | **Size:** ~80 MB

---

## 1. Time Range

| Field | Value |
|-------|-------|
| First entry | 2026-03-16 00:00:01.355 |
| Last entry  | 2026-03-16 23:59:58.669 |
| Coverage | Full 24-hour day |

---

## 2. Log Level Distribution

| File | INF | DBG | WAR | ERR | Total |
|------|-----|-----|-----|-----|-------|
| _1 | 50,237 | 22,886 | 48 | 53 | 73,226 |
| _2 | 51,476 | 20,669 | 39 | 116 | 72,300 |
| _3 | 51,029 | 21,565 | 36 | 70 | 72,700 |
| _4 | 50,918 | 21,464 | 51 | 73 | 72,506 |
| _5 | 50,823 | 21,600 | 54 | 89 | 72,566 |
| _6 | 49,565 | 23,488 | 47 | 62 | 73,162 |
| _7 | 47,493 | 26,524 | 87 | 15 | 74,119 |
| _8 | 44,295 | 28,167 | 54 | 17 | 72,533 |
| **TOT** | **395,836** | **186,363** | **416** | **495** | **583,112** |

INF 67.9% | DBG 32.0% | WAR+ERR 911 lines (0.16% error rate)

---

## 3. Category Distribution (Top 20)

| Rank | Category | Count |
|------|----------|-------|
| 1 | [SMDR] | 258,556 |
| 2 | (no-category) | 98,901 |
| 3 | [RTP] | 49,172 |
| 4 | [FILE] | 39,543 |
| 5 | [FIND] | 16,810 |
| 6 | [OUTBOUND] | 13,482 |
| 7 | [REC-CHECK] | 13,202 |
| 8 | [RTP-STATS] | 13,159 |
| 9 | [RTP-UNMATCHED] | 12,297 |
| 10 | [SSPP] | 11,375 |
| 11 | [RTCP-BYE-UNMATCHED] | 10,182 |
| 12 | [RTP-GRACE] | 8,770 |
| 13 | [HUNT-GROUP] | 8,179 |
| 14 | [TRANSFER] | 7,796 |
| 15 | [RTCP-BYE-STATE] | 4,996 |
| 16 | [ENDED] | 4,470 |
| 17 | [SMDR-REDIRECT-DEFERRED] | 3,724 |
| 18 | [SMDR-O] | 2,079 |
| 19 | [RTCP-STOP-STATE] | 1,384 |
| 20 | [CLEANUP-PERF] | 1,173 |

Other: [SSPP-DROP]:1,048 / [RTP-INFO-GRACE]:889 / [DURATION-MISMATCH]:625 / [SSPP-GRACE]:347 / [SESSION-SAVE]:247 / [NO-TYPE5]:212 / [RTP-LOSS]:181 / [HUNT-STUB-ACTIVE]:103 / [SMDR-REDIRECT]:86 / [RTP-REORDER]:37 / [RTCP-BYE-IGNORE]:33 / [RTP-SUPPRESS]:20 / [HUNT-STUB]:19 / [RTP-RESET]:6 / [SRWLOCK-DIAG]:3 / [OUTBOUND-RESET]:3

---

## 4. Call Volume by Hour

| Hour | IR (Ring) | IA (Answer) | I (End) | O (Out) | RS | Total |
|------|-----------|-------------|---------|---------|------|-------|
| 00 | 154 | 144 | 146 | 49 | 1,207 | 493 |
| 01 | 125 | 109 | 106 | 45 | 1,070 | 385 |
| 02 | 106 | 96 | 97 | 41 | 943 | 340 |
| 03 | 36 | 33 | 34 | 17 | 351 | 120 |
| 04 | 33 | 29 | 28 | 16 | 294 | 106 |
| 05 | 48 | 44 | 45 | 21 | 435 | 158 |
| 06 | 40 | 35 | 35 | 11 | 382 | 121 |
| 07 | 85 | 79 | 77 | 36 | 679 | 277 |
| 08 | 254 | 229 | 226 | 52 | 2,090 | 761 |
| 09 | 599 | 514 | 510 | 85 | 4,164 | 1,708 |
| 10 | 699 | 577 | 574 | 108 | 4,722 | 1,958 |
| 11 | 668 | 590 | 583 | 103 | 4,410 | 1,944 |
| 12 | 535 | 493 | 488 | 105 | 3,662 | 1,621 |
| 13 | 652 | 618 | 614 | 103 | 4,252 | 1,987 |
| 14 | 710 | 649 | 652 | 123 | 4,796 | 2,134 |
| 15 | 710 | 631 | 625 | 117 | 4,551 | 2,083 |
| 16 | 624 | 566 | 564 | 119 | 4,027 | 1,873 |
| 17 | 508 | 471 | 469 | 94 | 3,560 | 1,542 |
| 18 | 449 | 393 | 389 | 94 | 3,181 | 1,325 |
| 19 | 599 | 566 | 556 | 93 | 3,515 | 1,814 |
| **20** | **1,140** | **1,118** | **1,119** | **189** | 4,686 | **3,566** |
| **21** | **1,156** | **1,134** | **1,127** | 170 | 3,388 | **3,587** |
| 22 | 971 | 963 | 968 | 168 | 2,483 | 3,070 |
| 23 | 699 | 695 | 697 | 127 | 1,312 | 2,218 |
| **TOTAL** | **11,600** | **10,776** | **10,729** | **2,086** | **64,160** | **35,191** |

---

## 5. Peak Hours Analysis

| Rank | Hour | Total Events | Notes |
|------|------|-------------|-------|
| 1 | **21:00** | 3,587 | Evening peak - highest of the day |
| 2 | **20:00** | 3,566 | Evening surge |
| 3 | **22:00** | 3,070 | Late evening sustained activity |
| 4 | **23:00** | 2,218 | Near-midnight still elevated |
| 5 | **14:00** | 2,134 | Peak business hours |

Evening hours 20-22 carry 70-82% more call volume than peak business hours (14-15).
Pattern indicates an evening-shift call center (outbound telemarketing or customer service).

- Quietest: 03:00-06:00 (100-160 events/hour)
- Business peak (14-15): ~2,100 events/hour
- Evening peak (20-22): ~3,000-3,600 events/hour

---

## 6. RTP Recording Statistics

### Session Counts
| Metric | Value |
|--------|-------|
| FILE OPEN (recording sessions) | **13,200** |
| FILE CLOSE with Size data | 13,182 |
| Total data recorded | **15.085 GB** (16,196,869,412 bytes) |
| Average file size | 1,228,711 bytes (1.17 MB) |
| Average duration | 37.9 seconds |
| Minimum duration | 1 second |
| Maximum duration | 858 seconds (14.3 minutes) |

### Duration Distribution
| Range | Count | Pct |
|-------|-------|-----|
| 0-10s | 1,755 | 13.3% |
| 11-30s | 5,385 | 40.8% |
| 31-60s | 3,879 | 29.4% |
| 61-120s | 1,738 | 13.2% |
| 120s+ | 425 | 3.2% |

### RTP Packet Loss
| Metric | Value |
|--------|-------|
| RTP-STATS sessions analyzed | 13,159 |
| Sessions with any packet loss | 87 (0.66%) |
| Sessions with loss > 1% | **0** |
| Server avg loss rate | 0.0014% |
| Server max loss rate | 0.94% |
| Client avg loss rate | 0.0000% |
| Client max loss rate | 0.32% |

Network quality is **excellent** - 99.3% of sessions have zero packet loss, no session exceeded 1%.

---

## 7. Warning and Error Analysis

### Errors (ERR) - 495 total
| Type | Count | Detail |
|------|-------|--------|
| [SMDR] PENDING EXPIRED | **495** | All 495 errors are this single type |

All 495 errors: calls waiting for Type 5 signaling confirmation that timed out.
Avg wait: **1,954 seconds (~32.6 minutes)** | Range: 1,801-2,105s

### Warnings (WAR) - 416 total
| Type | Count | Detail |
|------|-------|--------|
| [NO-TYPE5] | 212 | SMDR incoming-end event without Type 5 signaling |
| [RTP-LOSS] | 181 | RTP packet sequence gap events |
| [SMDR] NO-MATCH-DEFER | 11 | Call records re-queued for retry |
| [RTP] SSRC COLLISION | 9 | All from idx:33, clustered at 13:59:01 |
| [TRANSFER] | 2 | Transfer routing anomalies |
| [OUTBOUND-OVERRIDE] | 1 | Outbound call override applied |

---

## 8. Extension Activity (Top 20)

| Rank | Ext | Events | Rank | Ext | Events |
|------|-----|--------|------|-----|--------|
| 1 | **3501** | **9,866** | 11 | 3592 | 786 |
| 2 | 3591 | 4,161 | 12 | 3364 | 740 |
| 3 | 3571 | 3,546 | 13 | 3346 | 698 |
| 4 | 3502 | 2,327 | 14 | 3284 | 675 |
| 5 | 3239 | 1,319 | 15 | 3332 | 673 |
| 6 | 3503 | 898 | 16 | 3395 | 671 |
| 7 | 3572 | 893 | 17 | 3232 | 649 |
| 8 | 3276 | 857 | 18 | 3229 | 623 |
| 9 | 3292 | 824 | 19 | 3278 | 622 |
| 10 | 3287 | 820 | 20 | 3378 | 621 |

Total unique extensions: **95** | Ext 3501 has 2.4x more events than 2nd-ranked 3591.

---

## 9. RTP-UNMATCHED Count

**12,297 total lines** (~0.93 events/session)

Normal range for session boundary transitions. RTP packets arriving before session is established or after teardown.

---

## 10. RTCP-BYE-UNMATCHED Count

**10,182 total lines** (~0.77 events/session)

Expected: RTCP BYE packets arriving after local session teardown is normal network timing behavior.

---

## 11. Hunt Group Transfers

| Metric | Value |
|--------|-------|
| Total [HUNT-GROUP] log lines | 8,179 |
| RTP Info redirect events | **2,861** |
| Coverage | ~26.5% of answered calls (2,861 / 10,776) |

~1 in 4 answered calls was routed through a hunt group before reaching the answering agent extension.

---

## 12. NO-TYPE5 Warnings

**212 total** (1.97% of 10,729 incoming call ends)

Top affected extensions:

| Ext | Count | Ext | Count |
|-----|-------|-----|-------|
| 3276 | 16 | 3232 | 11 |
| 3346 | 13 | 3278 | 10 |
| 3239 | 13 | 3284 | 8 |
| 3356 | 13 | 3214 | 8 |
| 3292 | 8 | 3378 | 8 |

Extensions 3276, 3346, 3239, 3356 may have PBX signaling configuration issues or experience more abrupt call terminations.

---

## 13. Unique Caller Numbers

| Metric | Value |
|--------|-------|
| Unique caller phone numbers | **8,003** |
| Average calls per unique caller | 1.45 |
| Top caller: 0220988290 | 130 calls (02-prefix landline, likely IVR/system) |
| 2nd caller: 01091233999 | 64 calls |

---

## 14. Unique DID Numbers

| Metric | Value |
|--------|-------|
| Unique DID values | **450** |

Top 10 DIDs:

| DID | Count | DID | Count |
|-----|-------|-----|-------|
| 2099 | 6,277 | 4701 | 782 |
| 4510 | 2,536 | 4837 | 764 |
| 4704 | 2,194 | 4718 | 699 |
| 4733 | 1,429 | 4567 | 675 |
| 9622 | 563 | 4705 | 529 |

DID 2099 is the dominant inbound line (~54% more traffic than the next DID 4510).

---

## 15. PENDING Queue Depth

| Metric | Value |
|--------|-------|
| Maximum PENDING ADD total | **58** |
| Timestamp of peak | 2026-03-16 11:13:42 (morning business hours) |
| Max ENDED queue depth | 165 |
| PENDING EXPIRED errors | 495 (avg wait 1,954s / ~32.6 min) |

Peak pending queue of 58 reached during morning business hours. The uniform ~32-min expiry window across all 495 PENDING EXPIRED errors indicates a fixed system timeout parameter.

---

## System Health Summary

| Indicator | Value | Status |
|-----------|-------|--------|
| Total inbound calls (IR) | 11,600 | Normal |
| Answer rate (IA/IR) | **92.9%** | Good |
| Inbound / Outbound split | 84.8% / 15.2% | Normal |
| RTP avg packet loss | 0.0014% | Excellent |
| Sessions with any packet loss | 0.66% | Excellent |
| Sessions with >1% packet loss | 0 | Excellent |
| Error rate | 0.085% of all lines | Very low |
| PENDING EXPIRED | 495 (4.3% of IR) | Monitor |
| NO-TYPE5 warnings | 212 (1.97% of I ends) | Acceptable |
| Total data recorded | **15.085 GB** | Normal |
| Unique callers today | 8,003 | Normal |
| Unique DIDs | 450 | Normal |
| Active extensions | 95 | Normal |

---

## Limitations

- Called numbers are partially masked in logs (trailing ** suffix) - full called-number analysis not possible
- File 1 contains carryover sessions from 03/15 logged at 03/16 00:00 (session start before midnight)
- Extension event counts include all SMDR types (not normalized per unique call instance)
- PENDING EXPIRED uniform ~32-min timeout likely a system design parameter, not individual anomalies
- RTP-UNMATCHED and RTCP-BYE-UNMATCHED counts are per log-line occurrence, not unique stream counts

---
*Scientist Agent | 583,112 log lines | 80.2 MB across 8 files | 2026-03-17*
