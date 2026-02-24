# AI-LogOps 개발 내역서

**프로젝트:** AI-LogOps Auto-Deployer  
**설명:** 다중 에이전트 원격 로그 분석 및 자동 배포 시스템  
**개발 기간:** 2026-02-16 ~ 현재  
**브랜치:** `develop`  
**최신 버전:** v1.7.0
**테스트:** 406 전체 통과

---

## 0-1. v1.4.4 (2026-02-21)

### 주요 변경 사항

#### Git 시크릿 제거 & 보안 강화
- `server/config.yaml`에서 하드코딩된 시크릿(OpenAI API Key, Telegram 봇 토큰) 제거
- `git filter-repo --replace-text`로 전체 히스토리(66개 커밋)에서 시크릿 완전 제거
- 모든 비밀 값은 `.env` 또는 환경 변수에서 로드하도록 변경

#### 서버 봇 명령 라우팅 정리
- `run_server.py`에서 dead code 제거: `agent_bot` 초기화, `send_to_agent()` 함수, agent 봇 전달 코드
- `_run_telegram()` 파라미터에서 `agent_bot_token`, `agent_chat_id` 제거
- 에이전트 send-only 전환 완료로 불필요해진 코드 정리

#### TCP 이벤트 텔레그램 알림
- `TCPServer`에 `notify_callback` 매개변수 추가
- 에이전트 접속/해제, 배포 완료/롤백 시 텔레그램 자동 알림
- `_telegram_notify()` 범용화 (HealthMonitor + TCPServer 공용)

#### E2E STT 파이프라인 테스트
- `tests/test_stt_pipeline_e2e.py` 신규 작성 (25개 테스트)
  - RecHandler 분석 결과 처리, 업로드 결정 로직 (8 tests)
  - WAV 저장/검색 (4 tests)
  - 통화 품질 점수 계산 (6 tests)
  - 전체 파이프라인 mock STT (4 tests)
  - RecHandler → SttResultPayload 변환 (3 tests)

#### 기존 테스트 수정 (14개 실패 → 0개)
- `test_self_update.py`: SelfUpdater 신 API에 맞게 전면 재작성
- `test_integration_phase4.py`: StubProcessManager.kill_all() + chunk 수 동적 계산
- `test_integration_final.py`: SelfUpdater API, faster_whisper optional import, chunk_size 수정

### 수정 파일

| 파일 | 변경 내용 |
|------|-----------|
| `server/config.yaml` | 시크릿 제거, .env 참조 안내 |
| `server/core/tcp_server.py` | notify_callback, _notify() 헬퍼, 이벤트 알림 |
| `run_server.py` | dead code 제거, _telegram_notify 범용화 |
| `tests/test_stt_pipeline_e2e.py` | 신규: 25개 E2E STT 테스트 |
| `tests/test_self_update.py` | SelfUpdater 신 API 전면 재작성 |
| `tests/test_integration_phase4.py` | kill_all() 스텁 + chunk 수 수정 |
| `tests/test_integration_final.py` | SelfUpdater + faster_whisper + chunk_size |

### 발견 사항

- GitHub Push Protection이 config.yaml의 하드코딩된 시크릿 차단 → filter-repo로 해결
- `unittest.mock.patch` 경로: lazy import 사용 시 모듈 경로가 달라짐
- `faster_whisper` 모듈 미설치 환경에서 테스트 시 sys.modules 스텁 필요
- `SelfUpdater` API가 완전히 변경됨 — 구 API와 호환 불가

## 0. v1.4.0 ~ v1.4.3 (2026-02-20)

### 주요 변경 사항

#### 프로세스 관리 강화 (3계층 방어)
- `ProcessManager`에 `find_all_pids()` + `kill_all()` 추가 — 다중 인스턴스 일괄 종료
- `updater.bat`에 종료 확인 루프 추가 — taskkill 후 최대 30초 폴링
- PID 파일 기반 싱글톤 잠금 (`acquire_instance_lock()` / `release_instance_lock()`)
- 모든 호출부 `kill()` → `kill_all()` 변경 (deploy_handler, scheduler, process_deploy)

#### 에이전트 안정성 향상
- Telegram poller graceful degradation — 토큰 플레이스홀더/빈값 검사
- GUI `_quit_app()` — `os._exit(0)` 3초 타이머로 좀비 프로세스 방지
- GUI/서비스 모드 자동 TCP 접속 + 재접속 (config.yaml `reconnect_delay` 설정 가능, 기본 60초)

#### 텔레그램 아키텍처 변경 (v1.4.3)
- **에이전트 텔레그램: send-only로 전환** — 폴링(getUpdates) 완전 제거
- 다중 에이전트 환경에서 동일 봇 토큰 폴링 충돌 방지
- 명령 수신은 서버 봇 → TCP 경유로 라우팅
- 에이전트는 알림 전송(`sendMessage`)만 수행

#### 배포 시스템 개선
- `deploy.py` 대화형 에이전트 선택 지원
- `--agent-id all` 전체 배포, `--agent-id PC-01` 특정 배포, 미지정 시 번호 선택
- zip에서 `config.yaml` 제외 (수동 압축 해제 시 사용자 설정 덮어쓰기 방지)
- `CHUNK_SIZE` 4096 → 65535 (프로토콜 H 필드 최대값). 서버 전송은 4096 유지 (하위 호환)

### 수정 파일

| 파일 | 변경 내용 |
|------|-----------|
| `agent/__init__.py` | v1.4.3 |
| `agent/core/process_mgr.py` | find_all_pids, kill_all, 싱글톤 잠금 |
| `agent/updater/self_update.py` | bat 템플릿 종료 확인 루프 |
| `agent/core/deploy_handler.py` | kill → kill_all |
| `agent/core/scheduler.py` | kill → kill_all |
| `agent/updater/process_deploy.py` | kill → kill_all |
| `agent/service/win_service.py` | 싱글톤 잠금 + 자동 TCP 접속/재접속 |
| `agent/gui/app.py` | 싱글톤 잠금 + quit 강제종료 + 자동 TCP 접속/재접속 |
| `agent/telegram/poller.py` | send-only (폴링 제거) |
| `agent/config.yaml` | reconnect_delay 60초 기본값 |
| `deploy.py` | 대화형 에이전트 선택 + 전체 배포 |
| `server/core/tcp_server.py` | deploy_chunk_size 4096 (하위 호환) |
| `shared/protocol.py` | CHUNK_SIZE 65535 |
| `tests/test_protocol.py` | CHUNK_SIZE assert 업데이트 |
| `tests/test_scheduler.py` | kill → kill_all 테스트 |
| `tests/test_integration_features.py` | kill → kill_all 테스트 |

### 발견 사항

- 에이전트 프로세스 다중 인스턴스 문제: `find_pid()`가 첫 매치만 반환
- `updater.bat`이 taskkill 후 종료 확인 없이 진행
- `TCPClient`에 자동 접속/재접속 로직 부재 (필드만 존재)
- Telegram `getUpdates` 단일 소비자 제약 → 다중 에이전트 시 send-only 필수
- PyInstaller 6.x는 `_internal/`에 데이터 파일 배치 → config.yaml root 복사 주의
- 서버 공인 IP 끝자리 불일치 (51 vs 61) — 현장 확인 필요

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
| 60 | `b396a1a` | 02-20 | Git 시크릿 제거 + filter-repo |
| 61 | `9ca6abf` | 02-21 | TCP 이벤트 알림, dead code 정리, E2E STT 테스트 |

**총 61개 커밋, 3일간 개발.**

---

## 0-2. v1.4.5 ~ v1.4.9 (2026-02-20 ~ 02-21)

### 주요 변경 사항

#### STT 녹취 분석 파이프라인 (v1.4.5, 02-20)
- `server/airec/analyzer/` 모듈 추가: faster-whisper 기반 STT 파이프라인
- `server/airec/` 녹취 서버 컴포넌트 생성 (RecordingWatcher, RecordingStorage, AnalysisScheduler)
- Agent↔Server 간 TCP 녹취 명령 프로토콜 설계
- `server/airec/database.py` — 녹취 분석 결과 MySQL 저장
- 녹취 분석 테스트 22개 추가, 기능 문서화

#### 에이전트 GUI + 버전 관리 (02-20)
- `agent/gui/app.py` — tkinter 기반 Windows 에이전트 GUI 추가
- 버전 관리 (`agent/__init__.py` `__version__`) 및 서비스/GUI 이중 실행 지원
- 텔레그램 2-봇 구조 (서버 봇 + 에이전트 봇) 정리
- 서버 경유 자동 배포 (`eeb3d04`) — HTTP API 업로드 → TCP 전달
- deploy 청크 64KB 증가, 비동기 처리 (`cfb5197`)

#### 프로세스 관리 강화 + 자동 재연결 (02-21)
- `fab8dee`: ProcessManager 강화, 자동 재연결, 텔레그램 send-only 전환, 멀티에이전트 배포
- TCP 재연결 로직 개선: 서버 재시작 시 빠른 재연결 (`afa63c7`)

#### µ-law WAV 지원 (02-21)
- `5c8ac1d`: 전화 녹음 포맷 µ-law fmt=7 PCM 디코딩 지원 (`wave` 모듈 우회, numpy rawread)
- 모노 µ-law WAV → 16bit PCM 스테레오 변환 후 STT 업로드
- `8179bf7`: 녹취 분석 로깅 추가, `get_wav_duration` µ-law 지원

#### 파일 안정화 + v1.4.8 / v1.4.9 (02-21)
- `9804961` (v1.4.8): 녹취 파일 쓰기 완료 후 분석 시작 (10초 안정화 대기)
- `eabcd2d` (v1.4.9): `rec_no` → `filename` 식별자 전환 — 전체 파이프라인 리팩토링
- `2f0177c`: 오래된 파일 우선 스캔 + 안정화 10초 대기
- `fbb500d`: 에이전트 GUI에 강제 재연결 버튼 추가
- `d9aae94`: 모노 녹음도 STT 업로드 허용 (기존 스테레오 전용 제한 해제)

### 수정된 주요 파일

| 파일 | 변경 내용 |
|------|----------|
| `server/airec/analyzer/stt.py` | STT 파이프라인 초기 구현 |
| `server/airec/analyzer/pipeline.py` | 녹취 분석 오케스트레이터 |
| `server/airec/watcher.py` | RecordingWatcher — 폴더 감시 |
| `agent/gui/app.py` | tkinter GUI 추가 |
| `agent/core/tcp_client.py` | 녹취 명령 콜백 추가 |
| `shared/protocol.py` | 녹취 프로토콜 타입 추가 |

---

## 0-3. v1.5.0 ~ v1.6.5 (2026-02-21 ~ 02-23)

### 주요 변경 사항

#### v1.5.0 — 서버 경유 프로세스 원격 배포 (02-21)
- `987888b`: 서버를 통한 원격 프로세스 배포 기능 추가
- `deploy.py --target process --process-dir` CLI 옵션
- 에이전트의 `ProcessDeployer`가 서버에서 받은 zip을 압축 해제 후 실행

#### 대시보드 — 녹취 분석 UI (02-21)
- `587a65b`: 대시보드에 녹취 분석 시작/중지 컨트롤 추가

#### v1.6.5 — 녹취 분석 서버-에이전트 통합 완성 (02-23)

**녹취 프로토콜 확장:**
- `5b4fdc6`: CMD_REC (START/STOP/STATUS), REC_DATA_REQ, REC_DATA_RESP 프로토콜 추가
- 서버가 에이전트에게 분석 명령 전송, 에이전트가 결과 반환하는 양방향 구조

**에이전트 DB 헬퍼:**
- `5a965ff`: 에이전트 로컈 DB(rec_his, ext_his)에서 녹취 데이터 조회 모듈 추가
- `01fbcc7`: TCP 클라이언트에 녹취 명령 및 데이터 쿼리 콜백 추가

**로그 선택적 전송 개선:**
- `10f0f54`: 파일 메타데이터 기반 선택적 로그 전송, 파일 목록 최적화

**녹취 감시 고도화:**
- `5308592`: 서버 주도 분석 — 서버가 요청 시에만 에이전트 분석 실행
- 오디오 품질 검사 (채널 RMS, 무음 비율, 드롭아웃 감지)
- 텔레그램 이상 알림 통합

**서비스/GUI 통합:**
- `b57d34a` (v1.6.5): win_service.py에 녹취 분석 완전 통합
- `82d0667`: 에이전트 GUI에 녹취 분석 상태 표시 추가

**서버 STT/품질 파이프라인 강화:**
- `501283e`: faster-whisper 한국어 최적화, call quality 분석기 개선
- `1a71e25`: 녹취 분석 결과 DB 스키마 (`rec_audio_quality` 테이블) 추가
- `6f1f139`: TCP 서버에 녹취 명령, 배포, 데이터 쿼리 핸들러 추가

**서버 옵션 및 RBAC:**
- `87cb6d4`: `public_url` 설정, `--no-telegram` 서버 옵션
- `7eb0516`: RBAC 사용자/권한 관리 시스템 추가
- `1d15a74`: RBAC를 대시보드 미들웨어 및 네비게이션에 통합

**대시보드 기능 대폭 확장:**
- `db7d347`: 녹취 분석 대시보드 — 실시간 컨트롤, 상태 표시
- `e224587`: 녹취 목록 + 상세 팝업 (파형 플레이어, 트랜스크립트)
- `42c72d0`: 로그 대시보드 실시간 스트리밍
- `50a2254`: 배포 대시보드에 프로세스 배포 + 녹취 클라이언트 배포 섹션 추가

**서버 관리 GUI:**
- `e7e4ecb`: tkinter 기반 서버 관리 프로그램 (`run_server_gui.py`) 추가
  - 에이전트 현황, 로그 뷰어, 배포 탭, 녹취 분석 탭

### 수정된 주요 파일

| 파일 | 변경 내용 |
|------|----------|
| `shared/protocol.py` | CMD_REC, REC_DATA_REQ/RESP 프로토콜 추가 |
| `agent/service/win_service.py` | 녹취 분석 완전 통합 (1,000줄+) |
| `agent/gui/app.py` | 녹취 분석 상태 표시 |
| `server/core/tcp_server.py` | 녹취 명령 핸들러 추가 |
| `server/airec/rec_handler.py` | 서버 측 녹취 메시지 처리 |
| `server/airec/analyzer/pipeline.py` | STT + 품질 분석 파이프라인 |
| `server/dashboard/` | RBAC, 녹취/로그/배포 대시보드 강화 |
| `run_server_gui.py` | 서버 관리 GUI 신규 추가 |

---

## 0-4. v1.7.0 (2026-02-23) — 에이전트 공통 로직 추출 + 멀티 서버 지원

### 주요 변경 사항

#### ConfigView (02-23)
- `9559b25`: `agent/core/config_view.py` — 중복 config 헬퍼 함수 통합
  - `win_service.py`의 `_as_mapping`/`_to_str` 등 5개 함수
  - `gui/app.py`의 `_m`/`_s`/`_i`/`_b`/`_ls` 등 5개 함수
  - → `ConfigView.s()`, `.i()`, `.b()`, `.ls()`, `.sub()` 통일

#### RecordingController (02-23)
- `4554333`: `agent/recording/controller.py` 신규 생성
  - `win_service.py`와 `gui/app.py`에 중복된 녹취 핸들러 약 315줄 추출
  - `handle_cmd_rec`, `handle_rec_upload_req`, `handle_stt_result`, `handle_rec_data_req` 메서드
  - `nonlocal` 클로저 → 인스턴스 속성으로 상태 관리

#### AgentRuntime 오케스트레이터 (02-23)
- `7fb7c22`: `agent/core/agent_runtime.py` 신규 생성
  - `win_service.py` `_run_agent()` ~600줄, `gui/app.py` `_run_agent_async()` ~600줄 → AgentRuntime으로 통합
  - `AgentHooks` dataclass: 모드별 콜백 (GUI/서비스 분기)
  - `asyncio.Event` 기반 정지 메커니즘

#### win_service.py / gui/app.py 리팩토링 (02-23)
- `0b41b03`: win_service.py 1,032줄 → ~200줄, gui/app.py 1,181줄 → ~400줄
  - 모든 공통 로직 제거, AgentRuntime 사용
  - 서비스 모드: win32event → asyncio.Event 브릿지
  - GUI 모드: `self._root.after(0, ...)` 콜백 훅 주입

#### 멀티 서버 지원 (02-23)
- `2dcfd95`: `agent/core/server_connection.py` 신규 생성
  - `ServerConfig`, `ServerConnection`, `RecordingOwnership` dataclass
  - 하위 호환: 기존 `connection:` 설정 → 단일 서버 `"default"`로 자동 변환
  - 새 포맷: `servers:` 리스트로 N개 서버 동시 연결
  - 녹취 소유권 잠금: 먼저 START 명령을 보낸 서버가 단독 소유
  - 로그 fan-out: 실시간 모드 활성 서버 전체에 병렬 전송

#### v1.7.0 배포 (02-23)
- `54d6d5f`: 에이전트 버전 1.7.0으로 업데이트 및 릴리스
- `d19ed38`: `CLAUDE.md` — AI-LogOps 프로젝트 규칙 및 배포 가이드 문서화

### 수정된 주요 파일

| 파일 | 변경 내용 |
|------|----------|
| `agent/core/config_view.py` | **신규** — 타입 안전 config 접근자 |
| `agent/recording/controller.py` | **신규** — 녹취 명령 핸들러 클래스 |
| `agent/core/agent_runtime.py` | **신규** — 에이전트 공통 오케스트레이터 |
| `agent/core/server_connection.py` | **신규** — 멀티 서버 연결 컨텍스트 |
| `agent/service/win_service.py` | 1,032 → ~200줄 (AgentRuntime 사용) |
| `agent/gui/app.py` | 1,181 → ~400줄 (AgentRuntime 사용) |
| `agent/__init__.py` | 버전 1.7.0 |
| `CLAUDE.md` | **신규** — 프로젝트 규칙 문서 |

---

## 0-5. v1.7.1 (2026-02-24) — STT 한국어 최적화 + 세그먼트 분할

### 주요 변경 사항

#### 한국어 STT 최적화 조사 및 구현

사용자 요구: 전화 녹취 STT 정확도 향상 및 대화 시간대별 분리

**조사 결과:**
- 한국어 파인튜닝 모델(`ghost613/faster-whisper-large-v3-turbo-korean`) 테스트 → µ-law 8kHz 전화 음성에서 0개 세그먼트 생성 → 채택 불가
- 기본 `large-v3-turbo` 모델이 전화 녹음에서 가장 안정적

**config.yaml `stt:` 셉션 추가:**
- VAD `min_silence_duration_ms` 700 → **300ms** 변경 (핵심 수정)
- VAD `threshold` 0.5 → 0.35, `min_speech_duration_ms` 200 → 150 ('네', '예' 캡처)
- `no_speech_threshold` 0.6 → 0.8 (환각 방지 강화)
- 오디오 전처리: 리샘플링(16kHz), 대역통과필터(300~3400Hz), 노이즈 감소, 음량 정규화

#### 오디오 전처리 파이프라인 추가 (`stt.py`)
- `_preprocess_audio(audio, sr)`: WAV 로드 → µ-law 디코딩 → 리샘플링 → 대역필터 → 노이즈감소 → 정규화
- 의존성 추가: `scipy`, `noisereduce` (`requirements.txt`)

#### 도메인 프롬프트 자동 업데이트 (`stt.py`)
- `_get_domain_prompt()`: 대리운전 도메인 어휘를 Whisper `initial_prompt`로 주입
- `_update_domain_prompt()`: STT 결과에서 위치/지명 패턴 추출, 50건마다 자동 갱신
- `_accumulate_stt_data()`: 지명 데이터 `storage/stt/training_data/`에 누적, 100건마다 vocabulary 갱신

#### 세그먼트 분할 알고리즘 (핵심 버그 수정)

**문제:** 전체 대화가 1개 세그먼트로 출력 (Whisper word_segments 인덱스 불일치)

**구현된 함수들:**

| 함수 | 역할 |
|------|------|
| `_map_words_to_segments()` | word timestamps를 VAD 분할 세그먼트에 재매핑 |
| `_split_long_segments()` | 8초 초과 세그먼트 후처리 분할 (3-tier) |
| `_split_by_words()` | 한국어 문장 종결 패턴으로 분할, elapsed 기준 버그 수정 |
| `_split_by_text()` | 텍스트 기반 분할 (word timestamps 없을 때) |
| `_split_by_time()` | Gap 감지(무음>2초) + 균등 시간 분할 (최후 수단) |

**핵심 수정 사항:**
1. `_split_by_text()` 누락된 `return` 문 추가
2. `_map_words_to_segments()` 신규 구현 — raw 세그먼트 word_segments를 VAD 분할 세그먼트에 재매핑
3. `transcribe()` 에서 `_map_words_to_segments()` 호출 추가
4. `_split_by_words()` `elapsed` 계산 기준 수정 (첫 단어 → 마지막 분할 시점)
5. `_split_by_words()` 분할 후 초과 세그먼트 재분할 추가
6. `_split_by_time()` gap 감지 로직 추가 (단어 간 무음 > 2초 강제 분할)

**테스트 결과:**
- 92초 스테레오 파일: 1덩어리 → 21개 세그먼트, 최대 5.9초
- 전체 WAV 파일 (20260221~20260224): 모든 세그먼트 ≤ 8.0초
- `run_pipeline()` 통합 테스트 PASS

#### pipeline.py 개선
- `_get_engine_model_name()`: `config.yaml stt.whisper.model`에서 동적으로 모델명 읽기

### 수정된 주요 파일

| 파일 | 변경 내용 |
|------|----------|
| `server/airec/analyzer/stt.py` | 오디오 전처리, VAD 최적화, 세그먼트 분할 알고리즘 전면 재작성 |
| `server/airec/analyzer/pipeline.py` | 동적 모델명 매핑 추가 |
| `server/config.yaml` | `stt:` 섹션 추가 (VAD, 전처리, 환각 방지 파라미터) |
| `requirements.txt` | `scipy`, `noisereduce` 추가 |
| `docs/STT_Korean_Optimization_Project.md` | **신규** — STT 최적화 프로젝트 상세 문서 |

---

## 4-1. 프로젝트 구조 (현재)

```
AI-LogOps/
├── agent/
│   ├── core/
│   │   ├── agent_runtime.py         # ★ AgentRuntime 오케스트레이터
│   │   ├── config_view.py           # ★ 타입 안전 config 접근자
│   │   ├── server_connection.py     # ★ 멀티 서버 연결 컨텍스트
│   │   ├── tcp_client.py
│   │   ├── log_watcher.py
│   │   ├── log_cmd_handler.py
│   │   └── deploy_handler.py
│   ├── recording/
│   │   ├── controller.py            # ★ RecordingController
│   │   ├── watcher.py               # 녹취 폴더 감시
│   │   ├── uploader.py              # HTTPS WAV 업로드
│   │   ├── audio_quality.py         # 오디오 품질 분석
│   │   ├── energy.py
│   │   ├── silence.py
│   │   └── models.py
│   ├── service/
│   │   └── win_service.py           # Windows 서비스 (AgentRuntime 사용)
│   ├── gui/
│   │   └── app.py                   # tkinter GUI (AgentRuntime 사용)
│   └── telegram/
│       └── poller.py
├── server/
│   ├── airec/
│   │   ├── analyzer/
│   │   │   ├── stt.py               # ★ STT 파이프라인 (한국어 최적화)
│   │   │   ├── pipeline.py          # ★ 녹취 분석 오케스트레이터
│   │   │   ├── call_quality.py      # 통화 품질 분석
│   │   │   └── keywords.py          # 키워드 매칭
│   │   ├── rec_handler.py           # 서버 측 녹취 메시지 처리
│   │   ├── storage.py               # WAV 파일 저장 관리
│   │   ├── watcher.py               # 서버 측 녹취 감시
│   │   └── database.py
│   ├── core/
│   │   ├── tcp_server.py
│   │   └── session_manager.py
│   ├── dashboard/
│   │   ├── main.py                  # FastAPI 대시보드
│   │   ├── middleware.py            # RBAC 미들웨어
│   │   ├── rbac.py                  # 사용자/권한 관리
│   │   └── routers/
│   │       ├── recordings.py
│   │       ├── deploy.py
│   │       └── logs.py
│   └── config.yaml
├── shared/
│   ├── protocol.py                  # TCP 프로토콜 (녹취 명령 포함)
│   └── models.py
├── tests/
│   ├── test_protocol_rec.py         # 녹취 프로토콜 테스트
│   ├── test_recording_watcher.py
│   ├── test_stt.py
│   └── ... (기타 테스트 파일)
├── docs/
│   ├── DEVELOPMENT_HISTORY.md
│   ├── FEATURES.md
│   ├── AI-LogOps_Technical_Spec_v2.1.md
│   ├── STT_Korean_Optimization_Project.md  # ★ STT 최적화 문서
│   └── plans/
│       ├── 2026-02-16-ai-logops-implementation.md
│       ├── 2026-02-17-agent-feature-enhancement.md
│       ├── 2026-02-17-selective-log-transfer-design.md
│       ├── 2026-02-17-selective-log-transfer.md
│       ├── 2026-02-20-airrec-agent-command-server.md
│       ├── 2026-02-23-extract-agent-common-logic.md
│       └── 2026-02-23-multi-server-support.md
├── run_server_gui.py                # ★ 서버 관리 GUI
├── agent.spec
├── build_agent.bat
├── deploy.py
├── requirements.txt
└── pytest.ini
```

---

## 5-1. 테스트 현황 (최신)

| 단계 | 테스트 수 | 누적 |
|------|----------|------|
| v1.0.0 (Phase 1~6) | 369 | 369 |
| v1.1 (기능 확장 4종) | +25 | 394 |
| v1.2 (선택적 로그 전송) | +12 | 406 |

**전체 406개 테스트 통과, 0 실패.**

---

## 6-1. 전체 커밋 이력 (추가분 v1.4.5~)

| # | 커밋 | 일시 | 설명 |
|---|------|------|------|
| 62 | `5c03d07` | 02-20 | STT 녹취 분석 파이프라인 + 서버-에이전트 아키텍처 |
| 63 | `5688fd1` | 02-20 | 에이전트 GUI, 버전 관리, 2-봇 텔레그램, 배포 개선 |
| 64 | `6037308` | 02-20 | 녹취 분석 테스트 + 기능 문서화 |
| 65 | `eeb3d04` | 02-20 | 서버 경유 에이전트 자동 배포 |
| 66 | `cfb5197` | 02-20 | deploy 청크 64KB 증가 + 비동기 처리 |
| 67 | `fab8dee` | 02-21 | 프로세스 관리 강화, 자동 재연결, 멀티에이전트 배포 |
| 68 | `b396a1a` | 02-21 | Git 시크릿 제거 (이미 기록됨) |
| 69 | `9ca6abf` | 02-21 | TCP 이벤트 알림, dead code 정리, E2E STT 테스트 (이미 기록됨) |
| 70 | `3878354` | 02-21 | v1.4.4 문서 업데이트 + 버전 업 |
| 71 | `5c8ac1d` | 02-21 | µ-law(fmt=7) WAV 지원 |
| 72 | `afa63c7` | 02-21 | 서버 재시작 시 빠른 재연결 + 모노 µ-law PCM 변환 |
| 73 | `8179bf7` | 02-21 | 녹취 분석 로깅 + get_wav_duration µ-law 지원 |
| 74 | `9804961` | 02-21 | **v1.4.8** 파일 안정화 후 분석 시작 |
| 75 | `eabcd2d` | 02-21 | **v1.4.9** rec_no→filename 식별자 전환 |
| 76 | `2f0177c` | 02-21 | 오래된 파일 우선 스캔 + 안정화 10초 대기 |
| 77 | `fbb500d` | 02-21 | 에이전트 GUI 강제 재연결 버튼 |
| 78 | `d9aae94` | 02-21 | 모노 녹음 STT 업로드 허용 |
| 79 | `987888b` | 02-21 | **v1.5.0** 서버 경유 프로세스 원격 배포 |
| 80 | `587a65b` | 02-21 | 대시보드 녹취 분석 시작/중지 UI |
| 81 | `5b4fdc6` | 02-23 | 녹취 프로토콜 명령 + 데이터 쿼리 타입 추가 |
| 82 | `5a965ff` | 02-23 | 에이전트 DB 헬퍼 (녹취 데이터 조회) |
| 83 | `01fbcc7` | 02-23 | TCP 클라이언트 녹취 명령/데이터 쿼리 콜백 |
| 84 | `10f0f54` | 02-23 | 로그 선택적 전송 + 파일 메타데이터 개선 |
| 85 | `5308592` | 02-23 | 녹취 감시 고도화 (서버 주도 + 품질 검사) |
| 86 | `b57d34a` | 02-23 | **v1.6.5** 녹취 분석 에이전트 서비스 통합 |
| 87 | `82d0667` | 02-23 | 녹취 분석 에이전트 GUI 통합 |
| 88 | `501283e` | 02-23 | 서버 STT + 품질 파이프라인 강화 |
| 89 | `1a71e25` | 02-23 | 서버 녹취 핸들러 + DB 스키마 추가 |
| 90 | `6f1f139` | 02-23 | TCP 서버 녹취 명령/배포/데이터 쿼리 핸들러 |
| 91 | `87cb6d4` | 02-23 | public_url 설정 + --no-telegram 옵션 |
| 92 | `7eb0516` | 02-23 | RBAC 사용자/권한 관리 시스템 |
| 93 | `1d15a74` | 02-23 | RBAC 대시보드 미들웨어 통합 |
| 94 | `db7d347` | 02-23 | 녹취 분석 대시보드 실시간 컨트롤 |
| 95 | `e224587` | 02-23 | 녹취 목록 + 상세 팝업 (파형 플레이어) |
| 96 | `42c72d0` | 02-23 | 로그 대시보드 실시간 스트리밍 |
| 97 | `50a2254` | 02-23 | 배포 대시보드 프로세스/녹취 클라이언트 배포 |
| 98 | `e7e4ecb` | 02-23 | 서버 관리 GUI (run_server_gui.py) |
| 99 | `d19ed38` | 02-23 | CLAUDE.md 프로젝트 규칙 문서화 |
| 100 | `9559b25` | 02-23 | ConfigView 타입 안전 config 접근자 |
| 101 | `4554333` | 02-23 | RecordingController 클래스 추가 |
| 102 | `7fb7c22` | 02-23 | AgentRuntime 오케스트레이터 |
| 103 | `0b41b03` | 02-23 | win_service + gui 리팩토링 (AgentRuntime 사용) |
| 104 | `2dcfd95` | 02-23 | 멀티 서버 연결 지원 |
| 105 | `54d6d5f` | 02-23 | **v1.7.0** 에이전트 버전 업 |

**총 105개 커밋, 8일간 개발.**
