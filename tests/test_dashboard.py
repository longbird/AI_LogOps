from __future__ import annotations
# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportMissingParameterType=false

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from server.dashboard.app import create_app
from server.dashboard.auth import (
    authenticate_user,
    create_access_token,
    verify_token,
)


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        follow_redirects=False,
    ) as ac:
        yield ac


@pytest.mark.asyncio
async def test_login_page_accessible(client):
    response = await client.get("/login")

    assert response.status_code == 200
    assert "AI-LogOps Login" in response.text


@pytest.mark.asyncio
async def test_login_success_redirects(client):
    response = await client.post(
        "/login",
        data={"username": "admin", "password": "admin123"},
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "access_token=" in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_login_failure_shows_error(client):
    response = await client.post(
        "/login",
        data={"username": "admin", "password": "wrong"},
    )

    assert response.status_code == 401
    assert "Invalid credentials" in response.text


@pytest.mark.asyncio
async def test_unauthenticated_redirect(client):
    response = await client.get("/dashboard")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_authenticated_access(client):
    token = create_access_token({"sub": "admin"})
    response = await client.get("/dashboard", cookies={"access_token": token})

    assert response.status_code == 200
    assert "Dashboard placeholder" in response.text


@pytest.mark.asyncio
async def test_logout_clears_cookie(client):
    response = await client.get("/logout")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    set_cookie = response.headers.get("set-cookie", "")
    assert "access_token=" in set_cookie
    assert "Max-Age=0" in set_cookie or "expires=" in set_cookie.lower()


def test_create_access_token():
    token = create_access_token({"sub": "admin"}, expires_delta=timedelta(minutes=5))

    payload = verify_token(token)
    assert payload is not None
    assert payload["sub"] == "admin"
    assert "exp" in payload


def test_verify_token_valid():
    token = create_access_token({"sub": "admin"})

    payload = verify_token(token)
    assert payload is not None
    assert payload["sub"] == "admin"


def test_verify_token_invalid():
    payload = verify_token("this.is.not.a.valid.token")

    assert payload is None


def test_authenticate_user():
    assert authenticate_user("admin", "admin123") is True
    assert authenticate_user("admin", "wrong") is False
