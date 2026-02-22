from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypedDict, cast

from jose import JWTError, jwt

from shared.utils import setup_logging

SECRET_KEY = "CHANGE_ME_IN_PRODUCTION"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440  # 24시간

logger = setup_logging("dashboard.auth")

# ── Permission Definitions ──

PERMISSIONS: dict[str, str] = {
    "dashboard": "대시보드",
    "logs": "로그",
    "deploys": "배포",
    "recordings": "녹취 분석",
    "rec_viewer": "녹취 조회",
    "admin": "계정 관리",
}

ALL_PERMISSIONS = list(PERMISSIONS.keys())

# 새 사용자 기본 권한
DEFAULT_USER_PERMISSIONS = ["dashboard", "rec_viewer"]

USERS_FILE = Path("storage/users.json")


# ── User Data Types ──


class UserData(TypedDict):
    password_hash: str
    salt: str
    role: str  # "admin" | "user"
    permissions: list[str]
    created_at: str


class UsersStore(TypedDict):
    users: dict[str, UserData]


# ── Password Hashing ──


def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """PBKDF2-SHA256으로 비밀번호 해싱. (hash_hex, salt_hex) 반환."""
    if salt is None:
        salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000
    )
    return hashed.hex(), salt


def _verify_password(password: str, password_hash: str, salt: str) -> bool:
    """비밀번호 검증."""
    computed, _ = _hash_password(password, salt)
    return secrets.compare_digest(computed, password_hash)


# ── User Storage (JSON File) ──


def _load_users() -> UsersStore:
    """users.json 로드. 없으면 기본 admin 계정 생성."""
    if USERS_FILE.exists():
        try:
            data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
            return cast(UsersStore, data)
        except (json.JSONDecodeError, KeyError):
            logger.warning("users.json 파싱 실패, 기본값 생성")

    # 기본 admin 계정 생성
    store = _create_default_store()
    _save_users(store)
    return store


def _save_users(store: UsersStore) -> None:
    """users.json 저장."""
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(
        json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _create_default_store() -> UsersStore:
    """기본 admin 계정으로 초기 저장소 생성."""
    pw_hash, salt = _hash_password("admin123")
    return {
        "users": {
            "admin": {
                "password_hash": pw_hash,
                "salt": salt,
                "role": "admin",
                "permissions": ALL_PERMISSIONS.copy(),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        }
    }


# ── User CRUD ──


def get_all_users() -> dict[str, Any]:
    """모든 사용자 목록 (비밀번호 제외)."""
    store = _load_users()
    result: dict[str, Any] = {}
    for username, user in store["users"].items():
        result[username] = {
            "role": user["role"],
            "permissions": user["permissions"],
            "created_at": user.get("created_at", ""),
        }
    return result


def get_user(username: str) -> UserData | None:
    """사용자 조회."""
    store = _load_users()
    return store["users"].get(username)


def create_user(
    username: str,
    password: str,
    role: str = "user",
    permissions: list[str] | None = None,
) -> bool:
    """사용자 생성. 이미 존재하면 False."""
    store = _load_users()
    if username in store["users"]:
        return False

    if permissions is None:
        permissions = DEFAULT_USER_PERMISSIONS.copy()

    # admin 역할은 모든 권한 자동 부여
    if role == "admin":
        permissions = ALL_PERMISSIONS.copy()

    pw_hash, salt = _hash_password(password)
    store["users"][username] = {
        "password_hash": pw_hash,
        "salt": salt,
        "role": role,
        "permissions": permissions,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_users(store)
    logger.info("사용자 생성: %s (role=%s)", username, role)
    return True


def update_user(
    username: str,
    password: str | None = None,
    role: str | None = None,
    permissions: list[str] | None = None,
) -> bool:
    """사용자 수정. 존재하지 않으면 False."""
    store = _load_users()
    if username not in store["users"]:
        return False

    user = store["users"][username]

    if password:
        pw_hash, salt = _hash_password(password)
        user["password_hash"] = pw_hash
        user["salt"] = salt

    if role is not None:
        user["role"] = role

    if permissions is not None:
        user["permissions"] = permissions

    # admin 역할은 모든 권한 자동 부여
    if user["role"] == "admin":
        user["permissions"] = ALL_PERMISSIONS.copy()

    _save_users(store)
    logger.info("사용자 수정: %s (role=%s)", username, user["role"])
    return True


def delete_user(username: str) -> bool:
    """사용자 삭제. 존재하지 않으면 False."""
    store = _load_users()
    if username not in store["users"]:
        return False

    del store["users"][username]
    _save_users(store)
    logger.info("사용자 삭제: %s", username)
    return True


# ── Authentication ──


def authenticate_user(username: str, password: str) -> dict[str, Any] | None:
    """사용자 인증. 성공 시 {username, role, permissions} 반환, 실패 시 None."""
    user = get_user(username)
    if user is None:
        return None

    if not _verify_password(password, user["password_hash"], user["salt"]):
        return None

    return {
        "username": username,
        "role": user["role"],
        "permissions": user["permissions"],
    }


# ── JWT Token ──


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


# ── Path Permission Mapping ──


def get_required_permission(path: str) -> str | list[str] | None:
    """URL 경로에 필요한 권한 반환. None이면 인증만 필요."""
    # Admin
    if path.startswith("/admin") or path.startswith("/api/admin/"):
        return "admin"
    # Dashboard
    if path in ("/", "/dashboard") or path.startswith("/api/agents"):
        return "dashboard"
    # Logs
    if (
        path.startswith("/logs")
        or path.startswith("/api/logs/")
        or path.startswith("/ws/logs/")
    ):
        return "logs"
    # Deploys
    if path.startswith("/deploys") or path.startswith("/api/dashboard/deploy/"):
        return "deploys"
    # Recordings (분석 제어)
    if path == "/recordings" or path.startswith("/ws/rec/"):
        return "recordings"
    if path.startswith("/api/rec/analyze") or path.startswith(
        "/api/rec/max-concurrent"
    ):
        return "recordings"
    # Rec Viewer
    if path == "/rec-viewer":
        return "rec_viewer"
    # 공유 API (녹취 분석 OR 녹취 조회 권한 중 하나)
    if (
        path.startswith("/api/rec/list")
        or path.startswith("/api/rec/detail/")
        or path.startswith("/recordings/detail/")
    ):
        return ["recordings", "rec_viewer"]
    # 기타 (인증만 필요)
    return None


def check_permission(
    user_permissions: list[str], required: str | list[str] | None
) -> bool:
    """사용자 권한이 요구 권한을 만족하는지 검사."""
    if required is None:
        return True
    if isinstance(required, str):
        return required in user_permissions
    # list: 하나라도 있으면 허용
    return any(p in user_permissions for p in required)
