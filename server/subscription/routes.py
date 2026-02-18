"""구독 인증 API 라우트."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from server.subscription.db import SubscriptionDB

router = APIRouter(prefix="/api/subscription", tags=["subscription"])

# ── 요청/응답 모델 ─────────────────────────────────


class ValidateRequest(BaseModel):
    subscription_key: str
    agent_id: str = ""


class ValidateResponse(BaseModel):
    valid: bool
    openai_api_key: str = ""
    openai_model: str = ""
    claude_api_key: str = ""
    claude_model: str = ""
    system_prompt: str = ""
    max_tokens: int = 2000
    expires_at: str | None = None
    message: str = ""


class CreateRequest(BaseModel):
    label: str = ""
    agent_id: str = "*"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    claude_api_key: str = ""
    claude_model: str = "claude-sonnet-4-20250514"
    system_prompt: str = ""
    max_tokens: int = 2000
    expires_at: str | None = None


class SubscriptionInfo(BaseModel):
    subscription_key: str
    label: str
    agent_id: str
    status: str
    openai_model: str
    claude_model: str
    expires_at: str | None
    created_at: str
    last_validated_at: str | None


class UpdateKeysRequest(BaseModel):
    openai_api_key: str | None = None
    claude_api_key: str | None = None


# ── 의존성 ─────────────────────────────────────────

_db_instance: SubscriptionDB | None = None


def set_db(db: SubscriptionDB) -> None:
    """앱 시작 시 DB 인스턴스를 주입한다."""
    global _db_instance  # noqa: PLW0603
    _db_instance = db


def get_db() -> SubscriptionDB:
    if _db_instance is None:
        raise RuntimeError("SubscriptionDB not initialized")
    return _db_instance


# ── 에이전트용 엔드포인트 ───────────────────────────


@router.post("/validate", response_model=ValidateResponse)
def validate_subscription(
    req: ValidateRequest,
    db: SubscriptionDB = Depends(get_db),
) -> ValidateResponse:
    """구독 키 검증. 유효하면 LLM API 키를 반환한다."""
    result = db.validate(req.subscription_key, req.agent_id)
    if result is None:
        return ValidateResponse(
            valid=False, message="Invalid or expired subscription key."
        )
    return ValidateResponse(
        valid=True,
        openai_api_key=result["openai_api_key"],
        openai_model=result["openai_model"],
        claude_api_key=result["claude_api_key"],
        claude_model=result["claude_model"],
        system_prompt=result["system_prompt"],
        max_tokens=result["max_tokens"],
        expires_at=result.get("expires_at"),
        message="Subscription valid.",
    )


# ── 관리자용 엔드포인트 ────────────────────────────

admin_router = APIRouter(prefix="/api/admin", tags=["admin"])


@admin_router.post("/subscription", response_model=dict[str, Any])
def create_subscription(
    req: CreateRequest,
    db: SubscriptionDB = Depends(get_db),
) -> dict[str, Any]:
    """새 구독 생성."""
    record = db.create_subscription(
        label=req.label,
        agent_id=req.agent_id,
        openai_api_key=req.openai_api_key,
        openai_model=req.openai_model,
        claude_api_key=req.claude_api_key,
        claude_model=req.claude_model,
        system_prompt=req.system_prompt,
        max_tokens=req.max_tokens,
        expires_at=req.expires_at,
    )
    if record is None:
        raise HTTPException(status_code=500, detail="Failed to create subscription")
    return record


@admin_router.get("/subscriptions", response_model=list[SubscriptionInfo])
def list_subscriptions(
    db: SubscriptionDB = Depends(get_db),
) -> list[dict[str, Any]]:
    """구독 목록 조회."""
    rows = db.list_subscriptions()
    # API 키는 마스킹
    for row in rows:
        for key_field in ("openai_api_key", "claude_api_key"):
            val = row.get(key_field, "")
            if val and len(val) > 8:
                row[key_field] = val[:4] + "****" + val[-4:]
    return rows


@admin_router.get("/subscription/{subscription_key}", response_model=dict[str, Any])
def get_subscription(
    subscription_key: str,
    db: SubscriptionDB = Depends(get_db),
) -> dict[str, Any]:
    """구독 상세 조회."""
    record = db.get_subscription(subscription_key)
    if record is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return record


@admin_router.delete("/subscription/{subscription_key}")
def revoke_subscription(
    subscription_key: str,
    db: SubscriptionDB = Depends(get_db),
) -> dict[str, str]:
    """구독 해지."""
    ok = db.revoke(subscription_key)
    if not ok:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return {"status": "revoked", "subscription_key": subscription_key}


@admin_router.patch("/subscription/{subscription_key}/keys")
def update_subscription_keys(
    subscription_key: str,
    req: UpdateKeysRequest,
    db: SubscriptionDB = Depends(get_db),
) -> dict[str, str]:
    """구독의 LLM API 키 업데이트."""
    ok = db.update_keys(
        subscription_key,
        openai_api_key=req.openai_api_key,
        claude_api_key=req.claude_api_key,
    )
    if not ok:
        raise HTTPException(
            status_code=404, detail="Subscription not found or no changes"
        )
    return {"status": "updated", "subscription_key": subscription_key}
