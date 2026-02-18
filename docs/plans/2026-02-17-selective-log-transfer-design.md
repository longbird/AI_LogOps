# Selective Log Transfer Protocol Design

**Date:** 2026-02-17
**Status:** Approved

## Problem

현재 `HIST_REQUEST` 시 에이전트가 매칭되는 모든 파일을 무조건 전송.
서버에 이미 존재하는 파일도 중복 전송되어 대역폭 낭비.

## Solution: 3-Step Selective Transfer

```
Step 1: Server → CMD_LOG(HIST_REQUEST, "YYYYMMDD") → Agent         [기존]
Step 2: Agent → LOG_FILE_LIST [{filename, size, md5}, ...] → Server [NEW]
Step 3: Server compares with local → LOG_FILE_SELECT [filenames] → Agent [NEW]
Step 4: Agent sends only selected files as LOG_HIST → Server        [기존]
```

## New Protocol Types

### PacketType Additions

| Type | Value | Direction | Purpose |
|------|-------|-----------|---------|
| `LOG_FILE_LIST` | `0x17` | Agent → Server | 파일 메타데이터 목록 |
| `LOG_FILE_SELECT` | `0x18` | Server → Agent | 전송 대상 파일 선택 |

### LogFileEntry (268B → 276B per entry)

```
Filename(256B) + FileSize(4B) + MD5(16B) = 276B
```

- Filename: null-padded UTF-8, 256 bytes
- FileSize: uint32, big-endian (max ~4GB)
- MD5: 16 bytes raw digest

### LogFileListPayload (Agent → Server)

```
[FileCount(2B)] + N × LogFileEntry(276B)
```

- FileCount: uint16 (max 65535)
- Variable length payload

### LogFileSelectPayload (Server → Agent)

```
[FileCount(2B)] + N × [Filename(256B)]
```

- FileCount: uint16
- Only filenames (no metadata needed for selection response)

## Server Comparison Logic

```
for each agent_file in file_list:
    local_path = storage/logs/{agent_id}/{filename}
    if not exists(local_path):       → 전송 대상 (새 파일)
    elif size != agent_file.size:    → 전송 대상 (크기 변경)
    elif md5(local) != agent_file.md5: → 전송 대상 (내용 변경)
    else:                            → 스킵
```

## Agent 2-Phase Flow

### Phase 1: CMD_LOG(HIST_REQUEST) 수신

1. `find_files_by_date(date_str)` 로 파일 검색
2. 각 파일의 filename, size, MD5 수집
3. `LOG_FILE_LIST` 패킷으로 서버에 전송
4. `asyncio.Event` 로 Phase 2 대기 (timeout 30s)

### Phase 2: LOG_FILE_SELECT 수신 (콜백)

1. 선택된 파일명 목록 수신
2. 해당 파일들만 `LOG_HIST` 로 전송
3. 완료 ACK 전송

## Sequence Diagram

```
Telegram User          Server                    Agent
    |                    |                          |
    |  /log_hist A1 20260217                        |
    |------------------->|                          |
    |                    |  CMD_LOG(HIST_REQUEST)    |
    |                    |------------------------->|
    |                    |                          | find files, compute MD5
    |                    |  LOG_FILE_LIST            |
    |                    |<-------------------------|
    |                    | compare with local       |
    |                    | storage/logs/A1/         |
    |                    |  LOG_FILE_SELECT          |
    |                    |------------------------->|
    |                    |                          | send selected only
    |                    |  LOG_HIST (file 1)        |
    |                    |<-------------------------|
    |                    |  LOG_HIST (file N)        |
    |                    |<-------------------------|
    |  "N/M 파일 전송 완료"  |                        |
    |<-------------------|                          |
```

## Files to Modify

| File | Change |
|------|--------|
| `shared/protocol.py` | PacketType +2, LogFileEntry, LogFileListPayload, LogFileSelectPayload |
| `agent/core/log_watcher.py` | `get_files_metadata(date_str)` 추가 |
| `agent/core/log_cmd_handler.py` | 2-phase `_handle_hist_request` + `handle_file_select()` |
| `agent/core/tcp_client.py` | `send_log_file_list()` + `on_log_file_select` 콜백 |
| `server/core/tcp_server.py` | `_handle_log_file_list()` + `_send_log_file_select()` |
| `server/storage/manager.py` | `get_stored_file_metadata(agent_id)` |
| Tests | 기존 업데이트 + 신규 추가 |
