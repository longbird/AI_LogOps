# RecSvrManager Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a C++ manager process (RecSvrManager) that enables zero-downtime AirREC updates via Named Pipe IPC, with graceful drain of in-progress recordings.

**Architecture:** Three subsystems — (1) AirREC C++ modifications for drain support + PipeClient, (2) RecSvrManager C++ new process, (3) AI-LogOps Agent Python integration. Named Pipe for IPC, directory junctions for version management, file-based update trigger.

**Tech Stack:** C++ (VS2015/v140, MFC), Python 3.13 (asyncio), Windows Named Pipes, directory junctions (`mklink /J`)

**Spec:** `docs/superpowers/specs/2026-03-14-recsvrmanager-design.md`

---

## File Structure

### AirREC Modifications (D:\Work\AirTech\PBXServer\AirRec\)

| File | Action | Responsibility |
|------|--------|----------------|
| `PipeClient.h` | Create | Named Pipe client class declaration, message types, PipeMessage struct |
| `PipeClient.cpp` | Create | Pipe connection, message loop, HEARTBEAT sender, drain/shutdown handlers |
| `calltable.h` | Modify | Add `m_bDraining` flag, `GetActiveCallCount()`, `SetDraining()` to `CCallTable` |
| `calltable.cpp` | Modify | Implement drain logic in `add_by_callid()`/`add()`/`add_by_rtp()` + drain check in `do_cleanup()` |
| `RecorderMain.cpp` | Modify | Instantiate and start `CPipeClient` in `CaptureProcessThread()` |
| `AirRecorder.vcxproj` | Modify | Add PipeClient.h/cpp to project |

### RecSvrManager (D:\Work\AirTech\PBXServer\RecSvrManager\ — new project)

| File | Action | Responsibility |
|------|--------|----------------|
| `RecSvrManager.sln` | Create | VS2015 solution |
| `RecSvrManager.vcxproj` | Create | Console app project (v140 toolset, x86) |
| `main.cpp` | Create | Entry point, config load, main loop, staging dir polling |
| `ProcessController.h/cpp` | Create | CreateProcess, WaitForExit, ForceKill, IsRunning |
| `PipeServer.h/cpp` | Create | Named Pipe server, multi-client, message dispatch |
| `PipeProtocol.h` | Create | Shared message types, PipeMessage struct (shared with AirREC) |
| `UpdateManager.h/cpp` | Create | Staging → versions, ExecuteUpdate sequence, rollback, state persistence |
| `BackupManager.h/cpp` | Create | Version directory management, junction ops, cleanup |
| `Config.h/cpp` | Create | INI file parser, config struct |
| `Logger.h/cpp` | Create | File-based logging |
| `RecSvrManager.ini` | Create | Default configuration |

### Agent Integration (D:\Work\AI_Projects\AI-LogOps\)

| File | Action | Responsibility |
|------|--------|----------------|
| `agent/updater/process_deploy.py` | Modify | Write `update-ready.flag` after staging zip extraction |
| `agent/core/deploy_handler.py` | Modify | Route PROCESS deploy to staging dir + flag file |

---

## Chunk 1: Shared Protocol + AirREC PipeClient

### Task 1: Create shared pipe protocol header

**Files:**
- Create: `D:\Work\AirTech\PBXServer\RecSvrManager\PipeProtocol.h`

This file will be shared between RecSvrManager and AirREC via include path.

- [ ] **Step 1: Create PipeProtocol.h with message types and struct**

```cpp
// PipeProtocol.h — Shared pipe protocol between RecSvrManager and AirREC
#pragma once
#include <windows.h>

#define PIPE_NAME L"\\\\.\\pipe\\AirRecManager"

// Manager → AirREC
#define MSG_DRAIN_REQUEST   0x01
#define MSG_SHUTDOWN        0x02
#define MSG_STATUS_REQUEST  0x03
#define MSG_CANCEL_DRAIN    0x04

// AirREC → Manager
#define MSG_HEARTBEAT       0x81
#define MSG_DRAIN_COMPLETE  0x82
#define MSG_STATUS_RESPONSE 0x83

#pragma pack(push, 1)

typedef struct {
    DWORD type;
    DWORD length;   // payload length in bytes (0 if no payload)
} PipeMessageHeader;

typedef struct {
    DWORD timeout_sec;
} DrainRequestPayload;

typedef struct {
    DWORD active_calls;
    DWORD pid;
} HeartbeatPayload;

typedef struct {
    DWORD state;         // 0=NORMAL, 1=DRAINING, 2=DRAIN_DONE
    DWORD active_calls;
} StatusResponsePayload;

#pragma pack(pop)
```

- [ ] **Step 2: Create RecSvrManager project directory**

```bash
mkdir -p "D:/Work/AirTech/PBXServer/RecSvrManager"
```

- [ ] **Step 3: Commit**

```bash
cd D:/Work/AirTech/PBXServer
git add RecSvrManager/PipeProtocol.h
git commit -m "feat: add shared pipe protocol header for RecSvrManager"
```

---

### Task 2: Add drain support to CCallTable

**Files:**
- Modify: `D:\Work\AirTech\PBXServer\AirRec\calltable.h` — add members to `CCallTable` class
- Modify: `D:\Work\AirTech\PBXServer\AirRec\calltable.cpp` — implement drain logic

- [ ] **Step 1: Add drain members to CCallTable in calltable.h**

Add these members to the `CCallTable` class (public section, after existing method declarations):

```cpp
// Drain support for graceful update
// volatile 필수: Pipe 스레드에서 설정, RTP/SIP 스레드에서 읽음.
// MSVC/x86에서 volatile은 acquire/release 시맨틱 제공하여 스레드 간 가시성 보장.
volatile bool m_bDraining;
void SetDraining(bool bDrain);
bool IsDraining() const { return m_bDraining; }
int  GetActiveCallCount();
```

- [ ] **Step 2: Initialize m_bDraining in CCallTable constructor**

In `calltable.cpp`, in `CCallTable::CCallTable()`, add after existing initializations:

```cpp
m_bDraining = false;
```

- [ ] **Step 3: Implement SetDraining() and GetActiveCallCount()**

In `calltable.cpp`, add:

```cpp
void CCallTable::SetDraining(bool bDrain)
{
    // volatile bool이므로 MSVC/x86에서 atomic store 보장.
    // mutex 불필요 — 단순 플래그 설정이고, 읽는 쪽도 volatile read.
    m_bDraining = bDrain;
}

int CCallTable::GetActiveCallCount()
{
    CSipMutexGuard guard(m_mtxTable);
    int nCount = 0;
    for (size_t i = 0; i < m_vecTable.size(); i++) {
        if (m_vecTable[i]->is_used) nCount++;
    }
    return nCount;
}
```

- [ ] **Step 4: Add drain guard to session creation methods**

In `calltable.cpp`, add the drain check INSIDE each method, immediately AFTER the first `m_mtxTable` lock acquisition, before the slot search loop. Each method manages its own locking internally, so the check must be placed inside the locked section to avoid a data race with `SetDraining()` called from the PipeClient thread.

For `add()` — after `CSipMutexGuard guard(m_mtxTable);` (or equivalent lock acquire):
```cpp
if (m_bDraining) {
    return -1;  // Reject new session during drain
}
```

For `add_by_callid()` — same pattern, after first lock acquire:
```cpp
if (m_bDraining) {
    return -1;
}
```

For `add_by_rtp()` — this method has multiple lock/unlock cycles. Place the check in the FIRST locked section, before slot allocation:
```cpp
if (m_bDraining) {
    return -1;
}
```

**Important**: `m_bDraining`은 `volatile bool`로 선언되어 mutex 밖에서도 읽기 안전하지만, 슬롯 할당 직전에 체크하는 것이 의미상 정확하므로 lock 내부에 배치.

**호출자 안전성 확인 완료**: `add()`/`add_by_callid()`/`add_by_rtp()`가 -1을 리턴하는 것은 기존에 없던 경로이지만, 주요 호출자 모두 안전하게 처리함:
- `SipHandler.cpp:228-232` — `if (idx < 0)` 체크 후 return
- `RecorderMain.cpp:925,945,1621,1741,1894,1913` — `if (idx < 0) return;` 패턴
- `RecorderMain.cpp:2270` — `if (idx >= 0)` 체크 후 진행
- 호출자가 -1을 배열 인덱스로 직접 사용하는 경우 **없음**

- [ ] **Step 5: Build AirREC to verify compilation**

```powershell
& 'C:\Program Files (x86)\MSBuild\14.0\Bin\MSBuild.exe' D:\Work\AirTech\PBXServer\AirRec\AirREC.sln '/p:Configuration=Release;Platform=x86' /v:minimal
```

Expected: Build succeeded. 0 Errors.

- [ ] **Step 6: Commit**

```bash
cd D:/Work/AirTech/PBXServer/AirRec
git add calltable.h calltable.cpp
git commit -m "feat: add drain support to CCallTable for graceful update"
```

---

### Task 3: Create PipeClient for AirREC

**Files:**
- Create: `D:\Work\AirTech\PBXServer\AirRec\PipeClient.h`
- Create: `D:\Work\AirTech\PBXServer\AirRec\PipeClient.cpp`

- [ ] **Step 1: Create PipeClient.h**

```cpp
// PipeClient.h — Named Pipe client for RecSvrManager communication
#pragma once
#include <windows.h>

class CCallTable;

class CPipeClient
{
public:
    CPipeClient();
    ~CPipeClient();

    // Note: Spec shows Start(&callTable, &smdrClient) but SmdrClient param is
    // unnecessary — spec section "SmdrClient.h/cpp — 수정 불필요" confirms
    // SMDR needs no modification. Drain is handled purely via CCallTable flag.
    bool Start(CCallTable* pCallTable);
    void Stop();
    bool IsConnected() const { return m_bConnected; }

private:
    static DWORD WINAPI PipeThreadProc(LPVOID lpParam);
    static DWORD WINAPI HeartbeatThreadProc(LPVOID lpParam);

    void PipeLoop();
    void HeartbeatLoop();

    bool Connect();
    void Disconnect();
    bool SendMessage(DWORD type, const void* payload, DWORD payloadLen);
    bool HandleMessage(DWORD type, const BYTE* payload, DWORD payloadLen);

    void OnDrainRequest(DWORD timeoutSec);
    void OnCancelDrain();
    void OnShutdown();
    void OnStatusRequest();

    void CheckDrainComplete();

    CCallTable*     m_pCallTable;
    HANDLE          m_hPipe;
    HANDLE          m_hPipeThread;
    HANDLE          m_hHeartbeatThread;
    volatile bool   m_bRunning;
    volatile bool   m_bConnected;
    DWORD           m_dwDrainTimeoutSec;
    DWORD           m_dwDrainStartTick;
};
```

- [ ] **Step 2: Create PipeClient.cpp**

```cpp
// PipeClient.cpp — Named Pipe client implementation
#include "stdafx.h"
#include "PipeClient.h"
#include "calltable.h"
#include "../RecSvrManager/PipeProtocol.h"
#include <stdio.h>

#define HEARTBEAT_INTERVAL_MS  10000
#define RECONNECT_INTERVAL_MS  5000
#define DRAIN_CHECK_INTERVAL_MS 1000

extern CCallTable* g_ct;

CPipeClient::CPipeClient()
    : m_pCallTable(NULL)
    , m_hPipe(INVALID_HANDLE_VALUE)
    , m_hPipeThread(NULL)
    , m_hHeartbeatThread(NULL)
    , m_bRunning(false)
    , m_bConnected(false)
    , m_dwDrainTimeoutSec(0)
    , m_dwDrainStartTick(0)
{
}

CPipeClient::~CPipeClient()
{
    Stop();
}

bool CPipeClient::Start(CCallTable* pCallTable)
{
    m_pCallTable = pCallTable;
    m_bRunning = true;

    m_hPipeThread = CreateThread(NULL, 0, PipeThreadProc, this, 0, NULL);
    if (!m_hPipeThread) return false;

    m_hHeartbeatThread = CreateThread(NULL, 0, HeartbeatThreadProc, this, 0, NULL);
    if (!m_hHeartbeatThread) return false;

    return true;
}

void CPipeClient::Stop()
{
    m_bRunning = false;
    Disconnect();

    if (m_hPipeThread) {
        WaitForSingleObject(m_hPipeThread, 5000);
        CloseHandle(m_hPipeThread);
        m_hPipeThread = NULL;
    }
    if (m_hHeartbeatThread) {
        WaitForSingleObject(m_hHeartbeatThread, 5000);
        CloseHandle(m_hHeartbeatThread);
        m_hHeartbeatThread = NULL;
    }
}

DWORD WINAPI CPipeClient::PipeThreadProc(LPVOID lpParam)
{
    ((CPipeClient*)lpParam)->PipeLoop();
    return 0;
}

DWORD WINAPI CPipeClient::HeartbeatThreadProc(LPVOID lpParam)
{
    ((CPipeClient*)lpParam)->HeartbeatLoop();
    return 0;
}

bool CPipeClient::Connect()
{
    m_hPipe = CreateFileW(
        PIPE_NAME,
        GENERIC_READ | GENERIC_WRITE,
        0, NULL, OPEN_EXISTING, 0, NULL);

    if (m_hPipe == INVALID_HANDLE_VALUE) return false;

    DWORD dwMode = PIPE_READMODE_MESSAGE;
    SetNamedPipeHandleState(m_hPipe, &dwMode, NULL, NULL);

    m_bConnected = true;
    return true;
}

void CPipeClient::Disconnect()
{
    m_bConnected = false;
    if (m_hPipe != INVALID_HANDLE_VALUE) {
        CloseHandle(m_hPipe);
        m_hPipe = INVALID_HANDLE_VALUE;
    }
}

void CPipeClient::PipeLoop()
{
    BYTE buffer[512];

    int nConnectFailCount = 0;
    while (m_bRunning) {
        if (!m_bConnected) {
            if (!Connect()) {
                nConnectFailCount++;
                // 로깅 스팸 방지: 첫 실패 + 이후 12회마다(1분) 로그
                if (nConnectFailCount == 1 || nConnectFailCount % 12 == 0) {
                    // LOG: "Pipe connection failed (attempt %d)", nConnectFailCount
                }
                Sleep(RECONNECT_INTERVAL_MS);
                continue;
            }
            nConnectFailCount = 0;  // 연결 성공 시 리셋
        }

        DWORD bytesRead = 0;
        BOOL ok = ReadFile(m_hPipe, buffer, sizeof(buffer), &bytesRead, NULL);

        if (!ok || bytesRead < sizeof(PipeMessageHeader)) {
            Disconnect();
            continue;
        }

        PipeMessageHeader* hdr = (PipeMessageHeader*)buffer;
        const BYTE* payload = buffer + sizeof(PipeMessageHeader);
        DWORD payloadLen = bytesRead - sizeof(PipeMessageHeader);

        HandleMessage(hdr->type, payload, payloadLen);
    }
}

void CPipeClient::HeartbeatLoop()
{
    while (m_bRunning) {
        if (m_bConnected && m_pCallTable) {
            HeartbeatPayload hb;
            hb.active_calls = (DWORD)m_pCallTable->GetActiveCallCount();
            hb.pid = GetCurrentProcessId();
            SendMessage(MSG_HEARTBEAT, &hb, sizeof(hb));

            // Check drain completion
            if (m_pCallTable->IsDraining()) {
                CheckDrainComplete();
            }
        }
        Sleep(HEARTBEAT_INTERVAL_MS);
    }
}

bool CPipeClient::SendMessage(DWORD type, const void* payload, DWORD payloadLen)
{
    if (!m_bConnected || m_hPipe == INVALID_HANDLE_VALUE) return false;

    PipeMessageHeader hdr;
    hdr.type = type;
    hdr.length = payloadLen;

    BYTE buf[512];
    memcpy(buf, &hdr, sizeof(hdr));
    if (payload && payloadLen > 0) {
        memcpy(buf + sizeof(hdr), payload, payloadLen);
    }

    DWORD written = 0;
    return WriteFile(m_hPipe, buf, sizeof(hdr) + payloadLen, &written, NULL) != FALSE;
}

bool CPipeClient::HandleMessage(DWORD type, const BYTE* payload, DWORD payloadLen)
{
    switch (type) {
    case MSG_DRAIN_REQUEST:
        if (payloadLen >= sizeof(DrainRequestPayload)) {
            DrainRequestPayload* p = (DrainRequestPayload*)payload;
            OnDrainRequest(p->timeout_sec);
        }
        return true;

    case MSG_CANCEL_DRAIN:
        OnCancelDrain();
        return true;

    case MSG_SHUTDOWN:
        OnShutdown();
        return true;

    case MSG_STATUS_REQUEST:
        OnStatusRequest();
        return true;
    }
    return false;
}

void CPipeClient::OnDrainRequest(DWORD timeoutSec)
{
    if (!m_pCallTable) return;
    m_dwDrainTimeoutSec = timeoutSec;
    m_dwDrainStartTick = GetTickCount();
    m_pCallTable->SetDraining(true);

    // Immediately check if already no active calls
    CheckDrainComplete();
}

void CPipeClient::OnCancelDrain()
{
    if (!m_pCallTable) return;
    m_pCallTable->SetDraining(false);
    m_dwDrainTimeoutSec = 0;
    m_dwDrainStartTick = 0;
}

void CPipeClient::OnShutdown()
{
    // CRITICAL: PostQuitMessage(0)은 WM_QUIT를 보내므로 OnClose()가 호출되지 않음.
    // OnClose()에는 SaveActiveSessions, DoAllCleanup, WAV 플러시 등 필수 정리 로직이 있음.
    // 반드시 WM_CLOSE를 보내야 OnClose()가 호출되어 정상 종료됨.
    m_bRunning = false;
    extern volatile bool g_bDrainShutdown;  // drain에 의한 종료 — OnClose 확인 다이얼로그 건너뜀
    g_bDrainShutdown = true;
    HWND hWnd = AfxGetMainWnd() ? AfxGetMainWnd()->m_hWnd : NULL;
    if (hWnd) {
        ::PostMessage(hWnd, WM_CLOSE, 0, 0);
    }
}

void CPipeClient::OnStatusRequest()
{
    if (!m_pCallTable) return;
    StatusResponsePayload resp;
    resp.state = m_pCallTable->IsDraining() ? 1 : 0;
    resp.active_calls = (DWORD)m_pCallTable->GetActiveCallCount();
    SendMessage(MSG_STATUS_RESPONSE, &resp, sizeof(resp));
}

void CPipeClient::CheckDrainComplete()
{
    if (!m_pCallTable || !m_pCallTable->IsDraining()) return;

    int nActive = m_pCallTable->GetActiveCallCount();
    if (nActive == 0) {
        // All calls completed — send DRAIN_COMPLETE and trigger graceful exit
        SendMessage(MSG_DRAIN_COMPLETE, NULL, 0);
        Sleep(500);  // Allow pipe message to be sent
        m_bRunning = false;
        // WM_CLOSE → OnClose() 호출 → SaveActiveSessions + DoAllCleanup + WAV 플러시
        extern volatile bool g_bDrainShutdown;
        g_bDrainShutdown = true;
        HWND hWnd = AfxGetMainWnd() ? AfxGetMainWnd()->m_hWnd : NULL;
        if (hWnd) {
            ::PostMessage(hWnd, WM_CLOSE, 0, 0);
        }
    }
}
```

- [ ] **Step 3: Add PipeClient files to AirRecorder.vcxproj**

Add `PipeClient.h` to `<ClInclude>` group and `PipeClient.cpp` to `<ClCompile>` group. Also add `../RecSvrManager` to AdditionalIncludeDirectories for PipeProtocol.h access.

- [ ] **Step 4: Build AirREC to verify**

```powershell
& 'C:\Program Files (x86)\MSBuild\14.0\Bin\MSBuild.exe' D:\Work\AirTech\PBXServer\AirRec\AirREC.sln '/p:Configuration=Release;Platform=x86' /v:minimal
```

Expected: Build succeeded. 0 Errors.

- [ ] **Step 5: Commit**

```bash
cd D:/Work/AirTech/PBXServer/AirRec
git add PipeClient.h PipeClient.cpp AirRecorder.vcxproj
git commit -m "feat: add PipeClient for RecSvrManager communication"
```

---

### Task 4: Integrate PipeClient into AirREC startup + drain 종료 지원

**Files:**
- Modify: `D:\Work\AirTech\PBXServer\AirRec\RecorderMain.cpp`
- Modify: `D:\Work\AirTech\PBXServer\AirRec\AirRecorderDlg.cpp`

- [ ] **Step 1: Add PipeClient include, global instance, and drain shutdown flag**

At top of `RecorderMain.cpp`, add:

```cpp
#include "PipeClient.h"

CPipeClient g_pipeClient;
volatile bool g_bDrainShutdown = false;  // drain 완료/SHUTDOWN 시 true → OnClose 확인 다이얼로그 건너뜀
```

- [ ] **Step 1b: Modify OnClose() in AirRecorderDlg.cpp to skip confirmation during drain**

In `CAirRecorderDlg::OnClose()` (line 388), the existing code shows a `MessageBox` confirmation dialog. Add drain bypass before it:

```cpp
void CAirRecorderDlg::OnClose()
{
    extern volatile bool g_bDrainShutdown;
    if (!g_bDrainShutdown) {
        // 기존 확인 다이얼로그 (사용자 수동 종료 시에만)
        if (MessageBox("Are you sure you want to exit?", ...) != IDYES)
            return;
    }

    // 이하 기존 정리 로직 그대로 유지:
    // SaveActiveSessions(), DoAllCleanup(), session_state.bin 삭제, 스레드 종료 등
    // ...
}
```

이렇게 하면 drain에 의한 `WM_CLOSE`는 확인 없이 정상 정리 경로를 탐. WAV 플러시, DB 기록, 세션 상태 저장 모두 정상 수행됨.

- [ ] **Step 2: Start PipeClient in CaptureProcessThread()**

In `CaptureProcessThread()`, after `g_pSmdrClient` initialization and `StartThread()` call (around line 144), add:

```cpp
// Start pipe client for RecSvrManager communication
g_pipeClient.Start(g_ct);
```

- [ ] **Step 3: Stop PipeClient on shutdown**

In the cleanup section of `CaptureProcessThread()` (before thread exits), add:

```cpp
g_pipeClient.Stop();
```

- [ ] **Step 4: Build and verify**

```powershell
& 'C:\Program Files (x86)\MSBuild\14.0\Bin\MSBuild.exe' D:\Work\AirTech\PBXServer\AirRec\AirREC.sln '/p:Configuration=Release;Platform=x86' /v:minimal
```

Expected: Build succeeded. 0 Errors.

- [ ] **Step 5: Commit**

```bash
cd D:/Work/AirTech/PBXServer/AirRec
git add RecorderMain.cpp
git commit -m "feat: integrate PipeClient into AirREC startup"
```

---

## Chunk 2: RecSvrManager Core

### Task 5: Create RecSvrManager VS project skeleton

**Files:**
- Create: `D:\Work\AirTech\PBXServer\RecSvrManager\RecSvrManager.sln`
- Create: `D:\Work\AirTech\PBXServer\RecSvrManager\RecSvrManager.vcxproj`
- Create: `D:\Work\AirTech\PBXServer\RecSvrManager\main.cpp`
- Create: `D:\Work\AirTech\PBXServer\RecSvrManager\Logger.h`
- Create: `D:\Work\AirTech\PBXServer\RecSvrManager\Logger.cpp`
- Create: `D:\Work\AirTech\PBXServer\RecSvrManager\Config.h`
- Create: `D:\Work\AirTech\PBXServer\RecSvrManager\Config.cpp`

- [ ] **Step 1: Create Config.h/cpp**

INI parser using `GetPrivateProfileString`/`GetPrivateProfileInt`.

```cpp
// Config.h
#pragma once
#include <string>

struct ManagerConfig {
    // [Process]
    std::wstring exePath;       // e.g., L"current\\REC_SVR.exe"
    std::wstring workDir;       // e.g., L"D:\\AirSoft\\Server2"
    std::wstring args;

    // [Update]
    std::wstring stagingDir;    // relative to workDir
    std::wstring versionsDir;   // relative to workDir
    int drainTimeoutSec;
    int maxBackupVersions;

    // [Pipe]
    std::wstring pipeName;
    int heartbeatIntervalSec;

    // [Log]
    std::wstring logDir;
    std::wstring logLevel;

    bool Load(const wchar_t* iniPath);
};
```

- [ ] **Step 2: Create Logger.h/cpp**

Simple file logger with timestamp and log level.

```cpp
// Logger.h
#pragma once
#include <string>

enum LogLevel { LOG_DEBUG, LOG_INFO, LOG_WARN, LOG_ERROR };

class CLogger {
public:
    static CLogger& Instance();
    bool Initialize(const std::wstring& logDir, const std::wstring& level);
    void Log(LogLevel level, const char* fmt, ...);
private:
    FILE* m_fp;
    LogLevel m_level;
    CRITICAL_SECTION m_cs;
};

#define LOG_I(...) CLogger::Instance().Log(LOG_INFO, __VA_ARGS__)
#define LOG_W(...) CLogger::Instance().Log(LOG_WARN, __VA_ARGS__)
#define LOG_E(...) CLogger::Instance().Log(LOG_ERROR, __VA_ARGS__)
#define LOG_D(...) CLogger::Instance().Log(LOG_DEBUG, __VA_ARGS__)
```

- [ ] **Step 3: Create main.cpp skeleton**

```cpp
// main.cpp — RecSvrManager entry point
#include <windows.h>
#include <stdio.h>
#include "Config.h"
#include "Logger.h"
#include "PipeProtocol.h"

ManagerConfig g_config;

int wmain(int argc, wchar_t* argv[])
{
    // Determine INI path (same dir as exe)
    wchar_t exePath[MAX_PATH];
    GetModuleFileNameW(NULL, exePath, MAX_PATH);
    std::wstring iniPath(exePath);
    iniPath = iniPath.substr(0, iniPath.rfind(L'\\') + 1) + L"RecSvrManager.ini";

    if (!g_config.Load(iniPath.c_str())) {
        printf("Failed to load config: %ls\n", iniPath.c_str());
        return 1;
    }

    CLogger::Instance().Initialize(g_config.logDir, g_config.logLevel);
    LOG_I("RecSvrManager starting...");

    // TODO: Task 6 — ProcessController
    // TODO: Task 7 — PipeServer
    // TODO: Task 8 — UpdateManager + BackupManager
    // TODO: Task 9 — Main loop (staging polling + command handling)

    LOG_I("RecSvrManager shutting down.");
    return 0;
}
```

- [ ] **Step 4: Create vcxproj and sln**

Create VS2015 project: Win32 Console Application, v140 toolset, x86, Release/Debug configs. Include all created files.

- [ ] **Step 5: Build to verify skeleton compiles**

```powershell
& 'C:\Program Files (x86)\MSBuild\14.0\Bin\MSBuild.exe' D:\Work\AirTech\PBXServer\RecSvrManager\RecSvrManager.sln '/p:Configuration=Release;Platform=x86' /v:minimal
```

Expected: Build succeeded.

- [ ] **Step 6: Create RecSvrManager.ini default config**

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

- [ ] **Step 7: Commit**

```bash
cd D:/Work/AirTech/PBXServer/RecSvrManager
git add -A
git commit -m "feat: create RecSvrManager project skeleton with config and logger"
```

---

### Task 6: Implement ProcessController

**Files:**
- Create: `D:\Work\AirTech\PBXServer\RecSvrManager\ProcessController.h`
- Create: `D:\Work\AirTech\PBXServer\RecSvrManager\ProcessController.cpp`

- [ ] **Step 1: Create ProcessController.h**

```cpp
// ProcessController.h — Process lifecycle management
#pragma once
#include <windows.h>
#include <string>

class CProcessController {
public:
    CProcessController();
    ~CProcessController();

    bool StartProcess(const std::wstring& exePath, const std::wstring& workDir, const std::wstring& args = L"");
    bool WaitForExit(DWORD timeoutMs);
    bool ForceKill();
    bool IsRunning() const;
    DWORD GetPid() const;
    HANDLE GetProcessHandle() const { return m_hProcess; }

private:
    HANDLE m_hProcess;
    HANDLE m_hThread;
    DWORD  m_dwPid;
};
```

- [ ] **Step 2: Create ProcessController.cpp**

Implement using `CreateProcessW`, `WaitForSingleObject`, `TerminateProcess`, `GetExitCodeProcess`.

- [ ] **Step 3: Add to vcxproj, build, verify**

```powershell
& 'C:\Program Files (x86)\MSBuild\14.0\Bin\MSBuild.exe' D:\Work\AirTech\PBXServer\RecSvrManager\RecSvrManager.sln '/p:Configuration=Release;Platform=x86' /v:minimal
```

- [ ] **Step 4: Commit**

```bash
cd D:/Work/AirTech/PBXServer/RecSvrManager
git add ProcessController.h ProcessController.cpp RecSvrManager.vcxproj
git commit -m "feat: add ProcessController for AirREC lifecycle management"
```

---

### Task 7: Implement PipeServer

**Files:**
- Create: `D:\Work\AirTech\PBXServer\RecSvrManager\PipeServer.h`
- Create: `D:\Work\AirTech\PBXServer\RecSvrManager\PipeServer.cpp`

- [ ] **Step 1: Create PipeServer.h**

```cpp
// PipeServer.h — Named Pipe server for AirREC communication
#pragma once
#include <windows.h>
#include <vector>
#include <string>
#include "PipeProtocol.h"

struct PipeConnection {
    HANDLE hPipe;
    HANDLE hThread;
    DWORD  dwClientPid;   // Identified from HEARTBEAT
    DWORD  dwLastHeartbeat; // GetTickCount() of last heartbeat
    bool   bActive;
};

// Callback for received messages
typedef void (*PipeMessageCallback)(DWORD clientPid, DWORD msgType, const BYTE* payload, DWORD len);

class CPipeServer {
public:
    CPipeServer();
    ~CPipeServer();

    bool Start(const std::wstring& pipeName, PipeMessageCallback callback);
    void Stop();

    bool SendToClient(DWORD clientPid, DWORD msgType, const void* payload, DWORD payloadLen);
    bool SendToAll(DWORD msgType, const void* payload, DWORD payloadLen);

    DWORD GetConnectedClientCount() const;
    bool IsClientConnected(DWORD pid) const;

private:
    static DWORD WINAPI AcceptThreadProc(LPVOID lpParam);
    static DWORD WINAPI ClientThreadProc(LPVOID lpParam);

    void AcceptLoop();
    void ClientLoop(PipeConnection* conn);

    std::wstring m_pipeName;
    PipeMessageCallback m_callback;
    std::vector<PipeConnection*> m_connections;
    CRITICAL_SECTION m_cs;
    volatile bool m_bRunning;
    HANDLE m_hAcceptThread;
};
```

- [ ] **Step 2: Create PipeServer.cpp**

Implement accept loop with `CreateNamedPipeW` (PIPE_UNLIMITED_INSTANCES), per-client thread with `ReadFile` message loop, `SendToClient` by matching `dwClientPid`.

Key details:
- Accept loop creates a new pipe instance for each incoming connection
- Client thread reads messages, dispatches via callback
- HEARTBEAT messages update `dwClientPid` and `dwLastHeartbeat` on the connection
- Thread-safe access to `m_connections` via `m_cs`

- [ ] **Step 3: Add to vcxproj, build, verify**

- [ ] **Step 4: Commit**

```bash
cd D:/Work/AirTech/PBXServer/RecSvrManager
git add PipeServer.h PipeServer.cpp RecSvrManager.vcxproj
git commit -m "feat: add PipeServer for multi-client Named Pipe communication"
```

---

### Task 8: Implement BackupManager and UpdateManager

**Files:**
- Create: `D:\Work\AirTech\PBXServer\RecSvrManager\BackupManager.h`
- Create: `D:\Work\AirTech\PBXServer\RecSvrManager\BackupManager.cpp`
- Create: `D:\Work\AirTech\PBXServer\RecSvrManager\UpdateManager.h`
- Create: `D:\Work\AirTech\PBXServer\RecSvrManager\UpdateManager.cpp`

- [ ] **Step 1: Create BackupManager.h/cpp**

```cpp
// BackupManager.h
#pragma once
#include <string>

class CBackupManager {
public:
    CBackupManager(const std::wstring& workDir, const std::wstring& versionsDir, int maxVersions);

    bool CreateVersionDir(const std::wstring& version);
    bool MoveToVersion(const std::wstring& srcDir, const std::wstring& version);
    bool UpdateJunction(const std::wstring& junctionName, const std::wstring& targetVersion);
    bool RemoveJunction(const std::wstring& junctionName);
    std::wstring GetJunctionTarget(const std::wstring& junctionName);
    bool CleanOldVersions();
    std::wstring GetVersionPath(const std::wstring& version);

private:
    std::wstring m_workDir;
    std::wstring m_versionsDir;
    int m_maxVersions;
};
```

Implement junction operations using `CreateSymbolicLinkW` with `SYMBOLIC_LINK_FLAG_DIRECTORY` or fallback to `DeviceIoControl(FSCTL_SET_REPARSE_POINT)` for directory junctions. Use `FindFirstFile`/`FindNextFile` for version enumeration.

Note: For directory junctions (`mklink /J`), use the `CreateDirectoryW` + `DeviceIoControl` approach or shell out to `cmd /c mklink /J` for simplicity on VS2015.

- [ ] **Step 2: Create UpdateManager.h/cpp**

```cpp
// UpdateManager.h
#pragma once
#include <string>
#include "ProcessController.h"
#include "PipeServer.h"
#include "BackupManager.h"

enum UpdatePhase {
    PHASE_IDLE = 0,
    PHASE_STAGING,
    PHASE_STARTING_NEW,
    PHASE_DRAINING,
    PHASE_AWAITING_CONFIRMATION,
    PHASE_CONFIRMED
};

struct UpdateState {
    UpdatePhase phase;
    std::wstring oldVersion;
    DWORD oldPid;
    std::wstring newVersion;
    DWORD newPid;
    DWORD drainTimeoutSec;
    DWORD startTick;
};

class CUpdateManager {
public:
    CUpdateManager(CProcessController* pOldProc, CPipeServer* pPipe, CBackupManager* pBackup,
                   const std::wstring& workDir, const std::wstring& stagingDir, int drainTimeoutSec);

    bool CheckForUpdate();          // Poll staging dir for update-ready.flag
    bool ExecuteUpdate();           // Full update sequence (async — returns after drain starts)
    void CheckDrainTimeout();       // Called from main loop — monitors drain timeout
    bool Rollback();                // Rollback to old version
    bool ConfirmStabilization();    // Manual confirmation → finalize

    UpdatePhase GetPhase() const { return m_state.phase; }
    const UpdateState& GetState() const { return m_state; }

    // Called by pipe message callback
    void OnDrainComplete(DWORD clientPid);
    void OnClientCrash(DWORD clientPid);

private:
    bool StageFiles(const std::wstring& version);
    bool StartNewProcess(const std::wstring& version);
    bool SendDrainToOld();
    bool ForceKillOld();
    void SaveState();
    void LoadState();
    void ClearState();
    std::wstring DetectVersion(const std::wstring& flagPath);

    CProcessController* m_pOldProc;
    CProcessController  m_newProc;
    CPipeServer* m_pPipe;
    CBackupManager* m_pBackup;

    std::wstring m_workDir;
    std::wstring m_stagingDir;
    int m_drainTimeoutSec;

    UpdateState m_state;
};
```

Implement:
- `CheckForUpdate()`: checks for `staging\update-ready.flag`, parses `version=` line
- `ExecuteUpdate()`: Full sequence with drain timeout monitoring:
  1. `StageFiles()` — staging → versions/v{new}
  2. `StartNewProcess()` — CreateProcess from versions/v{new}
  3. Wait for new process HEARTBEAT (30s timeout, fail → Case 1 rollback)
  4. `SendDrainToOld()` — DRAIN_REQUEST to old process, record `m_state.startTick = GetTickCount()`
  5. SaveState(PHASE_DRAINING)
  6. Return — drain completion is async via `OnDrainComplete()` callback
- `CheckDrainTimeout()`: **Called from main loop every 10s**. If `phase==DRAINING && (GetTickCount() - startTick) > drainTimeoutSec*1000`:
  - Send SHUTDOWN to old process
  - Wait 5s, then TerminateProcess if still alive
  - LOG_W with remaining active call count
  - Transition to AWAITING_CONFIRMATION
- `Rollback()`: SHUTDOWN new, restart old from version dir, update junction
- `ConfirmStabilization()`: update junction, clean old versions
- `SaveState()`/`LoadState()`: state to `update-state.json` (simple key=value format, no JSON lib needed)
- `OnDrainComplete()`: transition from DRAINING → AWAITING_CONFIRMATION, kill old process
- `OnClientCrash()`: if new process crashed during drain → CANCEL_DRAIN to old, transition to IDLE

- [ ] **Step 3: Add to vcxproj, build, verify**

- [ ] **Step 4: Commit**

```bash
cd D:/Work/AirTech/PBXServer/RecSvrManager
git add BackupManager.h BackupManager.cpp UpdateManager.h UpdateManager.cpp RecSvrManager.vcxproj
git commit -m "feat: add BackupManager and UpdateManager for version management"
```

---

### Task 9: Complete main.cpp with main loop

**Files:**
- Modify: `D:\Work\AirTech\PBXServer\RecSvrManager\main.cpp`

- [ ] **Step 1: Implement main loop**

Replace TODO sections in main.cpp with:

```cpp
#include "ProcessController.h"
#include "PipeServer.h"
#include "UpdateManager.h"
#include "BackupManager.h"

// Globals
CProcessController g_mainProc;
CPipeServer g_pipeServer;
CBackupManager* g_pBackup = NULL;
CUpdateManager* g_pUpdate = NULL;

void OnPipeMessage(DWORD clientPid, DWORD msgType, const BYTE* payload, DWORD len)
{
    switch (msgType) {
    case MSG_HEARTBEAT:
        // PipeServer already updates connection info
        break;
    case MSG_DRAIN_COMPLETE:
        if (g_pUpdate) g_pUpdate->OnDrainComplete(clientPid);
        break;
    case MSG_STATUS_RESPONSE:
        // Log status
        if (len >= sizeof(StatusResponsePayload)) {
            StatusResponsePayload* p = (StatusResponsePayload*)payload;
            LOG_I("Status from PID %u: state=%u, active_calls=%u", clientPid, p->state, p->active_calls);
        }
        break;
    }
}

int wmain(int argc, wchar_t* argv[])
{
    // ... config loading (existing code) ...

    // Initialize components
    g_pBackup = new CBackupManager(g_config.workDir, g_config.versionsDir, g_config.maxBackupVersions);
    g_pUpdate = new CUpdateManager(&g_mainProc, &g_pipeServer, g_pBackup,
        g_config.workDir, g_config.stagingDir, g_config.drainTimeoutSec);

    // Start pipe server
    g_pipeServer.Start(g_config.pipeName, OnPipeMessage);

    // ===== Crash Recovery (Spec Case 6) =====
    // LoadState() restores phase from update-state.json if RecSvrManager was restarted mid-update
    // UpdateManager constructor calls LoadState() internally
    UpdatePhase recoveredPhase = g_pUpdate->GetPhase();

    if (recoveredPhase != PHASE_IDLE) {
        LOG_W("Recovered from crash during update, phase=%d", (int)recoveredPhase);
        // Find any running AirREC processes by scanning process list
        // The current junction target is the "safe" version
        // If phase was DRAINING or STARTING_NEW → rollback to old version (safe choice)
        // If phase was AWAITING_CONFIRMATION → new version is running, keep it
        if (recoveredPhase == PHASE_AWAITING_CONFIRMATION) {
            LOG_I("Update was awaiting confirmation — resuming wait");
            // New process should still be running; re-attach via pipe
        } else {
            LOG_W("Update was in progress — rolling back to safe version");
            g_pUpdate->Rollback();
        }
    }

    // Start AirREC from current junction if not already running
    if (g_pUpdate->GetPhase() == PHASE_IDLE) {
        std::wstring exePath = g_config.workDir + L"\\" + g_config.exePath;
        if (!g_mainProc.StartProcess(exePath, g_config.workDir, g_config.args)) {
            LOG_E("Failed to start AirREC: %ls", exePath.c_str());
            return 1;
        }
        LOG_I("AirREC started, PID=%u", g_mainProc.GetPid());
    }

    // ===== Main Loop =====
    LOG_I("Entering main loop...");
    while (true) {
        // 1. Check for update trigger (only when idle)
        if (g_pUpdate->GetPhase() == PHASE_IDLE && g_pUpdate->CheckForUpdate()) {
            LOG_I("Update detected, starting update sequence...");
            if (!g_pUpdate->ExecuteUpdate()) {
                LOG_E("Update failed");
            }
        }

        // 2. Check drain timeout (only when draining)
        if (g_pUpdate->GetPhase() == PHASE_DRAINING) {
            g_pUpdate->CheckDrainTimeout();
        }

        // 3. Monitor main process (auto-restart if crashed, only when idle)
        if (g_pUpdate->GetPhase() == PHASE_IDLE && !g_mainProc.IsRunning()) {
            LOG_W("AirREC process died, restarting...");
            std::wstring exePath = g_config.workDir + L"\\" + g_config.exePath;
            g_mainProc.StartProcess(exePath, g_config.workDir, g_config.args);
        }

        Sleep(10000);  // 10 second polling interval
    }

    // Cleanup
    g_pipeServer.Stop();
    delete g_pUpdate;
    delete g_pBackup;

    return 0;
}
```

- [ ] **Step 2: Build full RecSvrManager**

```powershell
& 'C:\Program Files (x86)\MSBuild\14.0\Bin\MSBuild.exe' D:\Work\AirTech\PBXServer\RecSvrManager\RecSvrManager.sln '/p:Configuration=Release;Platform=x86' /v:minimal
```

Expected: Build succeeded. 0 Errors.

- [ ] **Step 3: Commit**

```bash
cd D:/Work/AirTech/PBXServer/RecSvrManager
git add main.cpp RecSvrManager.vcxproj
git commit -m "feat: complete RecSvrManager main loop with update detection and process monitoring"
```

---

## Chunk 3: Agent Integration

### Task 10: Modify Agent DeployHandler for flag-based trigger

**Files:**
- Modify: `D:\Work\AI_Projects\AI-LogOps\agent\updater\process_deploy.py`
- Modify: `D:\Work\AI_Projects\AI-LogOps\agent\core\deploy_handler.py`

- [ ] **Step 1: Update ProcessDeployer to write update-ready.flag**

In `process_deploy.py`, after zip extraction to staging directory in `execute_deploy()`, add flag file creation:

```python
# After extracting staged files to update_dir
flag_path = self.update_dir / "update-ready.flag"
version = self._detect_version()  # Parse version.h from staged files if present
flag_content = f"version={version}\n" if version else f"version=v{datetime.now().strftime('%Y%m%d_%H%M%S')}\n"
flag_path.write_text(flag_content, encoding="utf-8")
logger.info(f"Update flag written: {flag_path} ({flag_content.strip()})")
```

Add `_detect_version()` method:

```python
def _detect_version(self) -> str | None:
    """Try to detect version from staged version.h file."""
    version_h = self.update_dir / "version.h"
    if version_h.exists():
        import re
        content = version_h.read_text(encoding="utf-8", errors="ignore")
        match = re.search(r'#define\s+VERSION_STRING\s+"([^"]+)"', content)
        if match:
            return match.group(1)
    return None
```

- [ ] **Step 2: Update DeployHandler to skip kill/start for PROCESS target**

In `deploy_handler.py`, modify `_execute_process_deploy()`:
- Keep zip extraction to staging
- Remove the call to `process_deployer.execute_deploy()` (which does kill → copy → start)
- Instead, just write the flag and let RecSvrManager handle the rest
- Send deploy result back to server

```python
async def _execute_process_deploy(self):
    """Extract to staging and write flag for RecSvrManager."""
    try:
        # Extract zip to staging (existing logic)
        await self.process_deployer.extract_staged_files(self._received_file_path)

        # Write update-ready.flag (RecSvrManager polls for this)
        self.process_deployer.write_update_flag()

        # Report success — RecSvrManager handles the actual update
        await self._send_deploy_result(success=True, message="Update staged for RecSvrManager")
    except Exception as e:
        logger.error(f"Process deploy staging failed: {e}")
        await self._send_deploy_result(success=False, message=str(e))
```

- [ ] **Step 3: Run Agent tests**

```bash
cd D:/Work/AI_Projects/AI-LogOps
python -m pytest tests/ -v --tb=short -q 2>&1 | head -50
```

- [ ] **Step 4: Commit**

```bash
cd D:/Work/AI_Projects/AI-LogOps
git add agent/updater/process_deploy.py agent/core/deploy_handler.py
git commit -m "feat: update deploy flow to use flag-based trigger for RecSvrManager"
```

---

### Task 11: Update CtrlHandler for RecSvrManager forwarding

**Files:**
- Modify: `D:\Work\AI_Projects\AI-LogOps\agent\core\ctrl_handler.py`

The spec requires: "CMD_CTRL (RESTART/STOP/START) → RecSvrManager로 전달. RecSvrManager가 내부적으로 AirREC 제어."

Since Agent now manages RecSvrManager (not AirREC directly), the existing CtrlHandler already works correctly for RESTART/STOP/START of the PROCESS target — it will restart/stop/start RecSvrManager.exe. RecSvrManager in turn manages AirREC lifecycle.

However, we need to verify the behavior is correct:

- [ ] **Step 1: Verify CtrlHandler behavior with RecSvrManager**

Review `ctrl_handler.py` `_resolve_mgr()` method. When `target == DeployTarget.PROCESS`, it returns `self.process_mgr` which will now point to RecSvrManager.exe. Confirm:
- RESTART: kills RecSvrManager → Agent restarts it → RecSvrManager starts AirREC. Correct.
- STOP: kills RecSvrManager → AirREC orphaned (RecSvrManager should handle child process cleanup on exit). Verify RecSvrManager cleanup logic in Task 9.
- START: starts RecSvrManager → RecSvrManager starts AirREC. Correct.

- [ ] **Step 2: Add cleanup logic to RecSvrManager for STOP scenario**

In RecSvrManager's `main.cpp`, add a console control handler to gracefully shut down AirREC when RecSvrManager receives CTRL_C or is terminated:

```cpp
BOOL WINAPI ConsoleCtrlHandler(DWORD dwCtrlType)
{
    if (dwCtrlType == CTRL_C_EVENT || dwCtrlType == CTRL_CLOSE_EVENT ||
        dwCtrlType == CTRL_SHUTDOWN_EVENT) {
        LOG_I("Received shutdown signal, stopping AirREC...");
        // Send SHUTDOWN to all connected AirREC instances
        g_pipeServer.SendToAll(MSG_SHUTDOWN, NULL, 0);
        Sleep(3000);  // Wait for graceful exit
        // Force kill if still running
        if (g_mainProc.IsRunning()) g_mainProc.ForceKill();
        return TRUE;
    }
    return FALSE;
}

// In wmain(), after config load:
SetConsoleCtrlHandler(ConsoleCtrlHandler, TRUE);
```

- [ ] **Step 3: Commit**

```bash
cd D:/Work/AI_Projects/AI-LogOps
git add agent/core/ctrl_handler.py
cd D:/Work/AirTech/PBXServer/RecSvrManager
git add main.cpp
git commit -m "feat: add RecSvrManager cleanup handler for graceful STOP"
```

---

### Task 12: Update Agent config for RecSvrManager

**Files:**
- Modify: `D:\Work\AI_Projects\AI-LogOps\agent\config.yaml` (documentation only — actual deployment config is on agent machine)

- [ ] **Step 1: Document config change needed on deployment target**

The agent's `config.yaml` on the target machine (`PC-DAERIGO`) needs:

```yaml
target_process:
  name: "RecSvrManager.exe"            # Changed from REC_SVR.exe
  path: "D:/AirSoft/Server2/RecSvrManager.exe"  # Changed path
  backup_dir: "./backups"
```

This is a deployment-time configuration change, not a code change. Document in the spec or CLAUDE.md.

- [ ] **Step 2: Commit documentation update if needed**

---

## Chunk 4: Manual Integration Testing

### Task 13: End-to-end test on development machine

- [ ] **Step 1: Set up directory structure**

```powershell
# Create version directory structure
$base = "D:\AirSoft\Server2"
mkdir "$base\staging" -Force
mkdir "$base\versions\v2026.3.13.1" -Force
mkdir "$base\logs" -Force

# Copy current AirREC to first version
Copy-Item "$base\REC_SVR.exe" "$base\versions\v2026.3.13.1\" -Force
Copy-Item "$base\REC_SVR2.exe" "$base\versions\v2026.3.13.1\" -Force

# Create junction: current → versions\v2026.3.13.1
cmd /c "mklink /J `"$base\current`" `"$base\versions\v2026.3.13.1`""
```

- [ ] **Step 2: Test RecSvrManager standalone start**

```powershell
# Place RecSvrManager.exe and .ini in base dir
Copy-Item "D:\Work\AirTech\PBXServer\RecSvrManager\Release\RecSvrManager.exe" "$base\"
Copy-Item "D:\Work\AirTech\PBXServer\RecSvrManager\RecSvrManager.ini" "$base\"

# Run RecSvrManager
& "$base\RecSvrManager.exe"
```

Verify:
- RecSvrManager starts
- AirREC starts (check Task Manager for REC_SVR.exe)
- Pipe connection established (check RecSvrManager logs)
- HEARTBEAT messages received (check logs)

- [ ] **Step 3: Test update trigger**

```powershell
# Simulate an update: copy new version to staging, write flag
mkdir "$base\staging" -Force
Copy-Item "$base\versions\v2026.3.13.1\*" "$base\staging\" -Force
"version=v2026.3.14.1" | Out-File "$base\staging\update-ready.flag" -Encoding utf8
```

Verify:
- RecSvrManager detects flag file
- New AirREC process starts from versions\v2026.3.14.1
- DRAIN_REQUEST sent to old process
- Old process drains and exits (DRAIN_COMPLETE)
- RecSvrManager enters AWAITING_CONFIRMATION state

- [ ] **Step 4: Test rollback (if needed)**

Stop new process manually and verify old version can be restored.

- [ ] **Step 5: Document test results**
