from __future__ import annotations

import asyncio
from typing import Any, Protocol, cast

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from shared.protocol import RecAction
from shared.utils import setup_logging

logger = setup_logging("dashboard.recordings")

router = APIRouter()


class _TCPServerLike(Protocol):
    @property
    def rec_max_concurrent(self) -> int: ...

    async def set_rec_max_concurrent(self, value: int) -> None: ...

    @property
    def stt_engine(self) -> str: ...

    @property
    def openai_prompt(self) -> str: ...

    async def set_stt_engine(self, engine: str) -> None: ...

    async def set_openai_prompt(self, prompt: str) -> None: ...

    async def send_rec_data_req(
        self,
        agent_id: str,
        query_type: str,
        date_str: str = ...,
        filename: str = ...,
        search: str = ...,
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


def _ws_state(websocket: WebSocket) -> _DashState:
    return cast(_DashState, websocket.app.state)


@router.get("/rec-viewer", response_class=HTMLResponse)
async def rec_viewer_page(request: Request) -> HTMLResponse:
    """녹취 조회 전용 페이지."""
    state = _state(request)
    return cast(
        HTMLResponse,
        state.templates.TemplateResponse(
            "rec_viewer.html",
            {"request": request, "title": "녹취 조회"},
        ),
    )


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


@router.get("/recordings/detail/{filename:path}", response_class=HTMLResponse)
async def recording_detail_page(request: Request, filename: str) -> HTMLResponse:
    """녹취 분석 상세 페이지."""
    state = _state(request)
    return cast(
        HTMLResponse,
        state.templates.TemplateResponse(
            "rec_detail.html",
            {"request": request, "title": "Recording Detail", "filename": filename},
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


@router.get("/api/rec/files")
async def api_rec_files(
    request: Request,
    agent_id: str = "",
    date: str = "",
    search: str = "",
) -> JSONResponse:
    """녹취 파일 검색 (rec_his 직접 조회, 분석 여부 무관)."""
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
        query_type="file_search",
        date_str=date,
        search=search,
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
    logger.info(
        "rec analyze request: agent_id=%s action=%s date=%s",
        agent_id,
        action_str,
        date_str or "(today)",
    )
    success = await tcp_server.send_rec_command(agent_id, action, date_str)
    logger.info("rec analyze send result: success=%s agent_id=%s", success, agent_id)

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


@router.get("/api/rec/max-concurrent")
async def api_rec_max_concurrent_get(request: Request) -> JSONResponse:
    """현재 최대 동시 분석 건수 조회."""
    state = _state(request)
    tcp_server = state.tcp_server
    if tcp_server is None:
        return JSONResponse({"error": "server not configured"}, status_code=503)
    return JSONResponse({"max_concurrent": tcp_server.rec_max_concurrent})


@router.post("/api/rec/max-concurrent")
async def api_rec_max_concurrent_set(request: Request) -> JSONResponse:
    """최대 동시 분석 건수 변경. JSON body: {value: int}."""
    state = _state(request)
    tcp_server = state.tcp_server
    if tcp_server is None:
        return JSONResponse({"error": "server not configured"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    value = body.get("value")
    if not isinstance(value, int) or value < 1 or value > 10:
        return JSONResponse(
            {"error": "value must be integer between 1 and 10"}, status_code=400
        )

    await tcp_server.set_rec_max_concurrent(value)
    logger.info("max_concurrent changed to %d via API", value)
    return JSONResponse(
        {"status": "ok", "max_concurrent": tcp_server.rec_max_concurrent}
    )


@router.get("/api/rec/stt-engine")
async def api_rec_stt_engine_get(request: Request) -> JSONResponse:
    """현재 STT 엔진 및 OpenAI 프롬프트 조회."""
    state = _state(request)
    tcp_server = state.tcp_server
    if tcp_server is None:
        return JSONResponse({"error": "server not configured"}, status_code=503)
    return JSONResponse({
        "stt_engine": tcp_server.stt_engine,
        "openai_prompt": tcp_server.openai_prompt,
    })


@router.post("/api/rec/stt-engine")
async def api_rec_stt_engine_set(request: Request) -> JSONResponse:
    """STT 엔진 변경. JSON body: {engine: str, prompt?: str}."""
    state = _state(request)
    tcp_server = state.tcp_server
    if tcp_server is None:
        return JSONResponse({"error": "server not configured"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    engine = body.get("engine", "")
    valid_engines = ("local", "openai-whisper", "openai-gpt4o", "openai-diarize", "rtzr")
    if engine not in valid_engines:
        return JSONResponse(
            {"error": f"engine must be one of {valid_engines}"}, status_code=400
        )

    await tcp_server.set_stt_engine(engine)

    prompt = body.get("prompt")
    if prompt is not None:
        await tcp_server.set_openai_prompt(str(prompt))

    logger.info("stt_engine changed to %s via API", engine)
    return JSONResponse({
        "status": "ok",
        "stt_engine": tcp_server.stt_engine,
        "openai_prompt": tcp_server.openai_prompt,
    })


@router.websocket("/ws/rec/status/{agent_id}")
async def websocket_rec_status(websocket: WebSocket, agent_id: str) -> None:
    """WebSocket: 녹취 분석 진행상황 실시간 스트리밍."""
    await websocket.accept()
    session_mgr = _ws_state(websocket).session_mgr

    try:
        last_index = 0
        if session_mgr:
            session = session_mgr.get_session(agent_id)
            if session:
                last_index = len(session.rec_log_buffer)

        while True:
            await asyncio.sleep(0.5)
            if session_mgr is None:
                continue
            session = session_mgr.get_session(agent_id)
            if session is None:
                continue
            current_len = len(session.rec_log_buffer)
            if current_len > last_index:
                new_lines = session.rec_log_buffer[last_index:current_len]
                for line in new_lines:
                    await websocket.send_text(line)
                last_index = current_len
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
