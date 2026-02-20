from __future__ import annotations

from typing import Any, Protocol, cast

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from shared.utils import setup_logging

logger = setup_logging("dashboard.recordings")

router = APIRouter()


class _TCPServerLike(Protocol):
    async def send_rec_data_req(
        self,
        agent_id: str,
        query_type: str,
        date_str: str = ...,
        rec_no: int = ...,
        timeout: float = ...,
    ) -> Any | None: ...


class _SessionMgrLike(Protocol):
    def get_all_sessions(self) -> list[Any]: ...


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


@router.get("/api/rec/detail/{rec_no}")
async def api_rec_detail(
    request: Request,
    rec_no: int,
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
        rec_no=rec_no,
    )
    if resp is None:
        return JSONResponse(
            {"error": "agent timeout or not connected"}, status_code=504
        )

    if not resp.records:
        return JSONResponse({"error": "not found"}, status_code=404)

    return JSONResponse({"record": resp.records[0]})
