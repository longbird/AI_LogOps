"""배치 로그 분석: WEB 대시보드에서 로그 다운로드 + LogAnalyzer 분석 + 리포트 생성."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Protocol, cast

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from shared.protocol import LogAction
from shared.utils import setup_logging

logger = setup_logging("dashboard.batch_analysis")

router = APIRouter()

_ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent  # AI-LogOps/


class _TCPServerLike(Protocol):
    async def send_log_command(
        self,
        agent_id: str,
        action: LogAction,
        date: str = ...,
        folder_index: int = ...,
    ) -> bool: ...


class _SessionMgrLike(Protocol):
    def get_all_sessions(self) -> list[Any]: ...
    def get_session(self, agent_id: str) -> Any | None: ...


class _DashState(Protocol):
    templates: Jinja2Templates
    session_mgr: _SessionMgrLike | None
    tcp_server: _TCPServerLike | None


def _state(request: Request) -> _DashState:
    return cast(_DashState, request.app.state)


def _get_agent_ids(state: _DashState) -> list[str]:
    if state.session_mgr is None:
        return []
    return [s.agent_info.agent_id for s in state.session_mgr.get_all_sessions()]


def _log_dir(agent_id: str, date_str: str) -> Path:
    return _ROOT_DIR / "storage" / "logs" / agent_id / date_str


@router.get("/batch-analysis", response_class=HTMLResponse)
async def batch_analysis_page(request: Request) -> HTMLResponse:
    """배치 로그 분석 페이지."""
    state = _state(request)
    return cast(
        HTMLResponse,
        state.templates.TemplateResponse(
            "batch_analysis.html",
            {
                "request": request,
                "title": "로그 분석",
                "agents": _get_agent_ids(state),
            },
        ),
    )


@router.post("/api/batch-analysis/download")
async def api_download_logs(request: Request) -> JSONResponse:
    """에이전트에 로그 다운로드 요청."""
    state = _state(request)
    tcp = state.tcp_server
    if tcp is None:
        return JSONResponse({"error": "server not configured"}, status_code=503)

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    agent_id: str = body.get("agent_id", "")
    date_str: str = body.get("date", "")
    folder_index: int = int(body.get("folder_index", -1))

    if not agent_id or not date_str:
        return JSONResponse({"error": "agent_id and date required"}, status_code=400)

    success = await tcp.send_log_command(
        agent_id, LogAction.HIST_REQUEST, date_str, folder_index
    )
    if not success:
        return JSONResponse(
            {"error": f"failed to send command to {agent_id}"}, status_code=502
        )

    logger.info("log download requested: agent_id=%s date=%s", agent_id, date_str)
    return JSONResponse({"status": "ok", "agent_id": agent_id, "date": date_str})


@router.get("/api/batch-analysis/files")
async def api_list_log_files(
    request: Request, agent_id: str = "", date: str = ""
) -> JSONResponse:
    """다운로드된 로그 파일 목록 확인."""
    if not agent_id or not date:
        return JSONResponse({"files": [], "count": 0, "total_size": 0})

    log_path = _log_dir(agent_id, date)
    if not log_path.exists():
        return JSONResponse({"files": [], "count": 0, "total_size": 0})

    files = sorted(log_path.glob("*.txt"))
    total_size = sum(f.stat().st_size for f in files)
    return JSONResponse({
        "files": [f.name for f in files],
        "count": len(files),
        "total_size": total_size,
    })


@router.post("/api/batch-analysis/run")
async def api_run_analysis(request: Request) -> JSONResponse:
    """배치 로그 분석 실행. CPU-intensive이므로 asyncio.to_thread 사용."""
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    agent_id: str = body.get("agent_id", "")
    date_str: str = body.get("date", "")

    if not agent_id or not date_str:
        return JSONResponse({"error": "agent_id and date required"}, status_code=400)

    log_path = _log_dir(agent_id, date_str)
    files = sorted(log_path.glob("*.txt"))
    if not files:
        return JSONResponse(
            {"error": f"로그 파일 없음: {log_path}"}, status_code=404
        )

    def _analyze() -> tuple[str, dict[str, Any]]:
        from server.analysis.log_analyzer import LogAnalyzer
        from server.analysis.report_generator import generate_report

        analyzer = LogAnalyzer(agent_id, date_str)
        result = analyzer.analyze_files(files)
        report_md = generate_report(result)
        summary = {
            "total_lines": result.total_lines,
            "inbound": result.inbound_count,
            "outbound": result.outbound_count,
            "file_close_count": len(result.file_closes),
            "db_fail_count": result.db_fail_count,
            "unrecorded_count": len(result.unrecorded_calls),
            "duration_mismatch_count": len(result.duration_mismatches),
        }
        return report_md, summary

    try:
        report_md, summary = await asyncio.to_thread(_analyze)
    except Exception as exc:
        logger.exception("batch analysis failed: agent_id=%s date=%s", agent_id, date_str)
        return JSONResponse({"error": str(exc)}, status_code=500)

    logger.info(
        "batch analysis done: agent_id=%s date=%s lines=%s inbound=%s",
        agent_id, date_str, summary["total_lines"], summary["inbound"],
    )
    return JSONResponse({"report": report_md, "summary": summary})
