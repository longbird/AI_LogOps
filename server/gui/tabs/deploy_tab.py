"""배포 탭: 에이전트/프로세스/녹취클라이언트 배포 및 재시작."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog, ttk
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


class _DeploySection:
    """하나의 배포 대상 섹션 (에이전트/프로세스/녹취클라이언트)."""

    def __init__(
        self,
        parent: tk.Frame,
        app: ServerAppLike,
        title: str,
        target: str,
        log_text: tk.Text,
    ) -> None:
        self._app = app
        self._target = target
        self._log_text = log_text
        self._file_path = ""

        frame = tk.LabelFrame(
            parent,
            text=f"  {title}  ",
            bg=BG_FRAME,
            fg=FG_TEXT,
            font=("Segoe UI Semibold", 9),
            padx=8,
            pady=8,
            relief=tk.GROOVE,
            bd=1,
        )
        frame.pack(fill=tk.X, padx=8, pady=4)

        # 1행: 에이전트 선택
        row1 = tk.Frame(frame, bg=BG_FRAME)
        row1.pack(fill=tk.X, pady=(0, 4))

        tk.Label(
            row1,
            text="Agent ID:",
            bg=BG_FRAME,
            fg=FG_DIM,
            font=FONT_NORMAL,
        ).pack(side=tk.LEFT)

        self._agent_entry = tk.Entry(
            row1,
            bg="#1e1e1e",
            fg=FG_TEXT,
            insertbackground=FG_TEXT,
            font=FONT_NORMAL,
            width=24,
            relief=tk.FLAT,
            bd=1,
        )
        self._agent_entry.pack(side=tk.LEFT, padx=8)
        self._agent_entry.insert(0, "(auto: first connected)")
        self._agent_entry.bind("<FocusIn>", self._clear_placeholder)

        # 2행: 파일 선택
        row2 = tk.Frame(frame, bg=BG_FRAME)
        row2.pack(fill=tk.X, pady=(0, 4))

        tk.Label(
            row2,
            text="파일:",
            bg=BG_FRAME,
            fg=FG_DIM,
            font=FONT_NORMAL,
        ).pack(side=tk.LEFT)

        self._file_label = tk.Label(
            row2,
            text="(선택 안됨)",
            bg=BG_FRAME,
            fg=FG_DIM,
            font=FONT_MONO,
            anchor="w",
        )
        self._file_label.pack(side=tk.LEFT, padx=8, fill=tk.X, expand=True)

        tk.Button(
            row2,
            text="파일 선택...",
            command=self._select_file,
            bg=BG_BTN,
            fg=FG_TEXT,
            relief=tk.FLAT,
            font=("Segoe UI", 8),
            padx=8,
            cursor="hand2",
        ).pack(side=tk.RIGHT)

        # 3행: 버튼
        row3 = tk.Frame(frame, bg=BG_FRAME)
        row3.pack(fill=tk.X)

        self._deploy_btn = tk.Button(
            row3,
            text="📦 배포",
            command=self._deploy,
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
        self._deploy_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._restart_btn = tk.Button(
            row3,
            text="↻ 재시작",
            command=self._restart,
            bg="#e67e22",
            fg=FG_WHITE,
            activebackground="#f39c12",
            activeforeground=FG_WHITE,
            relief=tk.FLAT,
            font=FONT_NORMAL,
            padx=16,
            pady=3,
            cursor="hand2",
        )
        self._restart_btn.pack(side=tk.LEFT)

        self._status_label = tk.Label(
            row3,
            text="",
            bg=BG_FRAME,
            fg=FG_DIM,
            font=("Segoe UI", 8),
        )
        self._status_label.pack(side=tk.RIGHT)

    def _clear_placeholder(self, _event: Any) -> None:
        if self._agent_entry.get() == "(auto: first connected)":
            self._agent_entry.delete(0, tk.END)

    def _select_file(self) -> None:
        path = filedialog.askopenfilename(
            title=f"배포 파일 선택 ({self._target})",
            filetypes=[("ZIP files", "*.zip"), ("All files", "*.*")],
        )
        if path:
            self._file_path = path
            # 파일명만 표시 (경로가 길 수 있으므로)
            from pathlib import Path

            name = Path(path).name
            size_mb = Path(path).stat().st_size / (1024 * 1024)
            self._file_label.configure(
                text=f"{name} ({size_mb:.1f} MB)",
                fg=FG_TEXT,
            )

    def _log(self, msg: str) -> None:
        self._log_text.configure(state=tk.NORMAL)
        self._log_text.insert(tk.END, msg + "\n")
        self._log_text.configure(state=tk.DISABLED)
        self._log_text.see(tk.END)

    def _deploy(self) -> None:
        if not self._file_path:
            self._status_label.configure(text="파일을 선택하세요", fg="#f44747")
            return

        if not self._app.is_server_running():
            self._status_label.configure(text="서버가 실행 중이 아닙니다", fg="#f44747")
            return

        agent_id = self._agent_entry.get().strip()
        if agent_id == "(auto: first connected)":
            agent_id = ""

        self._deploy_btn.configure(state=tk.DISABLED)
        self._status_label.configure(text="배포 중...", fg="#cca700")
        self._log(f"[배포] {self._target} 배포 시작: {self._file_path}")

        def _do_deploy() -> None:
            result = self._app.api_deploy_upload(
                self._file_path, agent_id, self._target
            )
            self._log_text.after(0, self._on_deploy_done, result)

        threading.Thread(target=_do_deploy, daemon=True).start()

    def _on_deploy_done(self, result: dict[str, Any] | None) -> None:
        self._deploy_btn.configure(state=tk.NORMAL)
        if result is None:
            self._status_label.configure(text="배포 실패 (응답 없음)", fg="#f44747")
            self._log("[배포] 실패: 서버 응답 없음")
            return

        if "error" in result:
            self._status_label.configure(text=f"실패: {result['error']}", fg="#f44747")
            self._log(f"[배포] 실패: {result['error']}")
        else:
            agent = result.get("agent_id", "?")
            deploy_id = result.get("deploy_id", "?")
            self._status_label.configure(text=f"배포 완료 → {agent}", fg="#51cf66")
            self._log(
                f"[배포] 성공: agent={agent} deploy_id={deploy_id} "
                f"target={self._target}"
            )

    def _restart(self) -> None:
        if not self._app.is_server_running():
            self._status_label.configure(text="서버가 실행 중이 아닙니다", fg="#f44747")
            return

        agent_id = self._agent_entry.get().strip()
        if agent_id == "(auto: first connected)":
            agent_id = ""

        self._restart_btn.configure(state=tk.DISABLED)
        self._status_label.configure(text="재시작 중...", fg="#cca700")
        self._log(f"[재시작] {self._target} 재시작 요청")

        def _do_restart() -> None:
            body: dict[str, Any] = {"action": "restart", "target": self._target}
            if agent_id:
                body["agent_id"] = agent_id
            result = self._app.api_post("/api/ctrl/restart", body)
            self._log_text.after(0, self._on_restart_done, result)

        threading.Thread(target=_do_restart, daemon=True).start()

    def _on_restart_done(self, result: dict[str, Any] | None) -> None:
        self._restart_btn.configure(state=tk.NORMAL)
        if result and result.get("status") == "ok":
            self._status_label.configure(text="재시작 완료", fg="#51cf66")
            self._log("[재시작] 성공")
        else:
            err = (result or {}).get("error", "응답 없음")
            self._status_label.configure(text=f"재시작 실패: {err}", fg="#f44747")
            self._log(f"[재시작] 실패: {err}")


class DeployTab(tk.Frame):
    """배포 관리 탭: 에이전트/프로세스/녹취클라이언트."""

    def __init__(self, parent: ttk.Notebook, app: ServerAppLike) -> None:
        super().__init__(parent, bg=BG_DARK)
        self._app = app
        self._build_ui()

    def _build_ui(self) -> None:
        # 스크롤 가능한 컨테이너
        canvas = tk.Canvas(self, bg=BG_DARK, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg=BG_DARK)

        scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # 마우스 휠 바인딩
        def _on_mousewheel(event: Any) -> None:
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # ── 로그 영역 (하단, 미리 생성) ──
        log_outer = tk.Frame(scroll_frame, bg=BG_DARK)

        log_header = tk.Frame(log_outer, bg=BG_FRAME, padx=12, pady=4)
        log_header.pack(fill=tk.X, padx=8, pady=(8, 0))

        tk.Label(
            log_header,
            text="배포 로그",
            bg=BG_FRAME,
            fg=FG_TEXT,
            font=("Segoe UI Semibold", 10),
        ).pack(side=tk.LEFT)

        self._log_text = tk.Text(
            log_outer,
            wrap=tk.WORD,
            bg="#1e1e1e",
            fg=FG_TEXT,
            font=FONT_MONO,
            height=8,
            padx=8,
            pady=4,
            state=tk.DISABLED,
            borderwidth=0,
            highlightthickness=0,
        )
        self._log_text.pack(fill=tk.X, padx=8, pady=(0, 8))

        # ── 배포 섹션들 ──
        self._agent_section = _DeploySection(
            scroll_frame, self._app, "에이전트 배포 & 업데이트", "agent", self._log_text
        )
        self._process_section = _DeploySection(
            scroll_frame,
            self._app,
            "모니터링 프로세스 배포 & 업데이트",
            "process",
            self._log_text,
        )
        self._rec_client_section = _DeploySection(
            scroll_frame,
            self._app,
            "녹취 클라이언트 배포 & 업데이트",
            "rec_client",
            self._log_text,
        )

        log_outer.pack(fill=tk.X)
