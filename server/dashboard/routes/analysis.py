from __future__ import annotations

from typing import Any, Protocol, cast

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


class _TCPServerLike(Protocol):
    _monitor_states: dict[str, dict[int, Any]]


def _tcp_server(request: Request) -> _TCPServerLike | None:
    return cast(_TCPServerLike | None, request.app.state.tcp_server)  # pyright: ignore[reportAny]


@router.get("/api/analysis/{agent_id}")
async def get_analysis(
    request: Request, agent_id: str, folder: int = 0
) -> JSONResponse:
    """실시간 로그 분석 결과 반환. ?folder=N 으로 폴더 선택 (기본값 0)."""
    tcp = _tcp_server(request)
    if tcp is None:
        return JSONResponse({"error": "server not configured"}, status_code=503)

    folder_states = tcp._monitor_states.get(agent_id, {})
    monitor_state = folder_states.get(folder)
    if monitor_state is None:
        return JSONResponse({"status": "no_data"})
    return JSONResponse(monitor_state.to_dict())


@router.get("/api/analysis/{agent_id}/folders")
async def get_analysis_folders(request: Request, agent_id: str) -> JSONResponse:
    """해당 에이전트의 분석 데이터가 존재하는 폴더 인덱스 목록 반환."""
    tcp = _tcp_server(request)
    if tcp is None:
        return JSONResponse({"error": "server not configured"}, status_code=503)

    folder_states = tcp._monitor_states.get(agent_id, {})
    return JSONResponse({"folders": sorted(folder_states.keys())})


@router.post("/api/analysis/{agent_id}/reset")
async def reset_analysis(request: Request, agent_id: str) -> JSONResponse:
    """폴더 분석 상태 초기화. body: {\"folder_index\": N}."""
    tcp = _tcp_server(request)
    if tcp is None:
        return JSONResponse({"error": "server not configured"}, status_code=503)

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    folder_index: int = int(body.get("folder_index", 0))

    from server.analysis.monitor_state import MonitorState

    if agent_id not in tcp._monitor_states:
        tcp._monitor_states[agent_id] = {}
    tcp._monitor_states[agent_id][folder_index] = MonitorState()

    return JSONResponse({"status": "ok", "agent_id": agent_id, "folder_index": folder_index})
