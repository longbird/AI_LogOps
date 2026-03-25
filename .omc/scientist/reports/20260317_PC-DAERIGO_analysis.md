# SMDR/PBX Log Analysis Report
**Site:** PC-DAERIGO (Daerigo Branch)
**Date:** 2026-03-17
**Analyst:** Scientist Agent
**Generated:** 2026-03-18

---

```
Parsed 625,905 lines in 7.9s

======================================================================
AI-LOGOPS SMDR/PBX LOG ANALYSIS REPORT
Site: PC-DAERIGO (Daerigo Branch) | Date: 2026-03-17
======================================================================

-- 1. TIME RANGE
   First : 2026-03-17 00:00:00.886
   Last  : 2026-03-17 23:59:59.675
   Lines : 625,905

-- 2. LOG LEVEL DISTRIBUTION
   INF:    421,940  (67.42%)
   DBG:    202,420  (32.34%)
   WAR:      1,157  (0.18%)
   ERR:        366  (0.06%)
   OTHER:        0

-- 3. CALL VOLUME TOTALS
   IR (Incoming Ring)   : 12,498
   IA (Incoming Answer) : 11,790
   I  (Incoming End)    : 11,745
   O  (Outgoing End)    : 2,239
   Total call events    : 38,272

-- 4. ANSWER RATE
   IA/IR ratio : 11,790 / 12,498 = 94.34%

-- 5. RECORDING STATS
   FILE OPEN count   : 14,414
   FILE CLOSE count  : 14,385
   Total size        : 17,648,079,950 bytes  (16,830.5 MB)
   Avg size/file     : 1,226,839 bytes
   Duration Time:    avg=37.9s  min=1s  max=515s
   Total record time : 544,689s  (151.3 hours)

-- 6. RTP QUALITY
   Total RTP-STATS sessions : 14,345
   Sessions with loss > 0   : 97  (0.68%)
   Sessions with loss > 1%  : 3  (0.02%)

-- 7. ERROR / WARNING ANALYSIS
   [ERR] total: 366
         [SMDR]  366
   [WAR] total: 1,157
         [RTP]  679
         [NO-TYPE5]  262
         [RTP-LOSS]  195
         [SMDR]  20
         [OUTBOUND-OVERRIDE]  1

-- 8. TOP 15 ACTIVE EXTENSIONS
    1. Ext   3501  :  5,236 events
    2. Ext   3591  :  2,143 events
    3. Ext   3571  :  1,947 events
    4. Ext   3502  :  1,159 events
    5. Ext   3239  :  1,060 events
    6. Ext   3394  :    979 events
    7. Ext   3276  :    899 events
    8. Ext   3216  :    735 events
    9. Ext   3378  :    729 events
   10. Ext   3287  :    711 events
   11. Ext   3346  :    697 events
   12. Ext   3332  :    687 events
   13. Ext   3235  :    647 events
   14. Ext   3292  :    631 events
   15. Ext   3284  :    619 events

-- 9. UNIQUE CALLERS / DIDs
   Unique callers : 8,846
   Unique DIDs    : 442
   Top 10 DIDs:
      DID       2099  : 6,493 calls
      DID       4510  : 2,901 calls
      DID       4704  : 2,767 calls
      DID       4733  : 1,316 calls
      DID       4701  : 879 calls
      DID       4705  : 775 calls
      DID       9622  : 744 calls
      DID       4718  : 726 calls
      DID       4837  : 693 calls
      DID       4567  : 572 calls

-- 10. UNMATCHED PACKETS
   RTP-UNMATCHED        : 12,826
   RTCP-BYE-UNMATCHED   : 10,587

-- 11. HUNT GROUP / TRANSFER
   Hunt group events : 7,552
   Transfer events   : 9,281

-- 12. NO-TYPE5 WARNINGS
   NO-TYPE5 count : 262

-- 13. MAX PENDING QUEUE DEPTH
   Max depth : 56

-- 14. HOURLY CALL VOLUME
     Hr      IR      IA       I       O    Total    Ans%
   ----  ------  ------  ------  ------  -------  ------
   00:xx     408     401     403      65    1,277     98%
   01:xx     216     204     201      39      660     94%
   02:xx     139     134     134      35      442     96%
   03:xx      96      94      95      18      303     98%
   04:xx      71      70      68       8      217     99%
   05:xx      39      38      40      13      130     97%
   06:xx      64      58      58      18      198     91%
   07:xx      98      89      88      45      320     91%
   08:xx     245     224     221      53      743     91%
   09:xx     519     459     452      74    1,504     88%
   10:xx     577     544     545      95    1,761     94%
   11:xx     509     478     480      91    1,558     94%
   12:xx     534     517     508      82    1,641     97%
   13:xx     562     537     535      75    1,709     96%
   14:xx     657     636     634     103    2,030     97%
   15:xx     630     602     602     110    1,944     96%
   16:xx     679     604     600     122    2,005     89%
   17:xx     444     419     420     110    1,393     94%
   18:xx     468     410     410      80    1,368     88%
   19:xx     741     663     652     125    2,181     89%
   20:xx   1,376   1,264   1,252     199    4,091     92%
   21:xx   1,446   1,405   1,402     267    4,520     97%
   22:xx   1,196   1,162   1,166     241    3,765     97%
   23:xx     784     778     779     171    2,512     99%
    TOT  12,498  11,790  11,745   2,239   38,272

-- 15. CALL DURATION DISTRIBUTION (I+O ended calls)
       0-10s :  1,629  ( 11.6%)  #####
      11-30s :  6,041  ( 43.2%)  #####################
      31-60s :  4,102  ( 29.3%)  ##############
     61-120s :  1,804  ( 12.9%)  ######
       120+s :    408  (  2.9%)  #
       TOTAL : 13,984

======================================================================
END OF REPORT
======================================================================

```
