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
ANALYSIS_POLL_MS = 3_000  # 분석 패널 갱신 주기 (3초)


class AgentsTab(tk.Frame):
    """에이전트 접속 리스트 + 실시간 로그 뷰어."""

    def __init__(self, parent: ttk.Notebook, app: ServerAppLike) -> None:
        super().__init__(parent, bg=BG_DARK)
        self._app = app
        self._ws_thread: threading.Thread | None = None
        self._ws_stop_event = threading.Event()
        self._selected_agent: str = ""
        self._log_line_count = 0
        self._refresh_after_id: str | None = None  # 에이전트 리스트 타이머
        self._analysis_after_id: str | None = None  # 분석 폴링 타이머
        self._selected_folder: tk.IntVar  # 분석 폴더 선택 (0 또는 1)
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

        # ── 로그 헤더 ──
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

        # ── 하단: 로그(좌) + 분석 패널(우) — PanedWindow ──
        paned = tk.PanedWindow(
            self,
            orient=tk.HORIZONTAL,
            bg=BG_DARK,
            sashwidth=5,
            sashrelief=tk.FLAT,
            bd=0,
        )
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        # ── 왼쪽: 로그 텍스트 ──
        log_frame = tk.Frame(paned, bg="#1e1e1e")
        paned.add(log_frame, stretch="always", minsize=200)

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

        # ── 오른쪽: 분석 패널 ──
        analysis_outer = tk.Frame(paned, bg=BG_FRAME)
        paned.add(analysis_outer, stretch="never", minsize=220, width=300)

        analysis_header = tk.Frame(analysis_outer, bg=BG_FRAME)
        analysis_header.pack(fill=tk.X)
        tk.Label(
            analysis_header,
            text="실시간 분석",
            bg=BG_FRAME,
            fg=FG_TEXT,
            font=("Segoe UI Semibold", 9),
            padx=8,
            pady=3,
        ).pack(side=tk.LEFT)

        # 폴더 선택 Radiobutton
        self._selected_folder = tk.IntVar(value=0)
        folder_bar = tk.Frame(analysis_header, bg=BG_FRAME)
        folder_bar.pack(side=tk.RIGHT, padx=6, pady=2)
        for fi, label in ((0, "F0"), (1, "F1")):
            rb = tk.Radiobutton(
                folder_bar,
                text=label,
                variable=self._selected_folder,
                value=fi,
                command=self._on_folder_changed,
                bg=BG_FRAME,
                fg="#9cdcfe",
                selectcolor="#264f78",
                activebackground=BG_FRAME,
                activeforeground="#ffffff",
                font=("Consolas", 8, "bold"),
                relief=tk.FLAT,
                cursor="hand2",
            )
            rb.pack(side=tk.LEFT, padx=2)

        self._analysis_text = tk.Text(
            analysis_outer,
            wrap=tk.WORD,
            bg="#1e1e1e",
            fg=FG_TEXT,
            font=("Consolas", 8),
            padx=6,
            pady=4,
            state=tk.DISABLED,
            borderwidth=0,
            highlightthickness=0,
            width=36,
        )
        asy = ttk.Scrollbar(
            analysis_outer, orient=tk.VERTICAL, command=self._analysis_text.yview
        )
        self._analysis_text.configure(yscrollcommand=asy.set)
        asy.pack(side=tk.RIGHT, fill=tk.Y)
        self._analysis_text.pack(fill=tk.BOTH, expand=True)

        # 분석 텍스트 색상 태그
        self._analysis_text.tag_configure(
            "header", foreground="#d4d4d4", font=("Consolas", 8, "bold")
        )
        self._analysis_text.tag_configure(
            "section", foreground="#569cd6", font=("Consolas", 8, "bold")
        )
        self._analysis_text.tag_configure("label", foreground="#888888")
        self._analysis_text.tag_configure("val_green", foreground="#4ec9b0")
        self._analysis_text.tag_configure("val_red", foreground="#f44747")
        self._analysis_text.tag_configure("val_yellow", foreground="#cca700")
        self._analysis_text.tag_configure("val_blue", foreground="#9cdcfe")
        self._analysis_text.tag_configure("val_dim", foreground="#555555")
        self._analysis_text.tag_configure("dim", foreground="#555555")

        self._analysis_reset()

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
                import json
                from urllib.request import Request, urlopen

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
        self._selected_folder.set(0)  # 에이전트 변경 시 폴더 0으로 리셋
        self._analysis_reset()
        # 로그 스트리밍 시작 후 WebSocket 연결
        self._start_log_stream(agent_id)
        self._connect_ws(agent_id)
        # 분석 폴링 시작
        self._start_analysis_polling(agent_id)

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
        self._stop_analysis_polling()

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

    # ── 실시간 분석 폴링 ──

    def _start_analysis_polling(self, agent_id: str) -> None:
        """분석 폴링 시작 (즉시 1회 + 3초 주기)."""
        self._stop_analysis_polling()
        self._analysis_fetch_bg(agent_id)
        self._analysis_after_id = self.after(
            ANALYSIS_POLL_MS, self._analysis_poll_tick, agent_id
        )

    def _stop_analysis_polling(self) -> None:
        """분석 폴링 중지."""
        if self._analysis_after_id is not None:
            self.after_cancel(self._analysis_after_id)
            self._analysis_after_id = None

    def _analysis_poll_tick(self, agent_id: str) -> None:
        """폴링 타이머 콜백."""
        if self._selected_agent != agent_id:
            return  # 에이전트 변경됨 — 중단
        self._analysis_fetch_bg(agent_id)
        self._analysis_after_id = self.after(
            ANALYSIS_POLL_MS, self._analysis_poll_tick, agent_id
        )

    def _analysis_fetch_bg(self, agent_id: str) -> None:
        """백그라운드 스레드에서 분석 데이터 조회."""

        def _fetch() -> None:
            folder = self._selected_folder.get()
            data = self._app.api_get(f"/api/analysis/{agent_id}?folder={folder}")
            if data and "error" not in data:
                self.after(0, self._render_analysis, data, folder)

        threading.Thread(target=_fetch, daemon=True).start()

    def _on_folder_changed(self) -> None:
        """폴더 선택 변경 — 서버 MonitorState 초기화 후 폴링 재시작."""
        agent_id = self._selected_agent
        if not agent_id:
            return
        folder = self._selected_folder.get()
        self._stop_analysis_polling()

        def _reset_and_restart() -> None:
            self._app.api_post(
                f"/api/analysis/{agent_id}/reset",
                {"folder_index": folder},
            )
            self.after(0, self._start_analysis_polling, agent_id)

        threading.Thread(target=_reset_and_restart, daemon=True).start()

    def _analysis_reset(self) -> None:
        """분석 패널 초기화 (에이전트 미선택 상태)."""
        t = self._analysis_text
        t.configure(state=tk.NORMAL)
        t.delete("1.0", tk.END)
        t.insert(tk.END, "\n  에이전트를 선택하면\n  분석이 시작됩니다.", "dim")
        t.configure(state=tk.DISABLED)

    def _render_analysis(self, d: dict[str, Any], folder: int = 0) -> None:
        """분석 데이터를 분석 패널에 렌더링."""
        t = self._analysis_text
        t.configure(state=tk.NORMAL)
        t.delete("1.0", tk.END)

        def row(label: str, value: str, val_tag: str = "val_dim") -> None:
            t.insert(tk.END, f"  {label:<16}", "label")
            t.insert(tk.END, f" {value}\n", val_tag)

        def sec(title: str) -> None:
            t.insert(tk.END, f"\n\u2500 {title}\n", "section")

        # ── 헤더 (현재 폴더 표시) ──
        t.insert(tk.END, f"실시간 분석  [F{folder}]", "header")
        t.insert(tk.END, f"  {d.get('updated_at', '')}\n", "dim")
        t.insert(
            tk.END,
            f"  가동: {d.get('uptime', '-')}  라인: {d.get('total_lines', 0)}\n",
            "dim",
        )

        # ── 스레드 상태 ──
        th = d.get("thread") or {}
        sec("스레드 상태")
        rtp_c: int = int(th.get("rtp_count", 0))
        pkt_c: int = int(th.get("pkt_count", 0))
        act: int = int(th.get("active_sessions", 0))
        row(
            "RTP 스레드",
            f"{rtp_c}개 {th.get('rtp_indices', [])}",
            "val_green" if rtp_c > 0 else "val_dim",
        )
        row(
            "PKT 스레드",
            f"{pkt_c}개 {th.get('pkt_indices', [])}",
            "val_blue" if pkt_c > 0 else "val_dim",
        )
        row("활성 세션", str(act), "val_yellow" if act > 0 else "val_dim")

        # ── Duration Mismatch (1h) ──
        m = d.get("mismatch_1h") or {}
        sec("Duration Mismatch (1h)")
        cnt: int = int(m.get("count", 0))
        avg_lag: float | None = m.get("avg_lag")
        max_lag = m.get("max_lag")
        row("불일치 건수", str(cnt), "val_red" if cnt > 0 else "val_dim")
        row(
            "평균 지연",
            f"{avg_lag:.1f}s" if avg_lag is not None else "-",
            "val_yellow" if avg_lag else "val_dim",
        )
        row(
            "최대 지연",
            f"{max_lag}s" if max_lag is not None else "-",
            "val_yellow" if max_lag else "val_dim",
        )
        causes: list[Any] = list(m.get("causes") or [])
        if causes:
            row(
                "원인",
                ", ".join(f"{c[0]}×{c[1]}" for c in causes),
                "val_yellow",
            )

        # ── 세션 활동 (5min) ──
        a = d.get("activity_5min") or {}
        sec("세션 활동 (5min)")
        row(
            "RTP 시작/매칭/종료",
            f"{a.get('rtp_start', 0)}/{a.get('rtp_match', 0)}/{a.get('rtp_bye', 0)}",
            "val_green",
        )
        row(
            "파일 열기/닫기",
            f"{a.get('file_open', 0)}/{a.get('file_close', 0)}",
            "val_blue",
        )
        avg_sz: float | None = a.get("file_avg_size_kb")
        avg_dur = a.get("file_avg_duration")
        row(
            "평균 크기",
            f"{avg_sz:.1f}KB" if avg_sz is not None else "-",
            "val_dim",
        )
        row(
            "평균 통화시간",
            f"{avg_dur}s" if avg_dur is not None else "-",
            "val_dim",
        )
        row(
            "SMDR 전체/응답",
            f"{a.get('smdr_total', 0)}/{a.get('smdr_answer', 0)}",
            "val_dim",
        )
        pend: int = int(a.get("pending_count", 0))
        row("대기 세션", str(pend), "val_yellow" if pend > 0 else "val_dim")

        # ── Grace Period ──
        g = d.get("grace") or {}
        sec("Grace Period")
        expired: int = int(g.get("expired", 0))
        row("시작", str(g.get("started", 0)), "val_yellow")
        row("만료", str(expired), "val_red" if expired > 0 else "val_dim")
        row("재개", str(g.get("resumed", 0)), "val_green")
        row("종료", str(g.get("terminated", 0)), "val_dim")

        # ── 최근 불일치 ──
        rm: list[Any] = list(d.get("recent_mismatches") or [])
        if rm:
            sec("최근 불일치")
            for r in reversed(rm[-5:]):
                t.insert(tk.END, f"  {r.get('ts', '')} ", "dim")
                t.insert(tk.END, f"{r.get('ext', '')}", "val_yellow")
                t.insert(tk.END, f" {r.get('lag', '')}s ", "val_red")
                t.insert(tk.END, f"{r.get('cause', '')}\n", "dim")

        # ── 최근 스레드 이벤트 ──
        te: list[Any] = list(d.get("recent_thread_events") or [])
        if te:
            sec("스레드 이벤트")
            for ev in reversed(te[-5:]):
                t.insert(tk.END, f"  {ev.get('ts', '')} ", "dim")
                t.insert(tk.END, f"{ev.get('msg', '')}\n", "val_blue")

        # ── 활성 세션 ──
        as_: list[Any] = list(d.get("active_sessions") or [])
        if as_:
            sec(f"활성 세션 ({len(as_)})")
            for s in as_[:5]:
                t.insert(tk.END, f"  {s.get('cid', '')} ", "val_green")
                t.insert(tk.END, f"{s.get('open_time', '')}\n", "dim")

        t.configure(state=tk.DISABLED)
