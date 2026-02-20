"""실시간 로그 뷰어 윈도우.

별도 Toplevel 창으로 열리며, 로그 큐에서 실시간으로 레코드를 수신하여 표시한다.
"""

from __future__ import annotations

import tkinter as tk
from collections import deque
from queue import Empty, SimpleQueue
from tkinter import ttk


class LogViewer(tk.Toplevel):
    """실시간 로그 뷰어. SimpleQueue에서 로그 라인을 폴링하여 표시."""

    MAX_LINES: int = 2000
    POLL_MS: int = 100

    def __init__(
        self,
        parent: tk.Tk,
        log_queue: SimpleQueue[str],
        title: str = "AI-LogOps Log Viewer",
    ) -> None:
        super().__init__(parent)
        self.title(title)
        self.geometry("900x520")
        self.minsize(600, 300)
        self.configure(bg="#1e1e1e")

        self._log_queue: SimpleQueue[str] = log_queue
        self._auto_scroll: bool = True
        self._paused: bool = False
        self._line_count: int = 0

        self._build_ui()
        self._poll_logs()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        # ── 상단 툴바 ──
        toolbar = tk.Frame(self, bg="#2d2d2d", height=32)
        toolbar.pack(fill=tk.X, padx=0, pady=0)
        toolbar.pack_propagate(False)

        self._pause_btn = tk.Button(
            toolbar,
            text="⏸ 일시정지",
            command=self._toggle_pause,
            bg="#3c3c3c",
            fg="#cccccc",
            relief=tk.FLAT,
            padx=8,
            font=("Segoe UI", 9),
        )
        self._pause_btn.pack(side=tk.LEFT, padx=4, pady=4)

        tk.Button(
            toolbar,
            text="🗑 지우기",
            command=self._clear_log,
            bg="#3c3c3c",
            fg="#cccccc",
            relief=tk.FLAT,
            padx=8,
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT, padx=4, pady=4)

        self._scroll_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            toolbar,
            text="자동 스크롤",
            variable=self._scroll_var,
            command=self._toggle_auto_scroll,
            bg="#2d2d2d",
            fg="#cccccc",
            selectcolor="#3c3c3c",
            activebackground="#2d2d2d",
            activeforeground="#cccccc",
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT, padx=8, pady=4)

        self._status_label = tk.Label(
            toolbar,
            text="0 lines",
            bg="#2d2d2d",
            fg="#888888",
            font=("Segoe UI", 9),
        )
        self._status_label.pack(side=tk.RIGHT, padx=8, pady=4)

        # ── 로그 텍스트 영역 ──
        text_frame = tk.Frame(self, bg="#1e1e1e")
        text_frame.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        self._text = tk.Text(
            text_frame,
            wrap=tk.NONE,
            bg="#1e1e1e",
            fg="#d4d4d4",
            insertbackground="#d4d4d4",
            selectbackground="#264f78",
            selectforeground="#ffffff",
            font=("Consolas", 10),
            padx=8,
            pady=4,
            state=tk.DISABLED,
            borderwidth=0,
            highlightthickness=0,
        )

        scrollbar_y = ttk.Scrollbar(
            text_frame, orient=tk.VERTICAL, command=self._text.yview
        )
        scrollbar_x = ttk.Scrollbar(
            text_frame, orient=tk.HORIZONTAL, command=self._text.xview
        )
        self._text.configure(
            yscrollcommand=scrollbar_y.set,
            xscrollcommand=scrollbar_x.set,
        )

        scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y)
        scrollbar_x.pack(side=tk.BOTTOM, fill=tk.X)
        self._text.pack(fill=tk.BOTH, expand=True)

        # ── 로그 레벨 색상 태그 ──
        self._text.tag_configure("ERROR", foreground="#f44747")
        self._text.tag_configure("WARNING", foreground="#cca700")
        self._text.tag_configure("INFO", foreground="#4ec9b0")
        self._text.tag_configure("DEBUG", foreground="#888888")

    def _poll_logs(self) -> None:
        """큐에서 로그 라인을 가져와 텍스트에 추가한다."""
        if self._paused:
            self.after(self.POLL_MS, self._poll_logs)
            return

        batch: list[str] = []
        try:
            while len(batch) < 50:
                line = self._log_queue.get_nowait()
                batch.append(line)
        except Empty:
            pass

        if batch:
            self._text.configure(state=tk.NORMAL)
            for line in batch:
                tag = self._detect_level(line)
                self._text.insert(tk.END, line + "\n", tag)
                self._line_count += 1

            # 최대 라인 수 초과 시 오래된 라인 삭제
            if self._line_count > self.MAX_LINES:
                excess = self._line_count - self.MAX_LINES
                self._text.delete("1.0", f"{excess + 1}.0")
                self._line_count = self.MAX_LINES

            self._text.configure(state=tk.DISABLED)

            if self._auto_scroll:
                self._text.see(tk.END)

            self._status_label.configure(text=f"{self._line_count} lines")

        self.after(self.POLL_MS, self._poll_logs)

    @staticmethod
    def _detect_level(line: str) -> str:
        """로그 라인에서 레벨을 감지하여 태그 이름을 반환한다."""
        upper = line.upper()
        if "ERROR" in upper or "EXCEPTION" in upper or "CRITICAL" in upper:
            return "ERROR"
        if "WARNING" in upper or "WARN" in upper:
            return "WARNING"
        if "DEBUG" in upper:
            return "DEBUG"
        return "INFO"

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        self._pause_btn.configure(text="▶ 재개" if self._paused else "⏸ 일시정지")

    def _toggle_auto_scroll(self) -> None:
        self._auto_scroll = self._scroll_var.get()

    def _clear_log(self) -> None:
        self._text.configure(state=tk.NORMAL)
        self._text.delete("1.0", tk.END)
        self._text.configure(state=tk.DISABLED)
        self._line_count = 0
        self._status_label.configure(text="0 lines")

    def _on_close(self) -> None:
        self.withdraw()
