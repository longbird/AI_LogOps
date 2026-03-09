from __future__ import annotations

import time
from typing import Protocol, TypedDict, cast

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


class AgentCardData(TypedDict):
    agent_id: str
    version: str
    state: str
    session_id: str
    last_heartbeat_ago: str
    log_count: int
    deploy_count: int
    state_color: str
    process_status: str


def _state(request: Request) -> DashboardState:
    return cast(DashboardState, request.app.state)  # pyright: ignore[reportAny]


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request) -> HTMLResponse:
    """메인 대시보드 페이지. 에이전트 상태 카드 표시."""
    state = _state(request)
    templates = state.templates
    session_mgr = state.session_mgr
    agents = _get_agent_data(session_mgr)
    return cast(
        HTMLResponse,
        templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "title": "Dashboard",
                "agents": agents,
            },
        ),
    )


@router.get("/api/agents", response_class=HTMLResponse)
async def agents_partial(request: Request) -> HTMLResponse:
    """HTMX partial: 에이전트 카드 목록만 반환 (5초 자동 갱신용)."""
    state = _state(request)
    templates = state.templates
    session_mgr = state.session_mgr
    agents = _get_agent_data(session_mgr)
    return cast(
        HTMLResponse,
        templates.TemplateResponse(
            "partials/agent_cards.html",
            {
                "request": request,
                "agents": agents,
            },
        ),
    )


def _get_agent_data(session_mgr: SessionManagerLike | None) -> list[AgentCardData]:
    """SessionManager에서 에이전트 정보 추출."""
    if session_mgr is None:
        return []

    agents: list[AgentCardData] = []
    now = time.time()
    for session in session_mgr.get_all_sessions():
        info = session.agent_info
        last_hb_ago = int(now - session.last_heartbeat)
        agents.append(
            {
                "agent_id": info.agent_id,
                "version": info.version,
                "state": info.state.value,
                "session_id": session.session_id,
                "last_heartbeat_ago": (
                    f"{last_hb_ago}s ago" if last_hb_ago < 300 else "offline"
                ),
                "log_count": len(session.log_buffer),
                "deploy_count": len(session.deploy_history),
                "state_color": _state_color(info.state.value),
                "process_status": {0: "-", 1: "Running", 2: "Down"}.get(
                    session.process_status, "-"
                ),
            }
        )
    return agents


@router.get("/config", response_class=HTMLResponse)
async def config_page(request: Request) -> HTMLResponse:
    """에이전트 설정 관리 페이지."""
    state = _state(request)
    return cast(
        HTMLResponse,
        state.templates.TemplateResponse("config.html", {"request": request, "title": "설정 관리"}),
    )


def _state_color(state: str) -> str:
    colors = {
        "CONNECTED": "green",
        "STANDBY": "yellow",
        "DEPLOYING": "blue",
        "DEPLOY_VERIFY": "blue",
        "ERROR": "red",
        "INIT": "gray",
        "CONNECTING": "yellow",
        "UPDATING": "purple",
    }
    return colors.get(state, "gray")
