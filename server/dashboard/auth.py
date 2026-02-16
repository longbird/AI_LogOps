from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import cast

from jose import JWTError, jwt

from shared.utils import setup_logging

SECRET_KEY = "CHANGE_ME_IN_PRODUCTION"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

ADMIN_USERS = {"admin": "admin123"}

logger = setup_logging("dashboard.auth")


def create_access_token(
    data: dict[str, object],
    expires_delta: timedelta | None = None,
) -> str:
    to_encode: dict[str, object] = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode["exp"] = expire
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> dict[str, object] | None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return cast(dict[str, object], payload)
    except JWTError:
        logger.warning("Token verification failed")
        return None


def authenticate_user(username: str, password: str) -> bool:
    return ADMIN_USERS.get(username) == password
