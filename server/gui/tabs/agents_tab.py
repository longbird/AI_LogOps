"""에이전트 탭: 접속 에이전트 리스트 + 선택 에이전트 실시간 로그."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk
from typing import Any

from server.gui.constants import (
    BG_BTN,
    BG_DARK,
    BG_FRAME,
    FG_DIM,
    FG_TEXT,
    FG_WHITE,
    FONT_MONO,
    FONT_NORMAL,
    ServerAppLike,
)

REFRESH_MS = 10_000  # 에이전트 리스트 갱신 주기 (10초)
LOG_MAX_LINES = 2000


class AgentsTab(tk.Frame):
    """에이전트 접속 리스트 + 실시간 로그 뷰어."""

    def __init__(self, parent: ttk.Notebook, app: ServerAppLike) -> None:
        super().__init__(parent, bg=BG_DARK)
        self._app = app
        self._ws_thread: threading.Thread | None = None
        self._ws_stop_event = threading.Event()
        self._selected_agent: str = ""
        self._log_line_count = 0
        self._refresh_after_id: str | None = None  # 중복 타이머 방지
        self._build_ui()
        self._schedule_refresh()

    def _build_ui(self) -> None:
        # ── 상단: 에이전트 리스트 ──
        list_frame = tk.Frame(self, bg=BG_FRAME, padx=8, pady=8)
        list_frame.pack(fill=tk.X, padx=8, pady=(8, 4))

        header = tk.Frame(list_frame, bg=BG_FRAME)
        header.pack(fill=tk.X, pady=(0, 4))

        tk.Label(
            header,
            text="접속 에이전트",
            bg=BG_FRAME,
            fg=FG_TEXT,
            font=("Segoe UI Semibold", 10),
        ).pack(side=tk.LEFT)

        self._count_label = tk.Label(
            header,
            text="0 agents",
            bg=BG_FRAME,
            fg=FG_DIM,
            font=("Segoe UI", 8),
        )
        self._count_label.pack(side=tk.RIGHT)

        tk.Button(
            header,
            text="새로고침",
            command=self._manual_refresh,
            bg=BG_BTN,
            fg=FG_DIM,
            relief=tk.FLAT,
            font=("Segoe UI", 8),
            padx=8,
            cursor="hand2",
        ).pack(side=tk.RIGHT, padx=4)

        # Treeview (에이전트 테이블)
        style = ttk.Style()
        style.configure(
            "Agent.Treeview",
            background="#1e1e1e",
            foreground=FG_TEXT,
            fieldbackground="#1e1e1e",
            font=FONT_NORMAL,
            rowheight=24,
        )
        style.configure(
            "Agent.Treeview.Heading",
            background=BG_BTN,
            foreground=FG_TEXT,
            font=("Segoe UI Semibold", 9),
        )
        style.map("Agent.Treeview", background=[("selected", "#264f78")])

        columns = ("agent_id", "version", "state", "heartbeat")
        self._tree = ttk.Treeview(
            list_frame,
            columns=columns,
            show="headings",
            height=5,
            style="Agent.Treeview",
        )
        self._tree.heading("agent_id", text="Agent ID")
        self._tree.heading("version", text="Version")
        self._tree.heading("state", text="State")
        self._tree.heading("heartbeat", text="Heartbeat")

        self._tree.column("agent_id", width=180, minwidth=120)
        self._tree.column("version", width=80, minwidth=60)
        self._tree.column("state", width=100, minwidth=80)
        self._tree.column("heartbeat", width=100, minwidth=80)

        tree_scroll = ttk.Scrollbar(
            list_frame, orient=tk.VERTICAL, command=self._tree.yview
        )
        self._tree.configure(yscrollcommand=tree_scroll.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self._tree.bind("<<TreeviewSelect>>", self._on_agent_selected)

        # ── 하단: 에이전트 실시간 로그 ──
        log_header = tk.Frame(self, bg=BG_FRAME, padx=12, pady=4)
        log_header.pack(fill=tk.X, padx=8, pady=(4, 0))

        self._log_title = tk.Label(
            log_header,
            text="에이전트 로그 (선택하세요)",
            bg=BG_FRAME,
            fg=FG_TEXT,
            font=("Segoe UI Semibold", 10),
        )
        self._log_title.pack(side=tk.LEFT)

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

        log_frame = tk.Frame(self, bg="#1e1e1e")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        self._log_text = tk.Text(
            log_frame,
            wrap=tk.NONE,
            bg="#1e1e1e",
            fg="#4ec9b0",
            font=FONT_MONO,
            padx=8,
            pady=4,
            state=tk.DISABLED,
            borderwidth=0,
            highlightthickness=0,
        )

        sy = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self._log_text.yview)
        sx = ttk.Scrollbar(
            log_frame, orient=tk.HORIZONTAL, command=self._log_text.xview
        )
        self._log_text.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        sy.pack(side=tk.RIGHT, fill=tk.Y)
        sx.pack(side=tk.BOTTOM, fill=tk.X)
        self._log_text.pack(fill=tk.BOTH, expand=True)

    # ── 에이전트 리스트 갱신 ──

    def _schedule_refresh(self) -> None:
        """다음 갱신 예약 (중복 방지)."""
        if self._refresh_after_id is not None:
            self.after_cancel(self._refresh_after_id)
        self._refresh_after_id = self.after(REFRESH_MS, self._refresh_agents)

    def _manual_refresh(self) -> None:
        """새로고침 버튼: 즉시 갱신 (기존 타이머 체인 유지)."""
        self._do_fetch()

    def _refresh_agents(self) -> None:
        """타이머에 의한 주기적 갱신."""
        self._refresh_after_id = None
        # 탭이 보이지 않거나 서버 미실행 시 폴링 건너뜀
        if not self.winfo_ismapped() or not self._app.is_server_running():
            self._schedule_refresh()
            return
        self._do_fetch()
        self._schedule_refresh()

    def _do_fetch(self) -> None:
        """백그라운드 스레드에서 에이전트 목록 조회."""

        def _fetch() -> None:
            try:
                from urllib.request import Request, urlopen
                import json

                req = Request(f"{self._app.dashboard_url}/api/deploy/status")
                req.add_header("Authorization", f"Bearer {self._app.auth_token}")
                with urlopen(req, timeout=5) as resp:
                    data = dict(json.loads(resp.read().decode()))
            except Exception:
                data = None
            self.after(0, self._update_tree, data)

        threading.Thread(target=_fetch, daemon=True).start()

    def _update_tree(self, data: dict[str, Any] | None) -> None:
        # 현재 선택 기억
        sel = self._selected_agent

        self._tree.delete(*self._tree.get_children())

        if data is None:
            self._count_label.configure(text="서버 응답 없음")
            return

        agents = data.get("agents", [])
        self._count_label.configure(text=f"{len(agents)} agents")

        for agent in agents:
            agent_id = agent.get("agent_id", "?")
            version = agent.get("version", "?")
            state = agent.get("state", agent.get("connected", "?"))
            if state is True:
                state = "CONNECTED"
            heartbeat = agent.get("last_heartbeat_ago", "-")
            self._tree.insert(
                "", tk.END, iid=agent_id, values=(agent_id, version, state, heartbeat)
            )

        # 이전 선택 복원
        if sel and self._tree.exists(sel):
            self._tree.selection_set(sel)

    def _on_agent_selected(self, _event: Any) -> None:
        selection = self._tree.selection()
        if not selection:
            return
        agent_id = str(selection[0])
        if agent_id == self._selected_agent:
            return

        # 이전 에이전트 로그 스트리밍 중지
        prev = self._selected_agent
        self._selected_agent = agent_id
        if prev:
            self._stop_log_stream(prev)

        self._log_title.configure(text=f"에이전트 로그: {agent_id}")
        self._clear_log()
        # 로그 스트리밍 시작 후 WebSocket 연결
        self._start_log_stream(agent_id)
        self._connect_ws(agent_id)

    # ── 로그 스트리밍 시작/중지 (에이전트에 REAL_START/STOP 명령) ──

    def _start_log_stream(self, agent_id: str) -> None:
        """에이전트에 실시간 로그 스트리밍 시작 명령 전송."""

        def _do() -> None:
            result = self._app.api_post(
                f"/api/logs/{agent_id}/stream", {"action": "start"}
            )
            if result and result.get("status") == "ok":
                self.after(
                    0,
                    lambda: self._append_log(
                        f"[시스템] 실시간 로그 시작 요청 성공 (agent={agent_id})"
                    ),
                )
            else:
                self.after(
                    0,
                    lambda: self._append_log(
                        f"[시스템] 실시간 로그 시작 요청 실패 (agent={agent_id}, result={result})"
                    ),
                )

        threading.Thread(target=_do, daemon=True).start()

    def _stop_log_stream(self, agent_id: str) -> None:
        """에이전트에 실시간 로그 스트리밍 중지 명령 전송."""

        def _do() -> None:
            self._app.api_post(f"/api/logs/{agent_id}/stream", {"action": "stop"})

        threading.Thread(target=_do, daemon=True).start()

    # ── WebSocket 연결 (에이전트 로그) ──

    def _connect_ws(self, agent_id: str) -> None:
        # 기존 연결 종료
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
        """WebSocket 연결 및 메시지 수신 (백그라운드 스레드)."""
        try:
            import websockets.sync.client as ws_sync  # type: ignore[import-untyped]

            url = self._app.dashboard_url.replace("http://", "ws://").replace(
                "https://", "wss://"
            )
            url = f"{url}/ws/logs/{agent_id}"

            self.after(
                0,
                lambda: self._ws_status.configure(
                    text="WS 연결됨 · 로그 대기중...", fg="#51cf66"
                ),
            )
            msg_count = 0

            with ws_sync.connect(url) as ws:
                while not self._ws_stop_event.is_set():
                    try:
                        msg = ws.recv(timeout=0.5)
                        if isinstance(msg, str):
                            msg_count += 1
                            self.after(0, self._append_log, msg)
                            if msg_count % 10 == 1:
                                c = msg_count
                                self.after(
                                    0,
                                    lambda c=c: self._ws_status.configure(
                                        text=f"수신중 ({c}건)", fg="#51cf66"
                                    ),
                                )
                    except TimeoutError:
                        continue
                    except Exception:
                        break

        except ImportError:
            # websockets 없으면 폴링 폴백
            self._ws_polling_fallback(agent_id)
            return
        except Exception as e:
            self.after(
                0,
                lambda: self._ws_status.configure(text=f"연결 실패: {e}", fg="#f44747"),
            )

        # WebSocket 종료 시 스트리밍 자동 중지
        if not self._ws_stop_event.is_set():
            self._stop_log_stream(agent_id)

    def _ws_polling_fallback(self, agent_id: str) -> None:
        """websockets 라이브러리 없을 때 HTTP 폴링 폴백."""
        self.after(0, lambda: self._ws_status.configure(text="폴링 모드", fg="#cca700"))
        import time

        while not self._ws_stop_event.is_set():
            data = self._app.api_get(f"/api/logs/{agent_id}")
            if data and isinstance(data, dict):
                logs = data.get("logs", "")
                if logs:
                    self.after(0, self._append_log, str(logs))
            time.sleep(3)

    def _append_log(self, text: str) -> None:
        self._log_text.configure(state=tk.NORMAL)
        self._log_text.insert(tk.END, text + "\n")
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
