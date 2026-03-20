"""파일 관리 탭 — 듀얼 패널 파일 매니저.

서버(로컬) ↔ 에이전트(원격) 양방향 파일 전송.
"""

from __future__ import annotations

import os
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from server.gui.constants import (
    BG_BTN,
    BG_BTN_PRIMARY,
    BG_DARK,
    BG_FRAME,
    FG_DIM,
    FG_TEXT,
    FG_WHITE,
    FONT_NORMAL,
    FONT_SMALL,
)

if TYPE_CHECKING:
    from server.gui.constants import ServerAppLike


def _human_size(size: int) -> str:
    """바이트를 사람이 읽기 좋은 크기로 변환."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.1f} GB"


def _format_time(ts: float) -> str:
    """Unix timestamp → YYYY-MM-DD HH:MM."""
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError):
        return ""


class FileManagerTab(tk.Frame):
    """듀얼 패널 파일 매니저 탭."""

    def __init__(self, parent: tk.Widget, app: ServerAppLike) -> None:
        super().__init__(parent, bg=BG_DARK)
        self._app = app
        self._server_path: str = str(Path.cwd())
        self._agent_path: str = "C:/"
        self._transferring: bool = False

        self._build_ui()

    def _build_ui(self) -> None:
        # ── 상단: 에이전트 선택 ──
        top = tk.Frame(self, bg=BG_FRAME, height=36)
        top.pack(fill=tk.X, padx=4, pady=(4, 0))

        tk.Label(
            top, text="에이전트:", bg=BG_FRAME, fg=FG_TEXT, font=FONT_NORMAL
        ).pack(side=tk.LEFT, padx=(8, 4))

        self._agent_var = tk.StringVar(value="")
        self._agent_combo = ttk.Combobox(
            top,
            textvariable=self._agent_var,
            state="readonly",
            width=20,
            font=FONT_NORMAL,
        )
        self._agent_combo.pack(side=tk.LEFT, padx=4)
        self._agent_combo.bind("<<ComboboxSelected>>", self._on_agent_selected)

        tk.Button(
            top,
            text="새로고침",
            bg=BG_BTN,
            fg=FG_TEXT,
            font=FONT_SMALL,
            relief=tk.FLAT,
            command=self._refresh_agent_list,
        ).pack(side=tk.LEFT, padx=4)

        # ── 중앙: 듀얼 패널 ──
        mid = tk.Frame(self, bg=BG_DARK)
        mid.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # 왼쪽: 서버 패널
        left = tk.Frame(mid, bg=BG_FRAME)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 2))
        self._build_panel(left, "서버 (로컬)", is_server=True)

        # 오른쪽: 에이전트 패널
        right = tk.Frame(mid, bg=BG_FRAME)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(2, 0))
        self._build_panel(right, "에이전트 (원격)", is_server=False)

        # ── 하단: 전송 버튼 + 진행률 ──
        bot = tk.Frame(self, bg=BG_FRAME, height=80)
        bot.pack(fill=tk.X, padx=4, pady=(0, 4))
        bot.pack_propagate(False)

        btn_frame = tk.Frame(bot, bg=BG_FRAME)
        btn_frame.pack(pady=4)

        self._btn_to_agent = tk.Button(
            btn_frame,
            text="→ 에이전트로 복사",
            bg=BG_BTN_PRIMARY,
            fg=FG_WHITE,
            font=FONT_NORMAL,
            relief=tk.FLAT,
            command=self._copy_to_agent,
        )
        self._btn_to_agent.pack(side=tk.LEFT, padx=8)

        self._btn_to_server = tk.Button(
            btn_frame,
            text="← 서버로 복사",
            bg=BG_BTN_PRIMARY,
            fg=FG_WHITE,
            font=FONT_NORMAL,
            relief=tk.FLAT,
            command=self._copy_to_server,
        )
        self._btn_to_server.pack(side=tk.LEFT, padx=8)

        self._btn_run = tk.Button(
            btn_frame,
            text="▶ 원격 실행",
            bg="#27ae60",
            fg=FG_WHITE,
            font=FONT_NORMAL,
            relief=tk.FLAT,
            command=self._run_remote_file,
        )
        self._btn_run.pack(side=tk.LEFT, padx=8)

        self._progress = ttk.Progressbar(bot, mode="determinate", length=400)
        self._progress.pack(pady=2, padx=8, fill=tk.X)

        self._status_var = tk.StringVar(value="대기 중")
        tk.Label(
            bot,
            textvariable=self._status_var,
            bg=BG_FRAME,
            fg=FG_DIM,
            font=FONT_SMALL,
        ).pack(padx=8, anchor=tk.W)

    def _build_panel(
        self, parent: tk.Frame, title: str, *, is_server: bool
    ) -> None:
        """파일 패널 (경로 + Treeview) 구축."""
        tk.Label(
            parent, text=title, bg=BG_FRAME, fg=FG_TEXT, font=FONT_NORMAL
        ).pack(padx=8, pady=(4, 0), anchor=tk.W)

        # 경로 입력
        path_frame = tk.Frame(parent, bg=BG_FRAME)
        path_frame.pack(fill=tk.X, padx=4, pady=2)

        path_var = tk.StringVar(
            value=self._server_path if is_server else self._agent_path
        )
        entry = tk.Entry(path_frame, textvariable=path_var, font=FONT_SMALL)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        entry.bind("<Return>", lambda e: self._navigate(is_server, path_var.get()))

        tk.Button(
            path_frame,
            text="↑",
            bg=BG_BTN,
            fg=FG_TEXT,
            font=FONT_SMALL,
            width=3,
            relief=tk.FLAT,
            command=lambda: self._go_up(is_server),
        ).pack(side=tk.LEFT, padx=1)

        tk.Button(
            path_frame,
            text="⟳",
            bg=BG_BTN,
            fg=FG_TEXT,
            font=FONT_SMALL,
            width=3,
            relief=tk.FLAT,
            command=lambda: self._refresh(is_server),
        ).pack(side=tk.LEFT, padx=1)

        # Treeview
        tree_frame = tk.Frame(parent, bg=BG_FRAME)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        tree = ttk.Treeview(
            tree_frame,
            columns=("name", "size", "modified"),
            show="headings",
            selectmode="extended",
        )
        tree.heading("name", text="이름", anchor=tk.W)
        tree.heading("size", text="크기", anchor=tk.E)
        tree.heading("modified", text="수정일", anchor=tk.W)

        tree.column("name", width=200, minwidth=100)
        tree.column("size", width=80, minwidth=60, anchor=tk.E)
        tree.column("modified", width=120, minwidth=80)

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        tree.bind("<Double-1>", lambda e: self._on_double_click(is_server))

        if is_server:
            self._server_tree = tree
            self._server_path_var = path_var
            self._load_server_dir(self._server_path)
        else:
            self._agent_tree = tree
            self._agent_path_var = path_var

    # ── 탐색 ──

    def _navigate(self, is_server: bool, path: str) -> None:
        if is_server:
            self._server_path = path
            self._server_path_var.set(path)
            self._load_server_dir(path)
        else:
            self._agent_path = path
            self._agent_path_var.set(path)
            self._load_agent_dir(path)

    def _go_up(self, is_server: bool) -> None:
        if is_server:
            resolved = Path(self._server_path).resolve()
            parent = str(resolved.parent)
            self._navigate(True, parent)
        else:
            # 에이전트 경로: forward slash 기준으로 상위 계산
            normalized = self._agent_path.replace("\\", "/").rstrip("/")
            if "/" in normalized:
                parent = normalized.rsplit("/", 1)[0]
                if not parent:
                    parent = "/"
                # 드라이브 루트 (예: "C:") → "C:/"
                if len(parent) == 2 and parent[1] == ":":
                    parent += "/"
            else:
                parent = normalized
            self._navigate(False, parent)

    def _refresh(self, is_server: bool) -> None:
        if is_server:
            self._load_server_dir(self._server_path)
        else:
            self._load_agent_dir(self._agent_path)

    def _on_double_click(self, is_server: bool) -> None:
        tree = self._server_tree if is_server else self._agent_tree
        selected = tree.selection()
        if not selected:
            return
        item = tree.item(selected[0])
        name = item["values"][0]
        size_str = str(item["values"][1])

        if size_str == "<DIR>":
            if is_server:
                new_path = str(Path(self._server_path) / str(name))
                self._navigate(True, new_path)
            else:
                # 에이전트 경로: forward slash로 통일
                base = self._agent_path.replace("\\", "/").rstrip("/")
                new_path = base + "/" + str(name)
                self._navigate(False, new_path)

    def _on_agent_selected(self, event: Any = None) -> None:
        self._load_agent_dir(self._agent_path)

    # ── 서버 로컬 탐색 ──

    def _load_server_dir(self, path: str) -> None:
        tree = self._server_tree
        tree.delete(*tree.get_children())
        # 경로를 항상 절대 경로로 정규화
        try:
            path = str(Path(path).resolve())
        except (OSError, ValueError):
            pass
        self._server_path = path
        self._server_path_var.set(path)

        try:
            entries = []
            with os.scandir(path) as it:
                for entry in it:
                    try:
                        stat = entry.stat()
                        entries.append({
                            "name": entry.name,
                            "is_dir": entry.is_dir(),
                            "size": stat.st_size if not entry.is_dir() else 0,
                            "modified": stat.st_mtime,
                        })
                    except (PermissionError, OSError):
                        continue

            # 디렉토리 먼저, 이름순 정렬
            entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))

            for e in entries:
                size = "<DIR>" if e["is_dir"] else _human_size(e["size"])
                mod = _format_time(e["modified"])
                tree.insert("", tk.END, values=(e["name"], size, mod))

        except (PermissionError, OSError) as exc:
            self._status_var.set(f"오류: {exc}")

    # ── 에이전트 원격 탐색 ──

    def _load_agent_dir(self, path: str) -> None:
        agent_id = self._agent_var.get()
        if not agent_id:
            self._status_var.set("에이전트를 선택하세요")
            return

        self._agent_tree.delete(*self._agent_tree.get_children())
        self._agent_path = path
        self._agent_path_var.set(path)
        self._status_var.set("디렉토리 로딩 중...")

        def _do() -> None:
            try:
                encoded_path = quote(path, safe="")
                result = self._app.api_get(
                    f"/api/files/agent/{agent_id}/list?path={encoded_path}"
                )
                self._agent_tree.after(0, self._populate_agent_tree, result)
            except Exception as exc:
                self._agent_tree.after(
                    0, self._status_var.set, f"오류: {exc}"
                )

        threading.Thread(target=_do, daemon=True).start()

    def _populate_agent_tree(self, result: Any) -> None:
        tree = self._agent_tree
        tree.delete(*tree.get_children())

        if isinstance(result, dict):
            if not result.get("success", False):
                self._status_var.set(f"오류: {result.get('error', '알 수 없는 오류')}")
                return
            entries = result.get("entries", [])
            current_path = result.get("current_path", self._agent_path)
            self._agent_path = current_path
            self._agent_path_var.set(current_path)
        else:
            self._status_var.set("잘못된 응답 형식")
            return

        entries.sort(key=lambda e: (not e.get("is_dir", False), e.get("name", "").lower()))

        for e in entries:
            is_dir = e.get("is_dir", False)
            size = "<DIR>" if is_dir else _human_size(e.get("size", 0))
            mod = _format_time(e.get("modified", 0))
            tree.insert("", tk.END, values=(e["name"], size, mod))

        truncated = result.get("truncated", False)
        count = len(entries)
        msg = f"{count}개 항목"
        if truncated:
            msg += " (일부만 표시)"
        self._status_var.set(msg)

    # ── 파일 전송 ──

    def _get_selected_name(self, is_server: bool) -> str | None:
        tree = self._server_tree if is_server else self._agent_tree
        selected = tree.selection()
        if not selected:
            return None
        item = tree.item(selected[0])
        name = item["values"][0]
        size_str = str(item["values"][1])
        if size_str == "<DIR>":
            messagebox.showwarning("파일 관리", "디렉토리는 전송할 수 없습니다")
            return None
        return str(name)

    def _copy_to_agent(self) -> None:
        """서버 → 에이전트 파일 복사."""
        name = self._get_selected_name(is_server=True)
        if name is None:
            return
        agent_id = self._agent_var.get()
        if not agent_id:
            messagebox.showwarning("파일 관리", "에이전트를 선택하세요")
            return

        local_path = str(Path(self._server_path) / name)
        base = self._agent_path.replace("\\", "/").rstrip("/")
        remote_path = base + "/" + name

        self._do_transfer("to_agent", agent_id, local_path, remote_path, name)

    def _copy_to_server(self) -> None:
        """에이전트 → 서버 파일 복사."""
        name = self._get_selected_name(is_server=False)
        if name is None:
            return
        agent_id = self._agent_var.get()
        if not agent_id:
            messagebox.showwarning("파일 관리", "에이전트를 선택하세요")
            return

        base = self._agent_path.replace("\\", "/").rstrip("/")
        remote_path = base + "/" + name
        local_path = str(Path(self._server_path) / name)

        self._do_transfer("to_server", agent_id, local_path, remote_path, name)

    def _run_remote_file(self) -> None:
        """에이전트 PC에서 선택한 파일 실행."""
        name = self._get_selected_name(is_server=False)
        if name is None:
            return
        agent_id = self._agent_var.get()
        if not agent_id:
            messagebox.showwarning("파일 관리", "에이전트를 선택하세요")
            return

        base = self._agent_path.replace("\\", "/").rstrip("/")
        file_path = base + "/" + name

        self._status_var.set(f"실행 중: {name}")

        def _do() -> None:
            try:
                result = self._app.api_post(
                    f"/api/files/agent/{agent_id}/run",
                    {"file_path": file_path},
                )
                success = isinstance(result, dict) and result.get("success", False)
                error = result.get("error", "") if isinstance(result, dict) else str(result)

                def _done() -> None:
                    if success:
                        self._status_var.set(f"실행 완료: {name}")
                    else:
                        self._status_var.set(f"실행 실패: {error}")

                self._btn_run.after(0, _done)
            except Exception as exc:
                self._btn_run.after(
                    0, self._status_var.set, f"실행 오류: {exc}"
                )

        threading.Thread(target=_do, daemon=True).start()

    def _do_transfer(
        self,
        direction: str,
        agent_id: str,
        local_path: str,
        remote_path: str,
        filename: str,
    ) -> None:
        if self._transferring:
            messagebox.showwarning("파일 관리", "전송이 진행 중입니다")
            return

        self._transferring = True
        self._progress["value"] = 0
        self._status_var.set(f"전송 중: {filename}")
        self._btn_to_agent.configure(state=tk.DISABLED)
        self._btn_to_server.configure(state=tk.DISABLED)

        def _do() -> None:
            try:
                body = {
                    "direction": direction,
                    "agent_id": agent_id,
                    "local_path": local_path,
                    "remote_path": remote_path,
                }
                result = self._app.api_post("/api/files/transfer", body)
                success = isinstance(result, dict) and result.get("success", False)
                error = result.get("error", "") if isinstance(result, dict) else str(result)

                def _done() -> None:
                    self._transferring = False
                    self._btn_to_agent.configure(state=tk.NORMAL)
                    self._btn_to_server.configure(state=tk.NORMAL)
                    if success:
                        self._progress["value"] = 100
                        self._status_var.set(f"전송 완료: {filename}")
                        # 양쪽 새로고침
                        self._load_server_dir(self._server_path)
                        if agent_id:
                            self._load_agent_dir(self._agent_path)
                    else:
                        self._progress["value"] = 0
                        self._status_var.set(f"전송 실패: {error}")

                self._progress.after(0, _done)
            except Exception as exc:
                def _err() -> None:
                    self._transferring = False
                    self._btn_to_agent.configure(state=tk.NORMAL)
                    self._btn_to_server.configure(state=tk.NORMAL)
                    self._status_var.set(f"전송 오류: {exc}")
                self._progress.after(0, _err)

        threading.Thread(target=_do, daemon=True).start()

    # ── 유틸리티 ──

    def _refresh_agent_list(self) -> None:
        try:
            agent_ids = self._app.get_connected_agent_ids()
            self._agent_combo["values"] = agent_ids
            if agent_ids and not self._agent_var.get():
                self._agent_var.set(agent_ids[0])
        except Exception:
            pass
