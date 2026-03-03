"""서버 GUI 공용 상수 및 타입 (테마, 폰트, Protocol)."""

from __future__ import annotations

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

# ── 폰트 ──
FONT_TITLE = ("Segoe UI Semibold", 13)
FONT_NORMAL = ("Segoe UI", 9)
FONT_MONO = ("Consolas", 9)


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

    def api_deploy_upload(
        self, file_path: str, agent_id: str, target: str
    ) -> dict[str, Any] | None: ...
