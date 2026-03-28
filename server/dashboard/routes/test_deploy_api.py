"""테스트 배포 API — 파일 단위 배포 + 프로세스 제어 오케스트레이션."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Protocol, cast

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from shared.models import AgentSession
from shared.protocol import ConfigAction, CtrlAction, DeployTarget
from shared.utils import setup_logging

logger = setup_logging("dashboard.test_deploy_api")

router = APIRouter()


# ── Protocol 인터페이스 ──────────────────────────────────────

class _TCPServerLike(Protocol):
    async def send_ctrl_command_and_wait(
        self, agent_id: str, action: CtrlAction,
        target: int = 1, target_name: str = "",
        timeout: float = 30.0,
    ) -> dict: ...

    async def send_file_put(
        self, agent_id: str, local_path: str, remote_path: str,
        progress_callback: Any = None,
    ) -> dict: ...

    async def send_config_command(
        self, agent_id: str, action: ConfigAction, config_data: str = "",
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
    """에이전트 ID 확인. 빈 문자열이면 첫 연결 에이전트 사용. (agent_id, error)"""
    if state.session_mgr is None:
        return "", "서버가 실행 중이 아닙니다"
    if not agent_id:
        sessions = state.session_mgr.get_all_sessions()
        connected = [s for s in sessions if s.state == "connected"]
        if not connected:
            return "", "연결된 에이전트가 없습니다"
        agent_id = connected[0].agent_id
    elif state.session_mgr.get_session(agent_id) is None:
        return "", f"에이전트 '{agent_id}'를 찾을 수 없습니다"
    return agent_id, None


async def _get_target_process_info(
    state: _AppState, agent_id: str,
) -> tuple[dict | None, str | None]:
    """에이전트 config에서 target_process 정보 조회."""
    tcp = state.tcp_server
    if tcp is None:
        return None, "TCP 서버 미실행"
    fut = tcp.get_config_future(agent_id)
    sent = await tcp.send_config_command(agent_id, ConfigAction.GET)
    if not sent:
        return None, "설정 요청 전송 실패"
    try:
        config = await asyncio.wait_for(fut, timeout=10.0)
    except asyncio.TimeoutError:
        return None, "에이전트 응답 시간 초과"

    tp = config.get("target_process", {})
    if isinstance(tp, list):
        if not tp:
            return None, "target_process 설정 없음"
        tp = tp[0]
    if not tp or not tp.get("path"):
        return None, "target_process.path 미설정"
    return tp, None


# ── 엔드포인트 ───────────────────────────────────────────────

@router.get("/api/test-deploy/info/{agent_id}")
async def test_deploy_info(request: Request, agent_id: str) -> JSONResponse:
    """에이전트의 target_process 정보 조회."""
    state = _state(request)
    if state.tcp_server is None:
        return JSONResponse({"error": "서버 미실행"}, status_code=503)

    agent_id, err = _resolve_agent(state, agent_id)
    if err:
        return JSONResponse({"error": err}, status_code=404)

    tp, err = await _get_target_process_info(state, agent_id)
    if err:
        return JSONResponse({"error": err}, status_code=500)

    target_dir = str(Path(tp["path"]).parent)  # type: ignore[index]
    return JSONResponse({
        "agent_id": agent_id,
        "target_process": tp,
        "target_dir": target_dir,
        "process_name": tp.get("name", ""),  # type: ignore[union-attr]
    })


@router.post("/api/test-deploy/execute")
async def test_deploy_execute(request: Request) -> JSONResponse:
    """테스트 배포 실행: 백업 → 중지 → 파일 전송 → 시작."""
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
    skip_backup: bool = body.get("skip_backup", False)

    if not files:
        return JSONResponse({"error": "배포 파일이 없습니다"}, status_code=400)

    agent_id, err = _resolve_agent(state, raw_agent_id)
    if err:
        return JSONResponse({"error": err}, status_code=404)

    # 1) target_process 정보 조회
    tp, err = await _get_target_process_info(state, agent_id)
    if err:
        return JSONResponse({"error": f"설정 조회 실패: {err}"}, status_code=500)

    target_dir = str(Path(tp["path"]).parent).replace("\\", "/")  # type: ignore[index]
    steps: list[dict] = []

    # 2) 백업
    if not skip_backup:
        result = await tcp.send_ctrl_command_and_wait(
            agent_id, CtrlAction.BACKUP,
            target=DeployTarget.PROCESS, target_name=target_name,
            timeout=30.0,
        )
        steps.append({"step": "backup", **result})
        if not result.get("success"):
            # 백업 실패해도 계속 진행 (파일이 없을 수 있음)
            logger.warning("backup failed (continuing): %s", result)

    # 3) 프로세스 중지
    result = await tcp.send_ctrl_command_and_wait(
        agent_id, CtrlAction.STOP,
        target=DeployTarget.PROCESS, target_name=target_name,
        timeout=30.0,
    )
    steps.append({"step": "stop", **result})
    if not result.get("success"):
        logger.warning("stop may have failed (continuing): %s", result)

    # 4) 파일 전송
    transferred: list[str] = []
    for f in files:
        local_path = f.get("local_path", "")
        filename = f.get("filename", Path(local_path).name)
        remote_path = f"{target_dir}/{filename}"

        if not Path(local_path).exists():
            steps.append({
                "step": f"file:{filename}",
                "success": False,
                "error": f"파일 없음: {local_path}",
            })
            # 파일 전송 실패 시 롤백
            logger.error("file not found, rolling back: %s", local_path)
            await tcp.send_ctrl_command_and_wait(
                agent_id, CtrlAction.ROLLBACK,
                target=DeployTarget.PROCESS, target_name=target_name,
            )
            steps.append({"step": "rollback", "reason": "file_not_found"})
            return JSONResponse({"success": False, "steps": steps}, status_code=500)

        result = await tcp.send_file_put(agent_id, local_path, remote_path)
        size_mb = Path(local_path).stat().st_size / (1024 * 1024)
        steps.append({
            "step": f"file:{filename}",
            "size_mb": round(size_mb, 2),
            **result,
        })
        if not result.get("success"):
            logger.error("file transfer failed, rolling back: %s", result)
            await tcp.send_ctrl_command_and_wait(
                agent_id, CtrlAction.ROLLBACK,
                target=DeployTarget.PROCESS, target_name=target_name,
            )
            steps.append({"step": "rollback", "reason": "transfer_failed"})
            return JSONResponse({"success": False, "steps": steps}, status_code=500)

        transferred.append(filename)

    # 5) 프로세스 시작
    result = await tcp.send_ctrl_command_and_wait(
        agent_id, CtrlAction.START,
        target=DeployTarget.PROCESS, target_name=target_name,
        timeout=30.0,
    )
    steps.append({"step": "start", **result})

    success = result.get("success", False)
    return JSONResponse({
        "success": success,
        "agent_id": agent_id,
        "files": transferred,
        "pid": result.get("pid", 0),
        "steps": steps,
    })


@router.post("/api/test-deploy/rollback/{agent_id}")
async def test_deploy_rollback(request: Request, agent_id: str) -> JSONResponse:
    """수동 롤백: 프로세스 중지 + 백업 복원 + 재시작."""
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
        target=DeployTarget.PROCESS, target_name=target_name,
        timeout=30.0,
    )
    return JSONResponse({
        "success": result.get("success", False),
        "result": result,
    })
