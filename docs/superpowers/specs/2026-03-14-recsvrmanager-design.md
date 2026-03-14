# RecSvrManager: AirREC Graceful Update System

## Overview

AirREC 녹취 서버의 업데이트 시 진행중인 녹취 데이터가 유실되는 문제를 해결하기 위한 관리 프로세스(RecSvrManager) 설계.

RecSvrManager는 C++ 독립 프로세스로, AirREC의 전체 lifecycle을 관장하며 무중단 업데이트(graceful update)를 수행한다.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  AI-LogOps Agent (Python, Windows Service)              │
│  ┌──────────────┐                                       │
│  │ProcessManager│─── 시작/모니터링/종료 ────────────┐    │
│  └──────────────┘                                   │    │
│  ┌──────────────┐                                   ▼    │
│  │DeployHandler │─── 업데이트 zip 전달 ──→ RecSvrManager │
│  └──────────────┘                          (C++ exe)     │
└─────────────────────────────────────────────────────────┘
                                               │
                    Named Pipe (\\.\pipe\AirRecManager)
                                               │
                    ┌──────────────────────────┤
                    ▼                          ▼
              AirREC 기존플              AirREC 신규플
              (drain 모드)              (정상 동작)
              - 신규 녹취 거부           - 신규 녹취 처리
              - 진행중만 완료            - 관리플과 Pipe 연결
              - 완료 후 자체 종료
```

### Key Relationships

- **Agent → RecSvrManager**: Agent의 `ProcessManager`가 RecSvrManager를 시작/모니터링/종료. 기존 `ProcessManager` 패턴 그대로 사용.
- **Agent → RecSvrManager (deploy)**: `DeployHandler`가 `DeployTarget.PROCESS` 수신 시 zip을 RecSvrManager의 staging 디렉토리에 저장하고 업데이트 트리거.
- **RecSvrManager → AirREC**: `CreateProcess()`로 AirREC를 자식 프로세스로 실행. Named Pipe로 drain/shutdown 명령 전달.
- **AirREC → RecSvrManager**: Named Pipe로 HEARTBEAT, DRAIN_COMPLETE, STATUS 전송.

## Update Sequence

```
Agent          RecSvrManager       AirREC(기존)      AirREC(신규)
  │                 │                  │
  │──zip 전달──→    │                  │
  │                 │──신규플 준비(staging → versions/v{new})
  │                 │──CreateProcess───────────→│
  │                 │←────────Pipe 연결─────────│
  │                 │←────────HEARTBEAT─────────│
  │                 │                           │ (정상 가동 확인)
  │                 │──DRAIN_REQUEST──→│        │
  │                 │                  │──신규녹취 거부  │←─신규녹취 처리
  │                 │                  │──진행중만 완료
  │                 │←─DRAIN_COMPLETE──│
  │                 │                  │──자체 종료
  │                 │                           │ (단독 가동)
  │                 │  (수동 안정화 확인 대기)     │
  │──안정화 확인──→  │                           │
  │                 │──current 링크 변경          │
  │                 │──기존플 버전 백업 보존       │
  │←─완료 응답──    │                           │
```

### Critical Points

1. **신규플 먼저 시작** → 정상 가동 확인(첫 HEARTBEAT 수신) 후 기존플에 drain 요청
2. **동시 실행 기간** 존재: 기존플(drain 중) + 신규플(신규 녹취 처리)
3. **SMDR 중복 연결 허용**: PBX SMDR 서버는 다중 TCP 연결을 허용한다. 따라서 신규플 시작 시 즉시 SMDR 연결하여 양쪽 모두 SMDR 이벤트를 수신한다. 경합 방지는 drain 플래그로 처리 — 기존플은 `m_bDraining=true` 상태에서 새 세션 생성 SMDR 이벤트(IR/IA)를 무시하고, 기존 세션의 완료 이벤트(I/O)만 처리한다. 신규플은 정상적으로 모든 SMDR 이벤트를 처리한다.
4. **Npcap 동시 캡처**: 양쪽 모두 모든 RTP 패킷을 수신하지만, `CCallTable`이 call-ID 기반으로 자기 세션의 패킷만 처리하므로 데이터 오염 없음. 기존플은 drain 중 새 세션을 생성하지 않으므로 신규 통화의 RTP는 무시된다.
5. **안정화 판단**: 수동 확인 (서버에서 관리자가 확인 명령 전송)
6. **current 링크 변경 시점**: 수동 안정화 확인 후에 `current` 링크를 변경한다. 확인 전에는 기존 버전을 가리킨 채 유지.

## Named Pipe Protocol

```
파이프 이름: \\.\pipe\AirRecManager
방향: 양방향 (PIPE_ACCESS_DUPLEX)
모드: 메시지 기반 (PIPE_TYPE_MESSAGE)
다중 인스턴스: PIPE_UNLIMITED_INSTANCES (업데이트 시 기존플/신규플 동시 연결)
```

### Multi-Instance Pipe Handling

업데이트 중 기존플과 신규플이 동시에 연결된다. RecSvrManager는 각 연결을 별도 스레드로 처리하며, HEARTBEAT 메시지의 `pid` 필드로 기존플/신규플을 구분한다.

```
PipeServer
├── Connection[0] ← pid=1234 (기존플) → DRAIN_REQUEST 대상
├── Connection[1] ← pid=5678 (신규플) → 신규플로 식별
```

### Message Format

```c
typedef struct {
    DWORD type;      // 메시지 타입
    DWORD length;    // payload 길이 (바이트)
    BYTE  payload[]; // 가변 데이터
} PipeMessage;
```

### Message Types

| 방향 | Type | 이름 | Payload | 설명 |
|------|------|------|---------|------|
| Manager → AirREC | 0x01 | DRAIN_REQUEST | `{timeout_sec: DWORD}` | drain 모드 진입 요청 |
| Manager → AirREC | 0x02 | SHUTDOWN | 없음 | 즉시 종료 (강제) |
| Manager → AirREC | 0x03 | STATUS_REQUEST | 없음 | 현재 상태 조회 |
| Manager → AirREC | 0x04 | CANCEL_DRAIN | 없음 | drain 취소, 정상 모드 복귀 |
| AirREC → Manager | 0x81 | HEARTBEAT | `{active_calls: DWORD, pid: DWORD}` | 주기적 생존 신호 (10초) |
| AirREC → Manager | 0x82 | DRAIN_COMPLETE | 없음 | drain 완료, 종료 직전 |
| AirREC → Manager | 0x83 | STATUS_RESPONSE | `{state: DWORD, active_calls: DWORD}` | 상태 응답 |

### AirREC State

```c
enum RecState {
    STATE_NORMAL = 0,     // 정상 동작
    STATE_DRAINING = 1,   // drain 중 (신규 녹취 거부, 진행중만 처리)
    STATE_DRAIN_DONE = 2  // drain 완료, 종료 대기
};
```

## Directory Structure

```
D:\AirSoft\Server2\
├── current\                  ← 디렉토리 junction (mklink /J) → versions\v2.1.0
├── versions\
│   ├── v2.0.0\               ← 이전 버전 (백업)
│   │   ├── REC_SVR.exe
│   │   ├── REC_SVR2.exe
│   │   └── ...
│   ├── v2.1.0\               ← 현재 실행 중
│   │   ├── REC_SVR.exe
│   │   └── ...
│   └── v2.2.0\               ← 신규 (스테이징 완료)
│       ├── REC_SVR.exe
│       └── ...
├── staging\                  ← 업데이트 zip 압축 해제 임시 영역
├── Record\                   ← WAV 파일 (공유, 버전 무관)
│   ├── 20260314\
│   └── ...
├── RecSvrManager.exe
└── RecSvrManager.ini
```

### Directory Flow During Update

1. zip → `staging\` 에 압축 해제
2. `staging\` → `versions\v{new}\` 로 이동
3. 신규플: `versions\v{new}\REC_SVR.exe` 실행
4. 기존플: `versions\v{old}\REC_SVR.exe` drain 후 종료
5. 수동 안정화 확인 대기
6. 안정화 확인 후 → `current` junction → `versions\v{new}\` 로 변경 (`mklink /J`)
7. 오래된 versions 정리 (`MaxBackupVersions` 유지)
8. 롤백 시 → `current` junction → `versions\v{old}\` 복원

### Shared Resources (not versioned)

- `Record\` — WAV 파일 저장 (날짜별 분리, 충돌 없음)
- MySQL `rec_his` 테이블 — rec_no 유니크, 양쪽 동시 쓰기 가능

## Rollback Scenarios

### Case 1: 신규플 시작 실패

```
신규플 CreateProcess 실패 또는 Pipe 연결 안 됨
→ 기존플에 drain 보내지 않음
→ staging/versions 정리, 에러 로그
→ 기존플 그대로 유지
```

### Case 2: 신규플 시작 후 즉시 크래시 (drain 진행 중)

```
신규플 크래시 감지 (WaitForSingleObject or Pipe 끊김)
→ 기존플에 CANCEL_DRAIN (0x04) 전송
→ 기존플: m_bDraining = false, 정상 모드 복귀 (SMDR 연결은 유지 중이므로 재연결 불필요)
→ 신규플 버전 폴더 제거
→ 에러 로그
```

### Case 3: 수동 확인 전 롤백 요청

```
관리자가 롤백 명령 전송
→ 신규플에 SHUTDOWN (0x02) 전송
→ 기존 버전 폴더에서 다시 CreateProcess
→ current 링크 복원
→ 신규플 버전 폴더는 유지 (재시도 가능)
```

### Case 4: Drain 타임아웃 만료

```
DrainTimeoutSec 경과, 기존플이 아직 DRAIN_COMPLETE 전송 안 함
→ 기존플에 SHUTDOWN (0x02) 전송 (강제 종료)
→ 5초 대기 후 프로세스 미종료 시 TerminateProcess
→ 경고 로그 기록 (진행중이던 녹취 건수 포함)
→ 신규플은 이미 가동 중이므로 업데이트 계속 진행
→ 수동 안정화 확인 대기 상태로 전환
```

### Case 5: 안정화 확인 완료

```
관리자가 안정화 확인 명령 전송
→ current junction → 신규플 버전 폴더로 변경
→ 기존플 버전 폴더 → 백업으로 보존
→ MaxBackupVersions 초과 시 가장 오래된 것 삭제
```

### Case 6: RecSvrManager 크래시 복구

```
Agent가 RecSvrManager 종료 감지 → 자동 재시작
→ RecSvrManager 시작 시 상태 파일(update-state.json) 확인
→ 업데이트 진행중이었으면:
  - 실행 중인 AirREC 프로세스 탐색 (psutil 패턴)
  - current junction이 가리키는 버전의 AirREC를 primary로 인식
  - 상태 파일 기반으로 업데이트 재개 또는 롤백 결정
→ 업데이트 아니었으면:
  - current junction의 AirREC를 정상 시작
```

## RecSvrManager Internal Structure

```
RecSvrManager.exe
├── main()
│   ├── Config 로드 (RecSvrManager.ini)
│   ├── Named Pipe 서버 생성 (\\.\pipe\AirRecManager)
│   ├── AirREC 시작 (CreateProcess via current\ 링크)
│   └── 메인 루프 (명령 대기)
│
├── ProcessController
│   ├── StartProcess(exePath) → CreateProcess + 핸들 보관
│   ├── WaitForExit(timeout) → WaitForSingleObject
│   ├── ForceKill() → TerminateProcess
│   └── IsRunning() → 프로세스 상태 확인
│
├── PipeServer
│   ├── AirREC → Manager: HEARTBEAT, DRAIN_COMPLETE, STATUS_RESPONSE
│   └── Manager → AirREC: DRAIN_REQUEST, CANCEL_DRAIN, SHUTDOWN, STATUS_REQUEST
│
├── UpdateManager
│   ├── ReceiveUpdate(zipPath) → staging에 압축 해제 → versions/v{new}에 이동
│   ├── ExecuteUpdate()
│   │   ├── 1. versions/v{new}/REC_SVR.exe CreateProcess
│   │   ├── 2. 신규플 Pipe 연결 + HEARTBEAT 확인
│   │   ├── 3. 기존플에 DRAIN_REQUEST 전송
│   │   ├── 4. DRAIN_COMPLETE 대기 (타임아웃 시 SHUTDOWN → 강제 종료)
│   │   └── 5. 수동 안정화 확인 대기 → 확인 후 current junction 변경
│   └── Rollback() → 기존플 복원 + 신규플 종료
│
└── BackupManager
    ├── BackupVersion(version) → versions/에 보존
    ├── RestoreVersion(version) → current 링크 복원 + CreateProcess
    └── CleanOldVersions(keepCount) → MaxBackupVersions 초과 삭제
```

## Configuration

### RecSvrManager.ini

```ini
[Process]
ExePath=current\REC_SVR.exe
WorkDir=D:\AirSoft\Server2
Args=

[Update]
StagingDir=staging
VersionsDir=versions
DrainTimeoutSec=300
MaxBackupVersions=5

[Pipe]
PipeName=AirRecManager
HeartbeatIntervalSec=10

[Log]
LogDir=logs
LogLevel=INFO
```

## AirREC Modifications

### New File: PipeClient.h/cpp

Named Pipe 클라이언트. 별도 스레드에서 실행.

- `\\.\pipe\AirRecManager` 에 연결
- 메시지 수신 루프: DRAIN_REQUEST, CANCEL_DRAIN, SHUTDOWN, STATUS_REQUEST 처리
- HEARTBEAT 10초 주기 전송 (`active_calls`, `pid`)
- 연결 실패 시 재시도 (5초 간격)

### Modified: calltable.h/cpp

```cpp
// 추가 멤버
bool m_bDraining = false;          // drain 모드 플래그
int GetActiveCallCount();           // 진행중 통화 수 반환

// ProcessSmdrRecord() 수정
// drain 중에는 새 세션 생성만 차단, 기존 세션의 완료 이벤트(I/O status)는 계속 처리
if (m_bDraining && IsNewSession(smdrRecord)) {
    // 신규 통화 거부 (IR/IA 등 새 세션 시작 이벤트만 차단)
    return;
}
// 기존 call-ID의 완료 이벤트(I, O)는 정상 처리하여 DB 레코드 정상 종료

// drain 완료 체크 (active_calls == 0 && m_bDraining)
// → PipeClient를 통해 DRAIN_COMPLETE 전송 후 자체 종료
```

### SmdrClient.h/cpp — 수정 불필요

SMDR 중복 연결이 허용되므로 drain 시 SMDR 연결을 해제할 필요 없음. 기존플은 SMDR 연결을 유지한 채 `CCallTable`의 drain 플래그로 새 세션 생성만 차단한다. CANCEL_DRAIN 시에도 SMDR 재연결 없이 플래그만 복귀하면 된다.

### Modified: RecorderMain.cpp

```cpp
// Pipe 클라이언트 스레드 시작
CPipeClient pipeClient;
pipeClient.Start(&callTable, &smdrClient);
```

### Modified Files Summary

| 파일 | 변경 내용 |
|------|-----------|
| `PipeClient.h/cpp` | 신규. Named Pipe 클라이언트 스레드 |
| `calltable.h/cpp` | drain 플래그, GetActiveCallCount(), 신규 세션 생성 차단 |
| `RecorderMain.cpp` | PipeClient 스레드 시작 |

## Agent Integration Changes

### ProcessManager

- `target_process.name` → `RecSvrManager.exe` 로 변경
- `target_process.path` → RecSvrManager.exe 경로
- Agent는 RecSvrManager만 관리, AirREC는 RecSvrManager가 관리

### DeployHandler

- `DeployTarget.PROCESS` 수신 시:
  - zip을 RecSvrManager의 `StagingDir`에 저장
  - 업데이트 트리거: staging 디렉토리에 `update-ready.flag` 파일 생성
  - RecSvrManager가 staging 디렉토리를 주기적 폴링 (10초) → flag 파일 감지 시 업데이트 시작
  - flag 파일에 버전 정보 기록 (예: `version=2.2.0`)

### CtrlHandler

- `CMD_CTRL` (RESTART/STOP/START) → RecSvrManager로 전달
- RecSvrManager가 내부적으로 AirREC 제어

## Version Detection

업데이트 zip의 버전은 다음 순서로 결정한다:

1. `update-ready.flag` 파일의 `version=` 값 (Agent의 DeployHandler가 기록)
2. flag에 버전 없으면: zip 내 `version.h`의 `VERSION_STRING` 파싱
3. 둘 다 없으면: 타임스탬프 기반 자동 생성 (`v20260314_153000`)

## Update State Persistence

RecSvrManager는 업데이트 진행 상태를 `update-state.json`에 기록한다. 크래시 복구에 사용.

```json
{
  "phase": "draining",
  "old_version": "2.1.0",
  "old_pid": 1234,
  "new_version": "2.2.0",
  "new_pid": 5678,
  "started_at": "2026-03-14T15:30:00",
  "drain_timeout_sec": 300
}
```

**phase 값**: `idle`, `staging`, `starting_new`, `draining`, `awaiting_confirmation`, `confirmed`

업데이트 완료 또는 롤백 시 phase를 `idle`로 초기화.

## Non-Goals

- AirREC 간 데이터 마이그레이션 (불필요: DB와 WAV 파일은 공유 자원)
- RecSvrManager의 자체 업데이트 (Agent의 기존 ProcessDeployer로 처리)
- 자동 안정화 판단 (수동 확인으로 결정)
