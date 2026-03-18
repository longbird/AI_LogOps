# Dual-Pane File Manager Design

## Overview

Total Commander 스타일의 듀얼 패널 파일 매니저를 GUI 서버(tkinter)에 구현한다.
왼쪽 패널은 서버 로컬 파일시스템, 오른쪽 패널은 연결된 에이전트의 원격 파일시스템을 표시하며,
양방향 파일 전송을 지원한다.

## Requirements

- **양방향 전송**: 서버→에이전트, 에이전트→서버 모두 지원
- **보안**: 에이전트 전체 파일시스템 탐색 가능, 특정 폴더 쓰기/삭제 금지
- **파일 크기**: 100MB 이하
- **UI**: 기존 GUI에 "파일 관리" 탭 추가
- **구현 순서**: GUI 서버 먼저, 검증 후 웹 대시보드
- **동시성**: 에이전트당 하나의 파일 전송만 허용 (단일 전송 모델)

## Architecture

### TCP Protocol Extension

기존 바이너리 프로토콜(`shared/protocol.py`)에 6개 패킷 타입 추가:

| PacketType | Hex | Direction | Purpose |
|------------|-----|-----------|---------|
| `CMD_FILE_LIST` | `0x60` | Server→Agent | 디렉토리 목록 요청 |
| `CMD_FILE_LIST_ACK` | `0x61` | Agent→Server | 디렉토리 목록 응답 |
| `CMD_FILE_GET` | `0x62` | Server→Agent | 파일 다운로드 요청 (에이전트→서버) |
| `CMD_FILE_GET_ACK` | `0x63` | Agent→Server | 파일 메타 + FILE_CHUNK 시작 신호 |
| `CMD_FILE_PUT` | `0x64` | Server→Agent | 파일 업로드 시작 (서버→에이전트) |
| `CMD_FILE_PUT_ACK` | `0x65` | Agent→Server | 업로드 완료/실패 응답 |

### Concurrency Model (단일 전송)

에이전트당 한 번에 하나의 파일 전송만 허용한다. 기존 `FILE_CHUNK(0x11)` + `FILE_ACK(0x12)`를
그대로 재활용하되, 전송 컨텍스트를 상태로 관리한다.

- **에이전트 측**: `_active_transfer: Optional[str]` 플래그 — `"deploy"`, `"file_put"`, `"file_get"` 중 하나
  - 전송 진행 중 다른 전송 요청이 오면 `error: "전송 진행 중"` 응답으로 거부
  - 기존 `DeployHandler._is_receiving`과 동일한 패턴
- **서버 측**: `_agent_transfer_lock: dict[str, asyncio.Lock]` — 에이전트 ID별 전송 잠금
  - 파일 전송 시작 전 lock 획득, 완료/실패 시 해제
  - 배포(`send_deploy`)도 동일 lock 사용하여 충돌 방지

이 방식으로 `FILE_CHUNK`에 `request_id`를 추가할 필요 없이, 수신 측에서 현재 활성 전송 컨텍스트로
청크를 라우팅한다.

### Payload Definitions

모든 새 Payload는 **JSON 직렬화**를 사용한다 (가변 길이 필드가 많으므로, 기존 `RecDataRespPayload` 패턴).

```python
# CMD_FILE_LIST payload
@dataclass
class CmdFileListPayload:
    path: str           # 탐색할 디렉토리 경로
    request_id: str     # 요청 ID (응답 매칭용)

    def pack(self) -> bytes:
        return json.dumps({"path": self.path, "request_id": self.request_id}).encode()

    @classmethod
    def unpack(cls, data: bytes) -> "CmdFileListPayload":
        d = json.loads(data)
        return cls(path=d["path"], request_id=d["request_id"])

# CMD_FILE_LIST_ACK payload
@dataclass
class CmdFileListAckPayload:
    request_id: str
    success: bool
    error: str          # 실패 시 에러 메시지, 성공 시 ""
    current_path: str   # 정규화된 현재 경로
    entries: list       # list of {name, is_dir, size, modified}

# CMD_FILE_GET payload
@dataclass
class CmdFileGetPayload:
    remote_path: str    # 에이전트에서 읽을 파일 경로
    request_id: str

# CMD_FILE_GET_ACK payload
@dataclass
class CmdFileGetAckPayload:
    request_id: str
    success: bool
    error: str          # 실패 시 에러 메시지
    file_size: int      # 0 if failed
    sha256: str         # "" if failed
    filename: str       # "" if failed

# CMD_FILE_PUT payload
@dataclass
class CmdFilePutPayload:
    remote_path: str    # 에이전트에 저장할 전체 경로
    file_size: int
    sha256: str
    filename: str
    request_id: str

# CMD_FILE_PUT_ACK payload
@dataclass
class CmdFilePutAckPayload:
    request_id: str
    success: bool
    error: str
```

### FileTransferReceiver Refactoring

기존 `FileTransferReceiver.start_receive()`는 `CmdDeployPayload`에 결합되어 있다.
이를 범용 인터페이스로 리팩토링한다:

```python
# Before: start_receive(cmd: CmdDeployPayload)
# After:  start_receive(file_size: int, sha256: str, filename: str)

class FileTransferReceiver:
    def start_receive(self, file_size: int, sha256: str, filename: str) -> None:
        """범용 파일 수신 시작. CmdDeployPayload 의존성 제거."""
        self._expected_size = file_size
        self._expected_hash = sha256
        self._filename = filename
        ...
```

기존 `DeployHandler`는 `start_receive(cmd.file_size, cmd.sha256, cmd.filename)`으로 호출 변경.
서버 측에서도 동일 클래스를 사용하여 에이전트→서버 청크를 수신한다.

### Server-Side File Receiver

서버에 `FileTransferReceiver` 인스턴스를 관리하는 로직 추가:

```python
# tcp_server.py
class TCPServer:
    # 에이전트→서버 파일 다운로드용
    _file_receivers: dict[str, FileTransferReceiver]     # agent_id → receiver
    _file_get_futures: dict[str, asyncio.Future]         # agent_id → result future
    _file_get_save_paths: dict[str, Path]                # agent_id → 저장 경로

    async def _handle_file_get_ack(self, agent_id: str, payload: CmdFileGetAckPayload):
        if not payload.success:
            self._file_get_futures[agent_id].set_result({"success": False, "error": payload.error})
            return
        receiver = FileTransferReceiver(save_dir=self._temp_dir)
        receiver.start_receive(payload.file_size, payload.sha256, payload.filename)
        self._file_receivers[agent_id] = receiver

    async def _handle_file_chunk_from_agent(self, agent_id: str, chunk: FileChunkPayload):
        receiver = self._file_receivers.get(agent_id)
        if not receiver:
            return
        receiver.receive_chunk(chunk.seq_num, chunk.data)
        # FILE_ACK는 보내지 않음 (에이전트→서버 방향에서는 불필요, 아래 설명 참조)
        if receiver.is_complete():
            result_path = receiver.assemble()
            if result_path:
                # 최종 경로로 이동
                final_path = self._file_get_save_paths[agent_id]
                shutil.move(result_path, final_path)
                self._file_get_futures[agent_id].set_result({"success": True, "path": str(final_path)})
            else:
                self._file_get_futures[agent_id].set_result({"success": False, "error": "SHA-256 mismatch"})
            del self._file_receivers[agent_id]
```

### FILE_ACK Direction Policy

- **서버→에이전트 전송 (CMD_FILE_PUT, CMD_DEPLOY)**: 에이전트가 `FILE_ACK`를 서버로 전송 (기존 패턴 유지)
- **에이전트→서버 전송 (CMD_FILE_GET)**: `FILE_ACK` 불필요. 에이전트는 일방적으로 청크를 전송하고,
  서버는 `CMD_FILE_GET_ACK`의 메타데이터(file_size, sha256)로 완전성 검증. TCP 자체가 순서 보장하므로
  청크별 ACK는 성능 저하만 초래.
- 전송 실패 시: 서버가 timeout (60초 무응답) 감지 → Future에 에러 설정 → GUI에 전달

### Security Model

에이전트 `config.yaml`에 설정:

```yaml
file_manager:
  enabled: true                    # false이면 모든 파일 관리 명령 거부
  write_deny_paths:                # 쓰기/삭제 금지 경로 (대소문자 무시, Windows)
    - "C:/Windows"
    - "C:/Program Files"
    - "C:/Program Files (x86)"
  max_file_size_mb: 100            # 전송 가능 최대 파일 크기 (양방향 적용)
```

에이전트 측 검증 로직:
1. `file_manager.enabled`가 `false`이면 모든 파일 명령에 `error: "파일 관리 비활성화"` 응답
2. 모든 경로는 `Path.resolve()`로 정규화 (symlink/junction 해석 포함) → `..` traversal + symlink escape 방지
3. 쓰기 명령(`CMD_FILE_PUT`) 수신 시 resolved 경로가 `write_deny_paths` 하위인지 검증
4. `CMD_FILE_GET` **및** `CMD_FILE_PUT` 모두 `max_file_size_mb` 검증 (양방향)
5. 디렉토리 탐색(`CMD_FILE_LIST`): `os.scandir()` 중 `PermissionError`는 해당 항목 건너뛰기 (부분 성공)

보안 설계 결정:
- **읽기 제한 없음**: 내부 관리 도구이므로 전체 파일시스템 탐색 허용. 외부 노출 시에는 별도 인증 필요.
- **대용량 디렉토리**: 항목 수 5000개 초과 시 잘라내고 `truncated: true` 표시 (JSON 10MB 제한 대응)

서버 대시보드 API 보안:
- 기존 JWT 인증 (`server/dashboard/auth.py`) 적용 — 로그인 필수
- 모든 파일 작업에 감사 로그 기록 (`shared/utils.py` audit_log 활용)

### Components

#### 1. Protocol Layer (`shared/protocol.py`)

- `PacketType` enum에 6개 타입 추가 (0x60~0x65)
- 6개 Payload dataclass 추가 (JSON 직렬화, `pack()`/`unpack()`)
- `Packet._TYPE_LABELS` 업데이트

#### 2. FileTransferReceiver Refactoring (`agent/core/file_transfer.py`)

- `start_receive()` 시그니처를 `(file_size, sha256, filename)`으로 변경
- 기존 `DeployHandler` 호출부 수정
- 서버에서도 동일 클래스 import 가능하도록 `shared/`로 이동 고려 (또는 서버 측 복사)

#### 3. Agent File Handler (`agent/core/file_handler.py`) — 신규

- `FileHandler` 클래스: config 기반 보안 검증 + 파일 명령 처리
- `handle_cmd_file_list(payload)`: `os.scandir()` → 항목 목록 → `CMD_FILE_LIST_ACK`
- `handle_cmd_file_get(payload)`: 파일 읽기 → `CMD_FILE_GET_ACK` + `FILE_CHUNK` × N 스트리밍
- `handle_cmd_file_put(payload)`: `FileTransferReceiver` 시작
- `handle_file_chunk(payload)`: 청크 수신 → 완료 시 저장 → `CMD_FILE_PUT_ACK`
- `_validate_path(path, write=False)`: 경로 정규화 + 보안 검증

#### 4. Agent Runtime 연결 (`agent/core/agent_runtime.py`)

- `_create_server_connection()`에 `FileHandler` 콜백 등록:
  - `on_cmd_file_list` → `file_handler.handle_cmd_file_list`
  - `on_cmd_file_put` → `file_handler.handle_cmd_file_put`
  - `on_cmd_file_get` → `file_handler.handle_cmd_file_get`
  - `on_file_chunk` 기존 콜백에 file_handler 분기 추가

#### 5. Agent TCP Client (`agent/core/tcp_client.py`)

- `_recv_loop()`에 새 패킷 타입 디스패치 추가
- `on_cmd_file_list`, `on_cmd_file_get`, `on_cmd_file_put` 콜백 슬롯

#### 6. Server TCP Extension (`server/core/tcp_server.py`)

- `send_file_list(agent_id, path)`: Future 기반 요청-응답
- `send_file_get(agent_id, remote_path, save_path)`: 에이전트→서버 다운로드
- `send_file_put(agent_id, local_path, remote_path)`: 서버→에이전트 업로드 (기존 `send_deploy`와 유사)
- `_agent_transfer_lock`: 에이전트별 전송 잠금
- `_file_receivers`, `_file_get_futures`: 서버 측 수신 관리
- `_handle_client()`에 새 패킷 타입 처리 추가

#### 7. Dashboard API (`server/dashboard/routes/file_api.py`) — 신규 (Phase 2)

웹 대시보드용. GUI 검증 후 구현.

```
GET  /api/files/server/list?path=...          서버 로컬 디렉토리 목록
GET  /api/files/agent/{id}/list?path=...      에이전트 디렉토리 목록 (TCP 프록시)
POST /api/files/transfer                       파일 전송 요청
GET  /api/files/transfer/{id}/status           전송 진행률
```

#### 8. GUI Tab (`server/gui/tabs/file_manager_tab.py`) — 신규

tkinter 듀얼 패널. **GUI는 같은 프로세스 내의 TCP 서버를 직접 호출**한다 (HTTP 경유 아님).

```
┌─ 파일 관리 ─────────────────────────────────────────────┐
│ [에이전트: ▼ BS_SVR_89 ]                                 │
│                                                          │
│ ┌── 서버 (로컬) ──────────┐ ┌── 에이전트 (원격) ────────┐ │
│ │ [경로 입력]  [↑][새로고침]│ │ [경로 입력]  [↑][새로고침]│ │
│ │ ┌─ Treeview ──────────┐ │ │ ┌─ Treeview ──────────┐  │ │
│ │ │ 이름 | 크기 | 수정일  │ │ │ │ 이름 | 크기 | 수정일  │ │ │
│ │ │ [..] DIR   ...      │ │ │ │ [..] DIR   ...      │  │ │
│ │ │ logs/ DIR  ...      │ │ │ │ Server2/ DIR ...    │  │ │
│ │ │ update.zip 39MB ... │ │ │ │ REC_SVR2.exe 5MB   │  │ │
│ │ └─────────────────────┘ │ │ └─────────────────────┘  │ │
│ └─────────────────────────┘ └──────────────────────────┘ │
│                                                          │
│  [→ 에이전트로 복사]  [← 서버로 복사]  [취소]              │
│  ┌─ 진행률 ─────────────────────────────────────────┐    │
│  │ ████████████████░░░░ 75% update.zip (29/39 MB)   │    │
│  └──────────────────────────────────────────────────┘    │
│  상태: 전송 중...                                         │
└──────────────────────────────────────────────────────────┘
```

- `ttk.Treeview`: 컬럼 (이름, 크기, 수정일), 헤더 클릭 정렬
- 더블클릭: 폴더 진입, `[..]`: 상위 이동
- 선택 + 버튼: 파일 복사
- `ttk.Progressbar`: 전송 진행률
- [취소] 버튼: 진행 중 전송 취소 (Future cancel)
- 서버 패널: `os.scandir()`로 직접 탐색 (로컬)
- 에이전트 패널: TCP `CMD_FILE_LIST` → 비동기 콜백으로 업데이트

### Data Flow

**GUI→서버 로컬 탐색 (왼쪽 패널):**
```
GUI 경로 변경
  → os.scandir(path) — 동일 프로세스, 직접 호출
  → Treeview 업데이트
```

**GUI→에이전트 원격 탐색 (오른쪽 패널):**
```
GUI 경로 변경
  → app.async_run(tcp_server.send_file_list(agent_id, path))
  → TCP: CMD_FILE_LIST → 에이전트
  ← TCP: CMD_FILE_LIST_ACK (entries[])
  → Future 완료 → GUI 콜백 → Treeview 업데이트
```

**서버→에이전트 파일 복사:**
```
GUI "에이전트로 복사" 클릭
  → app.async_run(tcp_server.send_file_put(agent_id, local_path, remote_path))
  → TCP: CMD_FILE_PUT (metadata) → FILE_CHUNK × N → 에이전트
  ← TCP: FILE_ACK × N → CMD_FILE_PUT_ACK (success/fail)
  → Future 완료 → GUI 상태 업데이트
  진행률: send_file_put 내 콜백으로 바이트 전송량 보고 → GUI Progressbar 갱신
```

**에이전트→서버 파일 복사:**
```
GUI "서버로 복사" 클릭
  → app.async_run(tcp_server.send_file_get(agent_id, remote_path, local_save_path))
  → TCP: CMD_FILE_GET → 에이전트
  ← TCP: CMD_FILE_GET_ACK (metadata) → FILE_CHUNK × N → 서버
  → 서버 FileTransferReceiver로 조립 → 로컬 파일 저장
  → Future 완료 → GUI 상태 업데이트
  진행률: 수신 바이트 / 전체 바이트 → GUI Progressbar 갱신
```

### Error Handling

| Scenario | Handling |
|----------|----------|
| 에이전트 미연결 | 에이전트 패널 비활성화, "에이전트를 선택하세요" 표시 |
| 경로 존재하지 않음 | ACK `error` 필드 → GUI 상태바에 에러 표시 |
| 쓰기 금지 경로 | 에이전트 거부 → "접근 거부: 쓰기 금지 경로" |
| 파일 크기 초과 | 에이전트 거부 → "파일 크기 제한 초과 (100MB)" |
| 전송 중 연결 끊김 | 60초 timeout → 부분 파일 삭제 → 에러 표시 |
| SHA-256 불일치 | 전송 실패 → "파일 무결성 검증 실패" 표시 |
| 전송 중 다른 전송 시도 | 서버 lock으로 대기 또는 "전송 진행 중" 에러 |
| 사용자 취소 | Future cancel → 에이전트에 전송 중단 (부분 파일 삭제) |
| 디렉토리 권한 없음 | 접근 가능한 항목만 표시, 에러 항목 건너뛰기 |
| 대용량 디렉토리 (5000+) | 5000개까지만 표시 + truncated 경고 |

### Testing Strategy

1. **Protocol 단위 테스트**: 새 Payload 클래스의 pack/unpack 라운드트립 검증
2. **FileTransferReceiver 리팩토링 테스트**: 기존 deploy 테스트가 리팩토링 후에도 통과하는지 확인
3. **Agent FileHandler 테스트**: 보안 검증 (deny path, size limit, traversal) 단위 테스트
4. **통합 테스트**: 실제 TCP 연결로 list/get/put 양방향 전송 검증
5. **GUI 수동 테스트**: 에이전트 연결 후 파일 탐색/복사/진행률/에러 시나리오 검증

### Implementation Order

**Phase 1: GUI 서버 (이번 구현)**

1. `shared/protocol.py` — 패킷 타입 6개 + Payload 클래스 6개
2. `agent/core/file_transfer.py` — `FileTransferReceiver` 시그니처 리팩토링
3. `agent/core/deploy_handler.py` — 리팩토링된 시그니처로 호출부 수정
4. `agent/core/file_handler.py` — 신규, 에이전트 파일 명령 처리
5. `agent/core/tcp_client.py` — 새 패킷 디스패치 + 콜백 슬롯
6. `agent/core/agent_runtime.py` — FileHandler 생성 + 콜백 연결
7. `server/core/tcp_server.py` — 서버 측 send/receive + lock + receiver
8. `server/gui/tabs/file_manager_tab.py` — 신규, 듀얼 패널 GUI
9. `server/gui/app.py` — 탭 등록

**Phase 2: 웹 대시보드 (Phase 1 검증 후)**

10. `server/dashboard/routes/file_api.py` — HTTP API
11. 웹 프론트엔드 듀얼 패널 컴포넌트
