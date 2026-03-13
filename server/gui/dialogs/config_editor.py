"""에이전트 설정 원격 편집 다이얼로그."""
from __future__ import annotations

import json
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Any

from server.gui.constants import (
    BG_DARK,
    BG_FRAME,
    BG_BTN,
    BG_BTN_PRIMARY,
    Button,
    FG_DIM,
    FG_TEXT,
    FG_WHITE,
    FONT_HEADING,
    FONT_MONO,
    FONT_NORMAL,
    FONT_SMALL,
    FONT_SUBHEADING,
    ServerAppLike,
)

# Sensitive field pattern detection
SENSITIVE_PATTERNS = ("password", "token", "api_key", "secret", "bot_token")
MASK_VALUE = "********"

# 긴 경로 문자열을 표시할 필드 패턴
PATH_PATTERNS = ("path", "dir", "folder", "watch_dir", "upload_base_url", "server_url")

# Section display names (Korean)
SECTION_LABELS: dict[str, str] = {
    "agent": "에이전트 기본",
    "connection": "서버 연결",
    "servers": "서버 목록 (멀티)",
    "monitoring": "모니터링",
    "telegram": "텔레그램",
    "llm": "LLM 설정",
    "recording": "녹취 설정",
    "target_process": "대상 프로세스",
    "schedule": "스케줄",
    "rec_client": "녹취 클라이언트",
    "watch_folders": "감시 폴더",
    "remote_commands": "원격 커맨드",
}

# 서버 항목의 기본 템플릿
_SERVER_TEMPLATE: dict[str, Any] = {
    "name": "",
    "host": "",
    "port": 9500,
    "token": "",
    "heartbeat_interval": 30,
    "reconnect_delay": 60,
}

# 감시 폴더 항목의 기본 템플릿
_WATCH_FOLDER_TEMPLATE: dict[str, Any] = {
    "name": "",
    "path": "",
    "description": "",
}

# 원격 커맨드 항목의 기본 템플릿
_REMOTE_COMMAND_TEMPLATE: dict[str, Any] = {
    "name": "",
    "command": "",
    "args": [],
    "working_dir": "",
    "timeout": 60,
    "description": "",
}


def _is_sensitive(key: str) -> bool:
    lower = key.lower()
    return any(p in lower for p in SENSITIVE_PATTERNS)


def _is_path_field(key: str) -> bool:
    lower = key.lower()
    return any(p in lower for p in PATH_PATTERNS)


class ConfigEditorDialog(tk.Toplevel):
    """에이전트 설정 원격 편집 다이얼로그."""

    def __init__(self, parent: tk.Widget, app: ServerAppLike, agent_id: str) -> None:
        super().__init__(parent)
        self._app = app
        self._agent_id = agent_id
        self._config: dict[str, Any] = {}
        self._field_vars: dict[str, Any] = {}  # dotted key -> tk variable or widget
        self._sensitive_visible: dict[str, bool] = {}  # track show/hide state

        self.title(f"설정 편집 - {agent_id}")
        self.geometry("850x620")
        self.configure(bg=BG_DARK)
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()

        self._build_ui()
        self.after(200, self._fetch_config)

    def _build_ui(self) -> None:
        # ── Top bar ──
        top = tk.Frame(self, bg=BG_FRAME, padx=10, pady=6)
        top.pack(fill=tk.X, padx=8, pady=(8, 4))

        tk.Label(
            top, text=f"Agent: {self._agent_id}",
            bg=BG_FRAME, fg=FG_TEXT, font=FONT_HEADING,
        ).pack(side=tk.LEFT)

        self._status_label = tk.Label(
            top, text="", bg=BG_FRAME, fg=FG_DIM, font=FONT_SMALL,
        )
        self._status_label.pack(side=tk.RIGHT, padx=(8, 0))

        Button(
            top, text="불러오기", command=self._fetch_config,
            bg=BG_BTN, fg=FG_TEXT, relief=tk.FLAT, font=FONT_SMALL,
            padx=10, cursor="hand2",
        ).pack(side=tk.RIGHT, padx=4)

        # ── Main content (PanedWindow) ──
        paned = tk.PanedWindow(
            self, orient=tk.HORIZONTAL, bg=BG_DARK,
            sashwidth=4, sashrelief=tk.FLAT,
        )
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        # Left: Section list
        left_frame = tk.Frame(paned, bg=BG_FRAME, padx=6, pady=6)
        paned.add(left_frame, minsize=160, width=180)

        tk.Label(
            left_frame, text="설정 섹션",
            bg=BG_FRAME, fg=FG_TEXT, font=FONT_SUBHEADING,
        ).pack(fill=tk.X, pady=(0, 4))

        self._section_listbox = tk.Listbox(
            left_frame, bg="#1e1e1e", fg=FG_TEXT,
            selectbackground="#264f78", selectforeground=FG_WHITE,
            font=FONT_NORMAL, relief=tk.FLAT, borderwidth=0,
            activestyle="none",
        )
        self._section_listbox.pack(fill=tk.BOTH, expand=True)
        self._section_listbox.bind("<<ListboxSelect>>", self._on_section_selected)

        # Right: Form area
        right_frame = tk.Frame(paned, bg=BG_FRAME, padx=6, pady=6)
        paned.add(right_frame, minsize=400)

        self._form_title = tk.Label(
            right_frame, text="섹션을 선택하세요",
            bg=BG_FRAME, fg=FG_TEXT, font=FONT_HEADING,
        )
        self._form_title.pack(fill=tk.X, pady=(0, 6))

        # Scrollable form container
        canvas = tk.Canvas(right_frame, bg=BG_FRAME, highlightthickness=0)
        scrollbar = ttk.Scrollbar(right_frame, orient=tk.VERTICAL, command=canvas.yview)
        self._form_frame = tk.Frame(canvas, bg=BG_FRAME)

        self._form_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=self._form_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Mouse wheel scroll
        import sys
        def _on_mousewheel(event: Any) -> None:
            if sys.platform == "darwin":
                canvas.yview_scroll(-event.delta, "units")
            elif sys.platform.startswith("linux"):
                pass  # handled by Button-4/Button-5
            else:
                canvas.yview_scroll(-event.delta // 120, "units")

        canvas.bind("<MouseWheel>", _on_mousewheel)
        if sys.platform.startswith("linux"):
            canvas.bind("<Button-4>", lambda e: canvas.yview_scroll(-3, "units"))
            canvas.bind("<Button-5>", lambda e: canvas.yview_scroll(3, "units"))

        self._canvas = canvas

        # ── Bottom bar ──
        bottom = tk.Frame(self, bg=BG_DARK, padx=10, pady=8)
        bottom.pack(fill=tk.X, padx=8, pady=(4, 8))

        self._apply_status = tk.Label(
            bottom, text="", bg=BG_DARK, fg=FG_DIM, font=FONT_SMALL,
        )
        self._apply_status.pack(side=tk.LEFT)

        Button(
            bottom, text="닫기", command=self.destroy,
            bg=BG_BTN, fg=FG_TEXT, relief=tk.FLAT, font=FONT_NORMAL,
            padx=16, cursor="hand2",
        ).pack(side=tk.RIGHT, padx=4)

        Button(
            bottom, text="적용", command=self._apply_config,
            bg=BG_BTN_PRIMARY, fg=FG_WHITE, relief=tk.FLAT, font=FONT_NORMAL,
            padx=16, cursor="hand2",
        ).pack(side=tk.RIGHT, padx=4)

    def _fetch_config(self) -> None:
        """API를 통해 에이전트 설정을 가져옵니다."""
        self._set_status("설정 불러오는 중...")
        result = self._app.api_get(f"/api/config/{self._agent_id}")
        if result is None:
            self._set_status("설정 불러오기 실패 (서버 응답 없음)")
            return
        if "error" in result:
            self._set_status(f"오류: {result['error']}")
            return
        config = result.get("config", {})
        if not config:
            self._set_status("빈 설정 수신됨")
            return
        self._config = config
        self._populate_sections()
        self._set_status("설정 불러오기 완료")

    def _populate_sections(self) -> None:
        """섹션 리스트를 채웁니다."""
        self._section_listbox.delete(0, tk.END)
        for key in self._config:
            label = SECTION_LABELS.get(key, key)
            self._section_listbox.insert(tk.END, label)
        if self._section_listbox.size() > 0:
            self._section_listbox.selection_set(0)
            self._on_section_selected(None)

    def _on_section_selected(self, _event: Any) -> None:
        """섹션 선택 시 폼을 다시 빌드합니다."""
        sel = self._section_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        keys = list(self._config.keys())
        if idx >= len(keys):
            return
        section_key = keys[idx]
        section_data = self._config[section_key]
        label = SECTION_LABELS.get(section_key, section_key)
        self._form_title.configure(text=label)
        self._build_form(section_key, section_data)

    def _build_form(self, section_key: str, data: Any, prefix: str = "") -> None:
        """섹션 데이터에 따라 폼 위젯을 동적으로 생성합니다."""
        # Clear existing form widgets and field vars for this section
        for w in self._form_frame.winfo_children():
            w.destroy()
        # Clear field vars belonging to this section
        to_remove = [k for k in self._field_vars if k.startswith(f"{section_key}.")]
        for k in to_remove:
            del self._field_vars[k]

        full_prefix = f"{section_key}." if not prefix else prefix

        if section_key == "servers" and isinstance(data, list):
            self._render_servers_list(self._form_frame, data, full_prefix)
        elif section_key == "watch_folders" and isinstance(data, list):
            self._render_list_of_dicts(
                self._form_frame, data, full_prefix,
                item_label="폴더", template=_WATCH_FOLDER_TEMPLATE,
                add_callback=lambda: self._add_list_entry("watch_folders", _WATCH_FOLDER_TEMPLATE),
                remove_callback=lambda i: self._remove_list_entry("watch_folders", i),
                name_color="#4ec9b0",
            )
        elif section_key == "remote_commands" and isinstance(data, list):
            self._render_list_of_dicts(
                self._form_frame, data, full_prefix,
                item_label="커맨드", template=_REMOTE_COMMAND_TEMPLATE,
                add_callback=lambda: self._add_list_entry("remote_commands", _REMOTE_COMMAND_TEMPLATE),
                remove_callback=lambda i: self._remove_list_entry("remote_commands", i),
                name_color="#ce9178",
            )
        elif section_key == "schedule" and isinstance(data, dict):
            self._render_schedule(self._form_frame, data, full_prefix)
        elif isinstance(data, dict):
            self._render_dict(self._form_frame, data, full_prefix, 0)

        # Reset scroll position
        self._canvas.yview_moveto(0)

    # ── servers (list[dict]) 렌더링 ──

    def _render_servers_list(
        self, parent: tk.Widget, servers: list, prefix: str
    ) -> None:
        """서버 목록(list[dict])을 렌더링합니다."""
        # 서버 추가/삭제 버튼 영역
        btn_frame = tk.Frame(parent, bg=BG_FRAME)
        btn_frame.pack(fill=tk.X, pady=(0, 6))

        tk.Label(
            btn_frame, text=f"서버 {len(servers)}개 설정됨",
            bg=BG_FRAME, fg=FG_DIM, font=FONT_SMALL,
        ).pack(side=tk.LEFT)

        Button(
            btn_frame, text="+ 서버 추가",
            command=lambda: self._add_server_entry(),
            bg=BG_BTN, fg=FG_TEXT, relief=tk.FLAT, font=FONT_SMALL,
            padx=8, cursor="hand2",
        ).pack(side=tk.RIGHT, padx=4)

        for i, srv in enumerate(servers):
            if not isinstance(srv, dict):
                continue
            self._render_server_entry(parent, srv, prefix, i, len(servers))

    def _render_server_entry(
        self, parent: tk.Widget, srv: dict, prefix: str, idx: int, total: int
    ) -> None:
        """단일 서버 항목을 렌더링합니다."""
        # 서버 헤더
        header_frame = tk.Frame(parent, bg="#333333")
        header_frame.pack(fill=tk.X, pady=(6, 2), padx=4)

        srv_name = srv.get("name", f"server-{idx}")
        tk.Label(
            header_frame, text=f"  [{idx}] {srv_name}",
            bg="#333333", fg="#569cd6", font=FONT_SUBHEADING, anchor="w",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        Button(
            header_frame, text="삭제",
            command=lambda i=idx: self._remove_server_entry(i),
            bg="#5a1d1d", fg="#ff6b6b", relief=tk.FLAT, font=FONT_SMALL,
            padx=6, cursor="hand2",
        ).pack(side=tk.RIGHT, padx=4, pady=2)

        # 서버 필드들
        item_prefix = f"{prefix}{idx}."
        for key, value in srv.items():
            dotted = f"{item_prefix}{key}"
            row = tk.Frame(parent, bg=BG_FRAME)
            row.pack(fill=tk.X, pady=2, padx=(24, 8))

            tk.Label(
                row, text=key, bg=BG_FRAME, fg=FG_DIM,
                font=FONT_NORMAL, width=20, anchor="w",
            ).pack(side=tk.LEFT)

            if isinstance(value, bool):
                var = tk.BooleanVar(value=value)
                tk.Checkbutton(
                    row, variable=var, bg=BG_FRAME, fg=FG_TEXT,
                    selectcolor="#1e1e1e", activebackground=BG_FRAME,
                ).pack(side=tk.LEFT)
                self._field_vars[dotted] = var
            elif isinstance(value, int):
                var = tk.StringVar(value=str(value))
                tk.Entry(
                    row, textvariable=var, bg="#1e1e1e", fg=FG_TEXT,
                    font=FONT_MONO, insertbackground=FG_TEXT,
                    relief=tk.FLAT, width=12,
                ).pack(side=tk.LEFT)
                self._field_vars[dotted] = var
            elif _is_sensitive(key):
                var = tk.StringVar(value=str(value) if value else "")
                entry = tk.Entry(
                    row, textvariable=var, bg="#1e1e1e", fg=FG_TEXT,
                    font=FONT_MONO, insertbackground=FG_TEXT,
                    relief=tk.FLAT, show="*",
                )
                entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
                self._field_vars[dotted] = var
                self._sensitive_visible[dotted] = False

                def _toggle(e=entry, d=dotted):
                    vis = self._sensitive_visible.get(d, False)
                    e.configure(show="" if not vis else "*")
                    self._sensitive_visible[d] = not vis

                Button(
                    row, text="보기", command=_toggle,
                    bg=BG_BTN, fg=FG_DIM, relief=tk.FLAT, font=FONT_SMALL,
                    padx=6, cursor="hand2",
                ).pack(side=tk.LEFT)
            else:
                var = tk.StringVar(value=str(value) if value is not None else "")
                tk.Entry(
                    row, textvariable=var, bg="#1e1e1e", fg=FG_TEXT,
                    font=FONT_MONO, insertbackground=FG_TEXT, relief=tk.FLAT,
                ).pack(side=tk.LEFT, fill=tk.X, expand=True)
                self._field_vars[dotted] = var

    def _add_server_entry(self) -> None:
        """서버 목록에 새 항목을 추가합니다."""
        servers = self._config.get("servers", [])
        if not isinstance(servers, list):
            servers = []
        servers.append(dict(_SERVER_TEMPLATE))
        self._config["servers"] = servers
        self._build_form("servers", servers)

    def _remove_server_entry(self, idx: int) -> None:
        """서버 목록에서 항목을 삭제합니다."""
        servers = self._config.get("servers", [])
        if isinstance(servers, list) and 0 <= idx < len(servers):
            servers.pop(idx)
            self._config["servers"] = servers
            self._build_form("servers", servers)

    # ── list[dict] 범용 렌더링 (watch_folders, remote_commands) ──

    def _render_list_of_dicts(
        self,
        parent: tk.Widget,
        items: list,
        prefix: str,
        item_label: str,
        template: dict[str, Any],
        add_callback: Any,
        remove_callback: Any,
        name_color: str = "#569cd6",
    ) -> None:
        """list[dict] 구조를 렌더링합니다."""
        btn_frame = tk.Frame(parent, bg=BG_FRAME)
        btn_frame.pack(fill=tk.X, pady=(0, 6))

        tk.Label(
            btn_frame, text=f"{item_label} {len(items)}개 설정됨",
            bg=BG_FRAME, fg=FG_DIM, font=FONT_SMALL,
        ).pack(side=tk.LEFT)

        Button(
            btn_frame, text=f"+ {item_label} 추가",
            command=add_callback,
            bg=BG_BTN, fg=FG_TEXT, relief=tk.FLAT, font=FONT_SMALL,
            padx=8, cursor="hand2",
        ).pack(side=tk.RIGHT, padx=4)

        for i, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            self._render_list_item(
                parent, item, prefix, i, len(items),
                item_label=item_label,
                remove_callback=remove_callback,
                name_color=name_color,
            )

    def _render_list_item(
        self,
        parent: tk.Widget,
        item: dict,
        prefix: str,
        idx: int,
        total: int,
        item_label: str,
        remove_callback: Any,
        name_color: str,
    ) -> None:
        """단일 list[dict] 항목을 렌더링합니다."""
        header_frame = tk.Frame(parent, bg="#333333")
        header_frame.pack(fill=tk.X, pady=(6, 2), padx=4)

        item_name = item.get("name", f"{item_label}-{idx}")
        tk.Label(
            header_frame, text=f"  [{idx}] {item_name}",
            bg="#333333", fg=name_color, font=FONT_SUBHEADING, anchor="w",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        Button(
            header_frame, text="삭제",
            command=lambda i=idx: remove_callback(i),
            bg="#5a1d1d", fg="#ff6b6b", relief=tk.FLAT, font=FONT_SMALL,
            padx=6, cursor="hand2",
        ).pack(side=tk.RIGHT, padx=4, pady=2)

        item_prefix = f"{prefix}{idx}."
        for key, value in item.items():
            dotted = f"{item_prefix}{key}"
            row = tk.Frame(parent, bg=BG_FRAME)
            row.pack(fill=tk.X, pady=2, padx=(24, 8))

            tk.Label(
                row, text=key, bg=BG_FRAME, fg=FG_DIM,
                font=FONT_NORMAL, width=20, anchor="w",
            ).pack(side=tk.LEFT)

            if isinstance(value, bool):
                var = tk.BooleanVar(value=value)
                tk.Checkbutton(
                    row, variable=var, bg=BG_FRAME, fg=FG_TEXT,
                    selectcolor="#1e1e1e", activebackground=BG_FRAME,
                ).pack(side=tk.LEFT)
                self._field_vars[dotted] = var
            elif isinstance(value, list):
                text = tk.Text(
                    row, bg="#1e1e1e", fg=FG_TEXT, font=FONT_MONO,
                    height=min(len(value) + 1, 3), width=40,
                    insertbackground=FG_TEXT, relief=tk.FLAT, borderwidth=1,
                )
                text.insert("1.0", "\n".join(str(v) for v in value))
                text.pack(side=tk.LEFT, fill=tk.X, expand=True)
                self._field_vars[dotted] = text
            elif isinstance(value, int):
                var = tk.StringVar(value=str(value))
                tk.Entry(
                    row, textvariable=var, bg="#1e1e1e", fg=FG_TEXT,
                    font=FONT_MONO, insertbackground=FG_TEXT,
                    relief=tk.FLAT, width=12,
                ).pack(side=tk.LEFT)
                self._field_vars[dotted] = var
            elif _is_sensitive(key):
                var = tk.StringVar(value=str(value) if value else "")
                entry = tk.Entry(
                    row, textvariable=var, bg="#1e1e1e", fg=FG_TEXT,
                    font=FONT_MONO, insertbackground=FG_TEXT,
                    relief=tk.FLAT, show="*",
                )
                entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
                self._field_vars[dotted] = var
                self._sensitive_visible[dotted] = False

                def _toggle(e=entry, d=dotted):
                    vis = self._sensitive_visible.get(d, False)
                    e.configure(show="" if not vis else "*")
                    self._sensitive_visible[d] = not vis

                Button(
                    row, text="보기", command=_toggle,
                    bg=BG_BTN, fg=FG_DIM, relief=tk.FLAT, font=FONT_SMALL,
                    padx=6, cursor="hand2",
                ).pack(side=tk.LEFT)
            else:
                var = tk.StringVar(value=str(value) if value is not None else "")
                tk.Entry(
                    row, textvariable=var, bg="#1e1e1e", fg=FG_TEXT,
                    font=FONT_MONO, insertbackground=FG_TEXT, relief=tk.FLAT,
                ).pack(side=tk.LEFT, fill=tk.X, expand=True)
                self._field_vars[dotted] = var

    def _add_list_entry(self, section_key: str, template: dict[str, Any]) -> None:
        """list[dict] 섹션에 새 항목을 추가합니다."""
        items = self._config.get(section_key, [])
        if not isinstance(items, list):
            items = []
        items.append(dict(template))
        self._config[section_key] = items
        self._build_form(section_key, items)

    def _remove_list_entry(self, section_key: str, idx: int) -> None:
        """list[dict] 섹션에서 항목을 삭제합니다."""
        items = self._config.get(section_key, [])
        if isinstance(items, list) and 0 <= idx < len(items):
            items.pop(idx)
            self._config[section_key] = items
            self._build_form(section_key, items)

    # ── schedule 렌더링 (enabled 체크박스 추가) ──

    def _render_schedule(
        self, parent: tk.Widget, data: dict, prefix: str
    ) -> None:
        """스케줄 섹션을 렌더링합니다 (사용여부 체크박스 포함)."""
        # enabled 체크박스 (config에 없어도 UI에서 추가)
        enabled_key = f"{prefix}enabled"
        restart_times = data.get("restart_times", [])
        # enabled 판단: restart_times가 비어있지 않으면 사용 중
        is_enabled = data.get("enabled", len(restart_times) > 0)

        enabled_var = tk.BooleanVar(value=is_enabled)
        self._field_vars[enabled_key] = enabled_var

        enable_row = tk.Frame(parent, bg=BG_FRAME)
        enable_row.pack(fill=tk.X, pady=(4, 8), padx=8)

        tk.Label(
            enable_row, text="사용", bg=BG_FRAME, fg=FG_DIM,
            font=FONT_NORMAL, width=20, anchor="w",
        ).pack(side=tk.LEFT)

        cb = tk.Checkbutton(
            enable_row, variable=enabled_var, bg=BG_FRAME, fg=FG_TEXT,
            selectcolor="#1e1e1e", activebackground=BG_FRAME,
            activeforeground=FG_TEXT, text="스케줄 재시작 활성화",
            command=lambda: self._toggle_schedule_fields(fields_frame, enabled_var),
        )
        cb.pack(side=tk.LEFT)

        # 나머지 필드를 담을 프레임 (enabled에 따라 활성/비활성)
        fields_frame = tk.Frame(parent, bg=BG_FRAME)
        fields_frame.pack(fill=tk.X)

        # restart_times
        times_key = f"{prefix}restart_times"
        row = tk.Frame(fields_frame, bg=BG_FRAME)
        row.pack(fill=tk.X, pady=2, padx=8)

        tk.Label(
            row, text="restart_times", bg=BG_FRAME, fg=FG_DIM,
            font=FONT_NORMAL, width=20, anchor="w",
        ).pack(side=tk.LEFT)

        text = tk.Text(
            row, bg="#1e1e1e", fg=FG_TEXT, font=FONT_MONO,
            height=min(len(restart_times) + 1, 4), width=40,
            insertbackground=FG_TEXT, relief=tk.FLAT, borderwidth=1,
        )
        text.insert("1.0", "\n".join(str(v) for v in restart_times))
        text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._field_vars[times_key] = text

        tk.Label(
            fields_frame, text="HH:MM 형식으로 한 줄에 하나씩 입력 (예: 06:00)",
            bg=BG_FRAME, fg=FG_DIM, font=FONT_SMALL,
        ).pack(anchor="w", padx=28, pady=(2, 0))

        # 초기 상태 반영
        self._toggle_schedule_fields(fields_frame, enabled_var)

    def _toggle_schedule_fields(
        self, fields_frame: tk.Frame, enabled_var: tk.BooleanVar
    ) -> None:
        """스케줄 사용여부에 따라 필드를 활성/비활성화합니다."""
        state = "normal" if enabled_var.get() else "disabled"
        for child in fields_frame.winfo_children():
            for widget in child.winfo_children():
                try:
                    widget.configure(state=state)
                except tk.TclError:
                    pass
            # Text 위젯은 자식이 아닌 직접 포함된 경우도 처리
            try:
                child.configure(state=state)
            except tk.TclError:
                pass

    # ── 일반 dict 렌더링 ──

    def _render_dict(
        self, parent: tk.Widget, data: dict, prefix: str, depth: int
    ) -> None:
        """딕셔너리를 폼 필드로 재귀적으로 렌더링합니다."""
        for key, value in data.items():
            dotted = f"{prefix}{key}"

            if isinstance(value, dict):
                # Sub-section header
                sub_frame = tk.Frame(parent, bg=BG_FRAME)
                sub_frame.pack(fill=tk.X, pady=(8, 2), padx=(depth * 16, 0))
                tk.Label(
                    sub_frame, text=f"  {key}", bg=BG_FRAME, fg="#569cd6",
                    font=FONT_SUBHEADING, anchor="w",
                ).pack(fill=tk.X)
                separator = tk.Frame(sub_frame, bg="#444444", height=1)
                separator.pack(fill=tk.X, pady=(2, 0))
                self._render_dict(parent, value, f"{dotted}.", depth + 1)
                continue

            row = tk.Frame(parent, bg=BG_FRAME)
            row.pack(fill=tk.X, pady=2, padx=(depth * 16 + 8, 8))

            # Label
            tk.Label(
                row, text=key, bg=BG_FRAME, fg=FG_DIM,
                font=FONT_NORMAL, width=20, anchor="w",
            ).pack(side=tk.LEFT)

            if isinstance(value, bool):
                var = tk.BooleanVar(value=value)
                cb = tk.Checkbutton(
                    row, variable=var, bg=BG_FRAME, fg=FG_TEXT,
                    selectcolor="#1e1e1e", activebackground=BG_FRAME,
                    activeforeground=FG_TEXT,
                )
                cb.pack(side=tk.LEFT)
                self._field_vars[dotted] = var

            elif isinstance(value, list):
                text = tk.Text(
                    row, bg="#1e1e1e", fg=FG_TEXT, font=FONT_MONO,
                    height=min(len(value) + 1, 4), width=40,
                    insertbackground=FG_TEXT, relief=tk.FLAT, borderwidth=1,
                )
                text.insert("1.0", "\n".join(str(v) for v in value))
                text.pack(side=tk.LEFT, fill=tk.X, expand=True)
                self._field_vars[dotted] = text  # Store widget directly

            elif _is_sensitive(key):
                var = tk.StringVar(value=str(value) if value else "")
                entry = tk.Entry(
                    row, textvariable=var, bg="#1e1e1e", fg=FG_TEXT,
                    font=FONT_MONO, insertbackground=FG_TEXT,
                    relief=tk.FLAT, show="*",
                )
                entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
                self._field_vars[dotted] = var
                self._sensitive_visible[dotted] = False

                def _toggle_show(e=entry, d=dotted):
                    visible = self._sensitive_visible.get(d, False)
                    e.configure(show="" if not visible else "*")
                    self._sensitive_visible[d] = not visible

                Button(
                    row, text="보기", command=_toggle_show,
                    bg=BG_BTN, fg=FG_DIM, relief=tk.FLAT, font=FONT_SMALL,
                    padx=6, cursor="hand2",
                ).pack(side=tk.LEFT)

            elif isinstance(value, int):
                var = tk.StringVar(value=str(value))
                entry = tk.Entry(
                    row, textvariable=var, bg="#1e1e1e", fg=FG_TEXT,
                    font=FONT_MONO, insertbackground=FG_TEXT,
                    relief=tk.FLAT, width=12,
                )
                entry.pack(side=tk.LEFT, padx=(0, 4))
                self._field_vars[dotted] = var

            elif _is_path_field(key):
                # 경로 필드: 넓은 Entry
                var = tk.StringVar(value=str(value) if value is not None else "")
                entry = tk.Entry(
                    row, textvariable=var, bg="#1e1e1e", fg=FG_TEXT,
                    font=FONT_MONO, insertbackground=FG_TEXT,
                    relief=tk.FLAT,
                )
                entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
                self._field_vars[dotted] = var

            else:
                # String or other
                var = tk.StringVar(value=str(value) if value is not None else "")
                entry = tk.Entry(
                    row, textvariable=var, bg="#1e1e1e", fg=FG_TEXT,
                    font=FONT_MONO, insertbackground=FG_TEXT,
                    relief=tk.FLAT,
                )
                entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
                self._field_vars[dotted] = var

    def _collect_form_data(self) -> dict[str, Any]:
        """폼에서 수정된 설정 데이터를 수집합니다."""
        result: dict[str, Any] = {}
        for dotted_key, var in self._field_vars.items():
            parts = dotted_key.split(".")
            section = parts[0]

            # list[dict] 섹션: servers, watch_folders, remote_commands
            _LIST_SECTIONS = ("servers", "watch_folders", "remote_commands")
            if section in _LIST_SECTIONS and len(parts) >= 3:
                if section not in result:
                    result[section] = []
                item_idx = int(parts[1])
                while len(result[section]) <= item_idx:
                    result[section].append({})
                field_key = parts[2]
                val = self._extract_var_value(var, dotted_key)
                result[section][item_idx][field_key] = val
                continue

            # schedule.enabled → 사용여부에 따라 restart_times 처리
            if section == "schedule" and parts[-1] == "enabled":
                # enabled 값은 _apply에서 특수 처리
                if "schedule" not in result:
                    result["schedule"] = {}
                result["schedule"]["enabled"] = var.get() if isinstance(var, tk.BooleanVar) else True
                continue

            # 일반 dict 구조
            d = result
            for part in parts[:-1]:
                if part not in d:
                    d[part] = {}
                d = d[part]

            field_key = parts[-1]
            d[field_key] = self._extract_var_value(var, dotted_key)

        # schedule: enabled=False이면 restart_times를 비움
        sched = result.get("schedule", {})
        if isinstance(sched, dict) and not sched.get("enabled", True):
            sched["restart_times"] = []
        # enabled 필드는 원본 config에 없으므로 제거
        if isinstance(sched, dict):
            sched.pop("enabled", None)

        return result

    def _extract_var_value(self, var: Any, dotted_key: str) -> Any:
        """tk 변수/위젯에서 값을 추출하고 원본 타입을 유지합니다."""
        if isinstance(var, tk.BooleanVar):
            return var.get()
        elif isinstance(var, tk.Text):
            text = var.get("1.0", tk.END).strip()
            return [line.strip() for line in text.split("\n") if line.strip()]
        elif isinstance(var, tk.StringVar):
            val = var.get()
            orig = self._get_original_value(dotted_key)
            if isinstance(orig, int):
                try:
                    return int(val)
                except ValueError:
                    return val
            elif isinstance(orig, float):
                try:
                    return float(val)
                except ValueError:
                    return val
            return val
        return None

    def _get_original_value(self, dotted_key: str) -> Any:
        """원본 설정에서 값을 가져옵니다."""
        parts = dotted_key.split(".")
        d: Any = self._config
        for part in parts:
            if isinstance(d, dict):
                d = d.get(part)
            elif isinstance(d, list):
                try:
                    d = d[int(part)]
                except (ValueError, IndexError):
                    return None
            else:
                return None
        return d

    def _apply_config(self) -> None:
        """수정된 설정을 에이전트에 전송합니다."""
        collected = self._collect_form_data()
        if not collected:
            self._set_apply_status("변경할 설정이 없습니다", "#ffcc00")
            return

        self._set_apply_status("설정 적용 중...", FG_DIM)
        result = self._app.api_put(
            f"/api/config/{self._agent_id}",
            {"config": collected},
        )
        if result is None:
            self._set_apply_status("설정 적용 실패 (서버 응답 없음)", "#ff4444")
            return

        if result.get("success"):
            msg = result.get("message", "설정이 적용되었습니다")
            needs_restart = result.get("needs_restart", False)
            status = f"✓ {msg}"
            if needs_restart:
                status += " (연결 설정 변경 - 재시작 필요)"
                messagebox.showinfo(
                    "재시작 필요",
                    "연결 설정이 변경되었습니다.\n에이전트를 재시작해야 적용됩니다.",
                    parent=self,
                )
            self._set_apply_status(status, "#4ec9b0")
            # Refresh config display
            self.after(500, self._fetch_config)
        else:
            error = result.get("message", result.get("error", "알 수 없는 오류"))
            self._set_apply_status(f"✗ {error}", "#ff4444")

    def _set_status(self, text: str) -> None:
        self._status_label.configure(text=text)

    def _set_apply_status(self, text: str, color: str = FG_DIM) -> None:
        self._apply_status.configure(text=text, fg=color)
