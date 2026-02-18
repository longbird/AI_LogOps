"""구독 관리 SQLite 데이터베이스 — OAuth Device Flow 지원."""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_DEFAULT_DB_PATH = Path(__file__).resolve().parent / "subscriptions.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS subscriptions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_key TEXT    UNIQUE NOT NULL,
    label           TEXT    NOT NULL DEFAULT '',
    agent_id        TEXT    NOT NULL DEFAULT '*',
    openai_api_key  TEXT    NOT NULL DEFAULT '',
    openai_model    TEXT    NOT NULL DEFAULT 'gpt-4o-mini',
    claude_api_key  TEXT    NOT NULL DEFAULT '',
    claude_model    TEXT    NOT NULL DEFAULT 'claude-sonnet-4-20250514',
    system_prompt   TEXT    NOT NULL DEFAULT '',
    max_tokens      INTEGER NOT NULL DEFAULT 2000,
    status          TEXT    NOT NULL DEFAULT 'active',
    expires_at      TEXT,
    created_at      TEXT    NOT NULL,
    last_validated_at TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT    UNIQUE NOT NULL,
    password_hash   TEXT    NOT NULL,
    password_salt   TEXT    NOT NULL,
    name            TEXT    NOT NULL DEFAULT '',
    is_admin        INTEGER NOT NULL DEFAULT 0,
    subscription_id INTEGER,
    created_at      TEXT    NOT NULL,
    FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
);

CREATE TABLE IF NOT EXISTS device_codes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    device_code     TEXT    UNIQUE NOT NULL,
    user_code       TEXT    UNIQUE NOT NULL,
    login_token     TEXT    UNIQUE NOT NULL,
    agent_id        TEXT    NOT NULL DEFAULT '',
    user_id         INTEGER,
    status          TEXT    NOT NULL DEFAULT 'pending',
    expires_at      TEXT    NOT NULL,
    created_at      TEXT    NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS access_tokens (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    token           TEXT    UNIQUE NOT NULL,
    user_id         INTEGER NOT NULL,
    agent_id        TEXT    NOT NULL DEFAULT '',
    subscription_id INTEGER,
    expires_at      TEXT    NOT NULL,
    created_at      TEXT    NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
);
"""


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), 100_000
    ).hex()


def _generate_user_code() -> str:
    """8자리 영숫자 코드 (읽기 쉽게 0/O/1/I 제외)."""
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(chars) for _ in range(8))


class SubscriptionDB:
    """SQLite 기반 구독 + OAuth 저장소."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = str(db_path or _DEFAULT_DB_PATH)
        self._conn: sqlite3.Connection | None = None

    def open(self) -> None:
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.executescript(_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """기존 DB에 누락된 컬럼 추가 (마이그레이션)."""
        cursor = self.conn.execute("PRAGMA table_info(device_codes)")
        columns = {row[1] for row in cursor.fetchall()}
        if "login_token" not in columns:
            self.conn.execute(
                "ALTER TABLE device_codes ADD COLUMN login_token TEXT NOT NULL DEFAULT ''"
            )
            # 기존 행에 고유 토큰 부여
            rows = self.conn.execute("SELECT id FROM device_codes").fetchall()
            for row in rows:
                token = secrets.token_urlsafe(32)
                self.conn.execute(
                    "UPDATE device_codes SET login_token=? WHERE id=?",
                    (token, row[0]),
                )
            self.conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("DB not opened. Call open() first.")
        return self._conn

    # ══════════════════════════════════════════════════
    # 사용자 관리
    # ══════════════════════════════════════════════════

    def create_user(
        self,
        email: str,
        password: str,
        *,
        name: str = "",
        is_admin: bool = False,
        subscription_id: int | None = None,
    ) -> dict[str, Any] | None:
        """사용자 생성. 이미 존재하면 None."""
        salt = os.urandom(16).hex()
        pw_hash = _hash_password(password, salt)
        now = datetime.now(timezone.utc).isoformat()
        try:
            self.conn.execute(
                """INSERT INTO users
                   (email, password_hash, password_salt, name, is_admin, subscription_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (email, pw_hash, salt, name, int(is_admin), subscription_id, now),
            )
            self.conn.commit()
        except sqlite3.IntegrityError:
            return None
        return self.get_user_by_email(email)

    def verify_user(self, email: str, password: str) -> dict[str, Any] | None:
        """이메일+비밀번호 검증."""
        user = self.get_user_by_email(email)
        if user is None:
            return None
        pw_hash = _hash_password(password, user["password_salt"])
        if pw_hash != user["password_hash"]:
            return None
        return user

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        cursor = self.conn.execute("SELECT * FROM users WHERE email=?", (email,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_user_by_id(self, user_id: int) -> dict[str, Any] | None:
        cursor = self.conn.execute("SELECT * FROM users WHERE id=?", (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def assign_subscription(self, user_id: int, subscription_id: int) -> bool:
        cursor = self.conn.execute(
            "UPDATE users SET subscription_id=? WHERE id=?",
            (subscription_id, user_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    # ══════════════════════════════════════════════════
    # OAuth Device Flow
    # ══════════════════════════════════════════════════

    def create_device_code(
        self, agent_id: str = "", expires_minutes: int = 15
    ) -> dict[str, Any]:
        """디바이스 코드 + 사용자 코드 + 로그인 토큰 생성."""
        device_code = secrets.token_urlsafe(32)
        user_code = _generate_user_code()
        login_token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(minutes=expires_minutes)).isoformat()
        self.conn.execute(
            """INSERT INTO device_codes
               (device_code, user_code, login_token, agent_id, status, expires_at, created_at)
               VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
            (
                device_code,
                user_code,
                login_token,
                agent_id,
                expires_at,
                now.isoformat(),
            ),
        )
        self.conn.commit()
        return {
            "device_code": device_code,
            "user_code": user_code,
            "login_token": login_token,
            "expires_in": expires_minutes * 60,
            "interval": 5,
        }

    def get_device_by_user_code(self, user_code: str) -> dict[str, Any] | None:
        cursor = self.conn.execute(
            "SELECT * FROM device_codes WHERE user_code=?", (user_code.upper(),)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_device_by_login_token(self, login_token: str) -> dict[str, Any] | None:
        """login_token으로 디바이스 조회 (간소화 로그인용)."""
        cursor = self.conn.execute(
            "SELECT * FROM device_codes WHERE login_token=?", (login_token,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def approve_device(self, user_code: str, user_id: int) -> bool:
        """사용자가 브라우저에서 승인."""
        device = self.get_device_by_user_code(user_code)
        if device is None or device["status"] != "pending":
            return False
        expires_at = datetime.fromisoformat(device["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires_at:
            self.conn.execute(
                "UPDATE device_codes SET status='expired' WHERE user_code=?",
                (user_code.upper(),),
            )
            self.conn.commit()
            return False
        self.conn.execute(
            "UPDATE device_codes SET status='approved', user_id=? WHERE user_code=?",
            (user_id, user_code.upper()),
        )
        self.conn.commit()
        return True

    def approve_device_by_login_token(self, login_token: str, user_id: int) -> bool:
        """login_token으로 디바이스 승인 (간소화 로그인용)."""
        device = self.get_device_by_login_token(login_token)
        if device is None or device["status"] != "pending":
            return False
        expires_at = datetime.fromisoformat(device["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires_at:
            self.conn.execute(
                "UPDATE device_codes SET status='expired' WHERE login_token=?",
                (login_token,),
            )
            self.conn.commit()
            return False
        self.conn.execute(
            "UPDATE device_codes SET status='approved', user_id=? WHERE login_token=?",
            (user_id, login_token),
        )
        self.conn.commit()
        return True

    def poll_device(self, device_code: str) -> dict[str, Any]:
        """에이전트가 폴링. approved이면 access token 발급."""
        cursor = self.conn.execute(
            "SELECT * FROM device_codes WHERE device_code=?", (device_code,)
        )
        row = cursor.fetchone()
        if row is None:
            return {"status": "invalid"}
        device = dict(row)

        expires_at = datetime.fromisoformat(device["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires_at:
            if device["status"] == "pending":
                self.conn.execute(
                    "UPDATE device_codes SET status='expired' WHERE device_code=?",
                    (device_code,),
                )
                self.conn.commit()
            return {"status": "expired"}

        if device["status"] == "pending":
            return {"status": "authorization_pending"}

        if device["status"] == "approved":
            user_id = device["user_id"]
            if user_id is None:
                return {"status": "error", "message": "No user linked"}
            token_result = self._issue_access_token(
                user_id=user_id,
                agent_id=device["agent_id"],
            )
            if token_result is None:
                return {"status": "error", "message": "No active subscription for user"}
            self.conn.execute(
                "UPDATE device_codes SET status='consumed' WHERE device_code=?",
                (device_code,),
            )
            self.conn.commit()
            return {"status": "approved", **token_result}

        return {"status": device["status"]}

    def _issue_access_token(
        self, user_id: int, agent_id: str, expires_days: int = 30
    ) -> dict[str, Any] | None:
        user = self.get_user_by_id(user_id)
        if user is None:
            return None
        subscription_id = user.get("subscription_id")
        if subscription_id is None:
            return None
        cursor = self.conn.execute(
            "SELECT * FROM subscriptions WHERE id=? AND status='active'",
            (subscription_id,),
        )
        sub = cursor.fetchone()
        if sub is None:
            return None
        sub_dict = dict(sub)

        token = secrets.token_urlsafe(48)
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(days=expires_days)).isoformat()
        self.conn.execute(
            """INSERT INTO access_tokens
               (token, user_id, agent_id, subscription_id, expires_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (token, user_id, agent_id, subscription_id, expires_at, now.isoformat()),
        )
        self.conn.commit()
        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_in": expires_days * 86400,
            "openai_api_key": sub_dict["openai_api_key"],
            "openai_model": sub_dict["openai_model"],
            "claude_api_key": sub_dict["claude_api_key"],
            "claude_model": sub_dict["claude_model"],
            "system_prompt": sub_dict["system_prompt"],
            "max_tokens": sub_dict["max_tokens"],
        }

    def validate_access_token(self, token: str) -> dict[str, Any] | None:
        """access token 검증."""
        cursor = self.conn.execute(
            "SELECT * FROM access_tokens WHERE token=?", (token,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        token_data = dict(row)
        expires_at = datetime.fromisoformat(token_data["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires_at:
            return None
        sub_id = token_data.get("subscription_id")
        if sub_id is None:
            return None
        cursor2 = self.conn.execute(
            "SELECT * FROM subscriptions WHERE id=? AND status='active'",
            (sub_id,),
        )
        sub = cursor2.fetchone()
        if sub is None:
            return None
        sd = dict(sub)
        return {
            "valid": True,
            "openai_api_key": sd["openai_api_key"],
            "openai_model": sd["openai_model"],
            "claude_api_key": sd["claude_api_key"],
            "claude_model": sd["claude_model"],
            "system_prompt": sd["system_prompt"],
            "max_tokens": sd["max_tokens"],
            "expires_at": token_data["expires_at"],
        }

    # ══════════════════════════════════════════════════
    # 구독 CRUD (기존)
    # ══════════════════════════════════════════════════

    def create_subscription(
        self,
        *,
        label: str = "",
        agent_id: str = "*",
        openai_api_key: str = "",
        openai_model: str = "gpt-4o-mini",
        claude_api_key: str = "",
        claude_model: str = "claude-sonnet-4-20250514",
        system_prompt: str = "",
        max_tokens: int = 2000,
        expires_at: str | None = None,
    ) -> dict[str, Any] | None:
        key = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO subscriptions
                (subscription_key, label, agent_id,
                 openai_api_key, openai_model, claude_api_key, claude_model,
                 system_prompt, max_tokens, status, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
            (
                key,
                label,
                agent_id,
                openai_api_key,
                openai_model,
                claude_api_key,
                claude_model,
                system_prompt,
                max_tokens,
                expires_at,
                now,
            ),
        )
        self.conn.commit()
        return self._get_by_key(key)

    def validate(
        self, subscription_key: str, agent_id: str = ""
    ) -> dict[str, Any] | None:
        row = self._get_by_key(subscription_key)
        if row is None or row["status"] != "active":
            return None
        expires_at = row.get("expires_at")
        if expires_at:
            try:
                exp = datetime.fromisoformat(expires_at)
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) > exp:
                    self.conn.execute(
                        "UPDATE subscriptions SET status='expired' WHERE subscription_key=?",
                        (subscription_key,),
                    )
                    self.conn.commit()
                    return None
            except (ValueError, TypeError):
                pass
        allowed = row.get("agent_id", "*")
        if allowed != "*" and agent_id and allowed != agent_id:
            return None
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE subscriptions SET last_validated_at=? WHERE subscription_key=?",
            (now, subscription_key),
        )
        self.conn.commit()
        return {
            "valid": True,
            "subscription_key": subscription_key,
            "label": row["label"],
            "agent_id": row["agent_id"],
            "openai_api_key": row["openai_api_key"],
            "openai_model": row["openai_model"],
            "claude_api_key": row["claude_api_key"],
            "claude_model": row["claude_model"],
            "system_prompt": row["system_prompt"],
            "max_tokens": row["max_tokens"],
            "status": row["status"],
            "expires_at": row.get("expires_at"),
        }

    def list_subscriptions(self) -> list[dict[str, Any]]:
        cursor = self.conn.execute(
            "SELECT * FROM subscriptions ORDER BY created_at DESC"
        )
        return [dict(r) for r in cursor.fetchall()]

    def get_subscription(self, subscription_key: str) -> dict[str, Any] | None:
        return self._get_by_key(subscription_key)

    def revoke(self, subscription_key: str) -> bool:
        cursor = self.conn.execute(
            "UPDATE subscriptions SET status='revoked' WHERE subscription_key=?",
            (subscription_key,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def update_keys(
        self,
        subscription_key: str,
        *,
        openai_api_key: str | None = None,
        claude_api_key: str | None = None,
    ) -> bool:
        parts: list[str] = []
        values: list[str] = []
        if openai_api_key is not None:
            parts.append("openai_api_key=?")
            values.append(openai_api_key)
        if claude_api_key is not None:
            parts.append("claude_api_key=?")
            values.append(claude_api_key)
        if not parts:
            return False
        values.append(subscription_key)
        cursor = self.conn.execute(
            f"UPDATE subscriptions SET {', '.join(parts)} WHERE subscription_key=?",
            values,
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def _get_by_key(self, key: str) -> dict[str, Any] | None:
        cursor = self.conn.execute(
            "SELECT * FROM subscriptions WHERE subscription_key=?", (key,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None
