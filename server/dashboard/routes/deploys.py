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
from shared.protocol import ConfigAction
from shared.utils import setup_logging

logger = setup_logging("dashboard.deploys")

router = APIRouter()

DEPLOY_DIR = Path("storage/deploys")


class _TCPServerLike(Protocol):
    async def send_deploy(
        self,
        agent_id: str,
        file_path: str,
        deploy_target: str = "agent",
        original_filename: str = "",
        deploy_path: str = "",
    ) -> bool: ...

    def get_deploy_result_future(self, agent_id: str) -> asyncio.Future[Any] | None: ...

    async def send_ctrl_command(
        self,
        agent_id: str,
        action: Any,
        target: int = ...,
        target_name: str = "",
    ) -> bool: ...

    async def send_config_command(
        self,
        agent_id: str,
        action: Any,
        config_data: str = "",
    ) -> bool: ...

    def get_config_future(self, agent_id: str) -> asyncio.Future[Any]: ...

    async def send_exec_command(
        self,
        agent_id: str,
        command_name: str,
    ) -> bool: ...

    def get_exec_future(self, agent_id: str) -> asyncio.Future[Any]: ...


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
    session_mgr = state.session_mgr
    agents: list[str] = []
    if session_mgr:
        agents = [s.agent_info.agent_id for s in session_mgr.get_all_sessions()]
    return cast(
        HTMLResponse,
        templates.TemplateResponse(
            "deploys.html",
            {"request": request, "title": "배포", "agents": agents},
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


@router.get("/api/dashboard/agent/{agent_id}/watch-folders")
async def api_agent_watch_folders(request: Request, agent_id: str) -> JSONResponse:
    """에이전트의 감시 폴더 목록 조회 (CMD_CONFIG GET 경유)."""
    state = _state(request)
    tcp_server = state.tcp_server
    if tcp_server is None:
        return JSONResponse({"error": "서버가 실행 중이 아닙니다"}, status_code=503)

    fut = tcp_server.get_config_future(agent_id)
    ok = await tcp_server.send_config_command(agent_id, ConfigAction.GET)
    if not ok:
        return JSONResponse({"error": f"에이전트 '{agent_id}'에 연결할 수 없습니다"}, status_code=404)

    try:
        config_data = await asyncio.wait_for(fut, timeout=10)
    except asyncio.TimeoutError:
        return JSONResponse({"error": "에이전트 응답 타임아웃"}, status_code=504)

    watch_folders = config_data.get("watch_folders", [])
    remote_commands = config_data.get("remote_commands", [])
    safe_commands = []
    for cmd in remote_commands:
        if isinstance(cmd, dict) and cmd.get("name"):
            safe_commands.append({
                "name": cmd.get("name", ""),
                "description": cmd.get("description", ""),
            })

    return JSONResponse({
        "agent_id": agent_id,
        "watch_folders": watch_folders,
        "remote_commands": safe_commands,
    })


@router.post("/api/dashboard/agent/{agent_id}/exec")
async def api_agent_exec(request: Request, agent_id: str) -> JSONResponse:
    """에이전트에서 원격 커맨드 실행."""
    state = _state(request)
    tcp_server = state.tcp_server

    if tcp_server is None:
        return JSONResponse({"error": "서버가 실행 중이 아닙니다"}, status_code=503)

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    command_name = body.get("command_name", "")
    if not command_name:
        return JSONResponse({"error": "command_name이 필요합니다"}, status_code=400)

    fut = tcp_server.get_exec_future(agent_id)
    ok = await tcp_server.send_exec_command(agent_id, command_name)
    if not ok:
        return JSONResponse({"error": f"에이전트 '{agent_id}'에 연결할 수 없습니다"}, status_code=404)

    try:
        ack = await asyncio.wait_for(fut, timeout=120)
    except asyncio.TimeoutError:
        return JSONResponse({"error": "커맨드 실행 타임아웃 (120초)"}, status_code=504)

    return JSONResponse({
        "command_name": ack.command_name,
        "success": ack.success,
        "exit_code": ack.exit_code,
        "output": ack.output,
        "error": ack.error,
    })


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
    deploy_path = str(form.get("deploy_path", "")).strip()

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
                agent_id,
                str(temp_path),
                deploy_target=deploy_target,
                original_filename=filename,
                deploy_path=deploy_path,
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


@router.post("/api/dashboard/deploy/upload-folder")
async def api_deploy_upload_folder(request: Request) -> JSONResponse:
    """폴더 배포: 여러 파일을 수신하여 ZIP으로 압축 후 에이전트 전송."""
    import zipfile

    state = _state(request)
    tcp_server = state.tcp_server
    session_mgr = state.session_mgr

    if tcp_server is None or session_mgr is None:
        return JSONResponse({"error": "서버가 구성되지 않았습니다"}, status_code=503)

    form = await request.form()
    agent_id = str(form.get("agent_id", "")).strip()
    target = str(form.get("target", "process")).strip()
    deploy_path = str(form.get("deploy_path", "")).strip()

    if target not in ("agent", "process", "rec_client"):
        return JSONResponse(
            {"error": "target은 agent, process, rec_client 중 하나여야 합니다"},
            status_code=400,
        )

    files = form.getlist("files")
    paths = form.getlist("paths")

    if not files:
        return JSONResponse({"error": "파일이 없습니다"}, status_code=400)

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

    first_path = str(paths[0]) if paths else "folder"
    folder_name = first_path.split("/")[0] if "/" in first_path else "folder"
    zip_filename = f"{folder_name}.zip"
    temp_path = DEPLOY_DIR / f"{deploy_id}_{zip_filename}"

    file_count = 0
    with zipfile.ZipFile(str(temp_path), "w", zipfile.ZIP_DEFLATED) as zf:
        for i, uploaded_file in enumerate(files):
            rel_path = str(paths[i]) if i < len(paths) else getattr(uploaded_file, "filename", "file")
            parts = rel_path.split("/")
            archive_name = "/".join(parts[1:]) if len(parts) > 1 else rel_path
            content = await uploaded_file.read()
            zf.writestr(archive_name, content)
            file_count += 1

    size_mb = temp_path.stat().st_size / (1024 * 1024)
    logger.info(
        "folder deploy: id=%s agent=%s target=%s folder=%s files=%d size=%.1fMB",
        deploy_id,
        agent_id,
        target,
        folder_name,
        file_count,
        size_mb,
    )

    deploy_target = "process" if target == "process" else "agent"

    async def _push() -> None:
        try:
            ok = await tcp_server.send_deploy(
                agent_id,
                str(temp_path),
                deploy_target=deploy_target,
                original_filename=zip_filename,
                deploy_path=deploy_path,
            )
            if ok:
                logger.info("folder deploy done: %s → %s", deploy_id, agent_id)
                future = tcp_server.get_deploy_result_future(agent_id)
                if future is not None:
                    try:
                        ack = await asyncio.wait_for(future, timeout=120)
                        logger.info(
                            "folder deploy ack: %s status=%s", deploy_id, ack.status.name
                        )
                    except asyncio.TimeoutError:
                        logger.warning("folder deploy ack timeout: %s", deploy_id)
            else:
                logger.error("folder deploy failed: %s → %s", deploy_id, agent_id)
        except Exception:
            logger.exception("folder deploy error: %s", deploy_id)
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
            "folder": folder_name,
            "file_count": file_count,
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
    target_name: str = body.get("target_name", "")

    # agent_id 미지정 시 첫 번째 연결된 에이전트 사용
    if not agent_id and session_mgr is not None:
        sessions = session_mgr.get_all_sessions()
        if sessions:
            agent_id = sessions[0].agent_info.agent_id

    if not agent_id:
        return JSONResponse(
            {"error": "에이전트가 연결되어 있지 않습니다"}, status_code=404
        )

    from shared.protocol import CtrlAction, DeployTarget

    target_map = {
        "agent": DeployTarget.AGENT,
        "process": DeployTarget.PROCESS,
        "rec_client": DeployTarget.REC_CLIENT,
    }
    target_int = target_map.get(target, DeployTarget.PROCESS)

    success = await tcp_server.send_ctrl_command(
        agent_id, CtrlAction.RESTART, target=target_int, target_name=target_name
    )
    if success:
        logger.info(
            "restart command sent: agent=%s target=%s(%d) target_name=%s",
            agent_id, target, target_int, target_name,
        )
        return JSONResponse(
            {
                "status": "ok",
                "agent_id": agent_id,
                "target": target,
                "target_name": target_name,
            }
        )
    return JSONResponse(
        {"error": f"에이전트 {agent_id}에 명령 전송 실패"}, status_code=502
    )
