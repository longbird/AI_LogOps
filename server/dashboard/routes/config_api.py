"""에이전트 설정 원격 관리 API."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol, cast

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from shared.models import AgentSession
from shared.protocol import ConfigAction
from shared.utils import setup_logging

logger = setup_logging("dashboard.config_api")

router = APIRouter()


class _TCPServerLike(Protocol):
    async def send_config_command(
        self, agent_id: str, action: ConfigAction, config_data: str = ""
    ) -> bool: ...

    def get_config_future(self, agent_id: str) -> asyncio.Future[dict]: ...


class _SessionMgrLike(Protocol):
    def get_all_sessions(self) -> list[AgentSession]: ...
    def get_session(self, agent_id: str) -> AgentSession | None: ...


class _AppState(Protocol):
    session_mgr: _SessionMgrLike | None
    tcp_server: _TCPServerLike | None


def _state(request: Request) -> _AppState:
    return cast(_AppState, request.app.state)


@router.get("/api/config/{agent_id}")
async def get_agent_config(request: Request, agent_id: str) -> JSONResponse:
    """에이전트 설정을 가져옵니다. TCP CMD_CONFIG GET 전송 후 응답 대기."""
    state = _state(request)
    if state.tcp_server is None or state.session_mgr is None:
        return JSONResponse({"error": "서버가 실행 중이 아닙니다"}, status_code=503)

    session = state.session_mgr.get_session(agent_id)
    if session is None:
        return JSONResponse({"error": f"에이전트 '{agent_id}'를 찾을 수 없습니다"}, status_code=404)

    # Clear previous config data
    session.config_data = None

    # Create future and send command
    fut = state.tcp_server.get_config_future(agent_id)
    sent = await state.tcp_server.send_config_command(agent_id, ConfigAction.GET)
    if not sent:
        return JSONResponse({"error": "설정 요청 전송 실패"}, status_code=500)

    # Wait for response with timeout
    try:
        result = await asyncio.wait_for(fut, timeout=10.0)
        return JSONResponse({"config": result})
    except asyncio.TimeoutError:
        return JSONResponse({"error": "에이전트 응답 시간 초과 (10초)"}, status_code=504)


@router.put("/api/config/{agent_id}")
async def update_agent_config(request: Request, agent_id: str) -> JSONResponse:
    """에이전트 설정을 업데이트합니다."""
    state = _state(request)
    if state.tcp_server is None or state.session_mgr is None:
        return JSONResponse({"error": "서버가 실행 중이 아닙니다"}, status_code=503)

    session = state.session_mgr.get_session(agent_id)
    if session is None:
        return JSONResponse({"error": f"에이전트 '{agent_id}'를 찾을 수 없습니다"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "잘못된 JSON 본문"}, status_code=400)

    config_data = body.get("config")
    if config_data is None:
        return JSONResponse({"error": "'config' 필드가 필요합니다"}, status_code=400)

    # Clear previous result
    session.config_update_result = None

    config_json = json.dumps(config_data, ensure_ascii=False, default=str)

    fut = state.tcp_server.get_config_future(agent_id)
    sent = await state.tcp_server.send_config_command(
        agent_id, ConfigAction.UPDATE, config_json
    )
    if not sent:
        return JSONResponse({"error": "설정 업데이트 전송 실패"}, status_code=500)

    # Wait for ACK
    try:
        result = await asyncio.wait_for(fut, timeout=15.0)
        status_code = 200 if result.get("success") else 500
        return JSONResponse(result, status_code=status_code)
    except asyncio.TimeoutError:
        return JSONResponse({"error": "에이전트 응답 시간 초과 (15초)"}, status_code=504)
