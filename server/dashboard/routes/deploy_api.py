from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any, Protocol, cast

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from shared.utils import setup_logging

logger = setup_logging("dashboard.deploy_api")

router = APIRouter()


class _TCPServerLike(Protocol):
    auth_token: str

    async def send_deploy(
        self,
        agent_id: str,
        file_path: str,
        deploy_target: str = "agent",
        original_filename: str = "",
    ) -> bool: ...

    def get_deploy_result_future(self, agent_id: str) -> asyncio.Future[Any] | None: ...


class _SessionMgrLike(Protocol):
    def get_all_sessions(self) -> list[Any]: ...

    def get_session(self, agent_id: str) -> Any | None: ...


class _DashState(Protocol):
    tcp_server: _TCPServerLike | None
    session_mgr: _SessionMgrLike | None


def _state(request: Request) -> _DashState:
    return cast(_DashState, request.app.state)


def _verify_api_token(request: Request, authorization: str) -> None:
    """API token 검증. TCP auth_token과 동일한 Bearer 토큰."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401, detail="Missing or invalid Authorization header"
        )
    token = authorization[7:]
    tcp_server = getattr(request.app.state, "tcp_server", None)
    if tcp_server is None:
        raise HTTPException(status_code=503, detail="Server not configured")
    if token != getattr(tcp_server, "auth_token", ""):
        raise HTTPException(status_code=401, detail="Invalid API token")


DEPLOY_DIR = Path("storage/deploys")


@router.post("/api/deploy/upload")
async def upload_deploy(
    request: Request,
    file: UploadFile = File(...),
    agent_id: str = Form(""),
    target: str = Form(""),
    authorization: str = Header(""),
) -> JSONResponse:
    """deploy.py에서 zip 업로드 → TCP로 에이전트에 배포.

    Headers:
        Authorization: Bearer <tcp_auth_token>
    Form:
        file: zip 파일
        agent_id: 대상 에이전트 (비어있으면 첫 번째 연결된 에이전트)
        target: 배포 대상 ("agent"|"process"|"rec_client", 기본값: "agent")
    """
    _verify_api_token(request, authorization)

    if target not in ("", "agent", "process", "rec_client"):
        return JSONResponse(
            {"error": "invalid target, must be 'agent', 'process' or 'rec_client'"},
            status_code=400,
        )

    deploy_target = "process" if target == "process" else "agent"

    state = _state(request)
    tcp_server = state.tcp_server
    session_mgr = state.session_mgr

    if tcp_server is None or session_mgr is None:
        return JSONResponse({"error": "server not configured"}, status_code=503)

    # 대상 에이전트 결정
    if not agent_id:
        sessions = session_mgr.get_all_sessions()
        if not sessions:
            return JSONResponse({"error": "no agent connected"}, status_code=503)
        agent_id = sessions[0].agent_info.agent_id  # type: ignore[union-attr]
    else:
        if session_mgr.get_session(agent_id) is None:
            return JSONResponse(
                {"error": f"agent '{agent_id}' not connected"}, status_code=404
            )

    # 임시 파일 저장
    DEPLOY_DIR.mkdir(parents=True, exist_ok=True)
    deploy_id = uuid.uuid4().hex[:12]
    filename = file.filename or "AILogOps-Agent.zip"
    temp_path = DEPLOY_DIR / f"{deploy_id}_{filename}"

    content = await file.read()
    temp_path.write_bytes(content)
    size_mb = len(content) / (1024 * 1024)
    logger.info(
        "deploy uploaded: deploy_id=%s agent_id=%s file=%s size=%.1f MB",
        deploy_id,
        agent_id,
        filename,
        size_mb,
    )

    # TCP로 에이전트에 비동기 전송 + 에이전트 응답 대기
    async def _push_and_cleanup() -> None:
        try:
            ok = await tcp_server.send_deploy(
                agent_id,
                str(temp_path),
                deploy_target=deploy_target,
                original_filename=filename,
            )
            if not ok:
                logger.error(
                    "deploy push failed: deploy_id=%s agent_id=%s", deploy_id, agent_id
                )
                return

            logger.info(
                "deploy transfer done, waiting for agent ack: deploy_id=%s agent_id=%s",
                deploy_id,
                agent_id,
            )

            # 에이전트의 DEPLOY_VERIFIED/DEPLOY_ROLLBACK 응답 대기 (최대 120초)
            future = tcp_server.get_deploy_result_future(agent_id)
            if future is not None:
                try:
                    ack = await asyncio.wait_for(future, timeout=120)
                    logger.info(
                        "deploy result: deploy_id=%s agent_id=%s status=%s",
                        deploy_id,
                        agent_id,
                        ack.status.name,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "deploy ack timeout (120s): deploy_id=%s agent_id=%s",
                        deploy_id,
                        agent_id,
                    )
        except Exception:
            logger.exception("deploy push error: deploy_id=%s", deploy_id)
        finally:
            # 전송 완료 후 정리 (10초 대기)
            await asyncio.sleep(10)
            temp_path.unlink(missing_ok=True)

    _ = asyncio.create_task(_push_and_cleanup())

    return JSONResponse(
        {
            "status": "ok",
            "deploy_id": deploy_id,
            "agent_id": agent_id,
            "target": deploy_target,
            "file": filename,
            "size_mb": round(size_mb, 1),
            "message": f"Deploy started for agent '{agent_id}'. Transfer in progress.",
        }
    )


@router.get("/api/deploy/status")
async def deploy_status(
    request: Request,
    authorization: str = Header(""),
) -> JSONResponse:
    """연결된 에이전트 목록 및 배포 가능 상태 확인."""
    _verify_api_token(request, authorization)

    state = _state(request)
    session_mgr = state.session_mgr
    if session_mgr is None:
        return JSONResponse({"error": "server not configured"}, status_code=503)

    sessions = session_mgr.get_all_sessions()
    now = time.time()
    agents = []
    for s in sessions:
        info = s.agent_info  # type: ignore[union-attr]
        hb_ago = int(now - s.last_heartbeat)
        agents.append(
            {
                "agent_id": info.agent_id,
                "version": info.version,
                "state": info.state.value,
                "connected": True,
                "last_heartbeat_ago": (f"{hb_ago}s ago" if hb_ago < 300 else "offline"),
                "process_status": s.process_status,
            }
        )

    return JSONResponse({"agents": agents})
