from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class AgentState(str, Enum):
    INIT = "INIT"
    STANDBY = "STANDBY"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DEPLOYING = "DEPLOYING"
    DEPLOY_VERIFY = "DEPLOY_VERIFY"
    UPDATING = "UPDATING"
    ERROR = "ERROR"


@dataclass(slots=True)
class AgentInfo:
    agent_id: str
    version: str
    state: AgentState = AgentState.INIT


@dataclass(slots=True)
class AgentSession:
    agent_info: AgentInfo
    session_id: str
    writer: object | None = None
    last_heartbeat: float = field(default_factory=time.time)
    process_status: int = 0  # 0=미설정, 1=실행중, 2=다운 (aggregate)
    process_statuses: dict[str, int] = field(default_factory=dict)  # name -> ProcessStatus
    log_buffer: list[str] = field(default_factory=list)
    rec_log_buffer: list[str] = field(default_factory=list)
    deploy_history: list[dict[str, object]] = field(default_factory=list)
    config_data: dict[str, object] | None = None
    config_update_result: dict[str, object] | None = None


@dataclass(slots=True)
class DeployRecord:
    timestamp: float
    agent_id: str
    filename: str
    sha256: str
    success: bool
    rollback: bool = False
    detail: str = ""
