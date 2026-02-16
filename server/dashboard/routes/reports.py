from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path
from typing import Protocol, TypedDict, cast

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()


class StorageManagerLike(Protocol):
    base_dir: Path


class DashboardState(Protocol):
    templates: Jinja2Templates
    storage_mgr: StorageManagerLike | None


class ReportRow(TypedDict):
    agent_id: str
    filename: str
    size: int
    modified: str


def _state(request: Request) -> DashboardState:
    return cast(DashboardState, request.app.state)  # pyright: ignore[reportAny]


@router.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request) -> HTMLResponse:
    """AI 리포트 목록 페이지."""
    state = _state(request)
    templates = state.templates
    storage_mgr = state.storage_mgr
    reports = _list_reports(storage_mgr)
    return cast(
        HTMLResponse,
        templates.TemplateResponse(
            "reports.html",
            {
                "request": request,
                "title": "AI Reports",
                "reports": reports,
            },
        ),
    )


@router.get("/api/reports/{agent_id}/{report_name}", response_class=HTMLResponse)
async def get_report(request: Request, agent_id: str, report_name: str) -> HTMLResponse:
    """리포트 내용 반환. Markdown은 HTML로 변환."""
    storage_mgr = _state(request).storage_mgr
    if storage_mgr is None:
        return HTMLResponse("<p>Storage not configured.</p>")

    report_path = Path(storage_mgr.base_dir) / "reports" / agent_id / report_name
    if not report_path.exists():
        return HTMLResponse(
            f"<p>Report not found: {escape(report_name)}</p>", status_code=404
        )

    content = report_path.read_text(encoding="utf-8")

    if report_name.endswith(".md"):
        content = _markdown_to_html(content)
    else:
        content = (
            "<pre class='bg-gray-100 p-4 rounded overflow-auto'>"
            f"{escape(content)}"
            "</pre>"
        )
    return HTMLResponse(content)


def _list_reports(storage_mgr: StorageManagerLike | None) -> list[ReportRow]:
    """storage에서 리포트 목록 추출."""
    if storage_mgr is None:
        return []

    reports_dir = Path(storage_mgr.base_dir) / "reports"
    if not reports_dir.exists():
        return []

    reports: list[ReportRow] = []
    for agent_dir in sorted(reports_dir.iterdir()):
        if not agent_dir.is_dir():
            continue
        for report_file in sorted(agent_dir.iterdir()):
            if not report_file.is_file():
                continue
            stat = report_file.stat()
            reports.append(
                {
                    "agent_id": agent_dir.name,
                    "filename": report_file.name,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).strftime(
                        "%Y-%m-%d %H:%M"
                    ),
                }
            )

    return reports


def _markdown_to_html(md_text: str) -> str:
    """Simple markdown to HTML. Use basic conversion without external deps."""
    import re

    html = escape(md_text)
    html = re.sub(
        r"^### (.+)$",
        r'<h3 class="text-lg font-semibold mt-4 mb-2">\1</h3>',
        html,
        flags=re.MULTILINE,
    )
    html = re.sub(
        r"^## (.+)$",
        r'<h2 class="text-xl font-bold mt-6 mb-3">\1</h2>',
        html,
        flags=re.MULTILINE,
    )
    html = re.sub(
        r"^# (.+)$",
        r'<h1 class="text-2xl font-bold mt-6 mb-3">\1</h1>',
        html,
        flags=re.MULTILINE,
    )
    html = re.sub(r"^- (.+)$", r'<li class="ml-4">\1</li>', html, flags=re.MULTILINE)
    html = re.sub(r"\n\n", '</p><p class="my-2">', html)

    return f'<div class="prose max-w-none"><p class="my-2">{html}</p></div>'
