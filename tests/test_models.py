from __future__ import annotations

# pyright: reportMissingImports=false

from shared.models import AgentInfo, AgentSession, AgentState, DeployRecord


def test_agent_state_values_exist() -> None:
    expected = {
        "INIT",
        "STANDBY",
        "CONNECTING",
        "CONNECTED",
        "DEPLOYING",
        "DEPLOY_VERIFY",
        "UPDATING",
        "ERROR",
    }
    actual = {state.value for state in AgentState}
    assert actual == expected


def test_agent_info_creation_with_defaults() -> None:
    info = AgentInfo(agent_id="agent-01", version="1.0.0")
    assert info.agent_id == "agent-01"
    assert info.version == "1.0.0"
    assert info.state == AgentState.INIT


def test_agent_info_default_state_is_init() -> None:
    info = AgentInfo(agent_id="agent-02", version="2.0.0")
    assert info.state is AgentState.INIT


def test_agent_session_creation_with_agent_info() -> None:
    info = AgentInfo(agent_id="agent-03", version="1.1.0")
    session = AgentSession(agent_info=info, session_id="session-123")
    assert session.agent_info == info
    assert session.session_id == "session-123"


def test_agent_session_defaults() -> None:
    info = AgentInfo(agent_id="agent-04", version="1.2.0")
    session = AgentSession(agent_info=info, session_id="session-456")
    assert session.log_buffer == []
    assert session.deploy_history == []


def test_deploy_record_creation() -> None:
    record = DeployRecord(
        timestamp=1_700_000_000.0,
        agent_id="agent-05",
        filename="app.py",
        sha256="abc123",
        success=True,
    )
    assert record.timestamp == 1_700_000_000.0
    assert record.agent_id == "agent-05"
    assert record.filename == "app.py"
    assert record.sha256 == "abc123"
    assert record.success is True


def test_deploy_record_defaults() -> None:
    record = DeployRecord(
        timestamp=1_700_000_001.0,
        agent_id="agent-06",
        filename="service.py",
        sha256="def456",
        success=False,
    )
    assert record.rollback is False
    assert record.detail == ""
