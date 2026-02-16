from __future__ import annotations
# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportMissingParameterType=false

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from server.dashboard.app import create_app
from server.dashboard.auth import (
    authenticate_user,
    create_access_token,
    verify_token,
)
from server.core.session_mgr import SessionManager
from server.storage.manager import StorageManager


class _DummyWriter:
    def close(self) -> None:
        return None

    def is_closing(self) -> bool:
        return False


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        follow_redirects=False,
    ) as ac:
        yield ac


@pytest.mark.asyncio
async def test_login_page_accessible(client):
    response = await client.get("/login")

    assert response.status_code == 200
    assert "AI-LogOps Login" in response.text


@pytest.mark.asyncio
async def test_login_success_redirects(client):
    response = await client.post(
        "/login",
        data={"username": "admin", "password": "admin123"},
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "access_token=" in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_login_failure_shows_error(client):
    response = await client.post(
        "/login",
        data={"username": "admin", "password": "wrong"},
    )

    assert response.status_code == 401
    assert "Invalid credentials" in response.text


@pytest.mark.asyncio
async def test_unauthenticated_redirect(client):
    response = await client.get("/dashboard")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_authenticated_access(client):
    token = create_access_token({"sub": "admin"})
    response = await client.get("/dashboard", cookies={"access_token": token})

    assert response.status_code == 200
    assert "Agent Status" in response.text


@pytest.mark.asyncio
async def test_dashboard_page_shows_no_agents(client):
    token = create_access_token({"sub": "admin"})
    response = await client.get("/dashboard", cookies={"access_token": token})

    assert response.status_code == 200
    assert "No agents connected." in response.text


@pytest.mark.asyncio
async def test_dashboard_page_with_agents():
    session_mgr = SessionManager()
    _ = session_mgr.create_session("agent-001", "1.2.3", _DummyWriter())
    app = create_app(session_mgr=session_mgr)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        follow_redirects=False,
    ) as ac:
        token = create_access_token({"sub": "admin"})
        response = await ac.get("/dashboard", cookies={"access_token": token})

    assert response.status_code == 200
    assert "agent-001" in response.text
    assert "1.2.3" in response.text
    assert "CONNECTED" in response.text


@pytest.mark.asyncio
async def test_api_agents_partial():
    session_mgr = SessionManager()
    _ = session_mgr.create_session("agent-xyz", "2.0.0", _DummyWriter())
    app = create_app(session_mgr=session_mgr)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        follow_redirects=False,
    ) as ac:
        token = create_access_token({"sub": "admin"})
        response = await ac.get("/api/agents", cookies={"access_token": token})

    assert response.status_code == 200
    assert "agent-xyz" in response.text
    assert "CONNECTED" in response.text
    assert "grid grid-cols-1" in response.text


@pytest.mark.asyncio
async def test_api_agents_no_auth_redirects(client):
    response = await client.get("/api/agents")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_logout_clears_cookie(client):
    response = await client.get("/logout")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    set_cookie = response.headers.get("set-cookie", "")
    assert "access_token=" in set_cookie
    assert "Max-Age=0" in set_cookie or "expires=" in set_cookie.lower()


def test_create_access_token():
    token = create_access_token({"sub": "admin"}, expires_delta=timedelta(minutes=5))

    payload = verify_token(token)
    assert payload is not None
    assert payload["sub"] == "admin"
    assert "exp" in payload


def test_verify_token_valid():
    token = create_access_token({"sub": "admin"})

    payload = verify_token(token)
    assert payload is not None
    assert payload["sub"] == "admin"


def test_verify_token_invalid():
    payload = verify_token("this.is.not.a.valid.token")

    assert payload is None


def test_authenticate_user():
    assert authenticate_user("admin", "admin123") is True
    assert authenticate_user("admin", "wrong") is False


@pytest.mark.asyncio
async def test_logs_page_accessible(client):
    token = create_access_token({"sub": "admin"})
    response = await client.get("/logs", cookies={"access_token": token})

    assert response.status_code == 200
    assert "Log Viewer" in response.text


@pytest.mark.asyncio
async def test_logs_page_lists_agents():
    session_mgr = SessionManager()
    _ = session_mgr.create_session("PC-01", "1.0.0", _DummyWriter())
    _ = session_mgr.create_session("PC-02", "1.0.1", _DummyWriter())
    app = create_app(session_mgr=session_mgr)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        follow_redirects=False,
    ) as ac:
        token = create_access_token({"sub": "admin"})
        response = await ac.get("/logs", cookies={"access_token": token})

    assert response.status_code == 200
    assert "PC-01" in response.text
    assert "PC-02" in response.text


@pytest.mark.asyncio
async def test_api_logs_returns_log_buffer():
    session_mgr = SessionManager()
    session = session_mgr.create_session("PC-01", "1.0.0", _DummyWriter())
    assert session is not None
    session.log_buffer.extend(["line 1", "line 2"])
    app = create_app(session_mgr=session_mgr)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        follow_redirects=False,
    ) as ac:
        token = create_access_token({"sub": "admin"})
        response = await ac.get("/api/logs/PC-01", cookies={"access_token": token})

    assert response.status_code == 200
    assert "line 1" in response.text
    assert "line 2" in response.text


@pytest.mark.asyncio
async def test_api_logs_agent_not_found():
    session_mgr = SessionManager()
    app = create_app(session_mgr=session_mgr)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        follow_redirects=False,
    ) as ac:
        token = create_access_token({"sub": "admin"})
        response = await ac.get("/api/logs/NONEXIST", cookies={"access_token": token})

    assert response.status_code == 200
    assert "not found" in response.text.lower()


def test_websocket_logs_connects(app: FastAPI):
    client = TestClient(app)
    with client.websocket_connect("/ws/logs/PC-01"):
        pass


@pytest.mark.asyncio
async def test_reports_page_accessible(client):
    token = create_access_token({"sub": "admin"})
    response = await client.get("/reports", cookies={"access_token": token})

    assert response.status_code == 200
    assert "AI Reports" in response.text


@pytest.mark.asyncio
async def test_reports_page_with_files(tmp_path):
    storage_mgr = StorageManager(str(tmp_path))
    _ = storage_mgr.save_report("ai_pipeline", "Analysis_Report.md", "# Analysis")
    _ = storage_mgr.save_report("ai_pipeline", "Validation_Report.json", '{"ok": true}')
    app = create_app(storage_mgr=storage_mgr)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        follow_redirects=False,
    ) as ac:
        token = create_access_token({"sub": "admin"})
        response = await ac.get("/reports", cookies={"access_token": token})

    assert response.status_code == 200
    assert "Analysis_Report.md" in response.text
    assert "Validation_Report.json" in response.text
    assert "ai_pipeline" in response.text


@pytest.mark.asyncio
async def test_api_report_returns_content(tmp_path):
    storage_mgr = StorageManager(str(tmp_path))
    _ = storage_mgr.save_report("agent-01", "report.md", "# Title\n\n- item")
    app = create_app(storage_mgr=storage_mgr)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        follow_redirects=False,
    ) as ac:
        token = create_access_token({"sub": "admin"})
        response = await ac.get(
            "/api/reports/agent-01/report.md",
            cookies={"access_token": token},
        )

    assert response.status_code == 200
    assert "<h1" in response.text
    assert "Title" in response.text


@pytest.mark.asyncio
async def test_api_report_not_found(tmp_path):
    storage_mgr = StorageManager(str(tmp_path))
    app = create_app(storage_mgr=storage_mgr)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        follow_redirects=False,
    ) as ac:
        token = create_access_token({"sub": "admin"})
        response = await ac.get(
            "/api/reports/agent-01/missing.md",
            cookies={"access_token": token},
        )

    assert response.status_code == 404
    assert "Report not found" in response.text


@pytest.mark.asyncio
async def test_deploys_page_accessible(client):
    token = create_access_token({"sub": "admin"})
    response = await client.get("/deploys", cookies={"access_token": token})

    assert response.status_code == 200
    assert "Deploy History" in response.text


@pytest.mark.asyncio
async def test_deploys_page_with_history():
    session_mgr = SessionManager()
    session = session_mgr.create_session("PC-01", "1.0.0", _DummyWriter())
    assert session is not None
    session.deploy_history.append(
        {
            "timestamp": 1736200000.0,
            "filename": "agent.zip",
            "sha256": "abc123",
            "success": True,
            "rollback": False,
            "detail": "deployed",
        }
    )
    app = create_app(session_mgr=session_mgr)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        follow_redirects=False,
    ) as ac:
        token = create_access_token({"sub": "admin"})
        response = await ac.get("/deploys", cookies={"access_token": token})

    assert response.status_code == 200
    assert "PC-01" in response.text
    assert "agent.zip" in response.text
    assert "deployed" in response.text
