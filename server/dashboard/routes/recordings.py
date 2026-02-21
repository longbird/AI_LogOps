from __future__ import annotations

from typing import Any, Protocol, cast

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from shared.protocol import RecAction
from shared.utils import setup_logging

logger = setup_logging("dashboard.recordings")

router = APIRouter()


class _TCPServerLike(Protocol):
    async def send_rec_data_req(
        self,
        agent_id: str,
        query_type: str,
        date_str: str = ...,
        filename: str = ...,
        timeout: float = ...,
    ) -> Any | None: ...

    async def send_rec_command(
        self, agent_id: str, action: RecAction, date: str = ...
    ) -> bool: ...


class _SessionMgrLike(Protocol):
    def get_all_sessions(self) -> list[Any]: ...

    def get_session(self, agent_id: str) -> Any | None: ...


class _DashState(Protocol):
    templates: Jinja2Templates
    tcp_server: _TCPServerLike | None
    session_mgr: _SessionMgrLike | None


def _state(request: Request) -> _DashState:
    return cast(_DashState, request.app.state)


@router.get("/recordings", response_class=HTMLResponse)
async def recordings_page(request: Request) -> HTMLResponse:
    state = _state(request)
    templates = state.templates
    return cast(
        HTMLResponse,
        templates.TemplateResponse(
            "recordings.html",
            {"request": request, "title": "Recordings"},
        ),
    )


@router.get("/api/rec/list")
async def api_rec_list(
    request: Request,
    agent_id: str = "",
    date: str = "",
) -> JSONResponse:
    state = _state(request)
    tcp_server = state.tcp_server
    session_mgr = state.session_mgr

    if tcp_server is None or session_mgr is None:
        return JSONResponse({"error": "server not configured"}, status_code=503)

    if not agent_id:
        sessions = session_mgr.get_all_sessions()
        if not sessions:
            return JSONResponse({"error": "no agent connected"}, status_code=503)
        agent_id = sessions[0].agent_info.agent_id

    resp = await tcp_server.send_rec_data_req(
        agent_id=agent_id,
        query_type="list",
        date_str=date,
    )
    if resp is None:
        return JSONResponse(
            {"error": "agent timeout or not connected"}, status_code=504
        )

    return JSONResponse({"records": resp.records})


@router.get("/api/rec/detail/{filename:path}")
async def api_rec_detail(
    request: Request,
    filename: str,
    agent_id: str = "",
) -> JSONResponse:
    state = _state(request)
    tcp_server = state.tcp_server
    session_mgr = state.session_mgr

    if tcp_server is None or session_mgr is None:
        return JSONResponse({"error": "server not configured"}, status_code=503)

    if not agent_id:
        sessions = session_mgr.get_all_sessions()
        if not sessions:
            return JSONResponse({"error": "no agent connected"}, status_code=503)
        agent_id = sessions[0].agent_info.agent_id

    resp = await tcp_server.send_rec_data_req(
        agent_id=agent_id,
        query_type="detail",
        filename=filename,
    )
    if resp is None:
        return JSONResponse(
            {"error": "agent timeout or not connected"}, status_code=504
        )

    if not resp.records:
        return JSONResponse({"error": "not found"}, status_code=404)

    return JSONResponse({"record": resp.records[0]})


@router.post("/api/rec/analyze")
async def api_rec_analyze(request: Request) -> JSONResponse:
    """녹취 분석 시작/중지. JSON body: {agent_id, action, date?}."""
    state = _state(request)
    tcp_server = state.tcp_server
    session_mgr = state.session_mgr

    if tcp_server is None or session_mgr is None:
        return JSONResponse({"error": "server not configured"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    agent_id: str = body.get("agent_id", "")
    action_str: str = body.get("action", "")
    date_str: str = body.get("date", "")

    if action_str not in ("start", "stop"):
        return JSONResponse(
            {"error": "action must be 'start' or 'stop'"}, status_code=400
        )

    if not agent_id:
        sessions = session_mgr.get_all_sessions()
        if not sessions:
            return JSONResponse({"error": "no agent connected"}, status_code=503)
        agent_id = sessions[0].agent_info.agent_id

    if session_mgr.get_session(agent_id) is None:
        return JSONResponse(
            {"error": f"agent '{agent_id}' not connected"}, status_code=404
        )

    if date_str and (len(date_str) != 8 or not date_str.isdigit()):
        return JSONResponse({"error": "date must be YYYYMMDD format"}, status_code=400)

    action = RecAction.START if action_str == "start" else RecAction.STOP
    success = await tcp_server.send_rec_command(agent_id, action, date_str)

    if success:
        logger.info(
            "rec analyze %s: agent_id=%s date=%s",
            action_str,
            agent_id,
            date_str or "(today)",
        )
        return JSONResponse(
            {
                "status": "ok",
                "agent_id": agent_id,
                "action": action_str,
                "date": date_str,
            }
        )
    return JSONResponse(
        {"error": f"failed to send command to {agent_id}"}, status_code=502
    )
