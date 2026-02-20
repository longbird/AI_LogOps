"""HTML page routes for AirREC Web UI."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from server.airec.storage import RecordingStorage


def create_views_router(storage: RecordingStorage) -> APIRouter:
    """Create views router with storage dependency."""
    router = APIRouter(prefix="/airec", tags=["airec-views"])

    template_dir = Path(__file__).parent.parent / "templates"
    templates = Jinja2Templates(directory=str(template_dir))

    # Index redirect
    @router.get("/")
    def index():
        return RedirectResponse(url="/airec/recordings")

    # Recordings list
    @router.get("/recordings")
    def recordings_page(
        request: Request,
        agent_id: str = Query(default=""),
        date: str = Query(default=""),
        page: int = Query(default=1, ge=1),
        per_page: int = Query(default=50, ge=10, le=200),
    ):
        if not date:
            date = datetime.now().strftime("%Y%m%d")

        all_recs = storage.list_recordings(
            agent_id=agent_id or None,
            date_str=date or None,
        )

        # In-memory filtering for strict agent matching if needed
        if agent_id:
            all_recs = [r for r in all_recs if r["agent_id"] == agent_id]

        total = len(all_recs)
        total_pages = max(1, (total + per_page - 1) // per_page)
        offset = (page - 1) * per_page
        recordings = all_recs[offset : offset + per_page]

        return templates.TemplateResponse(
            request=request,
            name="recordings.html",
            context={
                "recordings": recordings,
                "agent_id": agent_id,
                "date": date,
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": total_pages,
            },
        )

    # Recording detail
    @router.get("/recording/{filename:path}")
    def recording_detail(request: Request, filename: str):
        file_path = storage.find_by_filename(filename)

        if file_path is None:
            from fastapi.responses import HTMLResponse

            return HTMLResponse("<h3>Recording not found</h3>", status_code=404)

        rec = {
            "filename": filename,
            "file_size": file_path.stat().st_size,
            "date": file_path.parent.name,
            "agent_id": file_path.parent.parent.name,
            "path": str(file_path),
        }

        return templates.TemplateResponse(
            request=request,
            name="detail.html",
            context={
                "rec": rec,
                "transcript": None,
                "quality": None,
                "segments": [],
            },
        )

    # Dashboard
    @router.get("/dashboard")
    def dashboard_page(
        request: Request,
        date: str = Query(default=""),
    ):
        if not date:
            date = datetime.now().strftime("%Y%m%d")

        # Compute stats from file storage
        all_recs = storage.list_recordings(date_str=date)
        total_today = len(all_recs)

        # Group by agent_id
        agent_counts: dict[str, int] = {}
        for r in all_recs:
            aid = str(r["agent_id"])
            agent_counts[aid] = agent_counts.get(aid, 0) + 1

        ext_stats = [
            {"ext": aid, "cnt": cnt, "avg_score": 0}
            for aid, cnt in sorted(agent_counts.items(), key=lambda x: -x[1])
        ]

        # Get recent 7 days
        daily_stats = []
        try:
            base_date = datetime.strptime(date, "%Y%m%d")
        except ValueError:
            base_date = datetime.now()

        for i in range(6, -1, -1):
            d = base_date - timedelta(days=i)
            ds = d.strftime("%Y%m%d")
            recs = storage.list_recordings(date_str=ds)
            daily_stats.append(
                {
                    "oper_day": ds,
                    "total": len(recs),
                    "transcribed": 0,
                    "avg_score": 0,
                }
            )

        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "date": date,
                "total_today": total_today,
                "transcribed_today": 0,
                "avg_score_today": 0,
                "ext_stats": ext_stats,
                "daily_stats": daily_stats,
            },
        )

    return router
