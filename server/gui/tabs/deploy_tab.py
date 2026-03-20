"""배포 탭: 에이전트/프로세스 배포 및 제어."""

from __future__ import annotations

import sys
import threading
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Any

from server.gui.constants import (
    BG_BTN,
    BG_BTN_PRIMARY,
    BG_DARK,
    BG_FRAME,
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


class _DeploySection:
    """하나의 배포 대상 섹션 (에이전트/프로세스)."""

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
            font=FONT_SUBHEADING,
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

        self._agent_combo = ttk.Combobox(
            row1,
            values=["(auto)"],
            state="readonly",
            width=22,
            font=FONT_NORMAL,
        )
        self._agent_combo.set("(auto)")
        self._agent_combo.pack(side=tk.LEFT, padx=8)
        self._agent_combo.bind("<Button-1>", self._refresh_agents)

        # 1-1행: 대상 프로세스 선택 (process target만)
        if target == "process":
            row1_1 = tk.Frame(frame, bg=BG_FRAME)
            row1_1.pack(fill=tk.X, pady=(0, 4))

            tk.Label(
                row1_1,
                text="대상 프로세스:",
                bg=BG_FRAME,
                fg=FG_DIM,
                font=FONT_NORMAL,
            ).pack(side=tk.LEFT)

            self._process_combo = ttk.Combobox(
                row1_1,
                values=["(기본)"],
                state="readonly",
                width=22,
                font=FONT_NORMAL,
            )
            self._process_combo.set("(기본)")
            self._process_combo.pack(side=tk.LEFT, padx=8)
            self._agent_combo.bind("<<ComboboxSelected>>", self._refresh_process_list)
        else:
            self._process_combo = None

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

        Button(
            row2,
            text="파일 선택...",
            command=self._select_file,
            bg=BG_BTN,
            fg=FG_TEXT,
            relief=tk.FLAT,
            font=FONT_SMALL,
            padx=8,
            cursor="hand2",
        ).pack(side=tk.RIGHT)

        # 3행: 버튼
        row3 = tk.Frame(frame, bg=BG_FRAME)
        row3.pack(fill=tk.X)

        self._deploy_btn = Button(
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

        self._stop_btn = Button(
            row3,
            text="■ 중지",
            command=self._stop,
            bg="#e74c3c",
            fg=FG_WHITE,
            activebackground="#c0392b",
            activeforeground=FG_WHITE,
            relief=tk.FLAT,
            font=FONT_NORMAL,
            padx=16,
            pady=3,
            cursor="hand2",
        )
        self._stop_btn.pack(side=tk.LEFT, padx=(0, 4))

        self._start_btn = Button(
            row3,
            text="▶ 시작",
            command=self._start,
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
        self._start_btn.pack(side=tk.LEFT)

        self._status_label = tk.Label(
            row3,
            text="",
            bg=BG_FRAME,
            fg=FG_DIM,
            font=FONT_SMALL,
        )
        self._status_label.pack(side=tk.RIGHT)

    def _refresh_agents(self, _event: Any = None) -> None:
        """에이전트 탭의 접속 목록에서 콤보박스 갱신."""
        ids = self._app.get_connected_agent_ids()
        self._agent_combo["values"] = ["(auto)"] + ids

    def _refresh_process_list(self, _event: Any = None) -> None:
        """에이전트의 target_process 목록을 가져와서 콤보박스를 갱신합니다."""
        if self._process_combo is None:
            return
        agent_id = self._agent_combo.get()
        if not agent_id or agent_id == "(auto)":
            self._process_combo["values"] = ["(기본)"]
            self._process_combo.set("(기본)")
            return
        # Fetch agent config to get target_process names
        result = self._app.api_get(f"/api/config/{agent_id}")
        if result and "config" in result:
            tp = result["config"].get("target_process", {})
            if isinstance(tp, list):
                names = [
                    p.get("name", f"process-{i}")
                    for i, p in enumerate(tp)
                    if p.get("name")
                ]
                if names:
                    self._process_combo["values"] = names
                    self._process_combo.set(names[0])
                    return
            elif isinstance(tp, dict) and tp.get("name"):
                self._process_combo["values"] = [tp["name"]]
                self._process_combo.set(tp["name"])
                return
        self._process_combo["values"] = ["(기본)"]
        self._process_combo.set("(기본)")

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

        agent_id = self._agent_combo.get().strip()
        if agent_id == "(auto)":
            agent_id = ""

        self._deploy_btn.configure(state=tk.DISABLED)
        self._status_label.configure(text="배포 중...", fg="#cca700")
        self._log(f"[배포] {self._target} 배포 시작: {self._file_path}")

        process_name = ""
        if self._process_combo is not None:
            process_name = self._process_combo.get()
            if process_name == "(기본)":
                process_name = ""

        def _do_deploy() -> None:
            result = self._app.api_deploy_upload(
                self._file_path, agent_id, self._target,
                deploy_path=process_name,
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

    def _send_ctrl(self, action: str) -> None:
        """프로세스 제어 명령 전송 (stop/start)."""
        if not self._app.is_server_running():
            self._status_label.configure(text="서버가 실행 중이 아닙니다", fg="#f44747")
            return

        agent_id = self._agent_combo.get().strip()
        if agent_id == "(auto)":
            agent_id = ""

        label = "중지" if action == "stop" else "시작"
        btn = self._stop_btn if action == "stop" else self._start_btn
        btn.configure(state=tk.DISABLED)
        self._status_label.configure(text=f"{label} 중...", fg="#cca700")
        self._log(f"[{label}] {self._target} {label} 요청")

        def _do() -> None:
            body: dict[str, Any] = {"action": action, "target": self._target}
            if agent_id:
                body["agent_id"] = agent_id
            if self._process_combo is not None:
                name = self._process_combo.get()
                if name and name != "(기본)":
                    body["target_name"] = name
            result = self._app.api_post("/api/ctrl/restart", body)
            self._log_text.after(0, self._on_ctrl_done, result, action, btn)

        threading.Thread(target=_do, daemon=True).start()

    def _on_ctrl_done(
        self, result: dict[str, Any] | None, action: str, btn: Any
    ) -> None:
        btn.configure(state=tk.NORMAL)
        label = "중지" if action == "stop" else "시작"
        if result and result.get("status") == "ok":
            self._status_label.configure(text=f"{label} 완료", fg="#51cf66")
            self._log(f"[{label}] 성공")
        else:
            err = (result or {}).get("error", "응답 없음")
            self._status_label.configure(text=f"{label} 실패: {err}", fg="#f44747")
            self._log(f"[{label}] 실패: {err}")

    def _stop(self) -> None:
        self._send_ctrl("stop")

    def _start(self) -> None:
        self._send_ctrl("start")


class _FolderDeploySection:
    """폴더를 압축하여 에이전트의 감시 폴더에 배포하는 섹션."""

    def __init__(
        self,
        parent: tk.Frame,
        app: ServerAppLike,
        log_text: tk.Text,
    ) -> None:
        self._app = app
        self._log_text = log_text
        self._folder_path = ""
        self._watch_folders: list[dict[str, str]] = []

        frame = tk.LabelFrame(
            parent,
            text="  폴더 배포  ",
            bg=BG_FRAME,
            fg=FG_TEXT,
            font=FONT_SUBHEADING,
            padx=8,
            pady=8,
            relief=tk.GROOVE,
            bd=1,
        )
        frame.pack(fill=tk.X, padx=8, pady=4)

        # 1행: 에이전트 선택 + 조회
        row1 = tk.Frame(frame, bg=BG_FRAME)
        row1.pack(fill=tk.X, pady=(0, 4))

        tk.Label(
            row1,
            text="Agent ID:",
            bg=BG_FRAME,
            fg=FG_DIM,
            font=FONT_NORMAL,
        ).pack(side=tk.LEFT)

        self._agent_combo = ttk.Combobox(
            row1,
            values=["(auto)"],
            state="readonly",
            width=22,
            font=FONT_NORMAL,
        )
        self._agent_combo.set("(auto)")
        self._agent_combo.pack(side=tk.LEFT, padx=8)
        self._agent_combo.bind("<Button-1>", self._refresh_agents)
        self._agent_combo.bind("<<ComboboxSelected>>", self._on_agent_selected)

        self._fetch_btn = Button(
            row1,
            text="폴더 조회",
            command=self._fetch_watch_folders,
            bg=BG_BTN,
            fg=FG_TEXT,
            relief=tk.FLAT,
            font=FONT_SMALL,
            padx=8,
            cursor="hand2",
        )
        self._fetch_btn.pack(side=tk.LEFT)

        # 2행: 배포 대상 폴더 선택
        row2 = tk.Frame(frame, bg=BG_FRAME)
        row2.pack(fill=tk.X, pady=(0, 4))

        tk.Label(
            row2,
            text="배포 경로:",
            bg=BG_FRAME,
            fg=FG_DIM,
            font=FONT_NORMAL,
        ).pack(side=tk.LEFT)

        self._dest_combo = ttk.Combobox(
            row2,
            values=["(에이전트 선택 후 조회)"],
            state="readonly",
            width=40,
            font=FONT_NORMAL,
        )
        self._dest_combo.set("(에이전트 선택 후 조회)")
        self._dest_combo.pack(side=tk.LEFT, padx=8, fill=tk.X, expand=True)

        # 3행: 소스 폴더 선택
        row3 = tk.Frame(frame, bg=BG_FRAME)
        row3.pack(fill=tk.X, pady=(0, 4))

        tk.Label(
            row3,
            text="소스 폴더:",
            bg=BG_FRAME,
            fg=FG_DIM,
            font=FONT_NORMAL,
        ).pack(side=tk.LEFT)

        self._folder_label = tk.Label(
            row3,
            text="(선택 안됨)",
            bg=BG_FRAME,
            fg=FG_DIM,
            font=FONT_MONO,
            anchor="w",
        )
        self._folder_label.pack(side=tk.LEFT, padx=8, fill=tk.X, expand=True)

        Button(
            row3,
            text="폴더 선택...",
            command=self._select_folder,
            bg=BG_BTN,
            fg=FG_TEXT,
            relief=tk.FLAT,
            font=FONT_SMALL,
            padx=8,
            cursor="hand2",
        ).pack(side=tk.RIGHT)

        # 4행: 배포 버튼 + 상태
        row4 = tk.Frame(frame, bg=BG_FRAME)
        row4.pack(fill=tk.X)

        self._deploy_btn = Button(
            row4,
            text="배포",
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

        self._status_label = tk.Label(
            row4,
            text="",
            bg=BG_FRAME,
            fg=FG_DIM,
            font=FONT_SMALL,
        )
        self._status_label.pack(side=tk.RIGHT)

    def _refresh_agents(self, _event: Any = None) -> None:
        ids = self._app.get_connected_agent_ids()
        self._agent_combo["values"] = ["(auto)"] + ids

    def _on_agent_selected(self, _event: Any = None) -> None:
        self._fetch_watch_folders()

    def _fetch_watch_folders(self) -> None:
        """에이전트의 감시 폴더 목록을 조회합니다."""
        agent_id = self._agent_combo.get().strip()
        if agent_id == "(auto)":
            ids = self._app.get_connected_agent_ids()
            if not ids:
                self._status_label.configure(text="연결된 에이전트 없음", fg="#f44747")
                return
            agent_id = ids[0]

        self._status_label.configure(text="폴더 조회 중...", fg="#cca700")

        def _do_fetch() -> None:
            result = self._app.api_get(
                f"/api/dashboard/agent/{agent_id}/watch-folders"
            )
            self._dest_combo.after(0, self._on_folders_fetched, result)

        threading.Thread(target=_do_fetch, daemon=True).start()

    def _on_folders_fetched(self, result: dict[str, Any] | None) -> None:
        if result is None or "error" in (result or {}):
            err = (result or {}).get("error", "응답 없음")
            self._status_label.configure(text=f"조회 실패: {err}", fg="#f44747")
            return

        folders = result.get("watch_folders", [])
        self._watch_folders = folders

        if not folders:
            self._dest_combo["values"] = ["(감시 폴더 없음)"]
            self._dest_combo.set("(감시 폴더 없음)")
            self._status_label.configure(text="감시 폴더가 설정되지 않았습니다", fg="#cca700")
            return

        display_values = []
        for wf in folders:
            name = wf.get("name", "")
            path = wf.get("path", "")
            desc = wf.get("description", "")
            label = f"{name} - {path}"
            if desc:
                label += f" ({desc})"
            display_values.append(label)

        self._dest_combo["values"] = display_values
        self._dest_combo.set(display_values[0])
        self._status_label.configure(
            text=f"{len(folders)}개 폴더 조회 완료", fg="#51cf66"
        )

    def _get_selected_deploy_path(self) -> str:
        """선택된 감시 폴더의 경로를 반환합니다."""
        idx = self._dest_combo.current()
        if idx < 0 or idx >= len(self._watch_folders):
            return ""
        return self._watch_folders[idx].get("path", "")

    def _select_folder(self) -> None:
        path = filedialog.askdirectory(title="배포할 소스 폴더 선택")
        if path:
            self._folder_path = path
            from pathlib import Path

            folder = Path(path)
            files = [
                f
                for f in folder.rglob("*")
                if f.is_file() and f.name != "config.yaml"
            ]
            total_size = sum(f.stat().st_size for f in files)
            size_mb = total_size / (1024 * 1024)
            self._folder_label.configure(
                text=f"{folder.name} ({len(files)}개 파일, {size_mb:.1f} MB)",
                fg=FG_TEXT,
            )

    def _log(self, msg: str) -> None:
        self._log_text.configure(state=tk.NORMAL)
        self._log_text.insert(tk.END, msg + "\n")
        self._log_text.configure(state=tk.DISABLED)
        self._log_text.see(tk.END)

    def _deploy(self) -> None:
        if not self._folder_path:
            self._status_label.configure(text="소스 폴더를 선택하세요", fg="#f44747")
            return

        deploy_path = self._get_selected_deploy_path()
        if not deploy_path:
            self._status_label.configure(text="배포 대상 경로를 선택하세요", fg="#f44747")
            return

        if not self._app.is_server_running():
            self._status_label.configure(text="서버가 실행 중이 아닙니다", fg="#f44747")
            return

        import os
        import tempfile
        import zipfile
        from pathlib import Path

        folder = Path(self._folder_path)
        agent_id = self._agent_combo.get().strip()
        if agent_id == "(auto)":
            agent_id = ""

        tmp = tempfile.NamedTemporaryFile(
            suffix=".zip", delete=False, prefix=f"{folder.name}_"
        )
        tmp.close()
        tmp_path = tmp.name

        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in folder.rglob("*"):
                if f.is_file() and f.name != "config.yaml":
                    zf.write(str(f), str(f.relative_to(folder)))

        self._deploy_btn.configure(state=tk.DISABLED)
        self._status_label.configure(text="압축 및 배포 중...", fg="#cca700")
        self._log(f"[폴더배포] 배포 시작: {self._folder_path} -> {deploy_path}")

        def _do_deploy() -> None:
            result = self._app.api_deploy_upload(
                tmp_path, agent_id, "process", deploy_path=deploy_path
            )
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            self._log_text.after(0, self._on_deploy_done, result)

        threading.Thread(target=_do_deploy, daemon=True).start()

    def _on_deploy_done(self, result: dict[str, Any] | None) -> None:
        self._deploy_btn.configure(state=tk.NORMAL)
        if result is None:
            self._status_label.configure(text="배포 실패 (응답 없음)", fg="#f44747")
            self._log("[폴더배포] 실패: 서버 응답 없음")
            return

        if "error" in result:
            self._status_label.configure(text=f"실패: {result['error']}", fg="#f44747")
            self._log(f"[폴더배포] 실패: {result['error']}")
        else:
            agent = result.get("agent_id", "?")
            deploy_id = result.get("deploy_id", "?")
            self._status_label.configure(text=f"배포 완료 -> {agent}", fg="#51cf66")
            self._log(
                f"[폴더배포] 성공: agent={agent} deploy_id={deploy_id}"
            )


class _RemoteCommandSection:
    """에이전트의 원격 커맨드를 실행하는 섹션."""

    def __init__(
        self,
        parent: tk.Frame,
        app: ServerAppLike,
        log_text: tk.Text,
    ) -> None:
        self._app = app
        self._log_text = log_text
        self._commands: list[dict[str, str]] = []

        frame = tk.LabelFrame(
            parent,
            text="  원격 커맨드 실행  ",
            bg=BG_FRAME,
            fg=FG_TEXT,
            font=FONT_SUBHEADING,
            padx=8,
            pady=8,
            relief=tk.GROOVE,
            bd=1,
        )
        frame.pack(fill=tk.X, padx=8, pady=4)

        # 1행: 에이전트 선택 + 조회
        row1 = tk.Frame(frame, bg=BG_FRAME)
        row1.pack(fill=tk.X, pady=(0, 4))

        tk.Label(
            row1,
            text="Agent ID:",
            bg=BG_FRAME,
            fg=FG_DIM,
            font=FONT_NORMAL,
        ).pack(side=tk.LEFT)

        self._agent_combo = ttk.Combobox(
            row1,
            values=["(auto)"],
            state="readonly",
            width=22,
            font=FONT_NORMAL,
        )
        self._agent_combo.set("(auto)")
        self._agent_combo.pack(side=tk.LEFT, padx=8)
        self._agent_combo.bind("<Button-1>", self._refresh_agents)
        self._agent_combo.bind("<<ComboboxSelected>>", self._on_agent_selected)

        self._fetch_btn = Button(
            row1,
            text="커맨드 조회",
            command=self._fetch_commands,
            bg=BG_BTN,
            fg=FG_TEXT,
            relief=tk.FLAT,
            font=FONT_SMALL,
            padx=8,
            cursor="hand2",
        )
        self._fetch_btn.pack(side=tk.LEFT)

        # 2행: 커맨드 선택
        row2 = tk.Frame(frame, bg=BG_FRAME)
        row2.pack(fill=tk.X, pady=(0, 4))

        tk.Label(
            row2,
            text="커맨드:",
            bg=BG_FRAME,
            fg=FG_DIM,
            font=FONT_NORMAL,
        ).pack(side=tk.LEFT)

        self._cmd_combo = ttk.Combobox(
            row2,
            values=["(에이전트 선택 후 조회)"],
            state="readonly",
            width=40,
            font=FONT_NORMAL,
        )
        self._cmd_combo.set("(에이전트 선택 후 조회)")
        self._cmd_combo.pack(side=tk.LEFT, padx=8, fill=tk.X, expand=True)

        # 3행: 실행 버튼 + 상태
        row3 = tk.Frame(frame, bg=BG_FRAME)
        row3.pack(fill=tk.X)

        self._exec_btn = Button(
            row3,
            text="실행",
            command=self._execute,
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
        self._exec_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._status_label = tk.Label(
            row3,
            text="",
            bg=BG_FRAME,
            fg=FG_DIM,
            font=FONT_SMALL,
        )
        self._status_label.pack(side=tk.RIGHT)

    def _refresh_agents(self, _event: Any = None) -> None:
        ids = self._app.get_connected_agent_ids()
        self._agent_combo["values"] = ["(auto)"] + ids

    def _on_agent_selected(self, _event: Any = None) -> None:
        self._fetch_commands()

    def _fetch_commands(self) -> None:
        """에이전트의 원격 커맨드 목록을 조회합니다."""
        agent_id = self._agent_combo.get().strip()
        if agent_id == "(auto)":
            ids = self._app.get_connected_agent_ids()
            if not ids:
                self._status_label.configure(text="연결된 에이전트 없음", fg="#f44747")
                return
            agent_id = ids[0]

        self._status_label.configure(text="조회 중...", fg="#cca700")

        def _do_fetch() -> None:
            result = self._app.api_get(
                f"/api/dashboard/agent/{agent_id}/watch-folders"
            )
            self._cmd_combo.after(0, self._on_commands_fetched, result)

        threading.Thread(target=_do_fetch, daemon=True).start()

    def _on_commands_fetched(self, result: dict[str, Any] | None) -> None:
        if result is None or "error" in (result or {}):
            err = (result or {}).get("error", "응답 없음")
            self._status_label.configure(text=f"조회 실패: {err}", fg="#f44747")
            return

        commands = result.get("remote_commands", [])
        self._commands = commands

        if not commands:
            self._cmd_combo["values"] = ["(등록된 커맨드 없음)"]
            self._cmd_combo.set("(등록된 커맨드 없음)")
            self._status_label.configure(text="등록된 커맨드가 없습니다", fg="#cca700")
            return

        display_values = []
        for cmd in commands:
            name = cmd.get("name", "")
            desc = cmd.get("description", "")
            label = name
            if desc:
                label += f" - {desc}"
            display_values.append(label)

        self._cmd_combo["values"] = display_values
        self._cmd_combo.set(display_values[0])
        self._status_label.configure(
            text=f"{len(commands)}개 커맨드 조회 완료", fg="#51cf66"
        )

    def _log(self, msg: str) -> None:
        self._log_text.configure(state=tk.NORMAL)
        self._log_text.insert(tk.END, msg + "\n")
        self._log_text.configure(state=tk.DISABLED)
        self._log_text.see(tk.END)

    def _execute(self) -> None:
        idx = self._cmd_combo.current()
        if idx < 0 or idx >= len(self._commands):
            self._status_label.configure(text="커맨드를 선택하세요", fg="#f44747")
            return

        if not self._app.is_server_running():
            self._status_label.configure(text="서버가 실행 중이 아닙니다", fg="#f44747")
            return

        command_name = self._commands[idx].get("name", "")
        agent_id = self._agent_combo.get().strip()
        if agent_id == "(auto)":
            ids = self._app.get_connected_agent_ids()
            agent_id = ids[0] if ids else ""

        if not agent_id:
            self._status_label.configure(text="에이전트를 선택하세요", fg="#f44747")
            return

        self._exec_btn.configure(state=tk.DISABLED)
        self._status_label.configure(text="실행 중...", fg="#cca700")
        self._log(f"[커맨드] {command_name} 실행 요청 -> {agent_id}")

        def _do_exec() -> None:
            result = self._app.api_post(
                f"/api/dashboard/agent/{agent_id}/exec",
                {"command_name": command_name},
            )
            self._cmd_combo.after(0, self._on_exec_done, result)

        threading.Thread(target=_do_exec, daemon=True).start()

    def _on_exec_done(self, result: dict[str, Any] | None) -> None:
        self._exec_btn.configure(state=tk.NORMAL)
        if result is None:
            self._status_label.configure(text="실행 실패 (응답 없음)", fg="#f44747")
            self._log("[커맨드] 실패: 서버 응답 없음")
            return

        if "error" in result:
            self._status_label.configure(text=f"실패: {result['error']}", fg="#f44747")
            self._log(f"[커맨드] 실패: {result['error']}")
            return

        cmd_name = result.get("command_name", "?")
        success = result.get("success", False)
        exit_code = result.get("exit_code", -1)
        output = result.get("output", "")
        error = result.get("error", "")

        if success:
            self._status_label.configure(
                text=f"성공: {cmd_name} (exit={exit_code})", fg="#51cf66"
            )
            self._log(f"[커맨드] 성공: {cmd_name} exit_code={exit_code}")
        else:
            self._status_label.configure(
                text=f"실패: {cmd_name} (exit={exit_code})", fg="#f44747"
            )
            self._log(f"[커맨드] 실패: {cmd_name} exit_code={exit_code}")

        if output:
            self._log(f"[출력] {output.strip()}")
        if error:
            self._log(f"[에러] {error.strip()}")


class DeployTab(tk.Frame):
    """배포 관리 탭: 에이전트/프로세스."""

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

        # 마우스 휠 바인딩 (크로스 플랫폼)
        def _on_mousewheel(event: Any) -> None:
            if sys.platform == "darwin":
                canvas.yview_scroll(int(-1 * event.delta), "units")
            else:
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        if sys.platform.startswith("linux"):
            canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))
            canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))

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
            font=FONT_HEADING,
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
        self._folder_section = _FolderDeploySection(
            scroll_frame, self._app, self._log_text
        )
        self._remote_cmd_section = _RemoteCommandSection(
            scroll_frame, self._app, self._log_text
        )

        log_outer.pack(fill=tk.X)
