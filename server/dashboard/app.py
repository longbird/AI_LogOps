from __future__ import annotations
# pyright: reportUnusedFunction=false

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Form, Request
from fastapi import Response as FastAPIResponse
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from shared.utils import setup_logging

logger = setup_logging("dashboard.app")


def create_app(
    session_mgr: object | None = None,
    storage_mgr: object | None = None,
    secret_key: str = "CHANGE_ME",
) -> FastAPI:
    app = FastAPI(title="AI-LogOps Dashboard")

    templates_dir = Path(__file__).parent / "templates"
    static_dir = Path(__file__).parent / "static"
    templates = Jinja2Templates(directory=str(templates_dir))
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.state.session_mgr = session_mgr
    app.state.storage_mgr = storage_mgr
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

        if not authenticate_user(username, password):
            return templates.TemplateResponse(
                "login.html",
                {"request": request, "error": "Invalid credentials"},
                status_code=401,
            )

        token = create_access_token({"sub": username})
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
        if request.url.path in ("/login", "/logout") or request.url.path.startswith(
            "/static"
        ):
            return await call_next(request)

        token = request.cookies.get("access_token")
        if not token:
            return RedirectResponse(url="/login", status_code=303)

        from server.dashboard.auth import verify_token

        payload = verify_token(token)
        if payload is None:
            return RedirectResponse(url="/login", status_code=303)

        request.state.user = payload.get("sub", "unknown")
        return await call_next(request)

    @app.get("/")
    async def root():
        return RedirectResponse(url="/dashboard", status_code=303)

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard(request: Request):
        logger.info(
            "Dashboard requested by %s", getattr(request.state, "user", "unknown")
        )
        return templates.TemplateResponse(
            "base.html",
            {
                "request": request,
                "title": "Dashboard",
                "content": "Dashboard placeholder - agent status cards coming in Task 25",
            },
        )

    return app
