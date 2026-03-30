"""테스트 배포 API — 기존 deploy/upload 파이프라인 경유."""

from __future__ import annotations

import asyncio
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Protocol, cast

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from shared.models import AgentSession
from shared.protocol import ConfigAction, CtrlAction, CtrlAckStatus, DeployTarget
from shared.utils import setup_logging

logger = setup_logging("dashboard.test_deploy_api")

router = APIRouter()


class _TCPServerLike(Protocol):
    async def send_ctrl_command_and_wait(
        self, agent_id: str, action: CtrlAction,
        target: int = ..., target_name: str = ...,
        timeout: float = ...,
    ) -> dict: ...
    async def send_config_command(
        self, agent_id: str, action: ConfigAction, config_data: str = ...,
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


def _resolve_agent(state: _AppState, agent_id: str) -> tuple[str, str | None]:
    if state.session_mgr is None:
        return "", "서버가 실행 중이 아닙니다"
    if not agent_id:
        sessions = state.session_mgr.get_all_sessions()
        connected = [s for s in sessions if s.agent_info.state.value == "CONNECTED"]
        if not connected:
            return "", "연결된 에이전트가 없습니다"
        agent_id = connected[0].agent_info.agent_id
    elif state.session_mgr.get_session(agent_id) is None:
        return "", f"에이전트 '{agent_id}'를 찾을 수 없습니다"
    return agent_id, None


@router.get("/api/test-deploy/info/{agent_id}")
async def test_deploy_info(request: Request, agent_id: str) -> JSONResponse:
    """에이전트의 target_process 정보 조회."""
    state = _state(request)
    tcp = state.tcp_server
    if tcp is None:
        return JSONResponse({"error": "서버 미실행"}, status_code=503)
    agent_id, err = _resolve_agent(state, agent_id)
    if err:
        return JSONResponse({"error": err}, status_code=404)
    fut = tcp.get_config_future(agent_id)
    sent = await tcp.send_config_command(agent_id, ConfigAction.GET)
    if not sent:
        return JSONResponse({"error": "설정 요청 전송 실패"}, status_code=500)
    try:
        config = await asyncio.wait_for(fut, timeout=10.0)
    except asyncio.TimeoutError:
        return JSONResponse({"error": "에이전트 응답 시간 초과"}, status_code=504)
    tp = config.get("target_process", {})
    if isinstance(tp, list):
        tp = tp[0] if tp else {}
    target_dir = str(Path(tp.get("path", "")).parent) if tp.get("path") else ""
    return JSONResponse({
        "agent_id": agent_id, "target_process": tp,
        "target_dir": target_dir, "process_name": tp.get("name", ""),
    })


@router.post("/api/test-deploy/execute")
async def test_deploy_execute(request: Request) -> JSONResponse:
    """테스트 배포: ZIP → /api/deploy/upload 경유 → RESTART.

    내부적으로 deploy/upload API와 동일한 파이프라인 사용 (검증됨).
    """
    state = _state(request)
    tcp = state.tcp_server
    if tcp is None:
        return JSONResponse({"error": "서버 미실행"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "잘못된 JSON"}, status_code=400)

    raw_agent_id = body.get("agent_id", "")
    files: list[dict] = body.get("files", [])
    target_name: str = body.get("target_name", "")

    if not files:
        return JSONResponse({"error": "배포 파일이 없습니다"}, status_code=400)
    agent_id, err = _resolve_agent(state, raw_agent_id)
    if err:
        return JSONResponse({"error": err}, status_code=404)

    for f in files:
        if not Path(f.get("local_path", "")).exists():
            return JSONResponse({"error": f"파일 없음: {f.get('local_path')}"}, status_code=400)

    # 1) ZIP 생성
    try:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip", prefix="td_")
        tmp.close()
        zip_path = tmp.name
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                zf.write(f["local_path"], f.get("filename", Path(f["local_path"]).name))
        zip_size = Path(zip_path).stat().st_size / (1024 * 1024)
        file_names = [f.get("filename", Path(f["local_path"]).name) for f in files]
    except Exception as e:
        return JSONResponse({"error": f"ZIP 생성 실패: {e}"}, status_code=500)

    steps: list[dict] = [
        {"step": "zip", "success": True, "files": file_names, "size_mb": round(zip_size, 2)},
    ]

    # 2) 내부적으로 deploy/upload와 동일한 방식으로 전송
    #    deploy_api.upload_deploy의 핵심 로직 재현: 파일 저장 → send_deploy 백그라운드
    from server.dashboard.routes.deploy_api import DEPLOY_DIR
    import uuid as _uuid

    deploy_id = _uuid.uuid4().hex[:12]
    temp_deploy = DEPLOY_DIR / f"td_{deploy_id}.zip"
    try:
        DEPLOY_DIR.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy2(zip_path, str(temp_deploy))
    finally:
        Path(zip_path).unlink(missing_ok=True)

    # 백그라운드 배포 + RESTART
    result_holder: dict[str, Any] = {}
    done_event = asyncio.Event()

    async def _deploy_and_restart() -> None:
        try:
            ok = await tcp.send_deploy(  # type: ignore[attr-defined]
                agent_id, str(temp_deploy),
                deploy_target="process",
                original_filename="test_deploy.zip",
                deploy_path=target_name,
            )
            if not ok:
                result_holder["error"] = "ZIP 전송 실패"
                return

            # 스테이징 ACK 대기
            deploy_fut = tcp.get_deploy_result_future(agent_id)  # type: ignore[attr-defined]
            if deploy_fut is not None:
                try:
                    ack = await asyncio.wait_for(deploy_fut, timeout=120)
                    if ack.status not in (CtrlAckStatus.SUCCESS, CtrlAckStatus.DEPLOY_VERIFIED):
                        result_holder["error"] = f"스테이징 실패: {ack.status.name}"
                        return
                except asyncio.TimeoutError:
                    result_holder["error"] = "스테이징 ACK 시간 초과"
                    return

            steps.append({"step": "transfer", "success": True, "size_mb": round(zip_size, 2)})

            # RESTART → ProcessDeployer: backup → stop → copy → start
            restart_result = await tcp.send_ctrl_command_and_wait(
                agent_id, CtrlAction.RESTART,
                target=DeployTarget.PROCESS, target_name=target_name,
                timeout=60.0,
            )
            steps.append({"step": "restart", **restart_result})
            result_holder["success"] = restart_result.get("success", False)
            result_holder["pid"] = restart_result.get("pid", 0)
        except Exception as exc:
            result_holder["error"] = str(exc)
            logger.exception("test-deploy error")
        finally:
            await asyncio.sleep(5)
            temp_deploy.unlink(missing_ok=True)
            done_event.set()

    asyncio.create_task(_deploy_and_restart())

    try:
        await asyncio.wait_for(done_event.wait(), timeout=180)
    except asyncio.TimeoutError:
        return JSONResponse({
            "success": False, "error": "전체 타임아웃 (180초)", "steps": steps,
        }, status_code=504)

    if "error" in result_holder:
        steps.append({"step": "error", "success": False, "error": result_holder["error"]})
        return JSONResponse({"success": False, "steps": steps}, status_code=500)

    return JSONResponse({
        "success": result_holder.get("success", False),
        "agent_id": agent_id, "files": file_names,
        "pid": result_holder.get("pid", 0), "steps": steps,
    })


@router.post("/api/test-deploy/restart-process")
async def test_deploy_restart(request: Request) -> JSONResponse:
    """스테이징된 파일로 프로세스 재시작 (ProcessDeployer 트리거)."""
    state = _state(request)
    tcp = state.tcp_server
    if tcp is None:
        return JSONResponse({"error": "서버 미실행"}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "잘못된 JSON"}, status_code=400)
    raw_agent_id = body.get("agent_id", "")
    target_name = body.get("target_name", "")
    agent_id, err = _resolve_agent(state, raw_agent_id)
    if err:
        return JSONResponse({"error": err}, status_code=404)
    result = await tcp.send_ctrl_command_and_wait(
        agent_id, CtrlAction.RESTART,
        target=DeployTarget.PROCESS, target_name=target_name,
        timeout=60.0,
    )
    return JSONResponse({
        "success": result.get("success", False),
        "result": result,
    })


@router.post("/api/test-deploy/rollback/{agent_id}")
async def test_deploy_rollback(request: Request, agent_id: str) -> JSONResponse:
    """수동 롤백."""
    state = _state(request)
    tcp = state.tcp_server
    if tcp is None:
        return JSONResponse({"error": "서버 미실행"}, status_code=503)
    agent_id, err = _resolve_agent(state, agent_id)
    if err:
        return JSONResponse({"error": err}, status_code=404)
    target_name = ""
    try:
        body = await request.json()
        target_name = body.get("target_name", "")
    except Exception:
        pass
    result = await tcp.send_ctrl_command_and_wait(
        agent_id, CtrlAction.ROLLBACK,
        target=DeployTarget.PROCESS, target_name=target_name, timeout=30.0,
    )
    return JSONResponse({"success": result.get("success", False), "result": result})
