# AI-LogOps Auto-Deployer Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a multi-agent remote log analysis & auto-deploy system with dual LLM support, TCP binary protocol, Telegram control, and web dashboard.

**Architecture:** Agent-Server model over custom TCP binary protocol. Agents run as Windows services on factory PCs, monitoring target app logs. Server orchestrates AI analysis (OpenAI/Claude), deployment, and provides Telegram + web dashboard interfaces. Shared package defines protocol and models used by both sides.

**Tech Stack:** Python 3.10+, asyncio, pywin32, python-telegram-bot, FastAPI, HTMX, Tailwind CSS, Jinja2, OpenAI API, Anthropic API, watchdog, psutil, pytest

**Reference:** `docs/AI-LogOps_Technical_Spec_v2.1.md` - 전체 기술 스펙 (반드시 참조)

---

## Phase 1: Skeleton 통신 구축 (Week 1)

> 목표: TCP 서버/클라이언트 통신, 텔레그램 봇 연동, Agent Standby 모드 완성

---

### Task 1: 프로젝트 스캐폴딩 + 의존성

**Files:**
- Create: `requirements.txt`
- Create: `shared/__init__.py`
- Create: `agent/__init__.py`
- Create: `agent/core/__init__.py`
- Create: `agent/telegram/__init__.py`
- Create: `agent/updater/__init__.py`
- Create: `agent/service/__init__.py`
- Create: `server/__init__.py`
- Create: `server/core/__init__.py`
- Create: `server/ai/__init__.py`
- Create: `server/ai/prompts/` (directory)
- Create: `server/telegram/__init__.py`
- Create: `server/dashboard/__init__.py`
- Create: `server/storage/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/fixtures/ai_responses/` (directory)
- Create: `agent/config.yaml`
- Create: `server/config.yaml`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `pytest.ini`

**Step 1: 디렉토리 구조 생성**

프로젝트 루트에 전체 패키지 구조를 생성한다. 모든 `__init__.py`는 빈 파일.

**Step 2: requirements.txt 작성**

```
# Core
asyncio-mqtt>=0.16.0
pyyaml>=6.0
python-dotenv>=1.0

# Agent
watchdog>=3.0
psutil>=5.9
pywin32>=306; sys_platform == "win32"

# Server - AI
openai>=1.12
anthropic>=0.18
jinja2>=3.1

# Server - Telegram
python-telegram-bot>=21.0

# Server - Dashboard
fastapi>=0.109
uvicorn>=0.27
python-jose[cryptography]>=3.3
python-multipart>=0.0.6

# Shared
pydantic>=2.5

# Dev
pytest>=8.0
pytest-asyncio>=0.23
httpx>=0.26
```

**Step 3: 설정 파일 작성**

`agent/config.yaml` - 스펙 섹션 2.3의 내용 그대로 복사.
`server/config.yaml` - 스펙 섹션 3.4의 내용 그대로 복사.

`.env.example`:
```
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ADMIN_CHAT_ID=...
DASHBOARD_SECRET_KEY=...
```

**Step 4: pytest.ini 작성**

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
python_files = test_*.py
python_functions = test_*
```

**Step 5: .gitignore 작성**

```
__pycache__/
*.pyc
.env
*.egg-info/
dist/
build/
.pytest_cache/
storage/
*.log
```

**Step 6: 의존성 설치 + git init**

```bash
pip install -r requirements.txt
git init
git add -A
git commit -m "chore: project scaffolding with package structure"
```

Run: `python -c "import asyncio, yaml, pydantic; print('OK')"` → Expected: `OK`

---

### Task 2: shared/protocol.py - 패킷 타입 및 헤더 정의

**Files:**
- Create: `shared/protocol.py`
- Create: `tests/test_protocol.py`

**Step 1: 패킷 타입 테스트 작성**

```python
# tests/test_protocol.py
"""TCP 바이너리 프로토콜 패킷 직렬화/역직렬화 테스트."""
import pytest
from shared.protocol import PacketType, PacketHeader, HEADER_SIZE


class TestPacketType:
    def test_auth_value(self):
        assert PacketType.AUTH == 0x01

    def test_auth_ack_value(self):
        assert PacketType.AUTH_ACK == 0x02

    def test_heartbeat_value(self):
        assert PacketType.HEARTBEAT == 0xFE

    def test_disconnect_value(self):
        assert PacketType.DISCONNECT == 0xFF

    def test_all_types_unique(self):
        values = [member.value for member in PacketType]
        assert len(values) == len(set(values))


class TestPacketHeader:
    def test_header_size_is_5_bytes(self):
        assert HEADER_SIZE == 5

    def test_pack_header(self):
        data = PacketHeader.pack(PacketType.AUTH, payload_length=104)
        assert len(data) == 5
        assert data[0] == 0x01
        # payload length in big-endian
        assert int.from_bytes(data[1:5], "big") == 104

    def test_unpack_header(self):
        data = PacketHeader.pack(PacketType.HEARTBEAT, payload_length=10)
        ptype, length = PacketHeader.unpack(data)
        assert ptype == PacketType.HEARTBEAT
        assert length == 10

    def test_pack_unpack_roundtrip(self):
        for pt in PacketType:
            for length in [0, 1, 4096, 10 * 1024 * 1024]:
                data = PacketHeader.pack(pt, length)
                unpacked_type, unpacked_len = PacketHeader.unpack(data)
                assert unpacked_type == pt
                assert unpacked_len == length

    def test_max_payload_validation(self):
        with pytest.raises(ValueError, match="payload"):
            PacketHeader.pack(PacketType.AUTH, 10 * 1024 * 1024 + 1)
```

**Step 2: 테스트 실행 → 실패 확인**

```bash
pytest tests/test_protocol.py -v
```
Expected: FAIL - `ModuleNotFoundError: No module named 'shared.protocol'`

**Step 3: 구현**

```python
# shared/protocol.py
"""TCP 바이너리 프로토콜 정의.

패킷 구조: [Header: 1B] [Payload Length: 4B big-endian] [Payload: NB]
최대 페이로드: 10MB. 파일 전송은 4KB 청크 단위.
"""
from __future__ import annotations

import struct
from enum import IntEnum

HEADER_SIZE = 5  # 1 (type) + 4 (length)
MAX_PAYLOAD_SIZE = 10 * 1024 * 1024  # 10MB
CHUNK_SIZE = 4096  # 4KB


class PacketType(IntEnum):
    """패킷 타입 헤더 값. 스펙 섹션 5.2 참조."""
    # Authentication
    AUTH = 0x01
    AUTH_ACK = 0x02

    # Log transfer
    LOG_HIST = 0x03
    LOG_REAL = 0x04

    # Deployment
    CMD_DEPLOY = 0x10
    FILE_CHUNK = 0x11
    FILE_ACK = 0x12

    # Control
    CMD_CTRL = 0x13
    CMD_CTRL_ACK = 0x14

    # Agent update
    AGENT_UPDATE = 0x20

    # Heartbeat & disconnect
    HEARTBEAT = 0xFE
    DISCONNECT = 0xFF


class AuthStatus(IntEnum):
    """AUTH_ACK 응답 상태."""
    SUCCESS = 0x00
    FAILED = 0x01
    VERSION_MISMATCH = 0x02


class CtrlAction(IntEnum):
    """CMD_CTRL 액션 코드."""
    STOP = 0x00
    START = 0x01
    RESTART = 0x02


class CtrlAckStatus(IntEnum):
    """CMD_CTRL_ACK 상태 코드."""
    SUCCESS = 0x00
    FAILED = 0x01
    DEPLOY_VERIFIED = 0x10
    DEPLOY_ROLLBACK = 0x11


class DisconnectReason(IntEnum):
    """DISCONNECT 사유 코드."""
    NORMAL = 0x00
    ERROR = 0x01
    HEARTBEAT_TIMEOUT = 0x02
    SERVER_SHUTDOWN = 0x03


class PacketHeader:
    """패킷 헤더 직렬화/역직렬화."""

    _STRUCT = struct.Struct("!BI")  # 1B unsigned char + 4B unsigned int (big-endian)

    @classmethod
    def pack(cls, packet_type: PacketType, payload_length: int) -> bytes:
        """헤더를 5바이트로 직렬화."""
        if payload_length > MAX_PAYLOAD_SIZE:
            raise ValueError(
                f"payload length {payload_length} exceeds max {MAX_PAYLOAD_SIZE}"
            )
        if payload_length < 0:
            raise ValueError(f"payload length must be >= 0, got {payload_length}")
        return cls._STRUCT.pack(int(packet_type), payload_length)

    @classmethod
    def unpack(cls, data: bytes) -> tuple[PacketType, int]:
        """5바이트 헤더를 (PacketType, payload_length)로 역직렬화."""
        if len(data) < HEADER_SIZE:
            raise ValueError(f"header must be {HEADER_SIZE} bytes, got {len(data)}")
        type_val, length = cls._STRUCT.unpack(data[:HEADER_SIZE])
        return PacketType(type_val), length
```

**Step 4: 테스트 실행 → 통과 확인**

```bash
pytest tests/test_protocol.py -v
```
Expected: ALL PASS

**Step 5: 커밋**

```bash
git add shared/protocol.py tests/test_protocol.py
git commit -m "feat: define TCP binary protocol packet types and header serialization"
```

---

### Task 3: shared/protocol.py - 페이로드 직렬화/역직렬화

**Files:**
- Modify: `shared/protocol.py`
- Modify: `tests/test_protocol.py`

**Step 1: AUTH/AUTH_ACK 페이로드 테스트 추가**

```python
# tests/test_protocol.py (추가)
from shared.protocol import (
    AuthPayload, AuthAckPayload, HeartbeatPayload,
    DisconnectPayload, Packet, AuthStatus, DisconnectReason,
)


class TestAuthPayload:
    def test_pack_unpack(self):
        payload = AuthPayload(
            agent_id="PC-FACTORY-01",
            version="1.0.0",
            token="a" * 64,
        )
        data = payload.pack()
        restored = AuthPayload.unpack(data)
        assert restored.agent_id == "PC-FACTORY-01"
        assert restored.version == "1.0.0"
        assert restored.token == "a" * 64

    def test_agent_id_padding(self):
        """agent_id는 32바이트로 패딩."""
        payload = AuthPayload(agent_id="A", version="1.0.0", token="b" * 64)
        data = payload.pack()
        assert len(data) == 32 + 8 + 64  # 104 bytes


class TestAuthAckPayload:
    def test_success(self):
        payload = AuthAckPayload(
            status=AuthStatus.SUCCESS,
            session_id="sess-001",
        )
        data = payload.pack()
        restored = AuthAckPayload.unpack(data)
        assert restored.status == AuthStatus.SUCCESS
        assert restored.session_id == "sess-001"

    def test_version_mismatch(self):
        payload = AuthAckPayload(
            status=AuthStatus.VERSION_MISMATCH,
            session_id="",
        )
        data = payload.pack()
        restored = AuthAckPayload.unpack(data)
        assert restored.status == AuthStatus.VERSION_MISMATCH


class TestHeartbeatPayload:
    def test_pack_unpack(self):
        import time
        ts = int(time.time())
        payload = HeartbeatPayload(timestamp=ts, cpu_percent=45, mem_percent=62)
        data = payload.pack()
        restored = HeartbeatPayload.unpack(data)
        assert restored.timestamp == ts
        assert restored.cpu_percent == 45
        assert restored.mem_percent == 62


class TestPacket:
    def test_full_packet_roundtrip(self):
        """완전한 패킷(헤더+페이로드) 직렬화/역직렬화."""
        auth = AuthPayload(agent_id="PC-01", version="1.0.0", token="x" * 64)
        raw = Packet.build(PacketType.AUTH, auth.pack())
        ptype, payload_data = Packet.parse_header(raw[:HEADER_SIZE])
        assert ptype == PacketType.AUTH

    def test_disconnect_packet(self):
        payload = DisconnectPayload(reason=DisconnectReason.HEARTBEAT_TIMEOUT)
        raw = Packet.build(PacketType.DISCONNECT, payload.pack())
        assert raw[0] == PacketType.DISCONNECT
```

**Step 2: 테스트 실행 → 실패 확인**

```bash
pytest tests/test_protocol.py -v -k "Auth or Heartbeat or Packet"
```
Expected: FAIL - `ImportError`

**Step 3: 페이로드 클래스 구현**

`shared/protocol.py`에 다음 클래스 추가:

```python
# -- shared/protocol.py 에 추가 --
from dataclasses import dataclass


@dataclass
class AuthPayload:
    """AUTH 패킷 페이로드: [AgentID(32B)] [Version(8B)] [Token(64B)] = 104B."""
    agent_id: str   # max 32 chars
    version: str    # max 8 chars
    token: str      # exactly 64 chars

    def pack(self) -> bytes:
        return (
            self.agent_id.encode("utf-8").ljust(32, b"\x00")
            + self.version.encode("utf-8").ljust(8, b"\x00")
            + self.token.encode("utf-8").ljust(64, b"\x00")
        )

    @classmethod
    def unpack(cls, data: bytes) -> "AuthPayload":
        agent_id = data[0:32].rstrip(b"\x00").decode("utf-8")
        version = data[32:40].rstrip(b"\x00").decode("utf-8")
        token = data[40:104].rstrip(b"\x00").decode("utf-8")
        return cls(agent_id=agent_id, version=version, token=token)


@dataclass
class AuthAckPayload:
    """AUTH_ACK 페이로드: [Status(1B)] [SessionID(16B)] = 17B."""
    status: AuthStatus
    session_id: str  # max 16 chars

    def pack(self) -> bytes:
        return (
            struct.pack("!B", int(self.status))
            + self.session_id.encode("utf-8").ljust(16, b"\x00")
        )

    @classmethod
    def unpack(cls, data: bytes) -> "AuthAckPayload":
        status = AuthStatus(data[0])
        session_id = data[1:17].rstrip(b"\x00").decode("utf-8")
        return cls(status=status, session_id=session_id)


@dataclass
class HeartbeatPayload:
    """HEARTBEAT 페이로드: [Timestamp(8B)] [CPU%(1B)] [Mem%(1B)] = 10B."""
    timestamp: int
    cpu_percent: int   # 0-100
    mem_percent: int   # 0-100

    def pack(self) -> bytes:
        return struct.pack("!QBB", self.timestamp, self.cpu_percent, self.mem_percent)

    @classmethod
    def unpack(cls, data: bytes) -> "HeartbeatPayload":
        ts, cpu, mem = struct.unpack("!QBB", data[:10])
        return cls(timestamp=ts, cpu_percent=cpu, mem_percent=mem)


@dataclass
class DisconnectPayload:
    """DISCONNECT 페이로드: [Reason(1B)] = 1B."""
    reason: DisconnectReason

    def pack(self) -> bytes:
        return struct.pack("!B", int(self.reason))

    @classmethod
    def unpack(cls, data: bytes) -> "DisconnectPayload":
        return cls(reason=DisconnectReason(data[0]))


class Packet:
    """완전한 패킷 조립/파싱 유틸리티."""

    @staticmethod
    def build(packet_type: PacketType, payload: bytes) -> bytes:
        """헤더 + 페이로드 결합."""
        header = PacketHeader.pack(packet_type, len(payload))
        return header + payload

    @staticmethod
    def parse_header(header_bytes: bytes) -> tuple[PacketType, int]:
        """헤더 파싱. 반환: (packet_type, payload_length)."""
        return PacketHeader.unpack(header_bytes)
```

**Step 4: 테스트 실행 → 통과**

```bash
pytest tests/test_protocol.py -v
```
Expected: ALL PASS

**Step 5: 커밋**

```bash
git add shared/protocol.py tests/test_protocol.py
git commit -m "feat: add payload serialization for AUTH, AUTH_ACK, HEARTBEAT, DISCONNECT"
```

---

### Task 4: shared/models.py - 데이터 모델

**Files:**
- Create: `shared/models.py`
- Create: `tests/test_models.py`

**Step 1: 모델 테스트 작성**

```python
# tests/test_models.py
"""데이터 모델 테스트."""
import pytest
from shared.models import AgentState, AgentSession, AgentInfo


class TestAgentState:
    def test_initial_state_is_init(self):
        assert AgentState.INIT.value == "INIT"

    def test_all_states_exist(self):
        states = {s.value for s in AgentState}
        expected = {"INIT", "STANDBY", "CONNECTING", "CONNECTED",
                    "DEPLOYING", "DEPLOY_VERIFY", "UPDATING", "ERROR"}
        assert states == expected


class TestAgentInfo:
    def test_creation(self):
        info = AgentInfo(
            agent_id="PC-01",
            version="1.0.0",
        )
        assert info.agent_id == "PC-01"
        assert info.version == "1.0.0"

    def test_defaults(self):
        info = AgentInfo(agent_id="PC-01", version="1.0.0")
        assert info.state == AgentState.INIT


class TestAgentSession:
    def test_creation_with_info(self):
        info = AgentInfo(agent_id="PC-01", version="1.0.0")
        session = AgentSession(
            agent_info=info,
            session_id="sess-001",
        )
        assert session.agent_info.agent_id == "PC-01"
        assert session.session_id == "sess-001"
        assert session.log_buffer == []
        assert session.deploy_history == []
```

**Step 2: 테스트 실행 → 실패 확인**

```bash
pytest tests/test_models.py -v
```

**Step 3: 모델 구현**

```python
# shared/models.py
"""AI-LogOps 공유 데이터 모델."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AgentState(Enum):
    """에이전트 상태 머신. 스펙 섹션 2.2 참조."""
    INIT = "INIT"
    STANDBY = "STANDBY"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DEPLOYING = "DEPLOYING"
    DEPLOY_VERIFY = "DEPLOY_VERIFY"
    UPDATING = "UPDATING"
    ERROR = "ERROR"


@dataclass
class AgentInfo:
    """에이전트 기본 정보."""
    agent_id: str
    version: str
    state: AgentState = AgentState.INIT


@dataclass
class AgentSession:
    """서버 측 에이전트 세션. 스펙 섹션 3.2 참조."""
    agent_info: AgentInfo
    session_id: str
    writer: Any = None           # asyncio.StreamWriter (런타임)
    last_heartbeat: float = field(default_factory=time.time)
    log_buffer: list[str] = field(default_factory=list)
    deploy_history: list[dict] = field(default_factory=list)


@dataclass
class DeployRecord:
    """배포 이력 레코드."""
    timestamp: float
    agent_id: str
    filename: str
    sha256: str
    success: bool
    rollback: bool = False
    detail: str = ""
```

**Step 4: 테스트 통과 확인**

```bash
pytest tests/test_models.py -v
```

**Step 5: 커밋**

```bash
git add shared/models.py tests/test_models.py
git commit -m "feat: add shared data models (AgentState, AgentSession, DeployRecord)"
```

---

### Task 5: shared/utils.py - 유틸리티 (로깅, 체크섬, 설정, 감사 로그)

**Files:**
- Create: `shared/utils.py`
- Create: `tests/test_utils.py`

**Step 1: 유틸리티 테스트 작성**

```python
# tests/test_utils.py
"""유틸리티 함수 테스트."""
import json
import os
import pytest
from shared.utils import (
    compute_sha256,
    load_yaml_config,
    setup_logging,
    audit_log,
)


class TestSHA256:
    def test_known_hash(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello world")
        h = compute_sha256(str(f))
        assert h == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        h = compute_sha256(str(f))
        assert h == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


class TestConfig:
    def test_load_yaml(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("server:\n  port: 9500\n")
        data = load_yaml_config(str(cfg))
        assert data["server"]["port"] == 9500


class TestAuditLog:
    def test_append_entry(self, tmp_path):
        log_path = tmp_path / "audit.jsonl"
        audit_log("DEPLOY", "PC-01", "admin", "test detail", str(log_path))
        audit_log("ROLLBACK", "PC-01", "admin", "rolled back", str(log_path))

        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 2
        entry = json.loads(lines[0])
        assert entry["action"] == "DEPLOY"
        assert entry["agent_id"] == "PC-01"
        assert "ts" in entry
```

**Step 2: 테스트 실행 → 실패**

```bash
pytest tests/test_utils.py -v
```

**Step 3: 구현**

```python
# shared/utils.py
"""공용 유틸리티 함수."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml


def compute_sha256(filepath: str) -> str:
    """파일의 SHA-256 해시를 16진수 문자열로 반환."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def load_yaml_config(filepath: str) -> dict:
    """YAML 설정 파일 로딩."""
    with open(filepath, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(name: str, level: int = logging.INFO) -> logging.Logger:
    """표준 로거 설정."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "[%(asctime)s] %(name)s %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(level)
    return logger


def audit_log(
    action: str,
    agent_id: str,
    user: str,
    detail: str,
    log_path: str = "storage/audit.jsonl",
) -> None:
    """감사 로그를 jsonl 파일에 append."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "agent_id": agent_id,
        "user": user,
        "detail": detail,
    }
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
```

**Step 4: 테스트 통과**

```bash
pytest tests/test_utils.py -v
```

**Step 5: 커밋**

```bash
git add shared/utils.py tests/test_utils.py
git commit -m "feat: add utilities (SHA-256, YAML config, logging, audit log)"
```

---

### Task 6: server/core/tcp_server.py - asyncio TCP 서버

**Files:**
- Create: `server/core/tcp_server.py`
- Create: `server/core/session_mgr.py`
- Create: `tests/test_tcp_server.py`

**Step 1: TCP 서버 테스트 작성**

```python
# tests/test_tcp_server.py
"""TCP 서버 테스트. asyncio 루프백으로 검증."""
import asyncio
import pytest
from shared.protocol import (
    PacketType, PacketHeader, Packet, HEADER_SIZE,
    AuthPayload, AuthAckPayload, AuthStatus,
)
from server.core.tcp_server import TCPServer
from server.core.session_mgr import SessionManager


@pytest.fixture
async def server_and_port():
    """테스트용 TCP 서버를 임의 포트에서 시작."""
    session_mgr = SessionManager(max_agents=10)
    srv = TCPServer(host="127.0.0.1", port=0, session_mgr=session_mgr,
                    auth_token="testtoken" + "x" * 55)  # 64 chars
    port = await srv.start()
    yield srv, port
    await srv.stop()


class TestTCPServerBind:
    async def test_server_starts_on_port(self, server_and_port):
        srv, port = server_and_port
        assert port > 0
        assert srv.is_running


class TestTCPServerAuth:
    async def test_valid_auth_returns_success(self, server_and_port):
        srv, port = server_and_port
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            # AUTH 패킷 전송
            auth = AuthPayload(
                agent_id="PC-TEST-01",
                version="1.0.0",
                token="testtoken" + "x" * 55,
            )
            writer.write(Packet.build(PacketType.AUTH, auth.pack()))
            await writer.drain()

            # AUTH_ACK 수신
            header = await reader.readexactly(HEADER_SIZE)
            ptype, length = PacketHeader.unpack(header)
            payload = await reader.readexactly(length)

            assert ptype == PacketType.AUTH_ACK
            ack = AuthAckPayload.unpack(payload)
            assert ack.status == AuthStatus.SUCCESS
            assert len(ack.session_id) > 0
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_invalid_token_returns_failed(self, server_and_port):
        srv, port = server_and_port
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            auth = AuthPayload(
                agent_id="PC-TEST-01",
                version="1.0.0",
                token="wrong_token" + "x" * 53,
            )
            writer.write(Packet.build(PacketType.AUTH, auth.pack()))
            await writer.drain()

            header = await reader.readexactly(HEADER_SIZE)
            ptype, length = PacketHeader.unpack(header)
            payload = await reader.readexactly(length)

            ack = AuthAckPayload.unpack(payload)
            assert ack.status == AuthStatus.FAILED
        finally:
            writer.close()
            await writer.wait_closed()
```

**Step 2: 테스트 실행 → 실패**

```bash
pytest tests/test_tcp_server.py -v
```

**Step 3: SessionManager 구현**

```python
# server/core/session_mgr.py
"""에이전트 세션 관리자. 스펙 섹션 3.2 참조."""
from __future__ import annotations

import asyncio
import uuid
import time
from shared.models import AgentInfo, AgentSession, AgentState
from shared.utils import setup_logging

logger = setup_logging("session_mgr")


class SessionManager:
    """최대 max_agents 동시 세션 관리."""

    def __init__(self, max_agents: int = 10):
        self.max_agents = max_agents
        self._sessions: dict[str, AgentSession] = {}  # agent_id -> session

    @property
    def sessions(self) -> dict[str, AgentSession]:
        return self._sessions

    def create_session(
        self, agent_id: str, version: str, writer: asyncio.StreamWriter
    ) -> AgentSession | None:
        """새 세션 생성. 이미 max이면 None 반환."""
        if len(self._sessions) >= self.max_agents and agent_id not in self._sessions:
            logger.warning("Max agents reached (%d), rejecting %s", self.max_agents, agent_id)
            return None

        session_id = uuid.uuid4().hex[:16]
        info = AgentInfo(agent_id=agent_id, version=version, state=AgentState.CONNECTED)
        session = AgentSession(agent_info=info, session_id=session_id, writer=writer)
        self._sessions[agent_id] = session
        logger.info("Session created: %s (session=%s)", agent_id, session_id)
        return session

    def remove_session(self, agent_id: str) -> None:
        """세션 제거."""
        if agent_id in self._sessions:
            del self._sessions[agent_id]
            logger.info("Session removed: %s", agent_id)

    def get_session(self, agent_id: str) -> AgentSession | None:
        return self._sessions.get(agent_id)

    def update_heartbeat(self, agent_id: str) -> None:
        session = self._sessions.get(agent_id)
        if session:
            session.last_heartbeat = time.time()

    def get_all_sessions(self) -> list[AgentSession]:
        return list(self._sessions.values())
```

**Step 4: TCPServer 구현**

```python
# server/core/tcp_server.py
"""asyncio 기반 비동기 TCP 서버. 스펙 섹션 3.1, 5.3 참조."""
from __future__ import annotations

import asyncio
from shared.protocol import (
    PacketType, PacketHeader, Packet, HEADER_SIZE,
    AuthPayload, AuthAckPayload, AuthStatus,
    HeartbeatPayload,
)
from shared.utils import setup_logging
from server.core.session_mgr import SessionManager

logger = setup_logging("tcp_server")


class TCPServer:
    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 9500,
        session_mgr: SessionManager | None = None,
        auth_token: str = "",
    ):
        self.host = host
        self.port = port
        self.session_mgr = session_mgr or SessionManager()
        self.auth_token = auth_token
        self._server: asyncio.Server | None = None
        self._is_running = False

    @property
    def is_running(self) -> bool:
        return self._is_running

    async def start(self) -> int:
        """서버 시작. 실제 바인딩된 포트 반환."""
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        # 실제 바인딩 포트 (port=0이면 OS 할당)
        addr = self._server.sockets[0].getsockname()
        self.port = addr[1]
        self._is_running = True
        logger.info("TCP Server listening on %s:%d", self.host, self.port)
        return self.port

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._is_running = False
            logger.info("TCP Server stopped")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """클라이언트 연결 핸들러."""
        addr = writer.get_extra_info("peername")
        logger.info("New connection from %s", addr)
        agent_id = None

        try:
            # Step 1: AUTH 대기
            agent_id = await self._handle_auth(reader, writer)
            if not agent_id:
                return

            # Step 2: 메인 루프 - 패킷 수신
            await self._recv_loop(reader, writer, agent_id)

        except (asyncio.IncompleteReadError, ConnectionResetError):
            logger.info("Connection lost: %s", agent_id or addr)
        except Exception:
            logger.exception("Error handling client %s", agent_id or addr)
        finally:
            if agent_id:
                self.session_mgr.remove_session(agent_id)
            writer.close()
            await writer.wait_closed()

    async def _handle_auth(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> str | None:
        """AUTH 패킷 처리 → AUTH_ACK 응답. 성공 시 agent_id 반환."""
        header_data = await reader.readexactly(HEADER_SIZE)
        ptype, length = PacketHeader.unpack(header_data)

        if ptype != PacketType.AUTH:
            logger.warning("Expected AUTH, got %s", ptype)
            writer.close()
            return None

        payload_data = await reader.readexactly(length)
        auth = AuthPayload.unpack(payload_data)

        # 토큰 검증
        if auth.token != self.auth_token:
            ack = AuthAckPayload(status=AuthStatus.FAILED, session_id="")
            writer.write(Packet.build(PacketType.AUTH_ACK, ack.pack()))
            await writer.drain()
            logger.warning("Auth failed for %s (bad token)", auth.agent_id)
            return None

        # 세션 생성
        session = self.session_mgr.create_session(
            auth.agent_id, auth.version, writer
        )
        if not session:
            ack = AuthAckPayload(status=AuthStatus.FAILED, session_id="")
            writer.write(Packet.build(PacketType.AUTH_ACK, ack.pack()))
            await writer.drain()
            return None

        # 성공 응답
        ack = AuthAckPayload(status=AuthStatus.SUCCESS, session_id=session.session_id)
        writer.write(Packet.build(PacketType.AUTH_ACK, ack.pack()))
        await writer.drain()
        logger.info("Auth success: %s (session=%s)", auth.agent_id, session.session_id)
        return auth.agent_id

    async def _recv_loop(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
        agent_id: str,
    ) -> None:
        """인증 후 패킷 수신 루프."""
        while True:
            header_data = await reader.readexactly(HEADER_SIZE)
            ptype, length = PacketHeader.unpack(header_data)

            payload_data = b""
            if length > 0:
                payload_data = await reader.readexactly(length)

            if ptype == PacketType.HEARTBEAT:
                self.session_mgr.update_heartbeat(agent_id)
                # Echo heartbeat back
                writer.write(Packet.build(PacketType.HEARTBEAT, payload_data))
                await writer.drain()

            elif ptype == PacketType.DISCONNECT:
                logger.info("Agent %s disconnected gracefully", agent_id)
                break

            elif ptype == PacketType.LOG_HIST:
                await self._handle_log_hist(agent_id, payload_data)

            elif ptype == PacketType.LOG_REAL:
                await self._handle_log_real(agent_id, payload_data)

            elif ptype == PacketType.FILE_ACK:
                await self._handle_file_ack(agent_id, payload_data)

            elif ptype == PacketType.CMD_CTRL_ACK:
                await self._handle_ctrl_ack(agent_id, payload_data)

            else:
                logger.warning("Unknown packet type 0x%02x from %s", ptype, agent_id)

    # -- 핸들러 스텁 (Phase 2-4에서 구현) --
    async def _handle_log_hist(self, agent_id: str, data: bytes) -> None:
        logger.info("LOG_HIST from %s (%d bytes)", agent_id, len(data))

    async def _handle_log_real(self, agent_id: str, data: bytes) -> None:
        logger.debug("LOG_REAL from %s", agent_id)

    async def _handle_file_ack(self, agent_id: str, data: bytes) -> None:
        logger.debug("FILE_ACK from %s", agent_id)

    async def _handle_ctrl_ack(self, agent_id: str, data: bytes) -> None:
        logger.debug("CMD_CTRL_ACK from %s", agent_id)
```

**Step 5: 테스트 통과 + 커밋**

```bash
pytest tests/test_tcp_server.py -v
git add server/core/ tests/test_tcp_server.py
git commit -m "feat: implement async TCP server with AUTH/session management"
```

---

### Task 7: agent/core/tcp_client.py - 비동기 TCP 클라이언트

**Files:**
- Create: `agent/core/tcp_client.py`
- Create: `tests/test_tcp_client.py`

**Step 1: TCP 클라이언트 테스트 작성**

```python
# tests/test_tcp_client.py
"""TCP 클라이언트 테스트. 실제 서버와 루프백 통신."""
import asyncio
import pytest
from shared.protocol import PacketType, AuthStatus
from server.core.tcp_server import TCPServer
from server.core.session_mgr import SessionManager
from agent.core.tcp_client import TCPClient

TOKEN = "testtoken" + "x" * 55  # 64 chars


@pytest.fixture
async def running_server():
    """테스트용 서버."""
    mgr = SessionManager(max_agents=10)
    srv = TCPServer(host="127.0.0.1", port=0, session_mgr=mgr, auth_token=TOKEN)
    port = await srv.start()
    yield srv, port, mgr
    await srv.stop()


class TestTCPClientConnect:
    async def test_connect_and_auth(self, running_server):
        srv, port, mgr = running_server
        client = TCPClient(
            agent_id="PC-TEST-01", version="1.0.0", token=TOKEN,
            host="127.0.0.1", port=port,
        )
        result = await client.connect()
        assert result is True
        assert client.session_id != ""
        assert client.is_connected
        await client.disconnect()

    async def test_heartbeat_exchange(self, running_server):
        srv, port, mgr = running_server
        client = TCPClient(
            agent_id="PC-TEST-02", version="1.0.0", token=TOKEN,
            host="127.0.0.1", port=port,
        )
        await client.connect()
        # Heartbeat 전송
        await client.send_heartbeat()
        # 서버로부터 echo 수신 확인은 recv_loop에서 처리
        await asyncio.sleep(0.1)
        await client.disconnect()

    async def test_auth_failure(self, running_server):
        srv, port, mgr = running_server
        client = TCPClient(
            agent_id="PC-BAD", version="1.0.0", token="wrong" + "x" * 60,
            host="127.0.0.1", port=port,
        )
        result = await client.connect()
        assert result is False
        assert not client.is_connected
```

**Step 2: 테스트 실행 → 실패**

```bash
pytest tests/test_tcp_client.py -v
```

**Step 3: TCPClient 구현**

```python
# agent/core/tcp_client.py
"""비동기 TCP 클라이언트. 스펙 섹션 2.1 TCPClient 참조."""
from __future__ import annotations

import asyncio
import time
import psutil
from shared.protocol import (
    PacketType, PacketHeader, Packet, HEADER_SIZE,
    AuthPayload, AuthAckPayload, AuthStatus,
    HeartbeatPayload, DisconnectPayload, DisconnectReason,
)
from shared.utils import setup_logging

logger = setup_logging("tcp_client")


class TCPClient:
    def __init__(
        self,
        agent_id: str,
        version: str,
        token: str,
        host: str = "127.0.0.1",
        port: int = 9500,
        heartbeat_interval: int = 30,
        reconnect_attempts: int = 5,
        reconnect_delay: int = 10,
    ):
        self.agent_id = agent_id
        self.version = version
        self.token = token
        self.host = host
        self.port = port
        self.heartbeat_interval = heartbeat_interval
        self.reconnect_attempts = reconnect_attempts
        self.reconnect_delay = reconnect_delay

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._is_connected = False
        self.session_id = ""
        self._recv_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None

        # 콜백 (Phase 2-4에서 설정)
        self.on_cmd_deploy = None   # async callback(payload_data)
        self.on_cmd_ctrl = None     # async callback(payload_data)
        self.on_agent_update = None # async callback(payload_data)

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    async def connect(self) -> bool:
        """서버에 접속 + AUTH. 성공 시 True."""
        try:
            self._reader, self._writer = await asyncio.open_connection(
                self.host, self.port
            )
        except (ConnectionRefusedError, OSError) as e:
            logger.error("Connection failed: %s", e)
            return False

        # AUTH 전송
        auth = AuthPayload(
            agent_id=self.agent_id, version=self.version, token=self.token
        )
        self._writer.write(Packet.build(PacketType.AUTH, auth.pack()))
        await self._writer.drain()

        # AUTH_ACK 수신
        try:
            header = await self._reader.readexactly(HEADER_SIZE)
            ptype, length = PacketHeader.unpack(header)
            payload = await self._reader.readexactly(length)
        except asyncio.IncompleteReadError:
            logger.error("Connection closed during auth")
            return False

        ack = AuthAckPayload.unpack(payload)
        if ack.status != AuthStatus.SUCCESS:
            logger.error("Auth failed: status=%s", ack.status.name)
            self._writer.close()
            await self._writer.wait_closed()
            return False

        self.session_id = ack.session_id
        self._is_connected = True
        logger.info("Connected to %s:%d (session=%s)", self.host, self.port, self.session_id)

        # 수신 루프 시작
        self._recv_task = asyncio.create_task(self._recv_loop())
        return True

    async def disconnect(self) -> None:
        """정상 종료."""
        if self._writer and self._is_connected:
            payload = DisconnectPayload(reason=DisconnectReason.NORMAL)
            self._writer.write(Packet.build(PacketType.DISCONNECT, payload.pack()))
            await self._writer.drain()

        self._is_connected = False
        if self._recv_task:
            self._recv_task.cancel()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()
        logger.info("Disconnected")

    async def send_heartbeat(self) -> None:
        """Heartbeat 패킷 전송."""
        if not self._is_connected or not self._writer:
            return
        hb = HeartbeatPayload(
            timestamp=int(time.time()),
            cpu_percent=int(psutil.cpu_percent()),
            mem_percent=int(psutil.virtual_memory().percent),
        )
        self._writer.write(Packet.build(PacketType.HEARTBEAT, hb.pack()))
        await self._writer.drain()

    async def send_packet(self, packet_type: PacketType, payload: bytes) -> None:
        """범용 패킷 전송."""
        if not self._is_connected or not self._writer:
            raise ConnectionError("Not connected")
        self._writer.write(Packet.build(packet_type, payload))
        await self._writer.drain()

    async def _recv_loop(self) -> None:
        """서버로부터 패킷 수신 루프."""
        try:
            while self._is_connected and self._reader:
                header = await self._reader.readexactly(HEADER_SIZE)
                ptype, length = PacketHeader.unpack(header)
                payload = b""
                if length > 0:
                    payload = await self._reader.readexactly(length)

                if ptype == PacketType.HEARTBEAT:
                    pass  # Echo 응답, 무시

                elif ptype == PacketType.CMD_DEPLOY:
                    if self.on_cmd_deploy:
                        await self.on_cmd_deploy(payload)

                elif ptype == PacketType.FILE_CHUNK:
                    if self.on_cmd_deploy:
                        await self.on_cmd_deploy(payload)

                elif ptype == PacketType.CMD_CTRL:
                    if self.on_cmd_ctrl:
                        await self.on_cmd_ctrl(payload)

                elif ptype == PacketType.AGENT_UPDATE:
                    if self.on_agent_update:
                        await self.on_agent_update(payload)

                elif ptype == PacketType.DISCONNECT:
                    logger.info("Server requested disconnect")
                    break

        except asyncio.IncompleteReadError:
            logger.info("Server connection lost")
        except asyncio.CancelledError:
            pass
        finally:
            self._is_connected = False
```

**Step 4: 테스트 통과 + 커밋**

```bash
pytest tests/test_tcp_client.py -v
git add agent/core/tcp_client.py tests/test_tcp_client.py
git commit -m "feat: implement async TCP client with AUTH, heartbeat, recv loop"
```

---

### Task 8: server/telegram/handler.py - 텔레그램 명령 핸들러 + Rate Limiting

**Files:**
- Create: `server/telegram/handler.py`
- Create: `server/telegram/rate_limiter.py`
- Create: `tests/test_telegram_handler.py`

**Step 1: Rate Limiter 테스트**

```python
# tests/test_telegram_handler.py
"""텔레그램 명령 핸들러 + Rate Limiter 테스트."""
import time
import pytest
from server.telegram.rate_limiter import RateLimiter


class TestRateLimiter:
    def test_allows_under_limit(self):
        rl = RateLimiter(window=10, max_requests=5)
        for _ in range(5):
            assert rl.check(chat_id=123) is True

    def test_blocks_over_limit(self):
        rl = RateLimiter(window=10, max_requests=3)
        for _ in range(3):
            rl.check(chat_id=123)
        assert rl.check(chat_id=123) is False

    def test_different_chats_independent(self):
        rl = RateLimiter(window=10, max_requests=2)
        rl.check(chat_id=1)
        rl.check(chat_id=1)
        assert rl.check(chat_id=1) is False
        assert rl.check(chat_id=2) is True  # 다른 채팅은 독립
```

**Step 2: 테스트 실행 → 실패**

```bash
pytest tests/test_telegram_handler.py -v
```

**Step 3: Rate Limiter 구현**

```python
# server/telegram/rate_limiter.py
"""텔레그램 명령 Rate Limiter. 스펙 섹션 8.1 참조."""
from __future__ import annotations

import time
from collections import defaultdict


class RateLimiter:
    """슬라이딩 윈도우 기반 요청 제한."""

    def __init__(self, window: int = 10, max_requests: int = 5):
        self.window = window
        self.max_requests = max_requests
        self._requests: dict[int, list[float]] = defaultdict(list)

    def check(self, chat_id: int) -> bool:
        """요청 허용 여부. True=허용, False=차단."""
        now = time.time()
        # 윈도우 밖의 오래된 요청 제거
        self._requests[chat_id] = [
            t for t in self._requests[chat_id] if now - t < self.window
        ]
        if len(self._requests[chat_id]) >= self.max_requests:
            return False
        self._requests[chat_id].append(now)
        return True
```

**Step 4: TelegramHandler 구현 (명령 파서 구조)**

```python
# server/telegram/handler.py
"""텔레그램 명령 핸들러. 스펙 섹션 6 참조.

실제 python-telegram-bot 연동은 이 구조 위에 구축.
이 모듈은 명령 파싱 + 라우팅 로직을 담당.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Awaitable
from shared.utils import setup_logging, audit_log
from server.telegram.rate_limiter import RateLimiter

logger = setup_logging("telegram_handler")


@dataclass
class ParsedCommand:
    """파싱된 텔레그램 명령."""
    command: str          # e.g. "status", "connect", "analyze"
    args: list[str]       # e.g. ["PC-01", "1.2.3.4", "9500"]
    chat_id: int
    raw_text: str


class TelegramHandler:
    """텔레그램 명령 라우터."""

    def __init__(self, admin_chat_ids: list[int]):
        self.admin_chat_ids = set(admin_chat_ids)
        self.rate_limiter = RateLimiter(window=10, max_requests=5)
        self._handlers: dict[str, Callable] = {}

    def register(self, command: str, handler: Callable[..., Awaitable]) -> None:
        """명령 핸들러 등록."""
        self._handlers[command] = handler

    def parse(self, text: str, chat_id: int) -> ParsedCommand | None:
        """텍스트를 명령으로 파싱. 비명령이면 None."""
        text = text.strip()
        if not text.startswith("/"):
            return None
        parts = text.split()
        command = parts[0][1:]  # '/status' -> 'status'
        args = parts[1:]
        return ParsedCommand(command=command, args=args, chat_id=chat_id, raw_text=text)

    async def handle(self, text: str, chat_id: int) -> str:
        """명령 처리. 응답 메시지 반환."""
        # Admin 체크
        if chat_id not in self.admin_chat_ids:
            return "Unauthorized."

        # Rate limit 체크
        if not self.rate_limiter.check(chat_id):
            return "Too many requests. Please wait."

        cmd = self.parse(text, chat_id)
        if not cmd:
            return "Unknown command."

        handler = self._handlers.get(cmd.command)
        if not handler:
            return f"Unknown command: /{cmd.command}"

        # 감사 로그
        audit_log(
            action=cmd.command.upper(),
            agent_id=cmd.args[0] if cmd.args else "",
            user=str(chat_id),
            detail=cmd.raw_text,
        )

        try:
            return await handler(cmd)
        except Exception as e:
            logger.exception("Command handler error: %s", cmd.command)
            return f"Error: {e}"
```

**Step 5: 테스트 통과 + 커밋**

```bash
pytest tests/test_telegram_handler.py -v
git add server/telegram/ tests/test_telegram_handler.py
git commit -m "feat: add Telegram command handler with rate limiting and audit logging"
```

---

### Task 9: agent/telegram/poller.py - 텔레그램 폴러

**Files:**
- Create: `agent/telegram/poller.py`

**Step 1: 구현**

이 모듈은 `python-telegram-bot` 라이브러리를 직접 사용하므로 단위 테스트보다 통합 테스트에 가깝다.
핵심 구조만 작성하고, 실제 봇 토큰 없이도 초기화까지 검증.

```python
# agent/telegram/poller.py
"""텔레그램 Long Polling 기반 명령 수신. 스펙 섹션 2.1 TelegramPoller 참조.

Agent 측 텔레그램 봇: /connect, /status, /disconnect 명령 수신.
"""
from __future__ import annotations

import asyncio
from typing import Callable, Awaitable
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from shared.utils import setup_logging

logger = setup_logging("agent_telegram")


class AgentTelegramPoller:
    """Agent 측 텔레그램 폴러."""

    def __init__(self, bot_token: str, admin_chat_id: int):
        self.bot_token = bot_token
        self.admin_chat_id = admin_chat_id
        self._app: Application | None = None

        # 콜백 (Agent 메인에서 설정)
        self.on_connect: Callable[..., Awaitable] | None = None
        self.on_disconnect: Callable[..., Awaitable] | None = None

    async def start(self) -> None:
        """폴링 시작."""
        self._app = Application.builder().token(self.bot_token).build()
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("connect", self._cmd_connect))
        self._app.add_handler(CommandHandler("disconnect", self._cmd_disconnect))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()
        logger.info("Telegram poller started")

    async def stop(self) -> None:
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()

    async def send_message(self, text: str) -> None:
        """관리자에게 메시지 전송."""
        if self._app:
            await self._app.bot.send_message(
                chat_id=self.admin_chat_id, text=text
            )

    def _is_admin(self, update: Update) -> bool:
        return update.effective_chat.id == self.admin_chat_id

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            return
        await update.message.reply_text("Agent: STANDBY")

    async def _cmd_connect(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            return
        args = context.args  # [IP, PORT]
        if not args or len(args) < 2:
            await update.message.reply_text("Usage: /connect <IP> <PORT>")
            return
        if self.on_connect:
            await self.on_connect(args[0], int(args[1]))
            await update.message.reply_text(f"Connecting to {args[0]}:{args[1]}...")
        else:
            await update.message.reply_text("Connect handler not configured.")

    async def _cmd_disconnect(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            return
        if self.on_disconnect:
            await self.on_disconnect()
            await update.message.reply_text("Disconnecting...")
```

**Step 2: 커밋** (봇 토큰 없이 동작하는 구조 코드)

```bash
git add agent/telegram/poller.py
git commit -m "feat: add Agent Telegram poller (connect/disconnect/status commands)"
```

---

### Task 10: Phase 1 통합 - Agent Standby → Connect 전체 흐름 검증

**Files:**
- Create: `tests/test_integration_phase1.py`

**Step 1: 통합 테스트 작성**

```python
# tests/test_integration_phase1.py
"""Phase 1 통합 테스트: Agent → Server 접속 → Heartbeat → 종료."""
import asyncio
import pytest
from server.core.tcp_server import TCPServer
from server.core.session_mgr import SessionManager
from agent.core.tcp_client import TCPClient

TOKEN = "integrationtest" + "x" * 49  # 64 chars


class TestPhase1Integration:
    async def test_full_connect_heartbeat_disconnect_flow(self):
        """완전한 접속 → 하트비트 → 종료 흐름."""
        # 1. 서버 시작
        mgr = SessionManager(max_agents=10)
        srv = TCPServer(host="127.0.0.1", port=0, session_mgr=mgr, auth_token=TOKEN)
        port = await srv.start()

        try:
            # 2. 클라이언트 접속
            client = TCPClient(
                agent_id="PC-INT-01", version="1.0.0", token=TOKEN,
                host="127.0.0.1", port=port,
            )
            assert await client.connect() is True
            assert client.is_connected

            # 3. 세션 등록 확인
            session = mgr.get_session("PC-INT-01")
            assert session is not None
            assert session.session_id == client.session_id

            # 4. Heartbeat 교환
            await client.send_heartbeat()
            await asyncio.sleep(0.1)

            # 5. 정상 종료
            await client.disconnect()
            await asyncio.sleep(0.1)

            # 6. 세션 정리 확인
            assert mgr.get_session("PC-INT-01") is None

        finally:
            await srv.stop()

    async def test_multiple_agents_connect(self):
        """멀티 에이전트 동시 접속."""
        mgr = SessionManager(max_agents=10)
        srv = TCPServer(host="127.0.0.1", port=0, session_mgr=mgr, auth_token=TOKEN)
        port = await srv.start()

        clients = []
        try:
            for i in range(3):
                client = TCPClient(
                    agent_id=f"PC-MULTI-{i:02d}", version="1.0.0", token=TOKEN,
                    host="127.0.0.1", port=port,
                )
                assert await client.connect() is True
                clients.append(client)

            assert len(mgr.get_all_sessions()) == 3

        finally:
            for c in clients:
                await c.disconnect()
            await asyncio.sleep(0.1)
            await srv.stop()
```

**Step 2: 테스트 실행 → 통과**

```bash
pytest tests/test_integration_phase1.py -v
```

**Step 3: 전체 Phase 1 테스트 실행**

```bash
pytest tests/ -v
```
Expected: ALL PASS

**Step 4: Phase 1 완료 커밋**

```bash
git add tests/test_integration_phase1.py
git commit -m "test: add Phase 1 integration tests (multi-agent connect/heartbeat/disconnect)"
```

---

## Phase 2: 로그 모니터링 (Week 2)

> 목표: 파일 변경 감지, 누적 로그 전송, 실시간 스트리밍, 저장 관리

---

### Task 11: agent/core/log_watcher.py - 파일 변경 감지 + 테일링

**Files:**
- Create: `agent/core/log_watcher.py`
- Create: `tests/test_log_watcher.py`

**Step 1: 테스트 작성**

```python
# tests/test_log_watcher.py
"""로그 파일 감시 테스트."""
import asyncio
import pytest
from agent.core.log_watcher import LogWatcher


class TestLogWatcher:
    async def test_detects_new_lines(self, tmp_path):
        """새 로그 라인 감지."""
        log_file = tmp_path / "app.log"
        log_file.write_text("line1\n")

        lines_received = []

        async def on_line(filename: str, line: str):
            lines_received.append(line)

        watcher = LogWatcher(
            watch_dirs=[str(tmp_path)],
            extensions=[".log"],
            on_new_line=on_line,
        )
        await watcher.start()

        # 파일에 새 라인 추가
        await asyncio.sleep(0.5)
        with open(log_file, "a") as f:
            f.write("line2\n")
            f.write("line3\n")

        await asyncio.sleep(1.0)
        await watcher.stop()

        assert "line2" in "".join(lines_received)

    async def test_reads_history(self, tmp_path):
        """기존 로그 히스토리 읽기."""
        log_file = tmp_path / "old.log"
        log_file.write_text("old_line1\nold_line2\n")

        watcher = LogWatcher(
            watch_dirs=[str(tmp_path)],
            extensions=[".log"],
            on_new_line=lambda f, l: None,
        )
        history = watcher.read_history(str(log_file), max_mb=1)
        assert b"old_line1" in history
```

**Step 2: 구현**

`watchdog` 라이브러리로 파일 시스템 이벤트 감지. 수정된 파일의 마지막 읽은 위치부터 새 라인을 읽어 콜백 호출.

핵심 인터페이스:
```python
class LogWatcher:
    def __init__(self, watch_dirs, extensions, on_new_line):
        """on_new_line: async callback(filename: str, line: str)"""
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def read_history(self, filepath: str, max_mb: int) -> bytes: ...
```

**Step 3: 테스트 통과 + 커밋**

```bash
pytest tests/test_log_watcher.py -v
git add agent/core/log_watcher.py tests/test_log_watcher.py
git commit -m "feat: add LogWatcher with file change detection and history reading"
```

---

### Task 12: LOG_HIST / LOG_REAL 페이로드 + 프로토콜 확장

**Files:**
- Modify: `shared/protocol.py` - `LogHistPayload`, `LogRealPayload` 추가
- Modify: `tests/test_protocol.py` - 해당 테스트 추가

**핵심 인터페이스:**

```python
@dataclass
class LogHistPayload:
    """LOG_HIST: [FileName(256B)] [FileSize(4B)] [Data(...)]"""
    filename: str   # max 256 chars
    data: bytes

@dataclass
class LogRealPayload:
    """LOG_REAL: [FileName(256B)] [LineLen(2B)] [Line(...)]"""
    filename: str
    line: str
```

**TDD 흐름**: 테스트 → 실패 → 구현 → 통과 → 커밋

```bash
git commit -m "feat: add LOG_HIST and LOG_REAL payload serialization"
```

---

### Task 13: 로그 전송 흐름 구현 (Agent → Server)

**Files:**
- Modify: `agent/core/tcp_client.py` - `send_log_history()`, `send_log_line()` 메서드 추가
- Modify: `server/core/tcp_server.py` - `_handle_log_hist()`, `_handle_log_real()` 스텁 구현
- Create: `server/storage/manager.py`
- Create: `tests/test_storage.py`

**StorageManager 핵심 인터페이스:**

```python
class StorageManager:
    """로그/리포트/백업 파일 관리. 스펙 섹션 3.1 참조."""
    def __init__(self, base_dir: str, max_retention_days: int, max_backups: int): ...
    def save_log_history(self, agent_id: str, filename: str, data: bytes) -> str: ...
    def append_realtime_log(self, agent_id: str, filename: str, line: str) -> None: ...
    def get_agent_logs(self, agent_id: str) -> list[str]: ...
    def cleanup_old_logs(self) -> int: ...  # 삭제된 파일 수 반환
```

```bash
git commit -m "feat: implement log transfer flow (Agent→Server) with StorageManager"
```

---

### Task 14: Phase 2 통합 테스트

**Files:**
- Create: `tests/test_integration_phase2.py`

**테스트 시나리오:**
1. 서버 시작 → Agent 접속
2. Agent가 LOG_HIST로 누적 로그 전송 → 서버 storage에 저장 확인
3. Agent가 LOG_REAL로 실시간 라인 전송 → 서버에서 수신 확인
4. StorageManager가 파일을 올바른 경로에 저장하는지 확인

```bash
git commit -m "test: add Phase 2 integration tests (log transfer end-to-end)"
```

---

## Phase 3: AI 파이프라인 (Week 3~3.5)

> 목표: 듀얼 LLM 프로바이더, 3단계 체이닝, Step 3.5 검증, 프롬프트 템플릿

---

### Task 15: server/ai/provider.py - AIProvider 추상화 (Strategy Pattern)

**Files:**
- Create: `server/ai/provider.py`
- Create: `tests/test_ai_provider.py`

**Step 1: 테스트 작성**

```python
# tests/test_ai_provider.py
"""AI 프로바이더 테스트 (Mock 기반)."""
import pytest
from unittest.mock import AsyncMock, patch
from server.ai.provider import AIProviderFactory, OpenAIProvider, ClaudeProvider


class TestAIProviderFactory:
    def test_create_openai(self):
        provider = AIProviderFactory.create("openai", api_key="test", model="gpt-4o")
        assert isinstance(provider, OpenAIProvider)

    def test_create_claude(self):
        provider = AIProviderFactory.create("claude", api_key="test", model="claude-sonnet-4-20250514")
        assert isinstance(provider, ClaudeProvider)

    def test_unknown_raises(self):
        with pytest.raises(ValueError):
            AIProviderFactory.create("unknown", api_key="test", model="x")


class TestRetryLogic:
    async def test_retries_on_failure(self):
        provider = OpenAIProvider(api_key="test", model="gpt-4o")
        provider._call_api = AsyncMock(side_effect=[Exception("timeout"), Exception("timeout"), "result"])
        result = await provider.generate("test prompt")
        assert result == "result"
        assert provider._call_api.call_count == 3

    async def test_fallback_on_exhausted_retries(self):
        """3회 실패 후 폴백 프로바이더 호출."""
        primary = OpenAIProvider(api_key="test", model="gpt-4o")
        fallback = ClaudeProvider(api_key="test", model="claude-sonnet-4-20250514")
        primary._call_api = AsyncMock(side_effect=Exception("fail"))
        fallback._call_api = AsyncMock(return_value="fallback result")

        primary.fallback = fallback
        result = await primary.generate_with_fallback("prompt")
        assert result == "fallback result"
```

**Step 2: 구현**

```python
# server/ai/provider.py (핵심 구조)
from abc import ABC, abstractmethod

class BaseAIProvider(ABC):
    """AI 프로바이더 공통 인터페이스."""
    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model
        self.fallback: BaseAIProvider | None = None

    @abstractmethod
    async def _call_api(self, prompt: str, system: str = "") -> str: ...

    async def generate(self, prompt: str, system: str = "",
                       max_retries: int = 3, backoff: list[int] = [5, 15, 30]) -> str:
        """재시도 포함 생성."""
        for attempt in range(max_retries):
            try:
                return await self._call_api(prompt, system)
            except Exception as e:
                if attempt < max_retries - 1:
                    await asyncio.sleep(backoff[attempt])
                else:
                    raise

    async def generate_with_fallback(self, prompt: str, system: str = "") -> str:
        """재시도 실패 시 폴백 프로바이더로 전환."""
        try:
            return await self.generate(prompt, system)
        except Exception:
            if self.fallback:
                return await self.fallback.generate(prompt, system)
            raise

class OpenAIProvider(BaseAIProvider): ...   # openai 라이브러리 사용
class ClaudeProvider(BaseAIProvider): ...   # anthropic 라이브러리 사용
class AIProviderFactory:
    @staticmethod
    def create(name: str, **kwargs) -> BaseAIProvider: ...
```

```bash
git commit -m "feat: add AI provider abstraction with Strategy Pattern, retry, and fallback"
```

---

### Task 16: server/ai/prompts/ - Jinja2 프롬프트 템플릿

**Files:**
- Create: `server/ai/prompts/analyze_prompt.j2`
- Create: `server/ai/prompts/plan_prompt.j2`
- Create: `server/ai/prompts/fix_prompt.j2`
- Create: `server/ai/prompts/review_prompt.j2` (v2.1)
- Create: `server/ai/prompt_loader.py`
- Create: `tests/test_prompts.py`

**테스트:** 각 템플릿이 변수 주입 후 올바른 형태의 프롬프트를 생성하는지.

스펙 섹션 4.3의 4개 프롬프트를 Jinja2 템플릿으로 변환.

```bash
git commit -m "feat: add Jinja2 prompt templates for 4-stage AI pipeline"
```

---

### Task 17: server/ai/pipeline.py - 3단계 AI 체이닝

**Files:**
- Create: `server/ai/pipeline.py`
- Create: `tests/test_ai_pipeline.py`
- Create: `tests/fixtures/ai_responses/analyze_response.json`
- Create: `tests/fixtures/ai_responses/plan_response.json`
- Create: `tests/fixtures/ai_responses/fix_response.json`

**Step 1: AI Mock fixture 기반 파이프라인 테스트**

```python
# tests/test_ai_pipeline.py
class TestAIPipeline:
    async def test_step1_analyze(self, mock_ai_provider, tmp_path):
        """Step 1: 로그 → Analysis Report."""
        pipeline = AIPipeline(provider=mock_ai_provider, storage_dir=str(tmp_path))
        report = await pipeline.step1_analyze("error log content here")
        assert "Analysis" in report or len(report) > 0
        # 파일 저장 확인
        assert (tmp_path / "Analysis_Report.md").exists()

    async def test_step2_plan(self, mock_ai_provider, tmp_path):
        """Step 2: Report → Plan."""
        pipeline = AIPipeline(provider=mock_ai_provider, storage_dir=str(tmp_path))
        plan = await pipeline.step2_plan("analysis report content")
        assert len(plan) > 0
        assert (tmp_path / "Improvement_Plan.md").exists()

    async def test_step3_fix(self, mock_ai_provider, tmp_path):
        """Step 3: Plan + Code → Fixed Code."""
        pipeline = AIPipeline(provider=mock_ai_provider, storage_dir=str(tmp_path))
        fixed = await pipeline.step3_fix("plan content", "original source code")
        assert len(fixed) > 0

    async def test_full_pipeline(self, mock_ai_provider, tmp_path):
        """Step 1→2→3 전체 흐름."""
        pipeline = AIPipeline(provider=mock_ai_provider, storage_dir=str(tmp_path))
        result = await pipeline.run_full("error logs", "source code")
        assert result.analysis_report is not None
        assert result.improvement_plan is not None
        assert result.fixed_code is not None
```

**핵심 인터페이스:**

```python
@dataclass
class PipelineResult:
    analysis_report: str
    improvement_plan: str
    fixed_code: str
    validation_report: dict | None = None  # Step 3.5

class AIPipeline:
    def __init__(self, provider: BaseAIProvider, storage_dir: str): ...
    async def step1_analyze(self, log_content: str) -> str: ...
    async def step2_plan(self, analysis_report: str) -> str: ...
    async def step3_fix(self, plan: str, source_code: str) -> str: ...
    async def step3_5_validate(self, original: str, fixed: str, plan: str) -> dict: ...
    async def run_full(self, log_content: str, source_code: str) -> PipelineResult: ...
```

```bash
git commit -m "feat: implement 3-stage AI chaining pipeline with file persistence"
```

---

### Task 18: server/ai/pipeline.py - Step 3.5 Automated Validation

**Files:**
- Modify: `server/ai/pipeline.py` - `step3_5_validate()` 구현
- Create: `tests/fixtures/ai_responses/review_response.json`
- Modify: `tests/test_ai_pipeline.py` - 검증 테스트 추가

**Step 1: 검증 테스트 추가**

```python
# tests/test_ai_pipeline.py (추가)
class TestStep35Validation:
    async def test_syntax_check_pass(self, mock_ai_provider, tmp_path):
        pipeline = AIPipeline(provider=mock_ai_provider, storage_dir=str(tmp_path))
        valid_code = "def hello():\n    return 'world'\n"
        report = await pipeline.step3_5_validate(
            original="def hello():\n    pass\n",
            fixed=valid_code,
            plan="Fix hello function",
        )
        assert report["syntax_check"] == "PASS"
        assert "confidence_score" in report

    async def test_syntax_check_fail(self, mock_ai_provider, tmp_path):
        pipeline = AIPipeline(provider=mock_ai_provider, storage_dir=str(tmp_path))
        invalid_code = "def hello(\n    return\n"  # 문법 오류
        report = await pipeline.step3_5_validate(
            original="def hello():\n    pass\n",
            fixed=invalid_code,
            plan="Fix hello function",
        )
        assert report["syntax_check"] == "FAIL"
        assert report["deploy_allowed"] is False

    async def test_cross_review_uses_different_provider(self, tmp_path):
        """OpenAI로 수정했으면 Claude로 리뷰."""
        primary = MockAIProvider("openai")
        fallback = MockAIProvider("claude")
        pipeline = AIPipeline(provider=primary, storage_dir=str(tmp_path))
        pipeline.review_provider = fallback
        report = await pipeline.step3_5_validate(
            original="code", fixed="new code", plan="plan"
        )
        assert report["cross_review"]["reviewer"] != "openai"

    async def test_confidence_below_70_requires_manual(self, mock_ai_provider, tmp_path):
        """신뢰도 70 미만이면 수동 승인 필요."""
        mock_ai_provider.set_review_confidence(45)
        pipeline = AIPipeline(provider=mock_ai_provider, storage_dir=str(tmp_path))
        report = await pipeline.step3_5_validate("orig", "fixed", "plan")
        assert report["requires_manual_approval"] is True
```

**구현 핵심:**

```python
async def step3_5_validate(self, original: str, fixed: str, plan: str) -> dict:
    """Step 3.5: 자동 검증. 스펙 섹션 4.2 v2.1 참조."""
    report = {}

    # 1. Syntax Check
    try:
        import ast
        ast.parse(fixed)
        report["syntax_check"] = "PASS"
    except SyntaxError as e:
        report["syntax_check"] = "FAIL"
        report["syntax_error"] = str(e)
        report["deploy_allowed"] = False
        report["requires_manual_approval"] = True
        return report

    # 2. AI Cross-Review (다른 프로바이더 사용)
    review_result = await self.review_provider.generate(
        self._render_review_prompt(original, fixed, plan)
    )
    report["cross_review"] = json.loads(review_result)

    # 3. Diff 생성
    import difflib
    diff = difflib.unified_diff(original.splitlines(), fixed.splitlines(), lineterm="")
    report["diff_summary"] = "\n".join(diff)

    # 4. 신뢰도 점수
    score = report["cross_review"].get("confidence_score", 0)
    report["confidence_score"] = score
    report["deploy_allowed"] = score >= 70
    report["requires_manual_approval"] = score < 70

    # 파일 저장
    self._save_json(report, "Validation_Report.json")
    return report
```

```bash
git commit -m "feat: add Step 3.5 Automated Validation (syntax check + cross-LLM review)"
```

---

### Task 19: 텔레그램 AI 명령어 연동 (/analyze, /plan, /fix, /auto)

**Files:**
- Modify: `server/telegram/handler.py` - AI 명령 핸들러 등록
- Create: `server/telegram/ai_commands.py`

**핵심:** `/analyze`, `/plan`, `/fix` 각각 파이프라인의 개별 단계를 호출. `/auto`는 전체 파이프라인 실행.

```bash
git commit -m "feat: wire Telegram AI commands to pipeline (/analyze, /plan, /fix, /auto)"
```

---

## Phase 4: 배포 자동화 (Week 4)

> 목표: 파일 청크 전송, 프로세스 교체, 배포 후 자동 롤백

---

### Task 20: 파일 전송 프로토콜 페이로드 + FileTransfer

**Files:**
- Modify: `shared/protocol.py` - `CmdDeployPayload`, `FileChunkPayload`, `FileAckPayload` 추가
- Create: `agent/core/file_transfer.py`
- Create: `tests/test_file_transfer.py`

**핵심:** 4KB 청크 단위 전송 + SeqNum으로 순서 보장 + SHA-256 무결성 검증.

```bash
git commit -m "feat: add chunk-based file transfer with SHA-256 verification"
```

---

### Task 21: agent/core/process_mgr.py - 프로세스 관리 + 헬스체크

**Files:**
- Create: `agent/core/process_mgr.py`
- Create: `tests/test_process_mgr.py`

**핵심 인터페이스:**

```python
class ProcessManager:
    def __init__(self, process_name: str, process_path: str, backup_dir: str): ...
    def find_pid(self) -> int | None: ...
    def kill(self) -> bool: ...
    def start(self) -> int: ...  # 반환: PID
    async def health_check(self, timeout: int = 30) -> bool: ...  # v2.1
    def backup_current(self) -> str: ...   # 백업 경로 반환
    def rollback(self) -> bool: ...        # 최신 백업으로 복원
```

```bash
git commit -m "feat: add ProcessManager with kill/start/health_check/rollback"
```

---

### Task 22: 배포 흐름 통합 (CMD_DEPLOY → FILE_CHUNK → VERIFY)

**Files:**
- Modify: `server/core/tcp_server.py` - 배포 명령 전송 로직
- Modify: `agent/core/tcp_client.py` - 파일 수신 + 프로세스 교체 + 헬스체크
- Create: `tests/test_integration_phase4.py`

**배포 시퀀스:**

```
Server: CMD_DEPLOY(filesize, sha256, filename)
Server: FILE_CHUNK(seq=0, data) → Agent: FILE_ACK(seq=0, OK)
Server: FILE_CHUNK(seq=1, data) → Agent: FILE_ACK(seq=1, OK)
...
Agent: 파일 조립 → SHA-256 검증
Agent: ProcessManager.backup_current()
Agent: ProcessManager.kill() → 파일 교체 → ProcessManager.start()
Agent: health_check(30초) → CMD_CTRL_ACK(DEPLOY_VERIFIED or DEPLOY_ROLLBACK)
```

```bash
git commit -m "feat: implement full deploy flow with auto-rollback on health check failure"
```

---

### Task 23: 텔레그램 배포 명령 (/deploy, /rollback)

**Files:**
- Modify: `server/telegram/handler.py`

```bash
git commit -m "feat: add /deploy and /rollback Telegram commands"
```

---

## Phase 5: 웹 대시보드 (Week 5)

> 목표: FastAPI 대시보드, WebSocket 실시간, 이력 관리, 서버 헬스 모니터링

---

### Task 24: server/dashboard/app.py - FastAPI 기본 설정 + JWT 인증

**Files:**
- Create: `server/dashboard/app.py`
- Create: `server/dashboard/auth.py`
- Create: `server/dashboard/templates/base.html`
- Create: `server/dashboard/templates/login.html`
- Create: `server/dashboard/static/` (Tailwind CSS CDN 사용)

**핵심:** FastAPI + Jinja2 + JWT 로그인. Tailwind CSS CDN으로 스타일링.

```bash
git commit -m "feat: setup FastAPI dashboard with JWT auth and Tailwind CSS"
```

---

### Task 25: 메인 대시보드 페이지 (에이전트 상태 카드)

**Files:**
- Create: `server/dashboard/templates/dashboard.html`
- Create: `server/dashboard/routes/dashboard.py`

**핵심:** SessionManager에서 에이전트 목록 가져와 상태 카드 렌더링. HTMX `hx-get`으로 5초 자동 갱신.

```bash
git commit -m "feat: add main dashboard page with agent status cards (HTMX auto-refresh)"
```

---

### Task 26: 실시간 로그 뷰어 (WebSocket)

**Files:**
- Create: `server/dashboard/templates/logs.html`
- Create: `server/dashboard/routes/logs.py`
- Modify: `server/dashboard/app.py` - WebSocket 엔드포인트

**핵심:** WebSocket으로 에이전트별 로그 스트리밍. Auto-scroll. 에이전트 드롭다운으로 선택.

```bash
git commit -m "feat: add real-time log viewer with WebSocket streaming"
```

---

### Task 27: AI 리포트 + 배포 이력 페이지

**Files:**
- Create: `server/dashboard/templates/reports.html`
- Create: `server/dashboard/templates/deploys.html`
- Create: `server/dashboard/routes/reports.py`
- Create: `server/dashboard/routes/deploys.py`

**핵심:** StorageManager에서 리포트/배포 이력 가져와 표시. Markdown 렌더링 (python-markdown). Diff 뷰어.

```bash
git commit -m "feat: add AI report viewer and deploy history timeline"
```

---

### Task 28: server/core/health_monitor.py - 서버 자체 모니터링

**Files:**
- Create: `server/core/health_monitor.py`
- Create: `tests/test_health_monitor.py`

**핵심 인터페이스:**

```python
class HealthMonitor:
    """서버 리소스 모니터링. 스펙 섹션 3.3 참조."""
    def __init__(self, config: dict, telegram_notifier: Callable): ...
    async def check_loop(self) -> None:  # 5분 주기
    async def _check_cpu(self) -> None:      # > 90% 3회 연속
    async def _check_memory(self) -> None:   # > 85%
    async def _check_disk(self) -> None:     # > 90%
    async def _check_ai_api_health(self) -> None:  # > 50% 오류율
```

psutil로 수집. 임계값 초과 시 텔레그램 알림 (30분 쿨다운).

```bash
git commit -m "feat: add server health monitoring with Telegram alerts"
```

---

## Phase 6: Windows 서비스 + 업데이트 (Week 5.5)

> 목표: Windows 서비스 등록, 자체 업데이트 + 3중 안전장치

---

### Task 29: agent/service/win_service.py - Windows 서비스

**Files:**
- Create: `agent/service/win_service.py`

**핵심:** pywin32의 `win32serviceutil.ServiceFramework` 상속. `SvcDoRun`에서 asyncio 루프 시작.

> Windows 환경 의존이므로 수동 테스트. `python agent/service/win_service.py install/start/stop/remove`

```bash
git commit -m "feat: add Windows service registration (AILogOps-Agent)"
```

---

### Task 30: agent/updater/self_update.py - 자체 업데이트 + 3중 안전장치

**Files:**
- Create: `agent/updater/self_update.py`
- Create: `agent/updater/updater_template.bat`

**핵심 인터페이스:**

```python
class SelfUpdater:
    def __init__(self, backup_dir: str, current_version: str): ...
    async def receive_update(self, data: bytes, sha256: str) -> bool:
        """새 바이너리 수신 + SHA-256 검증."""
    def generate_updater_bat(self) -> str:
        """updater.bat 생성 (롤백 로직 포함). 스펙 섹션 2.5 참조."""
    def execute_update(self) -> None:
        """bat 실행 후 프로세스 종료."""
    def verify_version_on_boot(self) -> bool:
        """.version 파일과 실제 버전 비교. 불일치 시 롤백."""
```

```bash
git commit -m "feat: add self-update mechanism with 3-safety rollback"
```

---

### Task 31: 최종 통합 테스트 + 마무리

**Files:**
- Run: 전체 테스트 스위트
- Verify: 모든 Phase 통합 동작

```bash
pytest tests/ -v --tb=short
```

**체크리스트:**
- [ ] 전체 테스트 통과
- [ ] TCP 통신: AUTH → LOG → HEARTBEAT → DEPLOY → DISCONNECT
- [ ] AI 파이프라인: 3단계 + Step 3.5 검증
- [ ] 텔레그램: 전 명령어 동작
- [ ] 대시보드: 로그인 → 상태 → 로그 → 리포트
- [ ] 보안: Rate Limiting + Audit Log
- [ ] 헬스 모니터링: CPU/MEM/DISK 알림

```bash
git add -A
git commit -m "chore: Phase 6 complete - final integration verification"
git tag v1.0.0
```

---

## Dependency Graph

```
Phase 1 (Skeleton)
  └──→ Phase 2 (Log Monitoring)
         └──→ Phase 3 (AI Pipeline)
                └──→ Phase 4 (Deploy Automation)
  └──→ Phase 5 (Dashboard) ← depends on Phase 2+3+4 data
Phase 6 (Windows Service) ← depends on all above
```

---

## Summary

| Phase | Tasks | Commits | Key Deliverables |
|-------|-------|---------|------------------|
| 1 | 1-10 | ~10 | TCP protocol, server, client, Telegram, Rate Limiting |
| 2 | 11-14 | ~4 | LogWatcher, LOG_HIST/REAL, StorageManager |
| 3 | 15-19 | ~5 | AIProvider, Pipeline 3.5 stages, Prompt templates |
| 4 | 20-23 | ~4 | FileTransfer, ProcessManager, Deploy+Rollback |
| 5 | 24-28 | ~5 | FastAPI dashboard, WebSocket, HealthMonitor |
| 6 | 29-31 | ~3 | WinService, SelfUpdater, Final integration |
| **Total** | **31** | **~31** | **Full system** |
