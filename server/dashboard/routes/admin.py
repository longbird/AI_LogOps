from __future__ import annotations

from typing import Any, Protocol, cast

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from shared.utils import setup_logging

logger = setup_logging("dashboard.admin")

router = APIRouter()


class _DashState(Protocol):
    templates: Jinja2Templates


def _state(request: Request) -> _DashState:
    return cast(_DashState, request.app.state)


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request) -> HTMLResponse:
    """계정 관리 페이지 (admin 권한 필요 — 미들웨어에서 검사됨)."""
    state = _state(request)
    return cast(
        HTMLResponse,
        state.templates.TemplateResponse(
            "admin.html",
            {"request": request, "title": "계정 관리"},
        ),
    )


# ── User CRUD API ──


@router.get("/api/admin/users")
async def api_list_users(request: Request) -> JSONResponse:
    """모든 사용자 목록 조회."""
    from server.dashboard.auth import PERMISSIONS, get_all_users

    users = get_all_users()
    return JSONResponse(
        {
            "users": users,
            "permissions": PERMISSIONS,
        }
    )


@router.post("/api/admin/users")
async def api_create_user(request: Request) -> JSONResponse:
    """사용자 생성."""
    from server.dashboard.auth import ALL_PERMISSIONS, create_user

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"error": "잘못된 요청입니다"}, status_code=400)

    username = str(body.get("username", "")).strip()
    password = str(body.get("password", "")).strip()
    role = str(body.get("role", "user")).strip()
    permissions: list[str] = body.get("permissions", [])

    if not username or not password:
        return JSONResponse(
            {"error": "사용자명과 비밀번호는 필수입니다"}, status_code=400
        )

    if len(username) < 2 or len(username) > 32:
        return JSONResponse({"error": "사용자명은 2~32자여야 합니다"}, status_code=400)

    if len(password) < 4:
        return JSONResponse(
            {"error": "비밀번호는 4자 이상이어야 합니다"}, status_code=400
        )

    if role not in ("admin", "user"):
        return JSONResponse(
            {"error": "역할은 admin 또는 user여야 합니다"}, status_code=400
        )

    # 유효한 권한만 필터링
    valid_perms = [p for p in permissions if p in ALL_PERMISSIONS]

    ok = create_user(username, password, role, valid_perms)
    if not ok:
        return JSONResponse(
            {"error": f"사용자 '{username}'이(가) 이미 존재합니다"}, status_code=409
        )

    return JSONResponse({"status": "ok", "username": username})


@router.put("/api/admin/users/{username}")
async def api_update_user(request: Request, username: str) -> JSONResponse:
    """사용자 수정."""
    from server.dashboard.auth import ALL_PERMISSIONS, get_user, update_user

    if get_user(username) is None:
        return JSONResponse(
            {"error": f"사용자 '{username}'을(를) 찾을 수 없습니다"}, status_code=404
        )

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"error": "잘못된 요청입니다"}, status_code=400)

    password = body.get("password")
    if password is not None:
        password = str(password).strip()
        if password and len(password) < 4:
            return JSONResponse(
                {"error": "비밀번호는 4자 이상이어야 합니다"}, status_code=400
            )
        if not password:
            password = None  # 빈 문자열이면 변경하지 않음

    role = body.get("role")
    if role is not None:
        role = str(role).strip()
        if role not in ("admin", "user"):
            return JSONResponse(
                {"error": "역할은 admin 또는 user여야 합니다"}, status_code=400
            )

    permissions = body.get("permissions")
    if permissions is not None:
        if not isinstance(permissions, list):
            return JSONResponse(
                {"error": "permissions는 배열이어야 합니다"}, status_code=400
            )
        permissions = [p for p in permissions if p in ALL_PERMISSIONS]

    ok = update_user(username, password=password, role=role, permissions=permissions)
    if not ok:
        return JSONResponse({"error": "수정 실패"}, status_code=500)

    return JSONResponse({"status": "ok", "username": username})


@router.delete("/api/admin/users/{username}")
async def api_delete_user(request: Request, username: str) -> JSONResponse:
    """사용자 삭제."""
    from server.dashboard.auth import delete_user

    # 자기 자신 삭제 방지
    current_user = getattr(request.state, "user", None)
    if current_user == username:
        return JSONResponse(
            {"error": "자기 자신은 삭제할 수 없습니다"}, status_code=400
        )

    ok = delete_user(username)
    if not ok:
        return JSONResponse(
            {"error": f"사용자 '{username}'을(를) 찾을 수 없습니다"}, status_code=404
        )

    return JSONResponse({"status": "ok", "username": username})
