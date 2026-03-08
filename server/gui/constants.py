"""서버 GUI 공용 상수 및 타입 (테마, 폰트, Protocol)."""

from __future__ import annotations

import sys
from queue import SimpleQueue
from typing import Any, Protocol

# ── 색상 ──
BG_DARK = "#252526"
BG_HEADER = "#007acc"
BG_FRAME = "#2d2d2d"
BG_BTN = "#3c3c3c"
BG_BTN_PRIMARY = "#0e639c"
FG_TEXT = "#d4d4d4"
FG_DIM = "#888888"
FG_WHITE = "white"

# ── 폰트 (플랫폼별) ──
if sys.platform == "darwin":
    FONT_FAMILY = "Helvetica Neue"
    FONT_FAMILY_BOLD = "Helvetica Neue"
    FONT_MONO_FAMILY = "Menlo"
elif sys.platform.startswith("linux"):
    FONT_FAMILY = "Ubuntu"
    FONT_FAMILY_BOLD = "Ubuntu"
    FONT_MONO_FAMILY = "DejaVu Sans Mono"
else:
    FONT_FAMILY = "Segoe UI"
    FONT_FAMILY_BOLD = "Segoe UI Semibold"
    FONT_MONO_FAMILY = "Consolas"

FONT_TITLE = (FONT_FAMILY_BOLD, 13)
FONT_HEADING = (FONT_FAMILY_BOLD, 10)
FONT_SUBHEADING = (FONT_FAMILY_BOLD, 9)
FONT_NORMAL = (FONT_FAMILY, 9)
FONT_SMALL = (FONT_FAMILY, 8)
FONT_MONO = (FONT_MONO_FAMILY, 9)
FONT_MONO_SMALL = (FONT_MONO_FAMILY, 8)
FONT_MONO_BOLD = (FONT_MONO_FAMILY, 8, "bold")


class ServerAppLike(Protocol):
    """탭에서 사용하는 ServerGUI 인터페이스."""

    _server_proc: Any
    _tcp_port: int
    _dashboard_port: int

    @property
    def dashboard_url(self) -> str: ...

    @property
    def auth_token(self) -> str: ...

    @property
    def server_log_queue(self) -> SimpleQueue[str]: ...

    def start_server(self) -> bool: ...

    def stop_server(self) -> bool: ...

    def is_server_running(self) -> bool: ...

    def api_get(self, path: str) -> dict[str, Any] | None: ...

    def api_post(self, path: str, body: dict[str, Any]) -> dict[str, Any] | None: ...

    def api_put(self, path: str, body: dict[str, Any]) -> dict[str, Any] | None: ...

    def api_delete(self, path: str) -> dict[str, Any] | None: ...

    def get_connected_agent_ids(self) -> list[str]: ...

    def api_deploy_upload(
        self, file_path: str, agent_id: str, target: str
    ) -> dict[str, Any] | None: ...
