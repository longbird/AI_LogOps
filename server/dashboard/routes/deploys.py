from __future__ import annotations

from datetime import datetime
from typing import Protocol, cast

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from shared.models import AgentSession

router = APIRouter()


class SessionManagerLike(Protocol):
    def get_all_sessions(self) -> list[AgentSession]: ...


class DashboardState(Protocol):
    templates: Jinja2Templates
    session_mgr: SessionManagerLike | None


def _state(request: Request) -> DashboardState:
    return cast(DashboardState, request.app.state)  # pyright: ignore[reportAny]


@router.get("/deploys", response_class=HTMLResponse)
async def deploys_page(request: Request) -> HTMLResponse:
    """배포 이력 페이지."""
    state = _state(request)
    templates = state.templates
    session_mgr = state.session_mgr
    deploys = _get_deploy_history(session_mgr)
    return cast(
        HTMLResponse,
        templates.TemplateResponse(
            "deploys.html",
            {
                "request": request,
                "title": "Deploy History",
                "deploys": deploys,
            },
        ),
    )


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
