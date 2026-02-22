"""녹취 탭: 녹취 분석 시작/중지 + 실시간 진행상황."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk
from typing import Any

from server.gui.constants import (
    BG_BTN,
    BG_BTN_PRIMARY,
    BG_DARK,
    BG_FRAME,
    FG_DIM,
    FG_TEXT,
    FG_WHITE,
    FONT_MONO,
    FONT_NORMAL,
    ServerAppLike,
)

LOG_MAX_LINES = 2000


class RecordingTab(tk.Frame):
    """녹취 분석 제어 + 실시간 진행상황 뷰어."""

    def __init__(self, parent: ttk.Notebook, app: ServerAppLike) -> None:
        super().__init__(parent, bg=BG_DARK)
        self._app = app
        self._ws_thread: threading.Thread | None = None
        self._ws_stop_event = threading.Event()
        self._log_line_count = 0
        self._build_ui()
        # 서버에서 현재 최대 동시 분석수 가져오기
        self._fetch_max_concurrent()

    def _build_ui(self) -> None:
        # ── 분석 제어 영역 ──
        ctrl = tk.Frame(self, bg=BG_FRAME, padx=12, pady=8)
        ctrl.pack(fill=tk.X, padx=8, pady=(8, 4))

        tk.Label(
            ctrl,
            text="녹취 분석 제어",
            bg=BG_FRAME,
            fg=FG_TEXT,
            font=("Segoe UI Semibold", 10),
        ).pack(anchor="w")

        # 입력 행
        input_frame = tk.Frame(ctrl, bg=BG_FRAME)
        input_frame.pack(fill=tk.X, pady=(8, 4))

        tk.Label(
            input_frame,
            text="Agent ID:",
            bg=BG_FRAME,
            fg=FG_DIM,
            font=FONT_NORMAL,
        ).pack(side=tk.LEFT)

        self._agent_entry = tk.Entry(
            input_frame,
            bg="#1e1e1e",
            fg=FG_TEXT,
            insertbackground=FG_TEXT,
            font=FONT_NORMAL,
            width=20,
            relief=tk.FLAT,
            bd=1,
        )
        self._agent_entry.pack(side=tk.LEFT, padx=8)
        self._agent_entry.insert(0, "(auto)")
        self._agent_entry.bind(
            "<FocusIn>",
            lambda e: (
                self._agent_entry.delete(0, tk.END)
                if self._agent_entry.get() == "(auto)"
                else None
            ),
        )

        tk.Label(
            input_frame,
            text="날짜:",
            bg=BG_FRAME,
            fg=FG_DIM,
            font=FONT_NORMAL,
        ).pack(side=tk.LEFT, padx=(16, 0))

        self._date_entry = tk.Entry(
            input_frame,
            bg="#1e1e1e",
            fg=FG_TEXT,
            insertbackground=FG_TEXT,
            font=FONT_NORMAL,
            width=12,
            relief=tk.FLAT,
            bd=1,
        )
        self._date_entry.pack(side=tk.LEFT, padx=8)
        self._date_entry.insert(0, "(today)")
        self._date_entry.bind(
            "<FocusIn>",
            lambda e: (
                self._date_entry.delete(0, tk.END)
                if self._date_entry.get() == "(today)"
                else None
            ),
        )

        # 버튼 행
        btn_frame = tk.Frame(ctrl, bg=BG_FRAME)
        btn_frame.pack(fill=tk.X, pady=(4, 0))

        self._start_btn = tk.Button(
            btn_frame,
            text="▶ 분석 시작",
            command=self._start_analysis,
            bg="#27ae60",
            fg=FG_WHITE,
            activebackground="#2ecc71",
            activeforeground=FG_WHITE,
            relief=tk.FLAT,
            font=FONT_NORMAL,
            padx=16,
            pady=3,
            cursor="hand2",
        )
        self._start_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._stop_btn = tk.Button(
            btn_frame,
            text="■ 분석 중지",
            command=self._stop_analysis,
            bg="#c0392b",
            fg=FG_WHITE,
            activebackground="#e74c3c",
            activeforeground=FG_WHITE,
            relief=tk.FLAT,
            font=FONT_NORMAL,
            padx=16,
            pady=3,
            cursor="hand2",
        )
        self._stop_btn.pack(side=tk.LEFT)

        # ── 최대 동시 분석수 ──
        tk.Label(
            btn_frame,
            text="최대 동시 분석:",
            bg=BG_FRAME,
            fg=FG_DIM,
            font=FONT_NORMAL,
        ).pack(side=tk.LEFT, padx=(24, 4))

        self._max_concurrent_var = tk.IntVar(value=3)
        self._max_concurrent_spin = tk.Spinbox(
            btn_frame,
            from_=1,
            to=10,
            width=3,
            textvariable=self._max_concurrent_var,
            bg="#1e1e1e",
            fg=FG_TEXT,
            buttonbackground=BG_BTN,
            font=FONT_NORMAL,
            relief=tk.FLAT,
            bd=1,
            command=self._on_max_concurrent_changed,
        )
        self._max_concurrent_spin.pack(side=tk.LEFT)

        self._status_label = tk.Label(
            btn_frame,
            text="",
            bg=BG_FRAME,
            fg=FG_DIM,
            font=("Segoe UI", 9),
        )
        self._status_label.pack(side=tk.LEFT, padx=16)

        # ── 실시간 진행상황 ──
        log_header = tk.Frame(self, bg=BG_FRAME, padx=12, pady=4)
        log_header.pack(fill=tk.X, padx=8, pady=(8, 0))

        tk.Label(
            log_header,
            text="분석 진행상황",
            bg=BG_FRAME,
            fg=FG_TEXT,
            font=("Segoe UI Semibold", 10),
        ).pack(side=tk.LEFT)

        self._ws_status = tk.Label(
            log_header,
            text="",
            bg=BG_FRAME,
            fg=FG_DIM,
            font=("Segoe UI", 8),
        )
        self._ws_status.pack(side=tk.RIGHT)

        tk.Button(
            log_header,
            text="지우기",
            command=self._clear_log,
            bg=BG_BTN,
            fg=FG_DIM,
            relief=tk.FLAT,
            font=("Segoe UI", 8),
            padx=8,
            cursor="hand2",
        ).pack(side=tk.RIGHT, padx=4)

        # 로그 텍스트
        log_frame = tk.Frame(self, bg="#1e1e1e")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        self._log_text = tk.Text(
            log_frame,
            wrap=tk.WORD,
            bg="#1e1e1e",
            fg=FG_TEXT,
            font=FONT_MONO,
            padx=8,
            pady=4,
            state=tk.DISABLED,
            borderwidth=0,
            highlightthickness=0,
        )

        sy = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=sy.set)
        sy.pack(side=tk.RIGHT, fill=tk.Y)
        self._log_text.pack(fill=tk.BOTH, expand=True)

        # 색상 태그
        self._log_text.tag_configure("ok", foreground="#51cf66")
        self._log_text.tag_configure("fail", foreground="#f44747")
        self._log_text.tag_configure("progress", foreground="#cca700")
        self._log_text.tag_configure("info", foreground="#74c0fc")

    # ── 분석 제어 ──

    def _get_agent_id(self) -> str:
        val = self._agent_entry.get().strip()
        return "" if val in ("(auto)", "") else val

    def _get_date(self) -> str:
        val = self._date_entry.get().strip()
        return "" if val in ("(today)", "") else val

    def _start_analysis(self) -> None:
        if not self._app.is_server_running():
            self._status_label.configure(text="서버가 실행 중이 아닙니다", fg="#f44747")
            return

        agent_id = self._get_agent_id()
        date = self._get_date()

        self._start_btn.configure(state=tk.DISABLED)
        self._status_label.configure(text="시작 중...", fg="#cca700")

        def _do() -> None:
            body: dict[str, str] = {"action": "start"}
            if agent_id:
                body["agent_id"] = agent_id
            if date:
                body["date"] = date
            result = self._app.api_post("/api/rec/analyze", body)
            self.after(0, self._on_start_done, result, agent_id)

        threading.Thread(target=_do, daemon=True).start()

    def _on_start_done(self, result: dict[str, Any] | None, agent_id: str) -> None:
        self._start_btn.configure(state=tk.NORMAL)

        if result and result.get("status") == "ok":
            actual_agent = result.get("agent_id", agent_id or "?")
            date_msg = result.get("date", "") or "today"
            self._status_label.configure(
                text=f"실행 중: {actual_agent} ({date_msg})",
                fg="#51cf66",
            )
            self._append_log(f"분석 시작: agent={actual_agent} date={date_msg}", "ok")
            # WebSocket 연결
            self._connect_ws(actual_agent)
        else:
            err = (result or {}).get("error", "응답 없음")
            self._status_label.configure(text=f"실패: {err}", fg="#f44747")
            self._append_log(f"분석 시작 실패: {err}", "fail")

    def _fetch_max_concurrent(self) -> None:
        """서버에서 현재 최대 동시 분석수 조회 → Spinbox에 반영."""
        if not self._app.is_server_running():
            return

        def _do() -> None:
            result = self._app.api_get("/api/rec/max-concurrent")
            if result and "max_concurrent" in result:
                val = int(result["max_concurrent"])
                self.after(0, lambda: self._max_concurrent_var.set(val))

        threading.Thread(target=_do, daemon=True).start()

    def _on_max_concurrent_changed(self) -> None:
        """최대 동시 분석수 변경 → 즉시 서버 API 호출."""
        if not self._app.is_server_running():
            return

        value = self._max_concurrent_var.get()

        def _do() -> None:
            result = self._app.api_post("/api/rec/max-concurrent", {"value": value})
            if result and result.get("status") == "ok":
                actual = result.get("max_concurrent", value)
                self.after(
                    0,
                    self._append_log,
                    f"최대 동시 분석수 변경: {actual}",
                    "info",
                )
            else:
                err = (result or {}).get("error", "응답 없음")
                self.after(
                    0,
                    self._append_log,
                    f"동시 분석수 변경 실패: {err}",
                    "fail",
                )

        threading.Thread(target=_do, daemon=True).start()

    def _stop_analysis(self) -> None:
        if not self._app.is_server_running():
            return

        agent_id = self._get_agent_id()

        def _do() -> None:
            body: dict[str, str] = {"action": "stop"}
            if agent_id:
                body["agent_id"] = agent_id
            result = self._app.api_post("/api/rec/analyze", body)
            self.after(0, self._on_stop_done, result)

        threading.Thread(target=_do, daemon=True).start()

    def _on_stop_done(self, result: dict[str, Any] | None) -> None:
        if result and result.get("status") == "ok":
            self._status_label.configure(text="중지됨", fg=FG_DIM)
            self._append_log("분석 중지", "info")
        else:
            err = (result or {}).get("error", "응답 없음")
            self._status_label.configure(text=f"중지 실패: {err}", fg="#f44747")

        # WebSocket 연결 해제
        self._ws_stop_event.set()

    # ── WebSocket (분석 진행상황) ──

    def _connect_ws(self, agent_id: str) -> None:
        self._ws_stop_event.set()
        if self._ws_thread and self._ws_thread.is_alive():
            self._ws_thread.join(timeout=2)

        self._ws_stop_event = threading.Event()
        self._ws_status.configure(text="연결 중...", fg="#cca700")

        self._ws_thread = threading.Thread(
            target=self._ws_loop, args=(agent_id,), daemon=True
        )
        self._ws_thread.start()

    def _ws_loop(self, agent_id: str) -> None:
        try:
            import websockets.sync.client as ws_sync  # type: ignore[import-untyped]

            url = self._app.dashboard_url.replace("http://", "ws://").replace(
                "https://", "wss://"
            )
            url = f"{url}/ws/rec/status/{agent_id}"

            self.after(
                0, lambda: self._ws_status.configure(text="연결됨", fg="#51cf66")
            )

            with ws_sync.connect(url) as ws:
                while not self._ws_stop_event.is_set():
                    try:
                        msg = ws.recv(timeout=0.5)
                        if isinstance(msg, str):
                            tag = self._detect_tag(msg)
                            self.after(0, self._append_log, msg, tag)
                    except TimeoutError:
                        continue
                    except Exception:
                        break

        except ImportError:
            self.after(
                0,
                lambda: self._ws_status.configure(
                    text="websockets 미설치", fg="#f44747"
                ),
            )
        except Exception as e:
            self.after(
                0,
                lambda: self._ws_status.configure(text=f"연결 실패: {e}", fg="#f44747"),
            )

    @staticmethod
    def _detect_tag(text: str) -> str:
        if "완료" in text or "성공" in text:
            return "ok"
        if "실패" in text or "스킵" in text:
            return "fail"
        if "시작" in text or "요청" in text:
            return "progress"
        return "info"

    def _append_log(self, text: str, tag: str = "info") -> None:
        import time

        ts = time.strftime("%H:%M:%S")
        self._log_text.configure(state=tk.NORMAL)
        self._log_text.insert(tk.END, f"[{ts}] {text}\n", tag)
        self._log_line_count += 1

        if self._log_line_count > LOG_MAX_LINES:
            excess = self._log_line_count - LOG_MAX_LINES
            self._log_text.delete("1.0", f"{excess + 1}.0")
            self._log_line_count = LOG_MAX_LINES

        self._log_text.configure(state=tk.DISABLED)
        self._log_text.see(tk.END)

    def _clear_log(self) -> None:
        self._log_text.configure(state=tk.NORMAL)
        self._log_text.delete("1.0", tk.END)
        self._log_text.configure(state=tk.DISABLED)
        self._log_line_count = 0
