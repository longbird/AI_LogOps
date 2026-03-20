"""파일 관리 API — 에이전트 디렉토리 탐색 + 양방향 파일 전송."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol, cast

from fastapi import APIRouter
from fastapi.requests import Request
from fastapi.responses import JSONResponse

router = APIRouter()


class _TCPServerLike(Protocol):
    async def send_file_list(self, agent_id: str, path: str) -> dict: ...
    async def send_file_get(
        self, agent_id: str, remote_path: str, save_path: str
    ) -> dict: ...
    async def send_file_put(
        self, agent_id: str, local_path: str, remote_path: str
    ) -> dict: ...
    async def send_file_run(self, agent_id: str, file_path: str) -> dict: ...


class _AppState(Protocol):
    tcp_server: Any


def _tcp(request: Request) -> _TCPServerLike | None:
    return cast(_AppState, request.app.state).tcp_server


@router.get("/api/files/server/list")
async def list_server_files(request: Request) -> JSONResponse:
    """서버 로컬 디렉토리 목록 반환."""
    path = request.query_params.get("path", ".")
    try:
        target = Path(path).resolve()
        if not target.is_dir():
            return JSONResponse({"success": False, "error": "디렉토리가 아닙니다"})
        entries = []
        with os.scandir(str(target)) as it:
            for entry in it:
                try:
                    stat = entry.stat()
                    entries.append({
                        "name": entry.name,
                        "is_dir": entry.is_dir(),
                        "size": stat.st_size if not entry.is_dir() else 0,
                        "modified": stat.st_mtime,
                    })
                except (PermissionError, OSError):
                    continue
        return JSONResponse({
            "success": True,
            "current_path": str(target),
            "entries": entries,
        })
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)})


@router.get("/api/files/agent/{agent_id}/list")
async def list_agent_files(request: Request, agent_id: str) -> JSONResponse:
    """에이전트 디렉토리 목록 — TCP 프록시."""
    path = request.query_params.get("path", "C:/")
    tcp = _tcp(request)
    if tcp is None:
        return JSONResponse({"success": False, "error": "서버가 실행 중이 아닙니다"})
    result = await tcp.send_file_list(agent_id, path)
    return JSONResponse(result)


@router.post("/api/files/transfer")
async def transfer_file(request: Request) -> JSONResponse:
    """양방향 파일 전송 (to_agent / to_server)."""
    tcp = _tcp(request)
    if tcp is None:
        return JSONResponse({"success": False, "error": "서버가 실행 중이 아닙니다"})

    body = await request.json()
    direction = body.get("direction")
    agent_id = body.get("agent_id")
    local_path = body.get("local_path")
    remote_path = body.get("remote_path")

    if direction == "to_agent":
        result = await tcp.send_file_put(agent_id, local_path, remote_path)
    elif direction == "to_server":
        result = await tcp.send_file_get(agent_id, remote_path, local_path)
    else:
        result = {"success": False, "error": f"잘못된 direction: {direction}"}

    return JSONResponse(result)


@router.post("/api/files/agent/{agent_id}/run")
async def run_agent_file(request: Request, agent_id: str) -> JSONResponse:
    """에이전트 PC에서 파일 실행."""
    tcp = _tcp(request)
    if tcp is None:
        return JSONResponse({"success": False, "error": "서버가 실행 중이 아닙니다"})
    body = await request.json()
    file_path = body.get("file_path", "")
    if not file_path:
        return JSONResponse({"success": False, "error": "file_path가 비어있습니다"})
    result = await tcp.send_file_run(agent_id, file_path)
    return JSONResponse(result)
