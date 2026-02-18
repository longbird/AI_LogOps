"""OAuth Device Flow 라우트 + 브라우저 인증 페이지."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from server.subscription.db import SubscriptionDB
from server.subscription.routes import get_db

auth_router = APIRouter(tags=["auth"])

# ── API: 에이전트용 ──────────────────────────────────


class DeviceCodeRequest(BaseModel):
    agent_id: str = ""


class DeviceCodeResponse(BaseModel):
    device_code: str
    user_code: str
    login_token: str
    verification_uri: str
    login_uri: str
    expires_in: int
    interval: int


class TokenPollRequest(BaseModel):
    device_code: str
    grant_type: str = "urn:ietf:params:oauth:grant-type:device_code"


@auth_router.post("/api/auth/device", response_model=DeviceCodeResponse)
def request_device_code(
    req: DeviceCodeRequest,
    request: Request,
    db: SubscriptionDB = Depends(get_db),
) -> DeviceCodeResponse:
    """에이전트가 device code 요청. user_code + verification_uri 반환."""
    result = db.create_device_code(agent_id=req.agent_id)
    base_url = str(request.base_url).rstrip("/")
    login_token = result["login_token"]
    return DeviceCodeResponse(
        device_code=result["device_code"],
        user_code=result["user_code"],
        login_token=login_token,
        verification_uri=f"{base_url}/auth/verify",
        login_uri=f"{base_url}/auth/login?token={login_token}",
        expires_in=result["expires_in"],
        interval=result["interval"],
    )


@auth_router.post("/api/auth/token")
def poll_token(
    req: TokenPollRequest,
    db: SubscriptionDB = Depends(get_db),
) -> dict[str, Any]:
    """에이전트가 device_code로 폴링. 승인되면 access_token 반환."""
    result = db.poll_device(req.device_code)
    status = result.get("status", "invalid")

    if status == "authorization_pending":
        return {"error": "authorization_pending"}
    if status == "expired":
        return {"error": "expired_token"}
    if status == "approved":
        return {
            "access_token": result["access_token"],
            "token_type": result["token_type"],
            "expires_in": result["expires_in"],
            "openai_api_key": result.get("openai_api_key", ""),
            "openai_model": result.get("openai_model", ""),
            "claude_api_key": result.get("claude_api_key", ""),
            "claude_model": result.get("claude_model", ""),
            "system_prompt": result.get("system_prompt", ""),
            "max_tokens": result.get("max_tokens", 2000),
        }
    if status == "error":
        return {"error": "server_error", "message": result.get("message", "")}
    return {"error": "invalid_grant"}


# ── 브라우저: 인증 페이지 ────────────────────────────

_VERIFY_PAGE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI-LogOps 디바이스 인증</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #f5f5f5; display: flex; justify-content: center; align-items: center;
         min-height: 100vh; }
  .card { background: #fff; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,.1);
          padding: 2rem; max-width: 420px; width: 90%; }
  h1 { font-size: 1.4rem; margin-bottom: 0.5rem; color: #333; }
  p { color: #666; margin-bottom: 1rem; font-size: 0.9rem; }
  label { display: block; font-weight: 600; margin-bottom: 0.3rem; color: #333; font-size: 0.9rem; }
  input { width: 100%; padding: 0.6rem; border: 1px solid #ddd; border-radius: 6px;
          font-size: 1rem; margin-bottom: 1rem; }
  input:focus { outline: none; border-color: #4a90d9; }
  .code-input { font-size: 1.5rem; text-align: center; letter-spacing: 0.3rem;
                text-transform: uppercase; font-weight: bold; }
  button { width: 100%; padding: 0.7rem; background: #4a90d9; color: #fff; border: none;
           border-radius: 6px; font-size: 1rem; cursor: pointer; }
  button:hover { background: #3a7bc8; }
  .error { color: #d32f2f; font-size: 0.85rem; margin-bottom: 1rem; }
  .success { color: #2e7d32; font-size: 0.85rem; margin-bottom: 1rem; }
  .divider { border-top: 1px solid #eee; margin: 1rem 0; }
</style>
</head>
<body>
<div class="card">
  <h1>AI-LogOps Device Authorization</h1>
  <p>Telegram 에이전트에서 표시된 코드를 입력하고, 계정으로 로그인하세요.</p>
  {message}
  <form method="post" action="/auth/verify">
    <label for="user_code">인증 코드</label>
    <input type="text" id="user_code" name="user_code" class="code-input"
           maxlength="8" placeholder="ABCD1234" value="{user_code}" required>
    <div class="divider"></div>
    <label for="email">이메일</label>
    <input type="email" id="email" name="email" placeholder="admin@example.com" value="{email}" required>
    <label for="password">비밀번호</label>
    <input type="password" id="password" name="password" required>
    <button type="submit">인증 승인</button>
  </form>
</div>
</body>
</html>"""

_SUCCESS_PAGE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>인증 완료</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #f5f5f5; display: flex; justify-content: center; align-items: center;
         min-height: 100vh; }
  .card { background: #fff; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,.1);
          padding: 2rem; max-width: 420px; width: 90%; text-align: center; }
  h1 { font-size: 1.4rem; margin-bottom: 0.5rem; color: #2e7d32; }
  p { color: #666; font-size: 0.9rem; }
  .check { font-size: 3rem; margin-bottom: 1rem; }
</style>
</head>
<body>
<div class="card">
  <div class="check">&#10003;</div>
  <h1>인증 완료</h1>
  <p>디바이스가 승인되었습니다.<br>이 창을 닫아도 됩니다.<br>Telegram에서 LLM 기능을 사용할 수 있습니다.</p>
</div>
</body>
</html>"""


# ── 간소화 로그인 (Claude Code 방식) ────────────────

_LOGIN_PAGE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI-LogOps 로그인</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #f5f5f5; display: flex; justify-content: center; align-items: center;
         min-height: 100vh; }
  .card { background: #fff; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,.1);
          padding: 2rem; max-width: 420px; width: 90%; }
  h1 { font-size: 1.4rem; margin-bottom: 0.5rem; color: #333; }
  p { color: #666; margin-bottom: 1rem; font-size: 0.9rem; }
  label { display: block; font-weight: 600; margin-bottom: 0.3rem; color: #333; font-size: 0.9rem; }
  input { width: 100%; padding: 0.6rem; border: 1px solid #ddd; border-radius: 6px;
          font-size: 1rem; margin-bottom: 1rem; }
  input:focus { outline: none; border-color: #4a90d9; }
  button { width: 100%; padding: 0.7rem; background: #4a90d9; color: #fff; border: none;
           border-radius: 6px; font-size: 1rem; cursor: pointer; }
  button:hover { background: #3a7bc8; }
  .error { color: #d32f2f; font-size: 0.85rem; margin-bottom: 1rem; }
</style>
</head>
<body>
<div class="card">
  <h1>AI-LogOps 에이전트 인증</h1>
  <p>로그인하면 에이전트가 자동으로 인증됩니다.</p>
  {message}
  <form method="post" action="/auth/login">
    <input type="hidden" name="token" value="{token}">
    <label for="email">이메일</label>
    <input type="email" id="email" name="email" placeholder="admin@example.com" value="{email}" required>
    <label for="password">비밀번호</label>
    <input type="password" id="password" name="password" required>
    <button type="submit">로그인</button>
  </form>
</div>
</body>
</html>"""


@auth_router.get("/auth/login", response_class=HTMLResponse)
def login_page(token: str = "") -> HTMLResponse:
    """간소화 로그인 페이지 — 토큰이 URL에 내장, 코드 입력 불필요."""
    html = _LOGIN_PAGE.format(message="", token=token, email="")
    return HTMLResponse(content=html)


@auth_router.post("/auth/login", response_class=HTMLResponse)
def login_submit(
    token: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: SubscriptionDB = Depends(get_db),
) -> HTMLResponse:
    """로그인 → login_token으로 디바이스 자동 승인."""
    # 1. 디바이스 확인
    device = db.get_device_by_login_token(token)
    if device is None:
        html = _LOGIN_PAGE.format(
            message='<p class="error">유효하지 않은 인증 링크입니다.</p>',
            token=token,
            email=email,
        )
        return HTMLResponse(content=html)

    if device["status"] != "pending":
        html = _LOGIN_PAGE.format(
            message='<p class="error">이미 사용되었거나 만료된 인증 링크입니다.</p>',
            token=token,
            email=email,
        )
        return HTMLResponse(content=html)

    # 2. 사용자 로그인
    user = db.verify_user(email, password)
    if user is None:
        html = _LOGIN_PAGE.format(
            message='<p class="error">이메일 또는 비밀번호가 올바르지 않습니다.</p>',
            token=token,
            email=email,
        )
        return HTMLResponse(content=html)

    # 3. 구독 확인
    if user.get("subscription_id") is None:
        html = _LOGIN_PAGE.format(
            message='<p class="error">해당 계정에 활성 구독이 없습니다.</p>',
            token=token,
            email=email,
        )
        return HTMLResponse(content=html)

    # 4. 디바이스 승인
    ok = db.approve_device_by_login_token(token, user["id"])
    if not ok:
        html = _LOGIN_PAGE.format(
            message='<p class="error">인증 링크가 만료되었습니다. 다시 시도하세요.</p>',
            token="",
            email=email,
        )
        return HTMLResponse(content=html)

    return HTMLResponse(content=_SUCCESS_PAGE)


# ── 기존 인증 코드 방식 (레거시 호환) ────────────────


@auth_router.get("/auth/verify", response_class=HTMLResponse)
def verify_page(user_code: str = "") -> HTMLResponse:
    """브라우저 인증 페이지 (레거시 — 인증 코드 입력 방식)."""
    html = _VERIFY_PAGE.format(message="", user_code=user_code, email="")
    return HTMLResponse(content=html)


@auth_router.post("/auth/verify", response_class=HTMLResponse)
def verify_submit(
    user_code: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: SubscriptionDB = Depends(get_db),
) -> HTMLResponse:
    """로그인 + 디바이스 승인 처리."""
    user_code = user_code.strip().upper()

    # 1. 디바이스 코드 확인
    device = db.get_device_by_user_code(user_code)
    if device is None:
        html = _VERIFY_PAGE.format(
            message='<p class="error">유효하지 않은 인증 코드입니다.</p>',
            user_code=user_code,
            email=email,
        )
        return HTMLResponse(content=html)

    if device["status"] != "pending":
        html = _VERIFY_PAGE.format(
            message='<p class="error">이미 사용되었거나 만료된 코드입니다.</p>',
            user_code=user_code,
            email=email,
        )
        return HTMLResponse(content=html)

    # 2. 사용자 로그인
    user = db.verify_user(email, password)
    if user is None:
        html = _VERIFY_PAGE.format(
            message='<p class="error">이메일 또는 비밀번호가 올바르지 않습니다.</p>',
            user_code=user_code,
            email=email,
        )
        return HTMLResponse(content=html)

    # 3. 구독 확인
    if user.get("subscription_id") is None:
        html = _VERIFY_PAGE.format(
            message='<p class="error">해당 계정에 활성 구독이 없습니다.</p>',
            user_code=user_code,
            email=email,
        )
        return HTMLResponse(content=html)

    # 4. 디바이스 승인
    ok = db.approve_device(user_code, user["id"])
    if not ok:
        html = _VERIFY_PAGE.format(
            message='<p class="error">인증 코드가 만료되었습니다. 다시 시도하세요.</p>',
            user_code="",
            email=email,
        )
        return HTMLResponse(content=html)

    return HTMLResponse(content=_SUCCESS_PAGE)
