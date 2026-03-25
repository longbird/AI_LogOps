# TECHNICAL SPECIFICATION

## AI-LogOps Auto-Deployer

**멀티 에이전트 아키텍처 | 듀얼 LLM 지원 | 웹 대시보드**

| 항목 | 값 |
|------|-----|
| 버전 | 2.1 |
| 작성일 | 2026-02-16 |
| 구현 언어 | Python 3.10+ |
| 관리 대상 | 2~5대 (Multi-Agent) |
| AI | OpenAI + Claude (Dual) |

> **v2.1 변경사항**: AI 파이프라인 검증 단계(Step 3.5) 추가, 에러 핸들링 강화, 테스트 전략 신설, 보안 보강, 자체 모니터링, Self-Update 복구 개선

---

## 목차 (Table of Contents)

1. [프로젝트 구조](#1-프로젝트-구조-project-structure)
2. [Remote Agent 상세 설계](#2-remote-agent-상세-설계)
3. [Command Server 상세 설계](#3-command-server-상세-설계)
4. [AI 파이프라인 상세 설계](#4-ai-파이프라인-상세-설계)
5. [TCP 통신 프로토콜 상세](#5-tcp-통신-프로토콜-상세)
6. [텔레그램 명령어 설계](#6-텔레그램-명령어-설계)
7. [웹 대시보드 설계](#7-웹-대시보드-설계)
8. [보안 설계](#8-보안-설계)
9. [구현 로드맵](#9-구현-로드맵-implementation-roadmap)
10. [테스트 전략](#10-테스트-전략) *(v2.1 신설)*

---

## 1. 프로젝트 구조 (Project Structure)

전체 프로젝트는 Agent, Server, Shared 세 개의 패키지로 분리되며, 각각 독립적으로 배포 가능합니다. Shared 패키지에는 통신 프로토콜, 데이터 모델 등 양측이 공유하는 코드가 위치합니다.

```
ai-logops/                        # 프로젝트 루트
├── agent/                        # Remote Agent 패키지
│   ├── core/                     # 핵심 모듈 (tcp_client, log_watcher, process_mgr)
│   ├── telegram/                 # 텔레그램 봇 핸들러
│   ├── updater/                  # 자체 업데이트 모듈
│   ├── service/                  # Windows 서비스 등록/관리
│   └── config.yaml               # 에이전트 설정 파일
├── server/                       # Command Server 패키지
│   ├── core/                     # TCP 서버, 세션 관리자, 헬스 모니터 (v2.1)
│   ├── ai/                       # AI 파이프라인 (provider, pipeline, prompts, validator)
│   ├── telegram/                 # 봇 핸들러 + 명령 라우터
│   ├── dashboard/                # 웹 대시보드 (FastAPI + templates)
│   ├── storage/                  # 로그/리포트/백업 저장소
│   └── config.yaml               # 서버 설정 파일
├── shared/                       # 공용 모듈 (protocol, models, utils)
├── tests/                        # 테스트 (v2.1 신설)
│   ├── fixtures/                 # AI 응답 fixture 등
│   ├── test_protocol.py
│   ├── test_tcp_client.py
│   ├── test_tcp_server.py
│   ├── test_ai_pipeline.py
│   └── ...
└── requirements.txt              # 의존성 목록
```

---

## 2. Remote Agent 상세 설계

### 2.1. 모듈 구성

Agent는 7개 핵심 모듈로 구성되며, 각 모듈은 단일 책임 원칙을 따릅니다.

| 모듈 | 파일 | 주요 책임 |
|------|------|-----------|
| TelegramPoller | `telegram/poller.py` | Telegram Long Polling으로 명령 수신, 상태 보고 |
| TCPClient | `core/tcp_client.py` | 비동기 TCP 접속, 패킷 송수신, 재접속 로직 |
| LogWatcher | `core/log_watcher.py` | watchdog 기반 파일 변경 감지 + 테일링 |
| ProcessManager | `core/process_mgr.py` | 프로세스 Kill/Start/PID 추적/헬스체크 |
| FileTransfer | `core/file_transfer.py` | 청크 단위 파일 업로드/다운로드 + 체크섬 |
| SelfUpdater | `updater/self_update.py` | 에이전트 바이너리 교체 + 롤백 로직 |
| WinService | `service/win_service.py` | pywin32 기반 Windows 서비스 등록/해제 |
| AgentConfig | `config.yaml` | 에이전트 ID, 모니터링 폴더, 프로세스 정보 |

### 2.2. Agent 생명주기 (Lifecycle)

Agent는 다음의 상태 머신으로 동작합니다:

```
INIT ──→ STANDBY ──→ CONNECTING ──→ CONNECTED ──→ DEPLOYING
  │         ↑            │              │             │
  │         │            ↓              ↓             ↓
  │         ├──── ERROR ←┘         UPDATING    DEPLOY_VERIFY (v2.1)
  │         │                         │             │
  │         ←─────────────────────────┘        ┌────┴────┐
  │                                         성공      실패
  │                                         확정    AUTO_ROLLBACK (v2.1)
  └──→ ERROR
```

- **INIT**: Windows 서비스로 시작, 설정 로딩, 텔레그램 폴러 초기화.
- **STANDBY**: 텔레그램 명령 대기. 주기적으로 "대기 중" 상태 보고.
- **CONNECTING**: `/connect` 명령 수신 시 TCP 접속 시도. TLS Handshake + AUTH 패킷 전송.
- **CONNECTED**: AUTH_ACK 수신 후 로그 전송 시작. Heartbeat 주기적 발송.
- **DEPLOYING**: CMD_DEPLOY 수신 시 파일 수신 → 프로세스 교체 수행.
- **DEPLOY_VERIFY** *(v2.1 추가)*: 배포 후 30초간 프로세스 헬스체크. 성공 시 확정, 실패 시 자동 롤백.
- **UPDATING**: 에이전트 자체 업데이트 수행 (bat 스크립트 위임).
- **ERROR**: 오류 발생 시 텔레그램 알림 후 STANDBY로 복귀 시도.

#### v2.1: 배포 후 자동 롤백 로직

```
배포 → 프로세스 시작 → 30초 헬스체크 대기
  ├── 프로세스 정상 (PID 존재 + 응답) → 배포 확정, 백업 보존
  └── 프로세스 실패 (PID 없음 or 비정상 종료)
      → 자동 롤백 (백업 복원) → 텔레그램 알림
```

- `ProcessManager`에 `health_check(timeout=30)` 메서드 추가
- 롤백은 기존 `backup_dir`의 최신 백업 사용
- `CMD_CTRL_ACK`에 `DEPLOY_VERIFIED` / `DEPLOY_ROLLBACK` 상태 추가

### 2.3. Agent 설정 파일 (config.yaml)

```yaml
# agent/config.yaml
agent:
  id: "PC-FACTORY-01"
  version: "1.0.0"

telegram:
  bot_token: "YOUR_BOT_TOKEN"
  admin_chat_id: 123456789

monitoring:
  log_folders:
    - "C:/Apps/TargetApp/logs"
    - "C:/Apps/TargetApp/error_logs"
  history_max_mb: 10
  watch_extensions: [".log", ".txt"]

target_process:
  name: "TargetApp.exe"
  path: "C:/Apps/TargetApp/TargetApp.exe"
  backup_dir: "C:/Apps/TargetApp/backups"

connection:
  heartbeat_interval: 30
  reconnect_attempts: 5
  reconnect_delay: 10
```

### 2.4. Windows 서비스 등록

pywin32의 `win32serviceutil`을 사용하여 Windows 서비스로 등록합니다.

- **서비스 이름**: `AILogOps-Agent`
- **설치**: `python agent/service/win_service.py install`
- **시작**: `python agent/service/win_service.py start`
- **제거**: `python agent/service/win_service.py remove`

서비스는 "자동" 시작 유형으로 등록되어 PC 부팅 시 자동 실행됩니다. 서비스 충돌 시 자동 재시작 옵션을 활성화합니다.

### 2.5. 자체 업데이트 (Self-Update) 메커니즘

에이전트는 자기 자신을 업데이트할 수 있습니다. 실행 중인 파일을 직접 교체할 수 없으므로, 별도의 업데이터 배치 파일(`updater.bat`)을 생성하여 위임합니다:

1. **Step 1**: 서버에서 `AGENT_UPDATE` 패킷으로 새 바이너리 수신.
2. **Step 2**: 임시 폴더에 새 파일 저장 + SHA-256 검증.
3. **Step 3**: `updater.bat` 생성: 서비스 정지 → **현재 바이너리 백업** → 파일 교체 → 서비스 시작.
4. **Step 4**: `subprocess`로 `updater.bat` 실행 후 현재 프로세스 종료.
5. **Step 5**: 새 버전 에이전트가 서비스로 시작되며 버전 보고.

#### v2.1: 3중 안전장치

```
(1) 업데이트 전: 현재 바이너리를 backup/에 보존 (절대 덮어쓰기 안 함)
(2) 부팅 시: 버전 파일(.version) 검증 → 불일치면 백업에서 복원
(3) 크래시 감지: Windows 서비스 재시작 실패 3회 → 백업 버전으로 교체
```

**개선된 updater.bat:**

```batch
@echo off
:: Step 1: 서비스 정지
net stop AILogOps-Agent
:: Step 2: 현재 파일 백업 (절대 삭제 안 함)
copy /Y "agent.exe" "backups\agent_%VERSION%.exe"
:: Step 3: 새 파일 배치
copy /Y "temp\agent_new.exe" "agent.exe"
:: Step 4: 서비스 시작
net start AILogOps-Agent
:: Step 5: 시작 검증 (10초 대기 후 상태 확인)
timeout /t 10 >nul
sc query AILogOps-Agent | find "RUNNING" >nul
if errorlevel 1 (
    :: 실패: 롤백
    copy /Y "backups\agent_%VERSION%.exe" "agent.exe"
    net start AILogOps-Agent
)
```

**버전 호환성**: AUTH 패킷의 `Version(8B)` 필드를 활용하여 서버에서 최소 지원 버전 검증. 미달 시 `AUTH_ACK(Status=VERSION_MISMATCH)` 반환.

---

## 3. Command Server 상세 설계

### 3.1. 모듈 구성

Server는 9개 모듈로 구성되며 *(v2.1: HealthMonitor 추가)*, asyncio 기반으로 모든 I/O가 비동기 처리됩니다.

| 모듈 | 파일 | 주요 책임 |
|------|------|-----------|
| TCPServer | `core/tcp_server.py` | asyncio 기반 비동기 서버, 멀티 에이전트 접속 |
| SessionManager | `core/session_mgr.py` | 에이전트별 세션/상태 관리, Heartbeat 모니터링 |
| **HealthMonitor** | `core/health_monitor.py` | **서버 자체 리소스 모니터링 + 알림** *(v2.1 추가)* |
| AIProvider | `ai/provider.py` | OpenAI/Claude API 추상화 레이어 (Strategy Pattern) |
| AIPipeline | `ai/pipeline.py` | 3단계 AI 체이닝 (분석→계획→코드수정) + 검증(v2.1) |
| PromptTemplates | `ai/prompts/` | 각 단계별 프롬프트 템플릿 (Jinja2) |
| TelegramHandler | `telegram/handler.py` | 명령 파싱, 승인 UI, 상태 알림 |
| Dashboard | `dashboard/app.py` | FastAPI 웹 대시보드 + WebSocket 실시간 |
| StorageManager | `storage/manager.py` | 로그/리포트/백업 파일 관리 |

### 3.2. 멀티 에이전트 세션 관리

`SessionManager`는 최대 10대의 에이전트를 동시에 관리합니다. 각 에이전트는 독립된 세션 객체를 가집니다:

- **AgentSession**: `agent_id`, `socket`, `state`, `last_heartbeat`, `log_buffer`, `deploy_history`
- **Heartbeat 모니터링**: 30초 간격으로 응답 확인. 3회 연속 실패 시 연결 끊김 + 텔레그램 경고.
- **상태 보고**: 각 에이전트의 상태 변경 시 텔레그램 + 대시보드 동시 알림.

### 3.3. 서버 자체 모니터링 (v2.1 추가)

`HealthMonitor`는 5분 주기로 서버 리소스를 체크하고 이상 시 텔레그램으로 알림합니다.

| 지표 | 임계값 | 알림 |
|------|--------|------|
| CPU 사용률 | > 90% (3회 연속) | ⚠ 서버 CPU 과부하 |
| 메모리 사용률 | > 85% | ⚠ 서버 메모리 부족 |
| 디스크 사용률 | > 90% | 디스크 부족 - 로그 정리 필요 |
| AI API 오류율 | > 50% (최근 1시간) | ⚠ AI API 불안정 |

```python
# server/core/health_monitor.py
class HealthMonitor:
    """5분 주기로 서버 리소스 체크, 이상 시 텔레그램 알림"""

    async def check_loop(self):
        while True:
            await self._check_cpu()
            await self._check_memory()
            await self._check_disk()
            await self._check_ai_api_health()
            await asyncio.sleep(300)  # 5분
```

- `psutil` 패키지로 CPU/메모리/디스크 수집
- 동일 이슈 반복 알림 방지: 30분에 1번만 발송
- Phase 5에서 대시보드 서버 상태 위젯과 연동 가능

### 3.4. Server 설정 파일 (config.yaml)

```yaml
# server/config.yaml
server:
  tcp_host: "0.0.0.0"
  tcp_port: 9500
  max_agents: 10

ai:
  default_provider: "openai"  # openai | claude
  openai:
    api_key: "sk-..."
    model: "gpt-4o"
  claude:
    api_key: "sk-ant-..."
    model: "claude-sonnet-4-20250514"

telegram:
  bot_token: "YOUR_BOT_TOKEN"
  admin_chat_ids: [123456789]

dashboard:
  host: "0.0.0.0"
  port: 8080
  secret_key: "CHANGE_ME"

storage:
  base_dir: "./storage"
  max_log_retention_days: 30
  max_backups_per_agent: 5

# v2.1: 헬스 모니터링 설정
health:
  check_interval: 300  # 5분
  cpu_threshold: 90
  memory_threshold: 85
  disk_threshold: 90
  alert_cooldown: 1800  # 30분 중복 방지
```

---

## 4. AI 파이프라인 상세 설계

### 4.1. 듀얼 LLM 프로바이더 (Strategy Pattern)

`AIProvider`는 Strategy Pattern으로 구현되어, 실행 중 `/ai_provider` 명령으로 LLM을 전환할 수 있습니다.

- **OpenAI**: GPT-4o / GPT-4-turbo. 대용량 코드 분석에 강점.
- **Claude**: Claude Sonnet 4 / Opus. 정확한 코드 수정과 장문 분석에 강점.
- **공통 인터페이스**: `analyze(log) -> Report`, `plan(report) -> Plan`, `fix(plan, code) -> FixedCode`

#### v2.1: AI API 재시도 + 폴백 로직

```python
# ai/provider.py
@retry(max_attempts=3, backoff=[5, 15, 30],
       fallback_provider="claude" if default == "openai" else "openai")
async def analyze(self, log_content: str) -> Report:
    ...
```

- 3회 재시도 + 지수 백오프 (5초, 15초, 30초)
- 3회 실패 시 **다른 LLM으로 자동 폴백** (듀얼 LLM의 핵심 가치)
- 각 단계 중간 결과는 storage에 저장 → 중단점부터 재시작 가능

### 4.2. 3단계 AI 체이닝 파이프라인 + 검증 (v2.1 확장)

각 단계는 독립적으로 실행되며, 중간 결과를 파일로 저장하여 추적성을 보장합니다.

| 단계 | 입력 | AI 처리 | 출력 | 최대 토큰 |
|------|------|---------|------|-----------|
| Step 1 | 수집된 로그 파일 | 오류 패턴 분석 + 빈도 통계 + 심각도 분류 | `Analysis_Report.md` | 16K (input) |
| Step 2 | Analysis Report | 우선순위 개선 항목 + 수정 방법 + 예상 효과 | `Improvement_Plan.md` | 8K (input) |
| Step 3 | Plan + Source Code | 코드 수정 + 변경 사항 diff + 주석 추가 | `Fixed_Source_Code/` | 32K (input) |
| **Step 3.5** | **Original + Fixed Code** | **자동 검증: Syntax + AI Cross-Review + 신뢰도** | **`Validation_Report.json`** | **8K (input)** |

#### v2.1: Step 3.5 - Automated Validation Layer

```
기존:  Step3(코드 수정) ──────────────────────→ /deploy
변경:  Step3(코드 수정) → Step 3.5(자동 검증) → /deploy
```

**검증 구성:**

| 검증 단계 | 내용 | 실패 시 |
|-----------|------|---------|
| 1. Syntax Check | Python `ast.parse()` 또는 대상 언어 파서 | 즉시 실패, Step 3 재실행 |
| 2. AI Cross-Review | **다른 프로바이더**로 코드 리뷰 (OpenAI 수정 → Claude 리뷰) | 심각도 HIGH면 재생성, MEDIUM 이하면 경고와 함께 통과 |
| 3. Diff 생성 | unified diff 생성 → 대시보드 + 텔레그램 발송 | - |
| 4. 신뢰도 점수 | AI 리뷰 결과 기반 0~100 점수 | 70점 미만이면 관리자 수동 승인 필요 |

**출력 형식 (`Validation_Report.json`):**

```json
{
  "syntax_check": "PASS",
  "cross_review": {
    "reviewer": "claude",
    "severity": "LOW",
    "issues": [
      {"line": 42, "type": "style", "message": "변수명 개선 권장"}
    ]
  },
  "confidence_score": 87,
  "diff_summary": "+15 lines, -8 lines, 3 files changed",
  "deploy_allowed": true,
  "requires_manual_approval": false
}
```

### 4.3. 프롬프트 템플릿 설계

각 단계의 프롬프트는 Jinja2 템플릿으로 관리되며, 변수 주입을 통해 유연하게 조정합니다.

**Step 1 프롬프트: 로그 분석 (`analyze_prompt.j2`)**

```
Role: 시스템 로그 분석 전문가
Input: {{ log_content }}
Task: 1) 오류 패턴 분석  2) 빈도/심각도 통계  3) 근본 원인 추정
Output: Markdown 형식의 Analysis_Report
```

**Step 2 프롬프트: 개선 계획 (`plan_prompt.j2`)**

```
Role: 소프트웨어 개선 기획자
Input: {{ analysis_report }}
Task: 1) 우선순위 개선 항목  2) 구체적 수정 방법  3) 예상 효과
Output: Markdown 형식의 Improvement_Plan
```

**Step 3 프롬프트: 코드 수정 (`fix_prompt.j2`)**

```
Role: 시니어 소프트웨어 개발자
Input: {{ improvement_plan }} + {{ source_code }}
Task: 1) 계획에 따른 코드 수정  2) 변경사항 diff  3) 주석 추가
Output: 수정된 소스 코드 전체
```

**Step 3.5 프롬프트: 코드 검증 (`review_prompt.j2`)** *(v2.1 추가)*

```
Role: 시니어 코드 리뷰어
Input: {{ original_code }} + {{ fixed_code }} + {{ improvement_plan }}
Task:
  1) 변경사항이 계획과 일치하는지 검증
  2) 버그, 보안 취약점, 논리 오류 탐지
  3) 신뢰도 점수 산출 (0~100)
Output: JSON { severity, issues[], confidence_score, deploy_recommendation }
```

---

## 5. TCP 통신 프로토콜 상세

### 5.1. 패킷 구조

모든 패킷은 다음의 공통 헤더 구조를 따릅니다:

```
[Header: 1 byte] [Payload Length: 4 bytes (big-endian)] [Payload: N bytes]
```

- 최대 페이로드 크기: 10MB
- 파일 전송은 4KB 청크 단위로 분할

### 5.2. 패킷 정의

| Header | 이름 | Payload 구조 | 방향 |
|--------|------|-------------|------|
| `0x01` | AUTH | `[AgentID(32B)] [Version(8B)] [Token(64B)]` | Agent→Server |
| `0x02` | AUTH_ACK | `[Status(1B)] [SessionID(16B)]` | Server→Agent |
| `0x03` | LOG_HIST | `[FileName(256B)] [FileSize(4B)] [Data(...)]` | Agent→Server |
| `0x04` | LOG_REAL | `[FileName(256B)] [LineLen(2B)] [Line(...)]` | Agent→Server |
| `0x10` | CMD_DEPLOY | `[FileSize(4B)] [SHA256(32B)] [FileName(256B)]` | Server→Agent |
| `0x11` | FILE_CHUNK | `[SeqNum(4B)] [ChunkSize(2B)] [Data(4096B)]` | Server→Agent |
| `0x12` | FILE_ACK | `[SeqNum(4B)] [Status(1B)]` | Agent→Server |
| `0x13` | CMD_CTRL | `[Action(1B): STOP/START/RESTART]` | Server→Agent |
| `0x14` | CMD_CTRL_ACK | `[Action(1B)] [PID(4B)] [Status(1B)]` | Agent→Server |
| `0x20` | AGENT_UPDATE | `[FileSize(4B)] [SHA256(32B)]` | Server→Agent |
| `0xFE` | HEARTBEAT | `[Timestamp(8B)] [CPU%(1B)] [Mem%(1B)]` | 양방향 |
| `0xFF` | DISCONNECT | `[Reason(1B)]` | 양방향 |

**v2.1: AUTH_ACK Status 값 확장**

| Status 값 | 의미 |
|-----------|------|
| `0x00` | 인증 성공 |
| `0x01` | 인증 실패 (잘못된 토큰) |
| `0x02` | 버전 불일치 (VERSION_MISMATCH) *(v2.1 추가)* |

**v2.1: CMD_CTRL_ACK Status 값 확장**

| Status 값 | 의미 |
|-----------|------|
| `0x00` | 성공 |
| `0x01` | 실패 |
| `0x10` | 배포 검증 성공 (DEPLOY_VERIFIED) *(v2.1 추가)* |
| `0x11` | 배포 검증 실패 - 자동 롤백 (DEPLOY_ROLLBACK) *(v2.1 추가)* |

### 5.3. 연결 흐름 (Sequence)

일반적인 세션 흐름:

1. **TLS Handshake**: Agent가 Server에 SSL/TLS 접속.
2. **AUTH**: Agent가 AgentID + Version + Token 전송.
3. **AUTH_ACK**: Server가 인증 결과 + SessionID 반환. *(v2.1: 버전 검증 포함)*
4. **LOG_HIST**: Agent가 누적 로그 파일 전송 (최대 N MB).
5. **LOG_REAL**: Agent가 실시간 로그 라인 스트리밍.
6. **HEARTBEAT**: 양방향 30초 간격으로 생존 확인.
7. **CMD_DEPLOY**: Server가 배포 시작 알림 → FILE_CHUNK 연속 전송 → FILE_ACK 확인.
8. **DEPLOY_VERIFY** *(v2.1)*: Agent가 30초간 프로세스 헬스체크 후 `CMD_CTRL_ACK(DEPLOY_VERIFIED/DEPLOY_ROLLBACK)` 전송.
9. **DISCONNECT**: 양측 중 하나가 종료 요청.

---

## 6. 텔레그램 명령어 설계

관리자가 사용할 수 있는 전체 명령어 목록입니다. 모든 명령은 등록된 `admin_chat_id`에서만 수락됩니다.

| 명령어 | 설명 | 예시 |
|--------|------|------|
| `/status` | 전체 에이전트 상태 조회 | `/status` |
| `/connect <ID> <IP> <PORT>` | 특정 에이전트에 TCP 접속 지시 | `/connect PC-01 1.2.3.4 9500` |
| `/analyze <ID>` | 수집된 로그 AI 분석 시작 | `/analyze PC-01` |
| `/plan <ID>` | 분석 기반 개선 계획 생성 | `/plan PC-01` |
| `/fix <ID>` | 코드 수정 실행 | `/fix PC-01` |
| `/deploy <ID>` | 수정된 파일 배포 승인 | `/deploy PC-01` |
| `/rollback <ID>` | 이전 버전으로 롤백 | `/rollback PC-01` |
| `/disconnect <ID>` | TCP 연결 종료 | `/disconnect PC-01` |
| `/update_agent <ID>` | 에이전트 자체 업데이트 | `/update_agent PC-01` |
| `/ai_provider <name>` | AI 프로바이더 전환 | `/ai_provider claude` |
| `/auto <ID>` | 분석→계획→수정→**검증**→배포 전체 자동화 | `/auto PC-01` |

> **v2.1 변경**: `/auto` 명령에 Step 3.5 검증이 자동 포함. 신뢰도 70점 미만 시 자동 중단 후 관리자 확인 요청.

### 6.1. 알림 메시지 형식

시스템이 발송하는 알림 메시지 형식:

| 상황 | 형식 |
|------|------|
| 상태 변경 | `[PC-01] 상태 변경: STANDBY -> CONNECTED` |
| AI 분석 완료 | `[PC-01] 분석 완료 - 오류 3건, 경고 12건. 상세: /report PC-01` |
| **검증 완료** *(v2.1)* | `[PC-01] 코드 검증 완료 - 신뢰도: 87/100. 배포 승인: /deploy PC-01` |
| **검증 실패** *(v2.1)* | `[PC-01] 코드 검증 실패 - 신뢰도: 45/100. 심각 이슈 2건. 수동 검토 필요.` |
| 배포 준비 | `[PC-01] 배포 준비 완료. 승인: /deploy PC-01` |
| 배포 완료 | `[PC-01] 배포 성공 - PID: 12345, 로그 모니터링 재개` |
| **배포 롤백** *(v2.1)* | `[PC-01] 배포 후 프로세스 미기동 - 자동 롤백 완료` |
| 오류 알림 | `[PC-01] Heartbeat 실패 (3회 연속). 연결 끊김.` |
| **서버 상태** *(v2.1)* | `서버 CPU 과부하 (92%, 3회 연속)` |

---

## 7. 웹 대시보드 설계

### 7.1. 기술 스택

- **Backend**: FastAPI (Python) + Uvicorn ASGI
- **Frontend**: Jinja2 템플릿 + HTMX (SPA 없이 동적 UI) + Tailwind CSS
- **실시간**: WebSocket으로 에이전트 상태/로그 실시간 푸시
- **인증**: JWT 토큰 기반 로그인

### 7.2. 페이지 구성

| 페이지 | 기능 | 기술 |
|--------|------|------|
| 메인 대시보드 | 에이전트 상태 카드, 접속/대기/오프라인 표시 | FastAPI + Jinja2 + HTMX |
| 실시간 로그 | 에이전트별 실시간 로그 스트리밍 뷰어 | WebSocket + Auto-scroll |
| AI 리포트 | 분석/계획/수정/**검증**(v2.1) 결과 열람 + 이력 관리 | Markdown 렌더링 |
| 배포 이력 | 배포/롤백 로그 타임라인 + diff 뷰어 | Timeline UI |
| 설정 | AI 프로바이더 전환, 에이전트 관리, 알림 설정 | YAML 에디터 |

---

## 8. 보안 설계

시스템의 모든 통신 구간에 보안 요소를 적용합니다. (내부망 전용 환경 기준)

| 구간 | 보안 요소 | 구현 방법 |
|------|-----------|-----------|
| TCP 통신 | TLS 1.3 암호화 | `ssl` 모듈 + 자체 인증서 |
| 인증 | 토큰 기반 인증 | AUTH 패킷에 64byte 토큰 포함 |
| 파일 무결성 | SHA-256 체크섬 | 배포 전후 해시 검증 |
| 텔레그램 | Admin Chat ID 화이트리스트 | 등록된 `chat_id`만 명령 수락 |
| 웹 대시보드 | JWT 인증 + HTTPS | FastAPI Security + 로그인 필수 |
| API 키 | 환경변수 관리 | `.env` 파일 + `dotenv` 로딩 |
| **Rate Limiting** *(v2.1)* | **텔레그램 명령 제한** | **10초당 5회 초과 시 차단** |
| **Audit Log** *(v2.1)* | **명령 실행 추적** | **`storage/audit.jsonl`에 기록** |

### 8.1. Rate Limiting (v2.1 추가)

```python
# server/telegram/handler.py
class RateLimiter:
    """동일 chat_id에서 10초에 5회 이상 명령 차단"""
    window = 10      # seconds
    max_requests = 5
```

- 실수 연타 및 봇 토큰 유출 시 보호
- In-memory 카운터 (내부망이므로 충분)

### 8.2. Audit Log (v2.1 추가)

```python
# shared/utils.py
def audit_log(action: str, agent_id: str, user: str, detail: str):
    """storage/audit.jsonl에 한 줄씩 append"""
    # {"ts": "2026-02-16T10:30:00", "action": "DEPLOY", "agent": "PC-01",
    #  "user": "admin", "detail": "v1.2.0 deployed, confidence: 87"}
```

- 누가, 언제, 어떤 명령을 실행했는지 추적
- jsonl(한 줄 JSON) 형식으로 append
- 장애 원인 추적 시 핵심 증거

---

## 9. 구현 로드맵 (Implementation Roadmap)

총 6단계, 약 6주간의 개발 계획입니다. 각 단계는 이전 단계의 완료를 전제로 합니다.

| 단계 | 기간 | 목표 | 산출물 |
|------|------|------|--------|
| Phase 1 | 1주 | Skeleton 통신 구축 | TCP 서버/클라이언트, Telegram 봇, Agent Standby |
| Phase 2 | 1주 | 로그 모니터링 | 누적 로그 전송, 실시간 스트리밍, 저장 관리 |
| Phase 3 | 1.5주 | AI 파이프라인 | 듀얼 LLM 지원, 3단계 체이닝, **Step 3.5 검증**(v2.1), 프롬프트 템플릿 |
| Phase 4 | 1주 | 배포 자동화 | 파일 전송, 프로세스 교체, 롤백, 백업, **배포 후 자동 롤백**(v2.1) |
| Phase 5 | 1주 | 웹 대시보드 | FastAPI 대시보드, WebSocket 실시간, 이력 관리, **서버 헬스 모니터링**(v2.1) |
| Phase 6 | 0.5주 | Windows 서비스 + 업데이트 | 서비스 등록, 자체 업데이트, **3중 안전장치**(v2.1), 최종 테스트 |

> **v2.1 변경**: 각 Phase에 해당 모듈 테스트 동시 작성 포함.

### 9.1. Phase 1 상세 구현 계획

Phase 1은 전체 시스템의 통신 백본을 구축하는 가장 중요한 단계입니다:

**Day 1-2: 기반 구조**
- `shared/protocol.py` - 패킷 정의 + 직렬화/역직렬화 함수
- `shared/models.py` - `AgentInfo`, `SessionState` 데이터 모델 (`dataclass`)
- `shared/utils.py` - 로깅, 체크섬, 설정 로더, **Audit Log**(v2.1)
- `tests/test_protocol.py` - 패킷 직렬화 단위 테스트 *(v2.1 추가)*

**Day 3-4: TCP 통신**
- `server/core/tcp_server.py` - asyncio 기반 TCP 서버 (accept, recv, send)
- `agent/core/tcp_client.py` - 비동기 TCP 클라이언트 (접속, 재접속, Heartbeat)
- AUTH / AUTH_ACK 패킷 교환 테스트
- `tests/test_tcp_client.py`, `tests/test_tcp_server.py` *(v2.1 추가)*

**Day 5-7: 텔레그램 연동**
- `agent/telegram/poller.py` - Long Polling 기반 명령 수신
- `server/telegram/handler.py` - 명령 파서 + 응답 전송 + **Rate Limiting**(v2.1)
- `/status`, `/connect`, `/disconnect` 명령어 구현
- Agent Standby 모드: 텔레그램 대기 → `/connect` → TCP 접속 전체 흐름 테스트

---

## 10. 테스트 전략 (v2.1 신설)

### 10.1. 테스트 구조

```
tests/
├── fixtures/
│   └── ai_responses/
│       ├── analyze_response.json    # Step 1 고정 응답
│       ├── plan_response.json       # Step 2 고정 응답
│       ├── fix_response.json        # Step 3 고정 응답
│       └── review_response.json     # Step 3.5 검증 응답
├── test_protocol.py      # 패킷 직렬화/역직렬화, 경계값 테스트
├── test_models.py        # 데이터 모델 검증
├── test_tcp_client.py    # 접속/재접속/Heartbeat 로직
├── test_tcp_server.py    # 세션 관리, 멀티 에이전트
├── test_log_watcher.py   # 파일 감지, 테일링
├── test_process_mgr.py   # Kill/Start/PID 추적
└── test_ai_pipeline.py   # 3단계 + 검증단계 전체 흐름
```

### 10.2. AI Mock 전략

```python
# tests/conftest.py
import pytest
from unittest.mock import AsyncMock

@pytest.fixture
def mock_ai_provider(tmp_path):
    """AIProvider를 고정 응답 fixture로 대체"""
    provider = MockAIProvider(fixtures_dir="tests/fixtures/ai_responses")
    return provider
```

- **pytest** + **pytest-asyncio** 사용
- 각 Phase 구현 시 해당 모듈 테스트 동시 작성
- AI 응답은 실제 API 호출 1회 캡처 → fixture 파일로 저장하여 재사용
- 파이프라인 전체 흐름은 fixture 체이닝으로 검증

### 10.3. Phase별 테스트 범위

| Phase | 테스트 대상 | 테스트 방법 |
|-------|------------|------------|
| Phase 1 | 패킷 직렬화, TCP 접속/인증 | Unit: 직렬화 검증, asyncio 루프백 |
| Phase 2 | 로그 감지, 파일 전송 | Unit: watchdog 이벤트 시뮬레이션 |
| Phase 3 | AI 파이프라인 3.5단계 | AI Mock: fixture 기반 흐름 검증 |
| Phase 4 | 배포, 롤백, 헬스체크 | Unit: 프로세스 매니저 모킹 |
| Phase 5 | 대시보드 API | httpx AsyncClient 기반 |
| Phase 6 | 서비스 등록, 업데이트 | 수동 테스트 (Windows 의존) |

---

*End of Document - v2.1*
