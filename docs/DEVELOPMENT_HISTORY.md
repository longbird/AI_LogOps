# AI-LogOps 개발 내역서

**프로젝트:** AI-LogOps Auto-Deployer  
**설명:** 다중 에이전트 원격 로그 분석 및 자동 배포 시스템  
**개발 기간:** 2026-02-16 ~ 2026-02-17  
**브랜치:** `develop`  
**최신 커밋:** `29610a4`  
**테스트:** 406개 전체 통과  

---

## 1. 프로젝트 개요

AI-LogOps는 원격 서버/PC에 설치된 에이전트가 로그 파일을 수집하고, 중앙 명령 서버에서 AI 분석 파이프라인을 통해 로그를 분석한 뒤, 자동으로 배포/롤백까지 수행하는 시스템입니다.

### 아키텍처

```
┌─────────────────────────────────────────────────┐
│                 Command Server                   │
│  ┌───────────┐  ┌──────────┐  ┌──────────────┐  │
│  │ TCP Server│  │ AI Pipe  │  │  Dashboard   │  │
│  │ (9500)    │  │ (3-Stage)│  │  (FastAPI)   │  │
│  └─────┬─────┘  └────┬─────┘  └──────────────┘  │
│        │              │                          │
│  ┌─────┴──────────────┴─────┐                    │
│  │     Telegram Bot          │                    │
│  │ (/analyze /deploy /log)   │                    │
│  └───────────────────────────┘                    │
└──────────────────┬──────────────────────────────┘
                   │ TCP Binary Protocol
     ┌─────────────┼─────────────┐
     │             │             │
┌────┴────┐  ┌────┴────┐  ┌────┴────┐
│ Agent A │  │ Agent B │  │ Agent C │
│ (Win)   │  │ (Win)   │  │ (Win)   │
└─────────┘  └─────────┘  └─────────┘
```

### 기술 스택

| 영역 | 기술 |
|------|------|
| 언어 | Python 3.10+ |
| 프로토콜 | TCP Binary (struct 기반 고정/가변 패킷) |
| AI | OpenAI GPT / Anthropic Claude (Strategy Pattern) |
| 텔레그램 | python-telegram-bot 21.0+ |
| 대시보드 | FastAPI + HTMX + Tailwind CSS + WebSocket |
| 에이전트 | Windows Service (pywin32) + watchdog |
| 배포 | PyInstaller (단일 실행파일) |
| 테스트 | pytest + pytest-asyncio (406 tests) |

---

## 2. 개발 타임라인

### Phase 1: 기반 구축 (2026-02-16 19:31 ~ 20:09)

프로젝트 스캐폴딩부터 TCP 통신 기반까지 구축.

| 커밋 | 설명 |
|------|------|
| `da8957e` | 프로젝트 스캐폴딩 (패키지 구조) |
| `ebcf75d` | TCP 바이너리 프로토콜 패킷 타입 및 헤더 직렬화 정의 |
| `dd26a89` | AUTH, AUTH_ACK, HEARTBEAT, DISCONNECT 페이로드 직렬화 |
| `92bef84` | AuthPayload 토큰 검증 완화 + 엣지케이스 테스트 |
| `341d200` | 공유 데이터 모델 (AgentState, AgentSession, DeployRecord) |
| `88a8b37` | 유틸리티 (SHA-256, YAML config, 로깅, 감사 로그) |
| `876fed5` | SessionManager (다중 에이전트 접속 관리) |
| `3b4580b` | 비동기 TCP 서버 (AUTH/세션 관리) |
| `276284c` | 비동기 TCP 클라이언트 (AUTH, heartbeat, recv loop) |
| `d33653e` | 텔레그램 Rate Limiter (슬라이딩 윈도우) |
| `23a772a` | 텔레그램 커맨드 핸들러 (관리자 인증, 감사 로깅) |
| `225c864` | 에이전트 텔레그램 폴러 (/connect, /disconnect, /status) |
| `5a6c3a8` | Phase 1 통합 테스트 (다중 에이전트 접속/heartbeat/해제) |

**구현 내용:**
- `PacketType` 열거형: AUTH(0x01), AUTH_ACK(0x02), HEARTBEAT(0xFE), DISCONNECT(0xFF) 등
- 5바이트 헤더 `[Type(1B)][PayloadLength(4B)]` + 가변 페이로드
- 비동기 TCP 서버/클라이언트 (asyncio.StreamReader/Writer)
- 세션 관리 (최대 접속 수, heartbeat 타임아웃)
- 텔레그램 봇 인터페이스 (관리자 인증, 명령 파싱)

### Phase 2: 로그 수집 (2026-02-16 20:16 ~ 20:22)

에이전트의 로그 파일 감시 및 서버 전송.

| 커밋 | 설명 |
|------|------|
| `4df3050` | LOG_HIST, LOG_REAL 페이로드 직렬화 |
| `2e773f8` | LogWatcher (watchdog 기반 파일 변경 감지, 히스토리 읽기) |
| `241f036` | TCP 클라이언트 로그 전송 메서드 (send_log_history, send_log_line) |
| `68da0ea` | StorageManager (로그/리포트/백업 파일 관리) |
| `e84179b` | TCP 서버 로그 핸들러 → StorageManager 연동 |
| `2c97646` | Phase 2 통합 테스트 (로그 전송 E2E) |

**구현 내용:**
- `LogWatcher`: watchdog Observer 기반 실시간 파일 변경 감지
- `LOG_HIST` 패킷: `[Filename(256B)][FileSize(4B)][Data(variable)]` — 누적 로그 전송
- `LOG_REAL` 패킷: `[Filename(256B)][LineLen(2B)][Line(variable)]` — 실시간 라인 전송
- `StorageManager`: `storage/logs/{agent_id}/` 계층 저장, 보존 기간 자동 정리

### Phase 3: AI 분석 파이프라인 (2026-02-16 20:30 ~ 20:57)

3단계 AI 체이닝 파이프라인 + Step 3.5 자동 검증.

| 커밋 | 설명 |
|------|------|
| `14917a4` | AI Provider 추상화 (Strategy Pattern, retry, fallback) |
| `f8d582f` | Jinja2 프롬프트 템플릿 (4단계 AI 파이프라인) |
| `6cf93c3` | 3-Stage AI 체이닝 파이프라인 (파일 영속화) |
| `49e65cd` | Step 3.5 자동 검증 (구문 검사 + Cross-LLM 리뷰) |
| `adcffa2` | 텔레그램 AI 명령 연동 (/analyze, /plan, /fix, /auto) |

**구현 내용:**
- **Stage 1 (분석)**: 로그 수집 → 오류 패턴/근본 원인 분석
- **Stage 2 (계획)**: 분석 결과 → 수정 계획 수립
- **Stage 3 (수정)**: 계획 → 코드 패치 생성
- **Stage 3.5 (검증)**: 구문 검사 + Cross-LLM 리뷰 (GPT가 생성하면 Claude가 검증, 역방향도 가능)
- OpenAI/Claude 듀얼 프로바이더, 자동 폴백, 지수 백오프 재시도

### Phase 4: 배포 시스템 (2026-02-16 21:26 ~ 21:45)

파일 전송 + 자동 배포 + 롤백.

| 커밋 | 설명 |
|------|------|
| `e72f8d7` | 청크 기반 파일 전송 (SHA-256 무결성 검증) |
| `c4890d8` | ProcessManager (kill/start/health_check/rollback) |
| `b3d8a83` | 전체 배포 플로우 (health check 실패 시 자동 롤백) |
| `d3e0d97` | 텔레그램 /deploy, /rollback 명령 |

**구현 내용:**
- `FILE_CHUNK` 패킷: `[SeqNum(4B)][ChunkSize(2B)][Data(max 4096B)]` — 4KB 청크 분할 전송
- `CMD_DEPLOY` → 파일 수신 → SHA-256 검증 → 프로세스 중지 → 교체 → 재시작 → Health Check
- Health Check 실패 시 3단계 안전장치: 백업 복원 → 프로세스 재시작 → 상태 보고
- 텔레그램에서 `/deploy agent-a ./patch.zip` 명령으로 원격 배포

### Phase 5: 웹 대시보드 (2026-02-17 00:17 ~ 00:37)

FastAPI 기반 실시간 모니터링 대시보드.

| 커밋 | 설명 |
|------|------|
| `60614dc` | FastAPI 대시보드 셋업 (JWT 인증, Tailwind CSS) |
| `6277569` | 메인 대시보드 (에이전트 상태 카드, HTMX 자동 갱신) |
| `3f2a863` | 실시간 로그 뷰어 (WebSocket 스트리밍) |
| `1b13df4` | AI 리포트 뷰어 + 배포 히스토리 타임라인 |
| `cea6ee2` | 서버 헬스 모니터링 (CPU/MEM/DISK 텔레그램 알림) |

**구현 내용:**
- JWT 인증 기반 웹 대시보드
- 에이전트 접속 상태 카드 (HTMX 자동 갱신 5초 간격)
- WebSocket 기반 실시간 로그 스트리밍 뷰어
- AI 분석 리포트 조회 + 배포 히스토리 타임라인
- 서버 CPU/MEM/DISK 임계치 모니터링 → 텔레그램 알림

### Phase 6: Windows 서비스 + 자동 업데이트 (2026-02-17 00:44 ~ 00:59)

에이전트 Windows 서비스 등록 및 자체 업데이트.

| 커밋 | 설명 |
|------|------|
| `799f57e` | Windows 서비스 등록 (AILogOps-Agent) |
| `ac0a7aa` | win_service pyright 진단 정렬 |
| `97a40b2` | 자체 업데이트 메커니즘 (3단계 안전 롤백) |
| `a8ca656` | **v1.0.0 태그** — Phase 6 완료, 최종 통합 검증 |

**구현 내용:**
- pywin32 기반 Windows 서비스 (`sc install AILogOps-Agent`)
- 서비스 장애 시 자동 재시작 (sc failure 설정)
- 자체 업데이트: 서버에서 새 바이너리 수신 → 백업 → 교체 → 재시작 → 실패 시 3단계 롤백

### v1.0.0 릴리스 현황

| 항목 | 수치 |
|------|------|
| 총 커밋 | 37개 |
| 총 파일 | 70개 |
| 총 코드 | 8,853줄 |
| 테스트 | 369개 전체 통과 |
| 태그 | `v1.0.0` (`a8ca656`) |

---

### 빌드 셋업 (2026-02-17 09:33)

| 커밋 | 설명 |
|------|------|
| `9ac8998` | PyInstaller 빌드 셋업 (agent.spec, build_agent.bat) |

**구현 내용:**
- `agent.spec`: PyInstaller 빌드 스펙 (hidden imports, data files, 단일 폴더 배포)
- `build_agent.bat`: 원클릭 빌드 스크립트
- `dist/agent/agent.exe` 단일 실행파일 생성

---

### 기능 확장 v1.1: 로그 전송 + 프로세스 관리 + 스케줄링 + 시스템 모니터링 (2026-02-17 17:02 ~ 17:47)

4가지 주요 기능을 하나의 통합 계획으로 동시 구현.

| 커밋 | 설명 |
|------|------|
| `102c4cf` | CMD_LOG 프로토콜 타입 (CMD_LOG=0x15, CMD_LOG_ACK=0x16) |
| `da4c8c3` | LogWatcher 날짜 기반 파일 검색 + 최신 파일 탐지 |
| `0c74bbe` | ProcessManager.start() args 파라미터 추가 |
| `68fdfd5` | SystemMonitor (CPU/Memory/Handle/GDI, ctypes) |
| `8d00103` | TCPClient on_cmd_log + TCPServer send_log_command() |
| `ab55f5b` | ProcessMonitorLoop (자동 재시작 + 텔레그램 알림) |
| `de6f1f4` | ProcessScheduler (HH:MM 예약 프로세스 재시작) |
| `e2e83b6` | LogCmdHandler (HIST_REQUEST/REAL_START/REAL_STOP) |
| `4b7af93` | 서버 텔레그램 /log_hist, /log_real 명령 |
| `ef7fa00` | 에이전트 /status SystemMonitor 데이터 강화 |
| `18df4e6` | win_service.py 전체 컴포넌트 와이어링 |
| `662fe8d` | 25개 통합 테스트 |
| `2ec2ba2` | config.yaml + 구현 계획 문서 |

#### Feature A: 명령 기반 로그 전송

```
텔레그램 /log_hist agent-a 20260217
  → 서버 CMD_LOG(HIST_REQUEST, "20260217") → 에이전트
  → 에이전트: YYYYMMDD_*.txt 파일 검색 → LOG_HIST 패킷으로 전송
  → 서버: storage/logs/{agent_id}/ 에 저장

텔레그램 /log_real agent-a start
  → 에이전트: 감시 폴더 최신 파일 실시간 tail → LOG_REAL 패킷 전송
  → /log_real agent-a stop 으로 중지
```

**프로토콜 추가:**
- `CMD_LOG` (0x15): `[Action(1B)][Date(8B)]` — 서버→에이전트 로그 명령
- `CMD_LOG_ACK` (0x16): `[Action(1B)][Status(1B)][FileCount(2B)]` — 에이전트→서버 응답

#### Feature B: 프로세스 관리

- `ProcessManager.start(args=[...])`: 설정 가능한 프로세스 실행 인수
- `ProcessMonitorLoop`: 대상 프로세스 모니터링 → 없으면 자동 실행 (args 포함)
- 프로세스 상태 변경 시 텔레그램 알림 (시작/중지/자동실행)
- `config.yaml`에서 `auto_restart`, `check_interval`, `args` 설정

#### Feature C: 스케줄링

- `ProcessScheduler`: 지정 시간(HH:MM)에 대상 프로세스 자동 재시작
- `config.yaml`에서 `restart_times: ["06:00", "18:00"]` 설정
- 같은 분(minute) 내 중복 재시작 방지
- 재시작 시 텔레그램 알림

#### Feature D: 시스템 모니터링

- `SystemMonitor`: 전체 시스템 + 대상 프로세스 메트릭 수집
  - 시스템: CPU%, Memory%, Handle Count, GDI Count
  - 프로세스: CPU%, Memory(MB), Handle Count, GDI Count (ctypes)
- 에이전트 텔레그램 `/status` 명령으로 직접 응답
- `format_status_report()` 로 가독성 있는 리포트 생성

#### config.yaml 구조

```yaml
agent:
  id: agent-01
  version: 1.1.0

connection:
  host: 192.168.1.100
  port: 9500
  token: my-secret-token

monitoring:
  log_folders: ["C:/Logs/AppServer"]
  watch_extensions: [".txt", ".log"]
  history_max_mb: 10

target_process:
  name: MyApp.exe
  path: "C:/Program Files/MyApp/MyApp.exe"
  args: ["--config", "production.yaml"]
  auto_restart: true
  check_interval: 30

schedule:
  restart_times: ["06:00", "18:00"]

telegram:
  bot_token: "123456:ABC..."
  admin_chat_id: 987654321
```

**v1.1 현황:**

| 항목 | 수치 |
|------|------|
| 커밋 | 13개 |
| 신규/수정 파일 | 20개 |
| 추가 코드 | 4,845줄 |
| 테스트 | 369 → 394 (+25개) |

---

### 기능 확장 v1.2: 선택적 로그 전송 프로토콜 (2026-02-17 18:05 ~ 18:20)

기존 "전체 파일 전송" 방식을 "메타데이터 비교 후 선별 전송"으로 재설계.

| 커밋 | 설명 |
|------|------|
| `af167ab` | LOG_FILE_LIST(0x17), LOG_FILE_SELECT(0x18) 프로토콜 타입 추가 |
| `1723116` | LogWatcher.get_files_metadata() (파일명+크기+MD5) |
| `b3b5a45` | StorageManager.get_stored_file_metadata() (서버 측 비교용) |
| `796ee9e` | TCPClient.send_log_file_list() + on_log_file_select 콜백 |
| `d9c7e6c` | LogCmdHandler 2-Phase 재설계 (asyncio.Event 기반) |
| `1e416ab` | TCPServer LOG_FILE_LIST 핸들러 (저장소 비교 로직) |
| `29610a4` | win_service.py 콜백 와이어링 |
| `1dc95ac` | E2E 통합 테스트 + 텔레그램 피드백 업데이트 |

#### 변경 전 (v1.1)

```
서버 → CMD_LOG(HIST_REQUEST) → 에이전트
에이전트 → 매칭 파일 전부 LOG_HIST → 서버 (중복 전송 발생)
```

#### 변경 후 (v1.2) — 3-Step Selective Transfer

```
Step 1: 서버 → CMD_LOG(HIST_REQUEST, "20260217") → 에이전트
Step 2: 에이전트 → LOG_FILE_LIST [{filename, size, MD5}, ...] → 서버
Step 3: 서버가 로컬 저장소와 비교 → LOG_FILE_SELECT [선택된 파일명] → 에이전트
Step 4: 에이전트 → 선택된 파일만 LOG_HIST → 서버
```

#### 비교 로직 (서버 측)

```
각 에이전트 파일에 대해:
  if 서버에 파일 없음        → 전송 대상 (신규)
  elif 파일 크기 다름        → 전송 대상 (변경)
  elif MD5 해시 다름         → 전송 대상 (내용 변경)
  else                      → 스킵 (동일)
```

#### 시퀀스 다이어그램

```
텔레그램 사용자        서버                        에이전트
    │                   │                            │
    │ /log_hist A1 0217 │                            │
    │──────────────────>│                            │
    │                   │  CMD_LOG(HIST_REQUEST)      │
    │                   │───────────────────────────>│
    │                   │                            │ 파일 검색, MD5 계산
    │                   │  LOG_FILE_LIST              │
    │                   │<───────────────────────────│
    │                   │ 로컬 저장소 비교             │
    │                   │  LOG_FILE_SELECT            │
    │                   │───────────────────────────>│
    │                   │                            │ 선택된 파일만 전송
    │                   │  LOG_HIST (file 1)          │
    │                   │<───────────────────────────│
    │                   │  LOG_HIST (file N)          │
    │                   │<───────────────────────────│
    │ "N/M 파일 전송"    │                            │
    │<──────────────────│                            │
```

#### 추가된 프로토콜 타입

| 패킷 타입 | 값 | 방향 | 페이로드 |
|----------|-----|------|---------|
| `LOG_FILE_LIST` | 0x17 | Agent→Server | `[Count(2B)] + N × [Filename(256B) + Size(4B) + MD5(16B)]` |
| `LOG_FILE_SELECT` | 0x18 | Server→Agent | `[Count(2B)] + N × [Filename(256B)]` |

#### 에이전트 2-Phase 처리 (asyncio.Event)

```python
Phase 1: HIST_REQUEST 수신
  → get_files_metadata(date_str)  # filename, size, MD5 수집
  → send_log_file_list(entries)   # LOG_FILE_LIST 전송
  → asyncio.Event 대기 (timeout 30초)

Phase 2: LOG_FILE_SELECT 수신 (콜백)
  → Event.set() 으로 Phase 1 대기 해제
  → 선택된 파일만 read_history() → send_log_history()
```

**v1.2 현황:**

| 항목 | 수치 |
|------|------|
| 커밋 | 8개 |
| 신규/수정 파일 | 12개 |
| 테스트 | 394 → 406 (+12개) |

---

## 3. TCP 바이너리 프로토콜 전체 맵

### 패킷 헤더 (5 bytes)

```
[PacketType(1B)] [PayloadLength(4B, big-endian)]
```

### 패킷 타입 일람

| 값 | 이름 | 방향 | 용도 | 페이로드 크기 |
|----|------|------|------|-------------|
| 0x01 | AUTH | C→S | 에이전트 인증 | 104B 고정 |
| 0x02 | AUTH_ACK | S→C | 인증 응답 | 17B 고정 |
| 0x03 | LOG_HIST | C→S | 누적 로그 전송 | 260B + 가변 |
| 0x04 | LOG_REAL | C→S | 실시간 로그 라인 | 258B + 가변 |
| 0x10 | CMD_DEPLOY | S→C | 배포 명령 | 292B 고정 |
| 0x11 | FILE_CHUNK | S→C | 파일 청크 | 6B + max 4096B |
| 0x12 | FILE_ACK | C→S | 청크 수신 확인 | 5B 고정 |
| 0x13 | CMD_CTRL | S→C | 프로세스 제어 | 1B 고정 |
| 0x14 | CMD_CTRL_ACK | C→S | 제어 응답 | 6B 고정 |
| 0x15 | CMD_LOG | S→C | 로그 전송 명령 | 9B 고정 |
| 0x16 | CMD_LOG_ACK | C→S | 로그 명령 응답 | 4B 고정 |
| 0x17 | LOG_FILE_LIST | C→S | 파일 메타 목록 | 2B + N×276B |
| 0x18 | LOG_FILE_SELECT | S→C | 파일 선택 목록 | 2B + N×256B |
| 0x20 | AGENT_UPDATE | S→C | 에이전트 업데이트 | 가변 |
| 0xFE | HEARTBEAT | 양방향 | 생존 확인 | 10B 고정 |
| 0xFF | DISCONNECT | 양방향 | 연결 해제 | 1B 고정 |

---

## 4. 프로젝트 구조

```
AI-LogOps/
├── shared/                          # 서버-에이전트 공통 모듈
│   ├── protocol.py                  # 패킷 타입, 페이로드 직렬화 (16개 타입)
│   ├── models.py                    # AgentState, AgentInfo, AgentSession, DeployRecord
│   └── utils.py                     # SHA-256, YAML config, 로깅, 감사 로그
│
├── server/                          # 명령 서버
│   ├── core/
│   │   ├── tcp_server.py            # TCP 서버 (AUTH, 패킷 라우팅, 배포, 로그 비교)
│   │   ├── session_mgr.py           # 다중 에이전트 세션 관리
│   │   └── health_monitor.py        # 서버 CPU/MEM/DISK 모니터링
│   ├── ai/
│   │   ├── provider.py              # AI Provider 추상화 (OpenAI/Claude)
│   │   ├── pipeline.py              # 3-Stage + Step 3.5 AI 파이프라인
│   │   ├── prompt_loader.py         # Jinja2 프롬프트 로더
│   │   └── prompts/*.j2             # 단계별 프롬프트 템플릿
│   ├── telegram/
│   │   ├── handler.py               # 명령 파싱, 관리자 인증
│   │   ├── rate_limiter.py          # 슬라이딩 윈도우 Rate Limiter
│   │   ├── ai_commands.py           # /analyze, /plan, /fix, /auto
│   │   ├── deploy_commands.py       # /deploy, /rollback
│   │   └── log_commands.py          # /log_hist, /log_real
│   ├── dashboard/
│   │   ├── app.py                   # FastAPI 앱 (JWT)
│   │   ├── auth.py                  # JWT 인증
│   │   ├── routes/*.py              # 대시보드/로그/리포트/배포 라우트
│   │   └── templates/*.html         # HTMX + Tailwind 템플릿
│   └── storage/
│       └── manager.py               # 로그/리포트/백업 저장 관리
│
├── agent/                           # 원격 에이전트
│   ├── core/
│   │   ├── tcp_client.py            # TCP 클라이언트 (콜백 기반 수신)
│   │   ├── log_watcher.py           # watchdog 파일 감시 + 메타데이터 수집
│   │   ├── log_cmd_handler.py       # 2-Phase 선택적 로그 전송 핸들러
│   │   ├── process_mgr.py           # 프로세스 시작/종료/롤백 (args 지원)
│   │   ├── process_monitor.py       # 프로세스 자동 재시작 모니터 루프
│   │   ├── scheduler.py             # HH:MM 예약 재시작 스케줄러
│   │   ├── system_monitor.py        # CPU/MEM/Handle/GDI 수집 (ctypes)
│   │   ├── file_transfer.py         # 파일 수신 + SHA-256 검증
│   │   └── deploy_handler.py        # 배포 처리 (백업/교체/검증/롤백)
│   ├── telegram/
│   │   └── poller.py                # /connect, /disconnect, /status (SystemMonitor 포함)
│   ├── service/
│   │   └── win_service.py           # Windows 서비스 (전체 컴포넌트 와이어링)
│   ├── updater/
│   │   └── self_update.py           # 자체 업데이트 (3단계 롤백)
│   └── config.yaml                  # 에이전트 설정 파일
│
├── tests/                           # 테스트 (406개)
│   ├── test_protocol.py             # 프로토콜 페이로드 직렬화 (40개)
│   ├── test_tcp_server.py           # TCP 서버 핸들러 (16개)
│   ├── test_tcp_client.py           # TCP 클라이언트 (12개)
│   ├── test_log_watcher.py          # 로그 감시 (12개)
│   ├── test_log_cmd_handler.py      # 로그 명령 핸들러 (9개)
│   ├── test_log_commands.py         # 텔레그램 로그 명령 (6개)
│   ├── test_storage.py              # 저장소 관리 (8개)
│   ├── test_process_mgr.py          # 프로세스 관리 (8개)
│   ├── test_process_monitor.py      # 자동 재시작 (5개)
│   ├── test_scheduler.py            # 스케줄러 (6개)
│   ├── test_system_monitor.py       # 시스템 모니터 (6개)
│   ├── test_integration_features.py # 기능 통합 테스트 (28개)
│   ├── test_integration_phase2.py   # Phase 2 E2E (5개)
│   ├── test_integration_final.py    # 최종 E2E (3개)
│   └── ... (기타 테스트 파일)
│
├── docs/
│   ├── AI-LogOps_Technical_Spec.docx
│   ├── AI-LogOps_Technical_Spec_v2.1.md
│   └── plans/
│       ├── 2026-02-16-ai-logops-implementation.md
│       ├── 2026-02-17-agent-feature-enhancement.md
│       ├── 2026-02-17-selective-log-transfer-design.md
│       └── 2026-02-17-selective-log-transfer.md
│
├── agent.spec                       # PyInstaller 빌드 스펙
├── build_agent.bat                  # 빌드 스크립트
├── requirements.txt                 # 의존성
└── pytest.ini                       # pytest 설정
```

---

## 5. 테스트 현황

| 단계 | 테스트 수 | 누적 |
|------|----------|------|
| v1.0.0 (Phase 1~6) | 369 | 369 |
| v1.1 (기능 확장 4종) | +25 | 394 |
| v1.2 (선택적 로그 전송) | +12 | 406 |

**전체 406개 테스트 통과, 0 실패.**

---

## 6. 전체 커밋 이력

| # | 커밋 | 일시 | 설명 |
|---|------|------|------|
| 1 | `da8957e` | 02-16 19:31 | 프로젝트 스캐폴딩 |
| 2 | `ebcf75d` | 02-16 19:37 | TCP 바이너리 프로토콜 정의 |
| 3 | `dd26a89` | 02-16 19:38 | AUTH/HEARTBEAT/DISCONNECT 직렬화 |
| 4 | `92bef84` | 02-16 19:42 | AuthPayload 검증 수정 |
| 5 | `341d200` | 02-16 19:47 | 공유 데이터 모델 |
| 6 | `88a8b37` | 02-16 19:47 | 유틸리티 모듈 |
| 7 | `876fed5` | 02-16 19:55 | SessionManager |
| 8 | `3b4580b` | 02-16 19:55 | TCP 서버 |
| 9 | `276284c` | 02-16 19:59 | TCP 클라이언트 |
| 10 | `d33653e` | 02-16 20:05 | 텔레그램 Rate Limiter |
| 11 | `23a772a` | 02-16 20:05 | 텔레그램 핸들러 |
| 12 | `225c864` | 02-16 20:05 | 에이전트 텔레그램 폴러 |
| 13 | `5a6c3a8` | 02-16 20:09 | Phase 1 통합 테스트 |
| 14 | `4df3050` | 02-16 20:16 | LOG_HIST/LOG_REAL 직렬화 |
| 15 | `2e773f8` | 02-16 20:16 | LogWatcher |
| 16 | `241f036` | 02-16 20:16 | 클라이언트 로그 전송 |
| 17 | `68da0ea` | 02-16 20:22 | StorageManager |
| 18 | `e84179b` | 02-16 20:22 | 서버 로그 핸들러 |
| 19 | `2c97646` | 02-16 20:22 | Phase 2 통합 테스트 |
| 20 | `14917a4` | 02-16 20:30 | AI Provider |
| 21 | `f8d582f` | 02-16 20:37 | Jinja2 프롬프트 템플릿 |
| 22 | `6cf93c3` | 02-16 20:44 | AI 파이프라인 (3-Stage) |
| 23 | `49e65cd` | 02-16 20:49 | Step 3.5 자동 검증 |
| 24 | `adcffa2` | 02-16 20:57 | 텔레그램 AI 명령 |
| 25 | `e72f8d7` | 02-16 21:26 | 파일 전송 (청크+SHA-256) |
| 26 | `c4890d8` | 02-16 21:31 | ProcessManager |
| 27 | `b3d8a83` | 02-16 21:40 | 배포 플로우 + 자동 롤백 |
| 28 | `d3e0d97` | 02-16 21:45 | 텔레그램 배포 명령 |
| 29 | `60614dc` | 02-17 00:17 | FastAPI 대시보드 셋업 |
| 30 | `6277569` | 02-17 00:23 | 대시보드 메인 페이지 |
| 31 | `3f2a863` | 02-17 00:27 | WebSocket 로그 뷰어 |
| 32 | `1b13df4` | 02-17 00:31 | AI 리포트 + 배포 히스토리 |
| 33 | `cea6ee2` | 02-17 00:37 | 서버 헬스 모니터링 |
| 34 | `799f57e` | 02-17 00:44 | Windows 서비스 |
| 35 | `ac0a7aa` | 02-17 00:47 | win_service pyright 정렬 |
| 36 | `97a40b2` | 02-17 00:53 | 자체 업데이트 |
| 37 | `a8ca656` | 02-17 00:59 | **v1.0.0 릴리스** |
| 38 | `9ac8998` | 02-17 09:33 | PyInstaller 빌드 셋업 |
| 39 | `102c4cf` | 02-17 17:02 | CMD_LOG 프로토콜 타입 |
| 40 | `da4c8c3` | 02-17 17:06 | LogWatcher 날짜 검색 |
| 41 | `0c74bbe` | 02-17 17:29 | ProcessManager args |
| 42 | `68fdfd5` | 02-17 17:32 | SystemMonitor |
| 43 | `8d00103` | 02-17 17:37 | CMD_LOG 핸들링 |
| 44 | `ab55f5b` | 02-17 17:34 | ProcessMonitorLoop |
| 45 | `de6f1f4` | 02-17 17:35 | ProcessScheduler |
| 46 | `e2e83b6` | 02-17 17:39 | LogCmdHandler |
| 47 | `4b7af93` | 02-17 17:40 | 텔레그램 로그 명령 |
| 48 | `ef7fa00` | 02-17 17:40 | 에이전트 /status 강화 |
| 49 | `18df4e6` | 02-17 17:45 | 전체 와이어링 |
| 50 | `662fe8d` | 02-17 17:46 | 통합 테스트 25개 |
| 51 | `2ec2ba2` | 02-17 17:47 | config.yaml + 계획 문서 |
| 52 | `af167ab` | 02-17 18:05 | LOG_FILE_LIST/SELECT 프로토콜 |
| 53 | `b3b5a45` | 02-17 18:08 | StorageManager 메타데이터 |
| 54 | `1723116` | 02-17 18:08 | LogWatcher 메타데이터 |
| 55 | `796ee9e` | 02-17 18:09 | TCPClient 파일 목록 전송 |
| 56 | `d9c7e6c` | 02-17 18:15 | LogCmdHandler 2-Phase 재설계 |
| 57 | `1e416ab` | 02-17 18:15 | TCPServer 파일 목록 비교 |
| 58 | `1dc95ac` | 02-17 18:19 | E2E 테스트 + 텔레그램 피드백 |
| 59 | `29610a4` | 02-17 18:20 | win_service 콜백 와이어링 |

**총 59개 커밋, 2일간 개발.**
