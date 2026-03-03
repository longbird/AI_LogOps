from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, cast

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from shared.models import AgentSession
from shared.utils import setup_logging

logger = setup_logging("dashboard.deploys")

router = APIRouter()

DEPLOY_DIR = Path("storage/deploys")


class _TCPServerLike(Protocol):
    async def send_deploy(
        self, agent_id: str, file_path: str, deploy_target: str = "agent"
    ) -> bool: ...

    def get_deploy_result_future(self, agent_id: str) -> asyncio.Future[Any] | None: ...

    async def send_ctrl_command(
        self, agent_id: str, action: Any, target: int = ...,
    ) -> bool: ...


class SessionManagerLike(Protocol):
    def get_all_sessions(self) -> list[AgentSession]: ...

    def get_session(self, agent_id: str) -> Any | None: ...


class DashboardState(Protocol):
    templates: Jinja2Templates
    session_mgr: SessionManagerLike | None
    tcp_server: _TCPServerLike | None


def _state(request: Request) -> DashboardState:
    return cast(DashboardState, request.app.state)  # pyright: ignore[reportAny]


@router.get("/deploys", response_class=HTMLResponse)
async def deploys_page(request: Request) -> HTMLResponse:
    """배포 관리 페이지."""
    state = _state(request)
    templates = state.templates
    return cast(
        HTMLResponse,
        templates.TemplateResponse(
            "deploys.html",
            {"request": request, "title": "배포"},
        ),
    )


# ── Dashboard Deploy API (cookie auth) ──


@router.get("/api/dashboard/deploy/agents")
async def api_deploy_agents(request: Request) -> JSONResponse:
    """연결된 에이전트 목록 조회."""
    state = _state(request)
    session_mgr = state.session_mgr
    if session_mgr is None:
        return JSONResponse({"agents": []})

    agents = []
    for s in session_mgr.get_all_sessions():
        info = s.agent_info
        agents.append(
            {
                "agent_id": info.agent_id,
                "version": info.version,
            }
        )
    return JSONResponse({"agents": agents})


@router.post("/api/dashboard/deploy/upload")
async def api_deploy_upload(
    request: Request,
    file: UploadFile = File(...),
) -> JSONResponse:
    """대시보드에서 배포 파일 업로드 + 에이전트 전송."""
    state = _state(request)
    tcp_server = state.tcp_server
    session_mgr = state.session_mgr

    if tcp_server is None or session_mgr is None:
        return JSONResponse({"error": "서버가 구성되지 않았습니다"}, status_code=503)

    form = await request.form()
    agent_id = str(form.get("agent_id", "")).strip()
    target = str(form.get("target", "agent")).strip()

    if target not in ("agent", "process", "rec_client"):
        return JSONResponse(
            {"error": "target은 agent, process, rec_client 중 하나여야 합니다"},
            status_code=400,
        )

    if not agent_id:
        sessions = session_mgr.get_all_sessions()
        if not sessions:
            return JSONResponse(
                {"error": "연결된 에이전트가 없습니다"}, status_code=503
            )
        agent_id = sessions[0].agent_info.agent_id
    else:
        if session_mgr.get_session(agent_id) is None:
            return JSONResponse(
                {"error": f"에이전트 '{agent_id}'가 연결되지 않았습니다"},
                status_code=404,
            )

    DEPLOY_DIR.mkdir(parents=True, exist_ok=True)
    deploy_id = uuid.uuid4().hex[:12]
    filename = file.filename or "deploy.zip"
    temp_path = DEPLOY_DIR / f"{deploy_id}_{filename}"

    content = await file.read()
    if len(content) == 0:
        return JSONResponse({"error": "빈 파일입니다"}, status_code=400)

    temp_path.write_bytes(content)
    size_mb = len(content) / (1024 * 1024)
    logger.info(
        "web deploy: id=%s agent=%s target=%s file=%s size=%.1fMB",
        deploy_id,
        agent_id,
        target,
        filename,
        size_mb,
    )

    deploy_target = "process" if target == "process" else "agent"

    async def _push() -> None:
        try:
            ok = await tcp_server.send_deploy(
                agent_id, str(temp_path), deploy_target=deploy_target
            )
            if ok:
                logger.info("deploy done: %s → %s", deploy_id, agent_id)
                future = tcp_server.get_deploy_result_future(agent_id)
                if future is not None:
                    try:
                        ack = await asyncio.wait_for(future, timeout=120)
                        logger.info(
                            "deploy ack: %s status=%s", deploy_id, ack.status.name
                        )
                    except asyncio.TimeoutError:
                        logger.warning("deploy ack timeout: %s", deploy_id)
            else:
                logger.error("deploy failed: %s → %s", deploy_id, agent_id)
        except Exception:
            logger.exception("deploy error: %s", deploy_id)
        finally:
            await asyncio.sleep(10)
            temp_path.unlink(missing_ok=True)

    _ = asyncio.create_task(_push())

    return JSONResponse(
        {
            "status": "ok",
            "deploy_id": deploy_id,
            "agent_id": agent_id,
            "target": target,
            "file": filename,
            "size_mb": round(size_mb, 1),
        }
    )


@router.get("/api/dashboard/deploy/history")
async def api_deploy_history(request: Request) -> JSONResponse:
    """배포 이력 조회."""
    state = _state(request)
    session_mgr = state.session_mgr
    deploys = _get_deploy_history(session_mgr)
    return JSONResponse({"deploys": deploys})


def _get_deploy_history(
    session_mgr: SessionManagerLike | None,
) -> list[dict[str, object]]:
    """모든 에이전트의 배포 이력 수집."""
    if session_mgr is None:
        return []

    deploys: list[dict[str, object]] = []
    for session in session_mgr.get_all_sessions():
        for deploy in session.deploy_history:
            timestamp = deploy.get("timestamp", 0)
            deploys.append(
                {
                    "agent_id": session.agent_info.agent_id,
                    **deploy,
                    "timestamp_str": _format_timestamp(timestamp),
                }
            )

    deploys.sort(key=lambda d: _sort_timestamp(d.get("timestamp")), reverse=True)
    return deploys


def _format_timestamp(raw_timestamp: object) -> str:
    if not isinstance(raw_timestamp, (int, float)):
        return "-"
    return datetime.fromtimestamp(raw_timestamp).strftime("%Y-%m-%d %H:%M:%S")


def _sort_timestamp(raw_timestamp: object) -> float:
    if not isinstance(raw_timestamp, (int, float)):
        return 0.0
    return float(raw_timestamp)


# ── Control API ──


@router.post("/api/ctrl/restart")
async def api_ctrl_restart(request: Request) -> JSONResponse:
    """에이전트에 CMD_CTRL RESTART 전송."""
    state = _state(request)
    tcp_server = state.tcp_server
    session_mgr = state.session_mgr

    if tcp_server is None:
        return JSONResponse({"error": "서버가 실행 중이 아닙니다"}, status_code=503)

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    agent_id: str = body.get("agent_id", "")
    target: str = body.get("target", "agent")

    # agent_id 미지정 시 첫 번째 연결된 에이전트 사용
    if not agent_id and session_mgr is not None:
        sessions = session_mgr.get_all_sessions()
        if sessions:
            agent_id = sessions[0].agent_info.agent_id

    if not agent_id:
        return JSONResponse({"error": "에이전트가 연결되어 있지 않습니다"}, status_code=404)

    from shared.protocol import CtrlAction, DeployTarget

    target_map = {"agent": DeployTarget.AGENT, "process": DeployTarget.PROCESS, "rec_client": DeployTarget.REC_CLIENT}
    target_int = target_map.get(target, DeployTarget.PROCESS)

    success = await tcp_server.send_ctrl_command(agent_id, CtrlAction.RESTART, target=target_int)
    if success:
        logger.info("restart command sent: agent=%s target=%s(%d)", agent_id, target, target_int)
        return JSONResponse({
            "status": "ok",
            "agent_id": agent_id,
            "target": target,
        })
    return JSONResponse(
        {"error": f"에이전트 {agent_id}에 명령 전송 실패"}, status_code=502
    )
