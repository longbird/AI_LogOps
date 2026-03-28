"""테스트 배포 탭: 파일 단위 배포 + 자동 프로세스 제어."""

from __future__ import annotations

import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
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


class TestDeployTab(tk.Frame):
    """테스트 배포 탭 — 파일 선택 → 백업 → 중지 → 전송 → 시작."""

    def __init__(self, parent: ttk.Notebook, app: ServerAppLike) -> None:
        super().__init__(parent, bg=BG_DARK)
        self._app = app
        self._files: list[dict[str, Any]] = []
        self._deploying = False

        self._build_ui()

    # ── UI 구성 ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        # 스크롤 가능한 컨테이너
        container = tk.Frame(self, bg=BG_DARK)
        container.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # ── 1) 에이전트/프로세스 선택 ──
        agent_frame = tk.LabelFrame(
            container,
            text="  대상 에이전트  ",
            bg=BG_FRAME, fg=FG_TEXT,
            font=FONT_SUBHEADING,
            padx=8, pady=8,
            relief=tk.GROOVE, bd=1,
        )
        agent_frame.pack(fill=tk.X, pady=(0, 4))

        row = tk.Frame(agent_frame, bg=BG_FRAME)
        row.pack(fill=tk.X)

        tk.Label(row, text="Agent:", bg=BG_FRAME, fg=FG_DIM,
                 font=FONT_NORMAL).pack(side=tk.LEFT)
        self._agent_combo = ttk.Combobox(
            row, values=["(auto)"], state="readonly",
            width=22, font=FONT_NORMAL,
        )
        self._agent_combo.set("(auto)")
        self._agent_combo.pack(side=tk.LEFT, padx=8)
        self._agent_combo.bind("<Button-1>", self._refresh_agents)

        tk.Label(row, text="Process:", bg=BG_FRAME, fg=FG_DIM,
                 font=FONT_NORMAL).pack(side=tk.LEFT, padx=(16, 0))
        self._process_combo = ttk.Combobox(
            row, values=["(기본)"], state="readonly",
            width=22, font=FONT_NORMAL,
        )
        self._process_combo.set("(기본)")
        self._process_combo.pack(side=tk.LEFT, padx=8)
        self._agent_combo.bind("<<ComboboxSelected>>", self._on_agent_selected)

        self._info_label = tk.Label(
            agent_frame, text="", bg=BG_FRAME, fg=FG_DIM, font=FONT_SMALL,
            anchor=tk.W,
        )
        self._info_label.pack(fill=tk.X, pady=(4, 0))

        # ── 2) 빌드 경로 + 파일 목록 ──
        file_frame = tk.LabelFrame(
            container,
            text="  배포 파일  ",
            bg=BG_FRAME, fg=FG_TEXT,
            font=FONT_SUBHEADING,
            padx=8, pady=8,
            relief=tk.GROOVE, bd=1,
        )
        file_frame.pack(fill=tk.X, pady=4)

        # 빌드 경로
        build_row = tk.Frame(file_frame, bg=BG_FRAME)
        build_row.pack(fill=tk.X, pady=(0, 4))
        tk.Label(build_row, text="빌드 경로:", bg=BG_FRAME, fg=FG_DIM,
                 font=FONT_NORMAL).pack(side=tk.LEFT)
        self._build_dir_var = tk.StringVar(value="D:/Work/Setup/AirREC/Server")
        self._build_dir_entry = tk.Entry(
            build_row, textvariable=self._build_dir_var,
            bg=BG_DARK, fg=FG_TEXT, font=FONT_NORMAL,
            insertbackground=FG_TEXT, relief=tk.FLAT, bd=1,
        )
        self._build_dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        Button(
            build_row, text="...", width=3,
            command=self._browse_build_dir,
        ).pack(side=tk.LEFT)

        list_row = tk.Frame(file_frame, bg=BG_FRAME)
        list_row.pack(fill=tk.X)

        self._file_listbox = tk.Listbox(
            list_row,
            height=5,
            bg=BG_DARK, fg=FG_TEXT,
            font=FONT_MONO,
            selectmode=tk.EXTENDED,
            relief=tk.FLAT, bd=0,
            highlightthickness=1,
            highlightcolor="#3c3c3c",
            highlightbackground="#2c2c2c",
        )
        self._file_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        btn_col = tk.Frame(list_row, bg=BG_FRAME)
        btn_col.pack(side=tk.RIGHT, padx=(8, 0))

        Button(
            btn_col, text="추가...", width=8,
            command=self._add_files,
        ).pack(pady=(0, 4))
        Button(
            btn_col, text="제거", width=8,
            command=self._remove_selected,
        ).pack()

        # ── 3) 실행 버튼 ──
        action_frame = tk.Frame(container, bg=BG_DARK)
        action_frame.pack(fill=tk.X, pady=4)

        self._deploy_btn = Button(
            action_frame, text="배포 && 재시작", width=14,
            bg=BG_BTN_PRIMARY, fg=FG_WHITE,
            command=self._do_deploy,
        )
        self._deploy_btn.pack(side=tk.LEFT, padx=(0, 4))

        Button(
            action_frame, text="중지", width=8,
            bg="#e74c3c", fg=FG_WHITE,
            command=lambda: self._do_ctrl("stop"),
        ).pack(side=tk.LEFT, padx=4)

        Button(
            action_frame, text="시작", width=8,
            bg="#27ae60", fg=FG_WHITE,
            command=lambda: self._do_ctrl("start"),
        ).pack(side=tk.LEFT, padx=4)

        self._rollback_btn = Button(
            action_frame, text="롤백", width=8,
            bg="#f39c12", fg=FG_WHITE,
            command=self._do_rollback,
        )
        self._rollback_btn.pack(side=tk.LEFT, padx=4)

        self._status_label = tk.Label(
            action_frame, text="", bg=BG_DARK, fg=FG_DIM, font=FONT_SMALL,
        )
        self._status_label.pack(side=tk.RIGHT)

        # ── 4) 로그 ──
        log_frame = tk.LabelFrame(
            container,
            text="  실행 로그  ",
            bg=BG_FRAME, fg=FG_TEXT,
            font=FONT_SUBHEADING,
            padx=8, pady=8,
            relief=tk.GROOVE, bd=1,
        )
        log_frame.pack(fill=tk.BOTH, expand=True, pady=4)

        self._log_text = tk.Text(
            log_frame,
            height=12, wrap=tk.WORD,
            bg=BG_DARK, fg=FG_TEXT,
            font=FONT_MONO,
            state=tk.DISABLED,
            relief=tk.FLAT, bd=0,
            highlightthickness=0,
        )
        scrollbar = ttk.Scrollbar(log_frame, command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    # ── 에이전트/프로세스 ────────────────────────────────────

    def _refresh_agents(self, _event: Any = None) -> None:
        ids = self._app.get_connected_agent_ids()
        self._agent_combo["values"] = ["(auto)"] + ids

    def _on_agent_selected(self, _event: Any = None) -> None:
        agent_id = self._agent_combo.get()
        if not agent_id or agent_id == "(auto)":
            self._process_combo["values"] = ["(기본)"]
            self._process_combo.set("(기본)")
            self._info_label.configure(text="")
            return
        self._info_label.configure(text="설정 조회 중...", fg="#cca700")
        threading.Thread(
            target=self._fetch_process_info, args=(agent_id,), daemon=True,
        ).start()

    def _fetch_process_info(self, agent_id: str) -> None:
        """에이전트 config에서 target_process 정보 직접 조회."""
        result = self._app.api_get(f"/api/config/{agent_id}")
        self._log_text.after(0, self._apply_process_info, result)

    def _apply_process_info(self, result: dict | None) -> None:
        if result is None or "error" in result:
            err = (result or {}).get("error", "조회 실패")
            self._info_label.configure(text=f"오류: {err}", fg="#f44747")
            return
        config = result.get("config", {})
        tp = config.get("target_process", {})

        # 리스트 또는 단일 dict 모두 처리
        if isinstance(tp, list):
            tp_list = [p for p in tp if p.get("name")]
        elif isinstance(tp, dict) and tp.get("name"):
            tp_list = [tp]
        else:
            tp_list = []

        if not tp_list:
            self._process_combo["values"] = ["(기본)"]
            self._process_combo.set("(기본)")
            self._info_label.configure(text="target_process 미설정", fg="#f44747")
            return

        # 프로세스 이름-경로 매핑 저장 (배포 시 경로 표시용)
        self._process_info_map = {p["name"]: p for p in tp_list}
        names = [p["name"] for p in tp_list]
        self._process_combo["values"] = names
        self._process_combo.set(names[0])
        self._update_path_label(names[0])
        self._process_combo.bind("<<ComboboxSelected>>", self._on_process_selected)

    def _on_process_selected(self, _event: Any = None) -> None:
        name = self._process_combo.get()
        self._update_path_label(name)
        self._auto_load_files(name)

    def _update_path_label(self, name: str) -> None:
        info = getattr(self, "_process_info_map", {}).get(name, {})
        path = info.get("path", "")
        target_dir = str(Path(path).parent) if path else ""
        self._info_label.configure(
            text=f"대상 경로: {target_dir}" if target_dir else "",
            fg=FG_DIM,
        )

    def _auto_load_files(self, process_name: str) -> None:
        """선택된 프로세스에 맞는 빌드 파일 자동 로드."""
        build_dir = Path(self._build_dir_var.get())
        if not build_dir.exists():
            return

        stem = Path(process_name).stem  # "REC_SVR2.exe" → "REC_SVR2"
        candidates = []
        for ext in ("*.exe", "*.map"):
            for f in build_dir.glob(ext):
                if f.stem == stem:
                    candidates.append(f)

        if not candidates:
            return

        # 기존 목록 초기화 후 로드
        self._files.clear()
        self._file_listbox.delete(0, tk.END)
        for f in candidates:
            self._add_file_entry(f)

    # ── 파일 관리 ────────────────────────────────────────────

    def _browse_build_dir(self) -> None:
        d = filedialog.askdirectory(
            title="빌드 출력 디렉토리 선택",
            initialdir=self._build_dir_var.get(),
        )
        if d:
            self._build_dir_var.set(d)

    def _add_file_entry(self, path: Path) -> None:
        """파일 하나를 목록에 추가 (중복 무시). 파일명·크기·수정시간 표시."""
        if any(f["local_path"] == str(path) for f in self._files):
            return
        stat = path.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%m-%d %H:%M")
        size_mb = stat.st_size / (1024 * 1024)
        entry = {
            "local_path": str(path),
            "filename": path.name,
            "size": stat.st_size,
        }
        self._files.append(entry)
        self._file_listbox.insert(
            tk.END,
            f"  {entry['filename']:<24s} {size_mb:>6.1f} MB  {mtime}",
        )

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="배포할 파일 선택",
            filetypes=[
                ("실행/디버그 파일", "*.exe *.dll *.map"),
                ("실행 파일", "*.exe *.dll"),
                ("MAP 파일", "*.map"),
                ("모든 파일", "*.*"),
            ],
        )
        for p in paths:
            self._add_file_entry(Path(p))

    def _remove_selected(self) -> None:
        selected = list(self._file_listbox.curselection())
        for idx in reversed(selected):
            self._file_listbox.delete(idx)
            self._files.pop(idx)

    # ── 로그 ─────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self._log_text.configure(state=tk.NORMAL)
        self._log_text.insert(tk.END, line + "\n")
        self._log_text.configure(state=tk.DISABLED)
        self._log_text.see(tk.END)

    def _log_safe(self, msg: str) -> None:
        """스레드에서 호출 가능한 로그."""
        self._log_text.after(0, self._log, msg)

    # ── 배포 실행 ────────────────────────────────────────────

    def _do_deploy(self) -> None:
        if self._deploying:
            return
        if not self._files:
            self._status_label.configure(text="파일을 추가하세요", fg="#f44747")
            return
        if not self._app.is_server_running():
            self._status_label.configure(text="서버 미실행", fg="#f44747")
            return

        # 운영 프로세스 배포 재확인
        agent_id = self._agent_combo.get().strip()
        display_agent = agent_id if agent_id != "(auto)" else "(자동 선택)"
        file_names = "\n".join(f"  - {f['filename']}" for f in self._files)
        confirmed = messagebox.askyesno(
            "배포 확인",
            f"다음 파일을 배포하고 프로세스를 재시작합니다.\n\n"
            f"에이전트: {display_agent}\n"
            f"프로세스: {self._process_combo.get()}\n\n"
            f"배포 파일:\n{file_names}\n\n"
            f"프로세스가 중지됩니다. 계속하시겠습니까?",
        )
        if not confirmed:
            return

        self._deploying = True
        self._deploy_btn.configure(state=tk.DISABLED)
        self._rollback_btn.configure(state=tk.DISABLED)
        self._status_label.configure(text="배포 중...", fg="#cca700")

        agent_id = self._agent_combo.get().strip()
        if agent_id == "(auto)":
            agent_id = ""
        target_name = self._process_combo.get().strip()
        if target_name == "(기본)":
            target_name = ""

        # 선택된 프로세스의 대상 경로를 직접 전달 (config 재조회 불필요)
        process_info = getattr(self, "_process_info_map", {}).get(target_name, {})
        proc_path = process_info.get("path", "")
        target_dir = str(Path(proc_path).parent).replace("\\", "/") if proc_path else ""

        body = {
            "agent_id": agent_id,
            "target_name": target_name,
            "target_dir": target_dir,
            "files": [
                {"local_path": f["local_path"], "filename": f["filename"]}
                for f in self._files
            ],
        }

        def _run() -> None:
            self._log_safe("배포 시작...")
            result = self._app.api_post("/api/test-deploy/execute", body)
            self._log_text.after(0, self._on_deploy_done, result)

        threading.Thread(target=_run, daemon=True).start()

    def _on_deploy_done(self, result: dict | None) -> None:
        self._deploying = False
        self._deploy_btn.configure(state=tk.NORMAL)
        self._rollback_btn.configure(state=tk.NORMAL)

        if result is None:
            self._status_label.configure(text="배포 실패 (응답 없음)", fg="#f44747")
            self._log("오류: 서버 응답 없음")
            return

        # 단계별 로그 출력
        steps = result.get("steps", [])
        for step in steps:
            step_name = step.get("step", "?")
            success = step.get("success", False)
            icon = "OK" if success else "FAIL"
            extra = ""
            if "size_mb" in step:
                extra = f" ({step['size_mb']} MB)"
            if "error" in step and step["error"]:
                extra += f" - {step['error']}"
            if "pid" in step and step["pid"]:
                extra += f" (pid={step['pid']})"
            self._log(f"  [{icon}] {step_name}{extra}")

        if result.get("success"):
            pid = result.get("pid", 0)
            files = result.get("files", [])
            self._status_label.configure(
                text=f"배포 완료 (pid={pid}, {len(files)}개 파일)",
                fg="#51cf66",
            )
            self._log(f"배포 완료: {len(files)}개 파일, pid={pid}")
        else:
            err = result.get("error", "")
            self._status_label.configure(text=f"배포 실패: {err}", fg="#f44747")
            self._log(f"배포 실패: {err}")

    # ── 프로세스 제어 ────────────────────────────────────────

    def _do_ctrl(self, action: str) -> None:
        if not self._app.is_server_running():
            self._status_label.configure(text="서버 미실행", fg="#f44747")
            return

        agent_id = self._agent_combo.get().strip()
        if agent_id == "(auto)":
            agent_id = ""
        target_name = self._process_combo.get().strip()
        if target_name == "(기본)":
            target_name = ""

        self._log(f"프로세스 {action} 요청...")

        def _run() -> None:
            # 기존 ctrl API 사용
            body = {
                "agent_id": agent_id,
                "action": action,
                "target": "process",
                "target_name": target_name,
            }
            result = self._app.api_post("/api/ctrl/restart", body)
            self._log_text.after(0, self._on_ctrl_done, action, result)

        threading.Thread(target=_run, daemon=True).start()

    def _on_ctrl_done(self, action: str, result: dict | None) -> None:
        if result is None:
            self._log(f"프로세스 {action}: 응답 없음")
            return
        if result.get("error"):
            self._log(f"프로세스 {action} 실패: {result['error']}")
        else:
            self._log(f"프로세스 {action} 완료")

    def _do_rollback(self) -> None:
        if not self._app.is_server_running():
            self._status_label.configure(text="서버 미실행", fg="#f44747")
            return

        agent_id = self._agent_combo.get().strip()
        if agent_id == "(auto)":
            ids = self._app.get_connected_agent_ids()
            agent_id = ids[0] if ids else ""
        if not agent_id:
            self._status_label.configure(text="에이전트 없음", fg="#f44747")
            return

        target_name = self._process_combo.get().strip()
        if target_name == "(기본)":
            target_name = ""

        self._log("롤백 요청...")
        self._rollback_btn.configure(state=tk.DISABLED)

        def _run() -> None:
            body = {"target_name": target_name}
            result = self._app.api_post(
                f"/api/test-deploy/rollback/{agent_id}", body,
            )
            self._log_text.after(0, self._on_rollback_done, result)

        threading.Thread(target=_run, daemon=True).start()

    def _on_rollback_done(self, result: dict | None) -> None:
        self._rollback_btn.configure(state=tk.NORMAL)
        if result is None:
            self._log("롤백 실패: 응답 없음")
            self._status_label.configure(text="롤백 실패", fg="#f44747")
            return
        if result.get("success"):
            self._log("롤백 완료")
            self._status_label.configure(text="롤백 완료", fg="#51cf66")
        else:
            err = result.get("result", {}).get("error", "알 수 없는 오류")
            self._log(f"롤백 실패: {err}")
            self._status_label.configure(text=f"롤백 실패: {err}", fg="#f44747")
