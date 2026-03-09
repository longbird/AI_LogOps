from __future__ import annotations

# pyright: reportUnusedFunction=false

import importlib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, cast

from fastapi import APIRouter, FastAPI, Form, Request
from fastapi import Response as FastAPIResponse
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from shared.utils import setup_logging

logger = setup_logging("dashboard.app")


def create_app(
    session_mgr: object | None = None,
    storage_mgr: object | None = None,
    tcp_server: object | None = None,
    secret_key: str = "CHANGE_ME",
    rec_storage: object | None = None,
) -> FastAPI:
    app = FastAPI(title="AI-LogOps Dashboard")
    dashboard_router = cast(
        APIRouter,
        importlib.import_module("server.dashboard.routes.dashboard").router,
    )
    logs_router = cast(
        APIRouter,
        importlib.import_module("server.dashboard.routes.logs").router,
    )
    reports_router = cast(
        APIRouter,
        importlib.import_module("server.dashboard.routes.reports").router,
    )
    deploys_router = cast(
        APIRouter,
        importlib.import_module("server.dashboard.routes.deploys").router,
    )
    recordings_router = cast(
        APIRouter,
        importlib.import_module("server.dashboard.routes.recordings").router,
    )
    deploy_api_router = cast(
        APIRouter,
        importlib.import_module("server.dashboard.routes.deploy_api").router,
    )
    admin_router = cast(
        APIRouter,
        importlib.import_module("server.dashboard.routes.admin").router,
    )
    analysis_router = cast(
        APIRouter,
        importlib.import_module("server.dashboard.routes.analysis").router,
    )
    config_api_router = cast(
        APIRouter,
        importlib.import_module("server.dashboard.routes.config_api").router,
    )

    templates_dir = Path(__file__).parent / "templates"
    static_dir = Path(__file__).parent / "static"
    templates = Jinja2Templates(directory=str(templates_dir))
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.state.session_mgr = session_mgr
    app.state.storage_mgr = storage_mgr
    app.state.tcp_server = tcp_server
    app.state.secret_key = secret_key
    app.state.templates = templates

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": None},
        )

    @app.post("/login")
    async def login(
        request: Request,
        username: Annotated[str, Form()],
        password: Annotated[str, Form()],
    ):
        from server.dashboard.auth import authenticate_user, create_access_token

        user_info = authenticate_user(username, password)
        if user_info is None:
            return templates.TemplateResponse(
                "login.html",
                {
                    "request": request,
                    "error": "아이디 또는 비밀번호가 올바르지 않습니다",
                },
                status_code=401,
            )

        token = create_access_token(
            {
                "sub": user_info["username"],
                "role": user_info["role"],
                "permissions": user_info["permissions"],
            }
        )
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(key="access_token", value=token, httponly=True)
        return response

    @app.get("/logout")
    async def logout():
        response = RedirectResponse(url="/login", status_code=303)
        response.delete_cookie("access_token")
        return response

    @app.middleware("http")
    async def auth_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[FastAPIResponse]],
    ) -> FastAPIResponse:
        # 인증 불필요 경로
        if (
            request.url.path in ("/login", "/logout")
            or request.url.path.startswith("/static")
            or request.url.path.startswith("/api/deploy/")
            or request.url.path == "/api/rec/upload"
            or request.url.path.startswith("/api/rec/stream/")
            or request.url.path.startswith("/api/logs/")
            or request.url.path.startswith("/api/analysis/")
        ):
            return await call_next(request)

        # JWT 검증
        token = request.cookies.get("access_token")
        if not token:
            return RedirectResponse(url="/login", status_code=303)

        from server.dashboard.auth import (
            check_permission,
            get_required_permission,
            verify_token,
        )

        payload = verify_token(token)
        if payload is None:
            return RedirectResponse(url="/login", status_code=303)

        # request.state에 사용자 정보 설정
        request.state.user = payload.get("sub", "unknown")
        request.state.role = payload.get("role", "user")
        raw_perms = payload.get("permissions", [])
        request.state.permissions = (
            list(raw_perms) if isinstance(raw_perms, list) else []
        )

        # 경로별 권한 검사
        required = get_required_permission(request.url.path)
        if not check_permission(request.state.permissions, required):
            # API 요청은 JSON 403, 페이지 요청은 HTML 403
            if request.url.path.startswith("/api/") or request.url.path.startswith(
                "/ws/"
            ):
                return JSONResponse({"error": "권한이 없습니다"}, status_code=403)
            return HTMLResponse(
                content=_forbidden_html(
                    str(request.state.user),
                    list(request.state.permissions),
                ),
                status_code=403,
            )

        return await call_next(request)

    @app.get("/")
    async def root():
        return RedirectResponse(url="/dashboard", status_code=303)

    app.include_router(dashboard_router)
    app.include_router(logs_router)
    app.include_router(reports_router)
    app.include_router(deploys_router)
    app.include_router(recordings_router)
    app.include_router(deploy_api_router)
    app.include_router(admin_router)
    app.include_router(analysis_router)
    app.include_router(config_api_router)

    # 녹취 파일 업로드/스트리밍 라우터 (에이전트 → 서버 HTTP 업로드, GUI → WAV 스트리밍)
    if rec_storage is not None:
        from server.airec.routers.stream import create_stream_router
        from server.airec.routers.upload import create_upload_router
        from server.airec.storage import RecordingStorage

        _rec_st = cast(RecordingStorage, rec_storage)
        upload_router = create_upload_router(_rec_st)
        stream_router = create_stream_router(_rec_st)
        app.include_router(upload_router)
        app.include_router(stream_router)

    return app


def _forbidden_html(username: str, permissions: list[str]) -> str:
    """403 Forbidden 페이지 HTML."""
    from server.dashboard.auth import PERMISSIONS

    links = ""
    perm_path_map = {
        "dashboard": "/dashboard",
        "logs": "/logs",
        "deploys": "/deploys",
        "recordings": "/recordings",
        "rec_viewer": "/rec-viewer",
        "admin": "/admin",
    }
    for perm in permissions:
        label = PERMISSIONS.get(perm, perm)
        href = perm_path_map.get(perm, "/dashboard")
        links += f'<a href="{href}" class="inline-block bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded text-sm mr-2 mb-2">{label}</a>'

    if not links:
        links = '<a href="/logout" class="inline-block bg-gray-600 hover:bg-gray-700 text-white px-4 py-2 rounded text-sm">로그아웃</a>'

    return f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="UTF-8"><title>접근 거부 - AI-LogOps</title>
<script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-gray-100 min-h-screen flex items-center justify-center">
<div class="bg-white p-8 rounded-lg shadow-md max-w-md text-center">
    <div class="text-6xl mb-4">🔒</div>
    <h1 class="text-2xl font-bold text-gray-800 mb-2">접근 권한 없음</h1>
    <p class="text-gray-500 mb-6"><b>{username}</b> 계정에 이 페이지에 대한 권한이 없습니다.<br>관리자에게 문의하세요.</p>
    <div>{links}</div>
</div>
</body></html>"""
