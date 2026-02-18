"""구독 인증 서버 FastAPI 앱."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fastapi import FastAPI

from server.subscription.auth_routes import auth_router
from server.subscription.db import SubscriptionDB
from server.subscription.routes import admin_router, router, set_db

app = FastAPI(
    title="AI-LogOps Subscription Server",
    description="구독 키 관리 + OAuth Device Flow 인증 서버",
    version="1.0.0",
)

app.include_router(router)
app.include_router(admin_router)
app.include_router(auth_router)

_db: SubscriptionDB | None = None


@app.on_event("startup")
def _startup() -> None:
    global _db  # noqa: PLW0603
    db_path = Path(__file__).resolve().parent / "subscriptions.db"
    _db = SubscriptionDB(db_path)
    _db.open()
    set_db(_db)


@app.on_event("shutdown")
def _shutdown() -> None:
    if _db is not None:
        _db.close()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def main() -> None:
    """CLI로 서버 실행."""
    parser = argparse.ArgumentParser(description="AI-LogOps Subscription Server")
    parser.add_argument(
        "--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port", type=int, default=8500, help="Bind port (default: 8500)"
    )
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError:
        print(
            "ERROR: uvicorn is required. Install with: pip install uvicorn",
            file=sys.stderr,
        )
        sys.exit(1)

    uvicorn.run(
        "server.subscription.app:app",
        host=args.host,
        port=args.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
