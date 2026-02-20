# AI-LogOps 기능 명세서

AI-LogOps는 윈도우 기반 시스템의 로그를 실시간으로 수집하고, AI를 활용하여 문제 원인을 분석하며, 필요한 경우 자동으로 배포 및 복구를 수행하는 통합 관리 솔루션입니다. 본 문서는 시스템의 모든 기능과 설정, 관리 방법을 다루는 종합 레퍼런스입니다.

## 1. 시스템 개요

### 시스템 아키텍처

AI-LogOps는 에이전트와 서버 간의 전용 바이너리 프로토콜을 기반으로 통신하며, 텔레그램과 웹 대시보드를 통해 관제 기능을 제공합니다.

```text
+---------------------+       TCP Binary Protocol       +---------------------+
|   Agent (Windows)   | <---------------------------> |   Command Server    |
+----------+----------+       (16 Packet Types)       +----------+----------+
           |                                                     |
           |                                         +-----------+-----------+
           |                                         |                       |
   [Windows Service]                         [Telegram Bot]           [Web Dashboard]
   [Log Collection]                          [AI Pipeline]            [HTMX / FastAPI]
   [Self Update]                             [Health Monitor]         [Real-time Logs]
```

- **에이전트 (Agent):** 윈도우 서비스 형태로 구동되며 원격 로그 수집, 프로세스 관리, 자체 업데이트 기능을 수행합니다.
- **서버 (Server):** TCP 서버를 핵심으로 하며 AI 분석 파이프라인, 텔레그램 봇, 웹 대시보드를 운영합니다.
- **공통 (Shared):** 16가지 패킷 타입을 가진 전용 바이너리 프로토콜과 데이터 모델, 유틸리티를 공유합니다.

## 2. 에이전트 (Agent)

### 2.1 Windows 서비스
에이전트는 `agent/service/win_service.py`에 정의된 `AILogOpsAgentService`를 통해 윈도우 서비스로 작동합니다.

- **서비스 명칭:** AILogOps-Agent
- **설치:** `AILogOps-Agent.exe install` (관리자 권한 필요)
- **시작:** `AILogOps-Agent.exe start` 또는 `net start AILogOps-Agent`
- **중지:** `AILogOps-Agent.exe stop`
- **제거:** `AILogOps-Agent.exe remove`
- **디버그 모드:** `AILogOps-Agent.exe debug` 명령어를 입력하거나 실행 파일을 직접 더블 클릭하면 자동으로 디버그 모드로 실행됩니다.
- **복구 기능:** 서비스 충돌 시 자동으로 재시작되도록 sc failure 설정이 적용되어 있습니다.
- **로그 기록:** `log/YYYYMMDD_NN.txt` 파일에 기록되며, 10MB 단위로 로테이션됩니다.

### 2.2 텔레그램 봇 명령어
에이전트의 모든 명령어는 `agent/telegram/poller.py`에서 처리하며, `admin_chat_id`에 등록된 관리자만 접근할 수 있습니다.

| 명령어 | 설명 | 사용 예시 |
| :--- | :--- | :--- |
| /status | 시스템 및 대상 프로세스의 상태 지표(CPU, 메모리, 핸들, GDI) 확인 | /status |
| /connect | 명령 서버에 수동 연결 시도 | /connect [IP] [PORT] |
| /disconnect | 서버와의 연결 종료 | /disconnect |
| /update | 전송된 업데이트 파일을 적용 (updater.bat 실행) | /update |
| /deploy | 스테이징된 파일을 대상 프로세스 경로로 배포 | /deploy |
| /last | 감시 중인 폴더에서 최근 로그 확인 | /last [N] (기본 10, 최대 100) |
| /analyze | 최근 로그를 AI로 분석 | /analyze [N] (기본 50, 최대 200) |
| /model | LLM 제공자 변경 | /model [openai, claude, openrouter] |
| /subscribe | 구독 인증을 위한 OAuth 디바이스 플로우 시작 | /subscribe |
| /unsubscribe | 활성화된 구독 해지 | /unsubscribe |
| /subscription | 현재 구독 상태 확인 | /subscription |

업데이트나 배포를 위한 파일은 봇 채팅창에 직접 zip 또는 exe 파일을 전송하면 에이전트가 이를 임시 경로에 저장합니다. 이후 /update나 /deploy 명령어를 통해 적용합니다.

### 2.3 로그 감시 (LogWatcher)
watchdog 라이브러리를 사용하여 지정된 폴더의 파일 변화를 실시간으로 감지합니다.

- **설정 항목:** `monitoring.log_folders` (목록), `monitoring.watch_extensions` (예: [".log", ".txt"])
- **작동 방식:** 새로운 로그 라인이 추가되면 콜백을 통해 실시간으로 읽어들입니다.
- **서버 전송:** 과거 로그 이력 읽기 및 파일 메타데이터(파일명, 크기, MD5) 수집을 통해 선택적 로그 전송 기능을 지원합니다.

### 2.4 프로세스 관리 (ProcessManager)
대상 프로세스의 생명 주기를 관리하며, psutil 라이브러리를 활용합니다.

- **설정 항목:** `target_process.name`, `target_process.path`, `target_process.backup_dir`, `target_process.args`
- **종료 프로세스:** 일반 종료 시도 후 10초간 대기하며, 이후에도 종료되지 않으면 강제 종료(kill)합니다.
- **백업 및 복구:** 배포 시 현재 파일을 타임스탬프가 포함된 이름으로 백업하여 문제가 발생할 경우 롤백할 수 있도록 지원합니다.

### 2.5 프로세스 자동 재시작 (ProcessMonitorLoop)
프로세스가 예기치 않게 종료되었을 때 자동으로 감지하여 재실행합니다.

- **설정 항목:** `target_process.auto_restart: true`, `target_process.check_interval: 30`
- **작동 방식:** 백그라운드 루프가 주기적으로 프로세스 상태를 확인하며, 종료 감지 시 재시작 후 관리자에게 알림을 전송합니다.

### 2.6 스케줄 재시작 (ProcessScheduler)
정해진 시간에 프로세스를 정기적으로 재시작합니다.

- **설정 항목:** `schedule.restart_times: ["08:00", "18:00"]`
- **재시작 절차:** 프로세스 종료 후 3초 대기, 시작 후 5초 대기하며 최종적으로 PID를 검증합니다.
- **실패 처리:** 실패 시 1회 재시도하며, 최종 실패 시 텔레그램으로 알림을 보냅니다.

### 2.7 시스템 모니터링 (SystemMonitor)
시스템 전체 및 대상 프로세스의 자원 사용량을 추적합니다.

- **전체 시스템:** CPU 점유율, 메모리 사용량, 전체 핸들 및 GDI 개수
- **대상 프로세스:** 해당 프로세스의 CPU, 메모리(MB), 핸들 및 GDI 개수 (Windows ctypes 사용)
- 해당 데이터는 /status 명령어의 응답으로 활용됩니다.

### 2.8 TCP 클라이언트 (TCPClient)
서버와의 바이너리 통신을 담당합니다.

- **연결 설정:** `connection.host`, `connection.port`, `connection.token`, `connection.heartbeat_interval` 등
- **인증:** AUTH 패킷을 통한 토큰 기반 인증을 수행합니다.
- **로그 전송:** 2단계 선택적 전송 방식을 사용합니다. 에이전트가 파일 목록을 보내면 서버가 필요한 파일만 요청하고, 에이전트는 해당 파일만 전송합니다.

### 2.9 LLM 통합 (llm/)
다양한 AI 모델 제공자를 통합 관리합니다.

- **지원 제공자:** OpenAI, Claude(Anthropic), OpenRouter
- **설정 항목:** `llm.auth_mode` (apikey 또는 subscription), `llm.default_provider`
- **인증 우선순위:** config.yaml 설정값, .env 파일, 환경 변수 순서로 API 키를 탐색합니다.

### 2.10 구독 인증 (SubscriptionClient)
구독 서버를 통한 API 키 관리 및 인증 기능을 제공합니다.

- **OAuth 디바이스 플로우:** /subscribe 명령어 실행 시 출력되는 URL로 접속하여 브라우저에서 인증하면 에이전트가 토큰을 획득합니다.
- **키 기반 인증:** config.yaml에 미리 등록된 키를 통해 시작 시 자동 인증을 수행합니다.
- **갱신:** 기본 24시간 주기로 인증 정보를 갱신합니다.

### 2.11 자체 업데이트 (SelfUpdater)
에이전트 실행 파일 및 구성 요소를 원격으로 업데이트합니다.

- **수신 지원:** 단일 zip, 분할 압축 파일(*.partNNofMM.zip), exe 파일 직접 수신 가능
- **업데이트 절차:** `temp/update/` 경로에 파일을 준비한 뒤 updater.bat를 생성합니다. 해당 배치 파일은 서비스를 중지하고 파일을 교체한 후 다시 시작하는 전 과정을 수행합니다.

### 2.12 프로세스 배포 (ProcessDeployer)
관리 대상 프로세스의 파일을 교체하고 배포합니다.

- **배포 절차:** 현재 실행 중인 프로세스를 백업하고 종료한 뒤, 관련 로그 폴더를 정리하고 새 파일을 복사합니다. 이후 프로세스를 실행하여 30초간 헬스 체크를 수행하고 실패 시 롤백합니다.
- **스마트 로그 정리:** 프로세스 경로와의 유사도를 계산하여 관련된 로그 폴더만 선별적으로 비웁니다.

## 3. 명령 서버 (Server)

### 3.1 TCP 서버
다수의 에이전트 연결을 관리하고 패킷을 중계합니다.

- **세션 관리:** 토큰 인증을 거친 세션만 유지하며 최대 연결 개수와 하트비트 타임아웃을 관리합니다.
- **패킷 라우팅:** 총 16종의 바이너리 패킷을 분석하여 적절한 처리 로직으로 전달합니다.

### 3.2 AI 분석 파이프라인
로그 데이터를 기반으로 문제를 진단하고 수정 방안을 제안하는 4단계 프로세스를 운영합니다.

1. **Analyze (분석):** 로그 데이터에서 이상 징후를 식별합니다.
2. **Plan (계획):** 해결을 위한 단계별 시나리오를 작성합니다.
3. **Fix (수정):** 실제 코드나 설정 수정안을 생성합니다. (GPT 생성 및 Claude 교차 검토 포함)
4. **Validate (검증):** 수정안의 구문 오류와 유효성을 최종 확인합니다.

### 3.3 서버 텔레그램 봇
서버 측 관리를 위한 명령어를 제공합니다.

- **제공 명령어:** /analyze, /plan, /fix, /auto (전체 파이프라인), /deploy, /rollback, /log_hist, /log_real
- **보안:** `admin_chat_ids` 목록에 포함된 사용자만 접근 가능하며, 모든 작업 내역은 감사 로그에 기록됩니다.

### 3.4 웹 대시보드
브라우저를 통해 시스템 상태를 시각적으로 확인합니다.

- **인증:** JWT 쿠키 기반의 보안 인증을 사용합니다.
- **기능:** 5초 주기 자동 갱신 대시보드, WebSocket 기반 실시간 로그 스트리밍, AI 리포트 뷰어, 배포 이력 타임라인 등을 포함합니다.

### 3.5 헬스 모니터링 (HealthMonitor)
서버 자체의 자원 사용량을 감시합니다.

- **임계치 설정:** CPU(90%), 메모리(85%), 디스크(90%) 기준을 넘어서면 관리자에게 즉시 경고 알림을 발송합니다.
- **알림 쿨다운:** 동일한 경고가 반복되지 않도록 1800초의 알림 유예 시간을 가집니다.

### 3.6 구독 서버 (server/subscription/)
사용자 구독 및 API 키 배포를 담당하는 FastAPI 애플리케이션입니다.

- OAuth 디바이스 플로우 엔드포인트를 제공하며, 관리자 API를 통해 구독 키를 생성하거나 삭제할 수 있습니다.

## 4. 빌드 및 배포

### 4.1 빌드 방법
Python 3.10 이상의 환경에서 PyInstaller를 사용하여 빌드합니다.

- **명령어:** `python -m PyInstaller agent.spec --noconfirm` 또는 `build_agent.bat` 실행
- **산출물:** `dist/AILogOps-Agent/` 폴더 내에 실행 파일과 의존성 라이브러리가 생성됩니다.

### 4.2 배포 스크립트 (deploy.py) 사용법
다양한 시나리오에 맞게 deploy.py를 활용할 수 있습니다.

```bash
# 빌드 후 압축 파일 생성
python deploy.py

# 빌드 후 특정 경로로 직접 업데이트 적용 (서비스 중지, 복사, 시작 포함)
python deploy.py --target-dir D:\Agent

# 빌드 과정을 건너뛰고 기존 파일을 압축하여 텔레그램으로 전송
python deploy.py --skip-build

# 텔레그램 전송 정보 직접 지정
python deploy.py --bot-token [TOKEN] --chat-id [ID]
```

- **직접 업데이트:** 에이전트와 같은 네트워크에 있을 때 유용하며, 설정 파일(config.yaml)을 보존하면서 파일을 교체합니다.
- **텔레그램 전송:** 원격지 에이전트 업데이트 시 사용하며, 파일이 20MB를 초과하면 자동으로 분할하여 전송합니다.

### 4.3 설치 절차
1. `build_agent.bat`를 실행하여 에이전트를 빌드합니다.
2. 생성된 `dist/AILogOps-Agent/` 폴더를 대상 장비로 복사합니다.
3. `config.yaml` 파일을 열어 봇 토큰, 관리자 ID, 로그 경로, 대상 프로세스 정보를 수정합니다.
4. 관리자 권한으로 터미널을 열고 `AILogOps-Agent.exe install` 명령어를 실행합니다.
5. `AILogOps-Agent.exe start` 명령어로 서비스를 시작합니다.
6. 텔레그램으로 "Agent started" 메시지가 도착하는지 확인합니다.

### 4.4 업데이트 절차
- **직접 업데이트:** `python deploy.py --target-dir [설치경로]` 명령어를 실행합니다.
- **원격 업데이트:** 텔레그램으로 전송받은 zip 파일을 에이전트 봇 채팅창으로 전달(Forward)한 뒤, `/update` 명령어를 전송합니다.

## 5. 설정 파일 (config.yaml) 전체 레퍼런스

```yaml
agent:
  id: "PC-FACTORY-01"       # 에이전트 식별자
  version: "1.0.0"          # 현재 버전

telegram:
  bot_token: "YOUR_BOT_TOKEN"
  # 개인 텔레그램 ID를 입력해야 합니다 (봇 ID 아님). @userinfobot 등을 통해 확인 가능합니다.
  admin_chat_id: 123456789

monitoring:
  log_folders:              # 감시할 로그 폴더 목록
    - "C:/Apps/TargetApp/logs"
  history_max_mb: 10         # 과거 로그 읽기 제한 용량
  watch_extensions: [".log", ".txt"]

target_process:
  name: "TargetApp.exe"     # 프로세스 실행 파일명
  path: "C:/Apps/TargetApp/TargetApp.exe" # 전체 경로
  backup_dir: "C:/Apps/TargetApp/backups" # 백업 저장 경로
  args: []                  # 시작 인자
  auto_restart: false       # 종료 시 자동 재시작 여부
  check_interval: 30        # 상태 확인 주기(초)

schedule:
  restart_times: []         # 예: ["08:00", "18:00"] 정기 재시작 시간

connection:
  host: "127.0.0.1"         # 명령 서버 IP
  port: 9500                # 명령 서버 포트
  token: ""                 # 인증 토큰
  heartbeat_interval: 30    # 하트비트 주기(초)
  reconnect_attempts: 5     # 재연결 시도 횟수
  reconnect_delay: 10       # 재연결 대기 시간(초)

llm:
  auth_mode: "apikey"       # apikey 또는 subscription
  default_provider: "openai" # 기본 모델 제공자
  system_prompt: "당신은 로그 분석 전문가입니다."
  max_tokens: 2000
  openai:
    api_key: ""             # 비어있을 경우 OPENAI_API_KEY 환경변수 참조
    model: "gpt-4o-mini"
  claude:
    api_key: ""             # 비어있을 경우 ANTHROPIC_API_KEY 환경변수 참조
    model: "claude-3-5-sonnet-20241022"
  openrouter:
    api_key: ""             # 비어있을 경우 OPENROUTER_API_KEY 환경변수 참조
    model: "openai/gpt-4o-mini"
  subscription:
    server_url: ""          # 구독 서버 주소
    key: ""                 # 사전 등록된 구독 키
    revalidate_hours: 24     # 인증 갱신 주기
```

## 6. TCP 바이너리 프로토콜

모든 패킷은 헤더(5바이트)로 시작합니다: `[PacketType(1B)][PayloadLength(4B, Big-Endian)]`

| 값 | 이름 | 방향 | 페이로드 크기 | 설명 |
| :--- | :--- | :--- | :--- | :--- |
| 0x01 | AUTH | C -> S | 104B | 에이전트 인증 요청 (ID, 버전, 토큰) |
| 0x02 | AUTH_ACK | S -> C | 17B | 인증 결과 응답 |
| 0x03 | LOG_HIST | C -> S | 260B + 가변 | 과거 로그 파일 데이터 전송 |
| 0x04 | LOG_REAL | C -> S | 258B + 가변 | 실시간 발생 로그 데이터 전송 |
| 0x10 | CMD_DEPLOY | S -> C | 292B | 배포 명령 하사 |
| 0x11 | FILE_CHUNK | S -> C | 6B + 가변 | 배포용 파일 조각 전송 (최대 4KB) |
| 0x12 | FILE_ACK | C -> S | 5B | 파일 조각 수신 확인 |
| 0x13 | CMD_CTRL | S -> C | 1B | 프로세스 제어 (시작, 종료, 재시작) |
| 0x14 | CMD_CTRL_ACK | C -> S | 6B | 제어 결과 응답 |
| 0x15 | CMD_LOG | S -> C | 9B | 로그 수집 설정 변경 제어 |
| 0x16 | CMD_LOG_ACK | C -> S | 4B | 로그 설정 변경 확인 |
| 0x17 | LOG_FILE_LIST | C -> S | 2B + N*276B | 에이전트 보유 로그 파일 목록 보고 |
| 0x18 | LOG_FILE_SELECT | S -> C | 2B + N*256B | 서버에서 수집할 파일 선택 지시 |
| 0x20 | AGENT_UPDATE | S -> C | 가변 | 에이전트 자체 업데이트 데이터 |
| 0xFE | HEARTBEAT | 양방향 | 10B | 상태 유지 및 자원 지표 교환 |
| 0xFF | DISCONNECT | 양방향 | 1B | 연결 종료 알림 |
