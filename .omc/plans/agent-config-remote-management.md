# Agent Config Remote Management Plan

## Overview
서버에서 에이전트의 config.yaml을 원격으로 가져오고, 편집하고, 암호화된 상태로 저장/전송하여 실시간 적용하는 기능.

## Architecture

```
Server GUI/Web ──[HTTP API]──> TCP Server ──[CMD_CONFIG]──> Agent
                                  │                           │
                              설정 편집 UI              config.yaml 읽기/쓰기
                              암호화/복호화              Fernet 암호화/복호화
                                                        핫 리로드
```

## Decision Summary
- **암호화**: Fernet 대칭키 (cryptography 라이브러리)
- **UI**: GUI(tkinter) + Web Dashboard 둘 다
- **적용 방식**: 핫 리로드 (config.yaml 저장 후 컴포넌트별 동적 반영)
- **암호화 대상**: 필드명 자동 감지 (password, token, api_key, secret 등)

---

## Phase 1: Protocol & Shared Layer

### 1-1. `shared/protocol.py` — 새 PacketType 추가
- `CMD_CONFIG = 0x40` (Server → Agent: 설정 요청/업데이트)
- `CMD_CONFIG_ACK = 0x41` (Agent → Server: 설정 응답)

### 1-2. `shared/protocol.py` — ConfigAction enum + Payload
```python
class ConfigAction(IntEnum):
    GET = 1       # 설정 가져오기 요청
    UPDATE = 2    # 설정 업데이트 전송

class CmdConfigPayload:
    action: ConfigAction
    config_data: str  # JSON-encoded config (빈 문자열 for GET)
```

### 1-3. `shared/crypto_utils.py` — 암호화 유틸리티 (새 파일)
- `SENSITIVE_PATTERNS = ["password", "token", "api_key", "secret", "key"]`
- `is_sensitive_field(field_name: str) -> bool` — 필드명 패턴 매칭
- `generate_key() -> bytes` — Fernet 키 생성
- `load_or_create_key(key_path: str) -> Fernet` — .env에서 키 로드 또는 생성
- `encrypt_value(fernet: Fernet, value: str) -> str` — 암호화 (ENC: 접두사)
- `decrypt_value(fernet: Fernet, value: str) -> str` — 복호화
- `encrypt_config(config: dict, fernet: Fernet) -> dict` — 재귀적으로 민감 필드 암호화
- `decrypt_config(config: dict, fernet: Fernet) -> dict` — 재귀적으로 복호화
- `mask_config(config: dict) -> dict` — UI 표시용 마스킹 (****)

---

## Phase 2: Agent Side

### 2-1. `agent/core/config_handler.py` — 설정 핸들러 (새 파일)
- `ConfigHandler` 클래스
  - `__init__(self, runtime: AgentRuntime, tcp_client: TCPClient)`
  - `handle_config_request(packet)` — GET: config.yaml 읽고 암호화 후 응답
  - `handle_config_update(packet)` — UPDATE: 수신된 설정을 복호화, config.yaml 저장, 핫 리로드 트리거
  - `_save_config(config: dict)` — YAML 저장 (config.yaml 백업 후 덮어쓰기)
  - `_hot_reload(old_config, new_config)` — 변경된 섹션만 감지하여 컴포넌트 재초기화

### 2-2. `agent/core/agent_runtime.py` — 수정
- `_create_server_connection()`에 ConfigHandler 등록
- `tcp_client.on_cmd_config` 콜백 등록
- `reload_config(changes: dict)` 메서드 추가 — 핫 리로드 엔트리포인트
  - monitoring 변경 → LogWatcher 재시작
  - telegram 변경 → TelegramNotifier 재생성
  - llm 변경 → LLM provider 재초기화
  - recording 변경 → RecordingController 재초기화
  - connection 변경 → "재시작 필요" 플래그 반환

### 2-3. `agent/core/tcp_client.py` — 수정
- `on_cmd_config` 콜백 등록 추가
- `_dispatch()` 에 `PacketType.CMD_CONFIG` 핸들링 추가

---

## Phase 3: Server TCP Layer

### 3-1. `server/core/tcp_server.py` — 수정
- `send_config_command(agent_id, action, config_data="")` 메서드 추가
- `_handle_config_ack(session, payload)` 핸들러 추가
  - GET 응답: `AgentSession`에 `config_data` 임시 저장
  - UPDATE ACK: 성공/실패 상태 저장

### 3-2. `server/core/session_mgr.py` — 수정
- `AgentSession`에 `config_data: dict | None` 필드 추가
- `config_update_status: str | None` 필드 추가

---

## Phase 4: Server Dashboard API

### 4-1. `server/dashboard/routes/config_api.py` — 새 파일
- `GET /api/config/{agent_id}` — 에이전트에 CMD_CONFIG GET 전송, 응답 대기(polling), 마스킹된 설정 반환
- `PUT /api/config/{agent_id}` — 수정된 설정을 CMD_CONFIG UPDATE로 전송
- `GET /api/config/{agent_id}/status` — 설정 업데이트 상태 확인 (polling)
- 설정 데이터는 서버에서 복호화 → UI에 표시 → 수정 → 다시 암호화하여 에이전트로 전송

### 4-2. `server/dashboard/app.py` — 수정
- config_api 블루프린트 등록

---

## Phase 5: Server GUI (tkinter)

### 5-1. `server/gui/tabs/agents_tab.py` — 수정 (설정 편집 섹션 추가)
- 에이전트 선택 시 "설정" 버튼 추가
- 클릭 → `ConfigEditorDialog` 열기

### 5-2. `server/gui/dialogs/config_editor.py` — 새 파일
- `ConfigEditorDialog(tk.Toplevel)` — 모달 다이얼로그
- 좌측: 설정 카테고리 트리 (agent, monitoring, telegram, connection, llm, recording)
- 우측: 선택된 카테고리의 필드 편집 폼
  - 일반 필드: Entry 위젯
  - 민감 필드: 마스킹 표시 + "보기/수정" 토글 버튼
  - bool 필드: Checkbutton
  - list 필드: 다중 행 Text
- 하단: "가져오기", "적용", "취소" 버튼
- "가져오기" → API 호출로 에이전트 설정 로드
- "적용" → API 호출로 수정된 설정 전송, 결과 표시

---

## Phase 6: Web Dashboard

### 6-1. `server/dashboard/templates/config.html` — 새 파일
- 에이전트 선택 드롭다운
- 카테고리별 아코디언 폼
- 민감 필드: 비밀번호 입력 + 보기 토글
- "설정 불러오기" / "적용" 버튼
- HTMX 비동기 통신

### 6-2. `server/dashboard/routes/dashboard.py` — 수정
- 네비게이션에 "설정" 메뉴 추가

---

## Phase 7: Encryption Integration

### 7-1. Agent 측 (.env 기반 키 관리)
- 에이전트 최초 실행 시 `FERNET_KEY` 없으면 자동 생성 → .env에 저장
- config.yaml 저장 시 민감 필드만 `ENC:gAAAAAB...` 형태로 저장
- config.yaml 로드 시 `ENC:` 접두사 감지하여 자동 복호화

### 7-2. 서버-에이전트 전송 시
- 에이전트 → 서버: 민감 필드 암호화 상태로 전송, 서버가 에이전트의 키로 복호화 불가 → **마스킹 전송**
- 서버 → 에이전트: 민감 필드를 평문으로 전송 (TCP 자체가 token 인증됨), 에이전트가 저장 시 암호화
- 또는: 민감 필드가 변경되지 않았으면 원본 유지 (마스킹 값은 무시)

### 7-3. 마스킹 로직
- 서버로 설정 전송 시: 민감 필드 = `"********"` (마스킹)
- 서버에서 수정 전송 시: `"********"` 값은 "변경 없음"으로 처리, 실제 입력된 새 값만 업데이트

---

## Implementation Order
1. `shared/crypto_utils.py` (암호화 유틸)
2. `shared/protocol.py` (CMD_CONFIG, ConfigAction, Payload)
3. `agent/core/config_handler.py` (설정 핸들러)
4. `agent/core/tcp_client.py` + `agent/core/agent_runtime.py` (콜백 등록)
5. `server/core/tcp_server.py` + `server/core/session_mgr.py` (서버 TCP)
6. `server/dashboard/routes/config_api.py` (REST API)
7. `server/gui/dialogs/config_editor.py` (GUI 다이얼로그)
8. `server/gui/tabs/agents_tab.py` (GUI 버튼 추가)
9. `server/dashboard/templates/config.html` (Web UI)
10. 테스트 및 통합 검증

## New Files
- `shared/crypto_utils.py`
- `agent/core/config_handler.py`
- `server/dashboard/routes/config_api.py`
- `server/gui/dialogs/config_editor.py`
- `server/dashboard/templates/config.html`

## Modified Files
- `shared/protocol.py`
- `agent/core/tcp_client.py`
- `agent/core/agent_runtime.py`
- `server/core/tcp_server.py`
- `server/core/session_mgr.py`
- `server/gui/tabs/agents_tab.py`
- `server/dashboard/routes/dashboard.py`
- `server/dashboard/app.py` (또는 블루프린트 등록 위치)

## Risks & Mitigations
| Risk | Mitigation |
|------|-----------|
| 암호화 키 분실 → 설정 복구 불가 | .env 백업 안내, config.yaml.bak 자동 생성 |
| 큰 설정 파일 TCP 전송 | JSON 압축 or 청크 분할 (현재 규모면 불필요) |
| 핫 리로드 중 오류 | 이전 설정 롤백, 에러 로그 전송 |
| 동시 수정 충돌 | 설정 편집 시 잠금(lock) 또는 마지막 수정 우선 |
