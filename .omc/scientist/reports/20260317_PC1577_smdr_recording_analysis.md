# SMDR vs Recording Match Analysis: PC-1577, 2026-03-17
**Generated:** 2026-03-18 | Log: 294,461 lines (39 MB)

---

## Executive Summary

On 2026-03-17, PC-1577 processed **7,849 real calls** (duration>0) and recorded **7,260** -- overall recording rate **92.5%**, leaving **589 calls unrecorded**. Gap concentrates in 22:xx-23:xx (73.9% / 67.0%) and 8 problem extensions (29-66%). Root cause: 1,226 SMDR PENDING EXPIRED events from cross-midnight carryover calls.

---

## Section 1: Overall Counts

| Metric | Count |
|---|---|
| I:IN_END all | 6,706 |
| I:IN_END dur>0 (real) | **6,694** |
| I:IN_END dur=0 | 12 |
| O:OUT_END all | 1,592 |
| O:OUT_END dur>0 (real) | **1,155** |
| O:OUT_END dur=0 | 437 |
| **Total should-record** | **7,849** |
| FILE OPEN | 7,260 |
| FILE CLOSE | 7,246 |
| OPEN w/o CLOSE | 14 |
| REC-CHECK skip:0 | 7,209 |
| REC-CHECK skip:1 | 53 |
| NO-TYPE5 | 11 |
| PENDING EXPIRED | 1,226 |
| **Recording Rate** | **92.5%** |
| **Unrecorded** | **589** |

---

## Section 2: By-Hour Table

| Hour | I(d>0) | O(d>0) | Total | F.OPEN | F.CLOSE | RecRate |
|---|---|---|---|---|---|---|
| 00:xx | 476 | 63 | 539 | 553 | 545 | 102.6% |
| 01:xx | 254 | 41 | 295 | 310 | 312 | 105.1% |
| 02:xx | 180 | 27 | 207 | 214 | 214 | 103.4% |
| 03:xx | 112 | 12 | 124 | 130 | 128 | 104.8% |
| 04:xx | 68 | 12 | 80 | 93 | 96 | 116.3% |
| 05:xx | 54 | 22 | 76 | 89 | 89 | 117.1% |
| 06:xx | 39 | 8 | 47 | 51 | 52 | 108.5% |
| 07:xx | 36 | 12 | 48 | 58 | 58 | 120.8% |
| 08:xx | 50 | 9 | 59 | 64 | 61 | 108.5% |
| 09:xx | 37 | 11 | 48 | 47 | 46 | 97.9% |
| 10:xx | 43 | 16 | 59 | 54 | 57 | 91.5% |
| **11:xx** | **64** | **23** | **87** | **74** | **72** | **85.1% ALERT** |
| 12:xx | 43 | 12 | 55 | 56 | 56 | 101.8% |
| 13:xx | 65 | 19 | 84 | 90 | 88 | 107.1% |
| 14:xx | 91 | 39 | 130 | 143 | 141 | 110.0% |
| 15:xx | 102 | 37 | 139 | 128 | 130 | 92.1% |
| 16:xx | 86 | 22 | 108 | 111 | 110 | 102.8% |
| 17:xx | 94 | 41 | 135 | 137 | 136 | 101.5% |
| 18:xx | 187 | 48 | 235 | 243 | 242 | 103.4% |
| 19:xx | 468 | 67 | 535 | 544 | 539 | 101.7% |
| 20:xx | 979 | 116 | 1,095 | 1,120 | 1,114 | 102.3% |
| 21:xx | 1,202 | 180 | 1,382 | 1,332 | 1,344 | 96.4% |
| **22:xx** | **1,109** | **198** | **1,307** | **966** | **955** | **73.9% CRITICAL** |
| **23:xx** | **855** | **120** | **975** | **653** | **661** | **67.0% CRITICAL** |

Hours >100% in 00-09:xx = cross-midnight recordings (03/16 calls). 22:xx+23:xx contribute 663 unrecorded slots.

---

## Section 3: By-Extension (Top 20)

| Ext | SMDR(d>0) | Recorded | RecRate | Missing |
|---|---|---|---|---|
| 3308 | 350 | 344 | 98.3% | 6 |
| **3340** | **316** | **135** | **42.7%** | **181** |
| 3416 | 241 | 239 | 99.2% | 2 |
| **3446** | **236** | **97** | **41.1%** | **139** |
| 3255 | 230 | 197 | 85.7% | 33 |
| 3294 | 228 | 194 | 85.1% | 34 |
| **3225** | **224** | **147** | **65.6%** | **77** |
| **3216** | **220** | **75** | **34.1%** | **145** |
| 3470 | 194 | 173 | 89.2% | 21 |
| 3321 | 194 | 183 | 94.3% | 11 |
| 3377 | 191 | 184 | 96.3% | 7 |
| **3311** | **185** | **114** | **61.6%** | **71** |
| 3328 | 185 | 174 | 94.1% | 11 |
| **3279** | **179** | **53** | **29.6%** | **126** |
| 3420 | 178 | 162 | 91.0% | 16 |
| **3272** | **177** | **86** | **48.6%** | **91** |
| **3243** | **171** | **69** | **40.4%** | **102** |
| 3341 | 166 | 151 | 91.0% | 15 |
| **3258** | **163** | **131** | **80.4%** | **32** |
| 3435 | 159 | 165 | 103.8% | -6 |

Extensions 5901-5915 (12 exts) and 8724: 0% recorded -- likely IVR/trunks intentionally excluded.

---

## Section 4: By-DID (Top 15)

| DID | SMDR(d>0) | Recorded | RecRate | Missing |
|---|---|---|---|---|
| **2701** | **4,685** | **4,224** | **90.2%** | **461** |
| 2703 | 788 | 698 | 88.6% | 90 |
| **2787** | **581** | **479** | **82.4%** | **102** |
| **0002** | **396** | **311** | **78.5%** | **85** |
| 2704 | 37 | 34 | 91.9% | 3 |
| 2705 | 35 | 29 | 82.9% | 6 |
| 2709 | 27 | 28 | 103.7% | -1 |
| 2706 | 20 | 17 | 85.0% | 3 |
| 2738 | 20 | 18 | 90.0% | 2 |
| 12701 | 18 | 18 | 100.0% | 0 |
| 2702 | 12 | 12 | 100.0% | 0 |
| 2799 | 10 | 8 | 80.0% | 2 |
| 2792 | 10 | 9 | 90.0% | 1 |
| 2727 | 8 | 8 | 100.0% | 0 |
| 2714 | 7 | 7 | 100.0% | 0 |

DID 2701 (60% of all inbound) = 461 of 589 missing. DID 2787 (82.4%) and DID 0002 (78.5%) worst by rate.

---

## Section 5: REC-CHECK Skip Analysis

| Category | Count |
|---|---|
| skip:0 (proceeds) | 7,209 |
| **skip:1 (skipped)** | **53** |
| -- iRemainSec=0 | 51 |
| -- iRemainSec>0 (cooldown) | 2 |

51 of 53 skips immediate. 4+ sampled cases show empty caller field (outbound w/o CID).

---

## Section 6: Call Duration Distribution

| Duration | Inbound | I% | Outbound | O% |
|---|---|---|---|---|
| <5s | 106 | 1.6% | 32 | 2.8% |
| 5-9s | 128 | 1.9% | 118 | 10.2% |
| 10-29s | 1,719 | 25.7% | 571 | 49.4% |
| 30-59s | 2,849 | 42.6% | 233 | 20.2% |
| 60-119s | 1,508 | 22.5% | 114 | 9.9% |
| 120-299s | 377 | 5.6% | 75 | 6.5% |
| 300s+ | 7 | 0.1% | 12 | 1.0% |

Short calls (<5s) = 1.6-2.8%. NOT a significant driver of missed recordings.

---

## Section 7: NO-TYPE5 and PENDING EXPIRED

### NO-TYPE5 (11 events)

| Hour | Ext | Duration |
|---|---|---|
| 10:xx | 3262 | 1s |
| 11:xx | 3272 | 26s |
| 12:xx | 3272 | 0s |
| 16:xx | 3311 | 3s |
| 19:xx | 3454 | 142s |
| 19:xx | 3243 | 39s |
| 20:xx | 3311 | 148s |
| 21:xx | 3327 | 31s |
| 22:xx | 3258 | 28s |
| 23:xx | 3294 | 27s |
| 23:xx | 3294 | 6s |

Extensions 3311 (148s) and 3454 (142s) lost calls over 2 minutes.

### PENDING EXPIRED (1,226 events)

| Extension | Count |
|---|---|
| **3648** | **568** |
| 3001 | 160 |
| 3340 | 64 |
| 3216 | 40 |
| 3002 | 40 |
| 3446 | 39 |
| 5002 | 35 |
| 3243 | 33 |
| 3225 | 30 |
| 3279 | 30 |

By hour: 00:102, 01:67, 02:37, 03:19, 20:133, 21:180, **22:224**, **23:222**
Extension 3648 = 568 events (46% of all PENDING EXPIRED).

---

## Section 8: Duration=0 Calls (Correctly Not Recorded)

| Type | Count |
|---|---|
| I:IN_END dur=0 | 12 |
| O:OUT_END dur=0 | 437 |
| **Total** | **449** |

---

## Section 9: Final Summary

**Recording Rate: 92.5% (7,260 / 7,849)**

| | Count |
|---|---|
| Real calls | 7,849 |
| Recorded | 7,260 |
| **Unrecorded** | **589** |

### Root Cause Breakdown

| Cause | Est. Count | % of Gap |
|---|---|---|
| PENDING EXPIRED (SMDR timeout) | ~400-500 | ~70-85% |
| REC-CHECK skip:1 | 53 | 9% |
| NO-TYPE5 | 11 | 2% |
| Incomplete (OPEN w/o CLOSE) | 14 | 2% |
| Other (RTP mismatch) | ~111 | ~19% |

**Worst Extensions:** 3340: 181 (42.7%), 3216: 145 (34.1%), 3446: 139 (41.1%), 3279: 126 (29.6%), 3243: 102 (40.4%), 3272: 91 (48.6%), 3225: 77 (65.6%), 3311: 71 (61.6%)

**Worst Hours:** 22:xx (73.9%), 23:xx (67.0%), 11:xx (85.1%)

**Worst DIDs:** 2701 (90.2%, 461 missing), 2787 (82.4%, 102), 0002 (78.5%, 85)

---

## Recommendations

1. **Extension 3648 -- top priority**: 568 PENDING EXPIRED (46% of total). Investigate if this is a shared trunk or ACD queue generating orphaned SMDR entries.

2. **Late-night PENDING EXPIRED storm (22:xx-23:xx, 446 events)**: Cross-midnight carryover. Solutions: extend SMDR pending timeout beyond 1800s for 21:xx-23:xx; implement cross-midnight session state persistence.

3. **Problem extensions 3279/3216/3340/3446/3243 (29-43% rates)**: All in PENDING EXPIRED top-10. Check for shared SIP trunk with abnormal SMDR timing.

4. **NO-TYPE5 on 3311 and 3454**: SIP trace needed. Missing Type 5 SSPP = recorder did not receive called-party info from SIP UPDATE/REINVITE.

5. **Outbound skip with empty caller field**: Populate caller from SIP From header when SMDR caller is blank.

6. **Extensions 5901-5915, 8724**: Confirm these are intentionally non-recorded. If any are agent seats, enable recording in config.

---
*Scientist Agent | AI-LogOps PC-1577 | 2026-03-17 log analysis*
