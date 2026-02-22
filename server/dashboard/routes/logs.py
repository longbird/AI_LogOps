from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol, cast

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from shared.models import AgentSession
from shared.protocol import LogAction

logger = logging.getLogger("logs_route")
router = APIRouter()


class SessionManagerLike(Protocol):
    def get_all_sessions(self) -> list[AgentSession]: ...

    def get_session(self, agent_id: str) -> AgentSession | None: ...


class _TCPServerLike(Protocol):
    async def send_log_command(
        self, agent_id: str, action: LogAction, date: str = ...
    ) -> bool: ...


class DashboardState(Protocol):
    templates: Jinja2Templates
    session_mgr: SessionManagerLike | None
    tcp_server: _TCPServerLike | None


def _state(request: Request) -> DashboardState:
    return cast(DashboardState, request.app.state)  # pyright: ignore[reportAny]


def _ws_state(websocket: WebSocket) -> DashboardState:
    return cast(DashboardState, websocket.app.state)  # pyright: ignore[reportAny]


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request) -> HTMLResponse:
    """로그 뷰어 페이지."""
    state = _state(request)
    templates = state.templates
    session_mgr = state.session_mgr
    agents: list[str] = []
    if session_mgr:
        agents = [s.agent_info.agent_id for s in session_mgr.get_all_sessions()]
    return cast(
        HTMLResponse,
        templates.TemplateResponse(
            "logs.html",
            {
                "request": request,
                "title": "Log Viewer",
                "agents": agents,
            },
        ),
    )


@router.get("/api/logs/{agent_id}", response_class=HTMLResponse)
async def get_agent_logs(request: Request, agent_id: str) -> HTMLResponse:
    """HTMX: 에이전트 로그 버퍼 반환."""
    session_mgr = _state(request).session_mgr
    if session_mgr is None:
        return HTMLResponse("<p>No session manager.</p>")
    session = session_mgr.get_session(agent_id)
    if session is None:
        return HTMLResponse(f"<p>Agent {agent_id} not found.</p>")
    lines = session.log_buffer[-200:]
    log_html = "\n".join(
        f"<div class='text-sm font-mono'>{line}</div>" for line in lines
    )
    if not lines:
        log_html = "<p class='text-gray-400'>No logs yet.</p>"
    return HTMLResponse(log_html)


@router.post("/api/logs/{agent_id}/stream")
async def toggle_log_stream(request: Request, agent_id: str) -> JSONResponse:
    """에이전트 실시간 로그 스트리밍 시작/중지."""
    state = _state(request)
    tcp_server = state.tcp_server
    if tcp_server is None:
        return JSONResponse({"error": "server not configured"}, status_code=503)

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    action_str: str = body.get("action", "")
    if action_str == "start":
        action = LogAction.REAL_START
    elif action_str == "stop":
        action = LogAction.REAL_STOP
    else:
        return JSONResponse(
            {"error": "action must be 'start' or 'stop'"}, status_code=400
        )

    success = await tcp_server.send_log_command(agent_id, action, "")
    if success:
        logger.info("log stream %s sent to agent=%s", action_str, agent_id)
        return JSONResponse(
            {"status": "ok", "agent_id": agent_id, "action": action_str}
        )
    logger.warning(
        "log stream %s FAILED for agent=%s (not found or disconnected)",
        action_str,
        agent_id,
    )
    return JSONResponse(
        {"error": f"failed to send command to {agent_id}"}, status_code=502
    )


@router.websocket("/ws/logs/{agent_id}")
async def websocket_logs(websocket: WebSocket, agent_id: str) -> None:
    """WebSocket: 에이전트 로그 실시간 스트리밍."""
    await websocket.accept()
    session_mgr = _ws_state(websocket).session_mgr

    try:
        last_index = 0
        if session_mgr:
            session = session_mgr.get_session(agent_id)
            if session:
                last_index = len(session.log_buffer)

        while True:
            await asyncio.sleep(0.5)
            if session_mgr is None:
                continue
            session = session_mgr.get_session(agent_id)
            if session is None:
                continue
            current_len = len(session.log_buffer)
            if current_len > last_index:
                new_lines = session.log_buffer[last_index:current_len]
                for line in new_lines:
                    await websocket.send_text(line)
                last_index = current_len
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
