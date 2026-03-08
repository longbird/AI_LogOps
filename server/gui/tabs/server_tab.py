"""서버 탭: 서버 시작/중지 + 실시간 서버 로그."""

from __future__ import annotations

import tkinter as tk
from queue import Empty
from tkinter import ttk

from server.gui.constants import (
    BG_BTN,
    BG_BTN_PRIMARY,
    BG_DARK,
    BG_FRAME,
    FG_DIM,
    FG_TEXT,
    FG_WHITE,
    FONT_HEADING,
    FONT_MONO,
    FONT_NORMAL,
    FONT_SMALL,
    ServerAppLike,
)

POLL_MS = 100
MAX_LINES = 3000


class ServerTab(tk.Frame):
    """서버 시작/중지 + 서버 로그 뷰어."""

    def __init__(self, parent: ttk.Notebook, app: ServerAppLike) -> None:
        super().__init__(parent, bg=BG_DARK)
        self._app = app
        self._line_count = 0
        self._auto_scroll = True
        self._build_ui()
        self._poll_logs()

    def _build_ui(self) -> None:
        # ── 상단: 서버 제어 ──
        ctrl = tk.Frame(self, bg=BG_FRAME, padx=12, pady=8)
        ctrl.pack(fill=tk.X, padx=8, pady=(8, 4))

        tk.Label(
            ctrl,
            text="서버 제어",
            bg=BG_FRAME,
            fg=FG_TEXT,
            font=FONT_HEADING,
        ).pack(side=tk.LEFT)

        self._stop_btn = tk.Button(
            ctrl,
            text="■ 중지",
            command=self._stop_server,
            bg="#c0392b",
            fg=FG_WHITE,
            activebackground="#e74c3c",
            activeforeground=FG_WHITE,
            relief=tk.FLAT,
            font=FONT_NORMAL,
            padx=16,
            pady=3,
            cursor="hand2",
            state=tk.DISABLED,
        )
        self._stop_btn.pack(side=tk.RIGHT, padx=(4, 0))

        self._start_btn = tk.Button(
            ctrl,
            text="▶ 시작",
            command=self._start_server,
            bg=BG_BTN_PRIMARY,
            fg=FG_WHITE,
            activebackground="#1177bb",
            activeforeground=FG_WHITE,
            relief=tk.FLAT,
            font=FONT_NORMAL,
            padx=16,
            pady=3,
            cursor="hand2",
        )
        self._start_btn.pack(side=tk.RIGHT, padx=(4, 0))

        self._restart_btn = tk.Button(
            ctrl,
            text="↻ 재시작",
            command=self._restart_server,
            bg=BG_BTN,
            fg=FG_TEXT,
            activebackground="#4c4c4c",
            activeforeground=FG_WHITE,
            relief=tk.FLAT,
            font=FONT_NORMAL,
            padx=16,
            pady=3,
            cursor="hand2",
            state=tk.DISABLED,
        )
        self._restart_btn.pack(side=tk.RIGHT, padx=(4, 0))

        # ── 상태 정보 ──
        info = tk.Frame(self, bg=BG_FRAME, padx=12, pady=6)
        info.pack(fill=tk.X, padx=8, pady=(4, 4))

        self._status_var = tk.StringVar(value="중지됨")
        self._pid_var = tk.StringVar(value="-")
        self._ports_var = tk.StringVar(value="-")

        rows = [
            ("상태", self._status_var),
            ("PID", self._pid_var),
            ("포트", self._ports_var),
        ]
        for i, (label_text, var) in enumerate(rows):
            tk.Label(
                info,
                text=label_text,
                bg=BG_FRAME,
                fg=FG_DIM,
                font=FONT_NORMAL,
                anchor="w",
                width=10,
            ).grid(row=i, column=0, sticky="w", pady=1)
            tk.Label(
                info,
                textvariable=var,
                bg=BG_FRAME,
                fg=FG_TEXT,
                font=FONT_NORMAL,
                anchor="w",
            ).grid(row=i, column=1, sticky="w", padx=(8, 0), pady=1)

        # ── 로그 툴바 ──
        log_toolbar = tk.Frame(self, bg=BG_FRAME, padx=12, pady=4)
        log_toolbar.pack(fill=tk.X, padx=8, pady=(4, 0))

        tk.Label(
            log_toolbar,
            text="서버 로그",
            bg=BG_FRAME,
            fg=FG_TEXT,
            font=FONT_HEADING,
        ).pack(side=tk.LEFT)

        tk.Button(
            log_toolbar,
            text="지우기",
            command=self._clear_log,
            bg=BG_BTN,
            fg=FG_DIM,
            relief=tk.FLAT,
            font=FONT_SMALL,
            padx=8,
            cursor="hand2",
        ).pack(side=tk.RIGHT, padx=4)

        self._scroll_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            log_toolbar,
            text="자동스크롤",
            variable=self._scroll_var,
            command=lambda: setattr(self, "_auto_scroll", self._scroll_var.get()),
            bg=BG_FRAME,
            fg=FG_DIM,
            selectcolor=BG_BTN,
            activebackground=BG_FRAME,
            activeforeground=FG_DIM,
            font=FONT_SMALL,
        ).pack(side=tk.RIGHT, padx=4)

        self._line_label = tk.Label(
            log_toolbar,
            text="0 lines",
            bg=BG_FRAME,
            fg=FG_DIM,
            font=FONT_SMALL,
        )
        self._line_label.pack(side=tk.RIGHT, padx=8)

        # ── 로그 텍스트 영역 ──
        log_frame = tk.Frame(self, bg="#1e1e1e")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        self._log_text = tk.Text(
            log_frame,
            wrap=tk.NONE,
            bg="#1e1e1e",
            fg=FG_TEXT,
            insertbackground=FG_TEXT,
            selectbackground="#264f78",
            font=FONT_MONO,
            padx=8,
            pady=4,
            state=tk.DISABLED,
            borderwidth=0,
            highlightthickness=0,
        )

        scrollbar_y = ttk.Scrollbar(
            log_frame, orient=tk.VERTICAL, command=self._log_text.yview
        )
        scrollbar_x = ttk.Scrollbar(
            log_frame, orient=tk.HORIZONTAL, command=self._log_text.xview
        )
        self._log_text.configure(
            yscrollcommand=scrollbar_y.set,
            xscrollcommand=scrollbar_x.set,
        )

        scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y)
        scrollbar_x.pack(side=tk.BOTTOM, fill=tk.X)
        self._log_text.pack(fill=tk.BOTH, expand=True)

        # 로그 색상 태그
        self._log_text.tag_configure("ERROR", foreground="#f44747")
        self._log_text.tag_configure("WARNING", foreground="#cca700")
        self._log_text.tag_configure("INFO", foreground="#4ec9b0")

    # ── 서버 제어 ──

    def _start_server(self) -> None:
        ok = self._app.start_server()
        if ok:
            self._status_var.set("실행 중")
            proc = self._app._server_proc
            if proc:
                self._pid_var.set(str(proc.pid))
            self._ports_var.set(
                f"TCP: {self._app._tcp_port}  |  HTTP: {self._app._dashboard_port}"
            )
            self._start_btn.configure(state=tk.DISABLED)
            self._stop_btn.configure(state=tk.NORMAL)
            self._restart_btn.configure(state=tk.NORMAL)

    def _stop_server(self) -> None:
        ok = self._app.stop_server()
        if ok:
            self.on_server_stopped()

    def _restart_server(self) -> None:
        self._status_var.set("재시작 중...")
        self._stop_server()
        self.after(2000, self._start_server)

    def on_server_stopped(self) -> None:
        """서버 프로세스가 종료되었을 때 UI 갱신."""
        self._status_var.set("중지됨")
        self._pid_var.set("-")
        self._ports_var.set("-")
        self._start_btn.configure(state=tk.NORMAL)
        self._stop_btn.configure(state=tk.DISABLED)
        self._restart_btn.configure(state=tk.DISABLED)

    # ── 로그 폴링 ──

    def _poll_logs(self) -> None:
        queue = self._app.server_log_queue
        batch: list[str] = []
        try:
            while len(batch) < 50:
                line = queue.get_nowait()
                batch.append(line)
        except Empty:
            pass

        if batch:
            self._log_text.configure(state=tk.NORMAL)
            for line in batch:
                tag = self._detect_level(line)
                self._log_text.insert(tk.END, line + "\n", tag)
                self._line_count += 1

            if self._line_count > MAX_LINES:
                excess = self._line_count - MAX_LINES
                self._log_text.delete("1.0", f"{excess + 1}.0")
                self._line_count = MAX_LINES

            self._log_text.configure(state=tk.DISABLED)

            if self._auto_scroll:
                self._log_text.see(tk.END)

            self._line_label.configure(text=f"{self._line_count} lines")

        self.after(POLL_MS, self._poll_logs)

    @staticmethod
    def _detect_level(line: str) -> str:
        upper = line.upper()
        if "ERROR" in upper or "EXCEPTION" in upper or "CRITICAL" in upper:
            return "ERROR"
        if "WARNING" in upper or "WARN" in upper:
            return "WARNING"
        return "INFO"

    def _clear_log(self) -> None:
        self._log_text.configure(state=tk.NORMAL)
        self._log_text.delete("1.0", tk.END)
        self._log_text.configure(state=tk.DISABLED)
        self._line_count = 0
        self._line_label.configure(text="0 lines")
