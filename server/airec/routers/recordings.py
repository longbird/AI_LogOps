"""Recording list endpoint.

Lists stored recordings with filtering by agent_id and date.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from server.airec.storage import RecordingStorage


def create_recordings_router(storage: RecordingStorage) -> APIRouter:
    """Create recordings list router with storage dependency."""
    router = APIRouter(prefix="/api/rec", tags=["airec-recordings"])

    def list_recordings(
        agent_id: Annotated[str | None, Query()] = None,
        date: Annotated[str | None, Query(description="YYYYMMDD")] = None,
        page: Annotated[int, Query(ge=1)] = 1,
        size: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, object]:
        """List stored recordings with optional filters."""
        all_recs = storage.list_recordings(
            agent_id=agent_id,
            date_str=date,
        )

        total = len(all_recs)
        offset = (page - 1) * size
        items = all_recs[offset : offset + size]

        return {
            "total": total,
            "page": page,
            "size": size,
            "items": items,
        }

    router.add_api_route("/recordings", list_recordings, methods=["GET"])

    return router
