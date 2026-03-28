"""로그 분석 탭: AirREC 로그 다운로드 + 분석 + 보고서 생성."""

from __future__ import annotations

import re
import threading
import tkinter as tk
from datetime import date
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Any

_ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent  # AI-LogOps/

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


class LogAnalysisTab(tk.Frame):
    """AirREC 로그 분석 탭: 다운로드 + 분석 + 보고서 뷰어."""

    def __init__(self, parent: ttk.Notebook, app: ServerAppLike) -> None:
        super().__init__(parent, bg=BG_DARK)
        self._app = app
        self._log_folders: list[str] = []
        self._download_poll_id: str | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        # ── 상단: 컨트롤 영역 ──
        ctrl_frame = tk.LabelFrame(
            self,
            text="  로그 분석  ",
            bg=BG_FRAME,
            fg=FG_TEXT,
            font=FONT_SUBHEADING,
            padx=8,
            pady=8,
            relief=tk.GROOVE,
            bd=1,
        )
        ctrl_frame.pack(fill=tk.X, padx=8, pady=(8, 4))

        # Row 1: 에이전트 + 날짜 + 폴더
        row1 = tk.Frame(ctrl_frame, bg=BG_FRAME)
        row1.pack(fill=tk.X, pady=(0, 4))

        tk.Label(
            row1, text="에이전트:", bg=BG_FRAME, fg=FG_DIM, font=FONT_NORMAL,
        ).pack(side=tk.LEFT)

        self._agent_combo = ttk.Combobox(
            row1, values=[], state="readonly", width=22, font=FONT_NORMAL,
        )
        self._agent_combo.pack(side=tk.LEFT, padx=(4, 12))
        self._agent_combo.bind("<Button-1>", self._refresh_agents)
        self._agent_combo.bind("<<ComboboxSelected>>", self._on_agent_selected)

        tk.Label(
            row1, text="날짜:", bg=BG_FRAME, fg=FG_DIM, font=FONT_NORMAL,
        ).pack(side=tk.LEFT)

        self._date_entry = tk.Entry(
            row1,
            width=10,
            bg="#1e1e1e",
            fg=FG_TEXT,
            insertbackground=FG_TEXT,
            font=FONT_NORMAL,
            relief=tk.FLAT,
        )
        self._date_entry.pack(side=tk.LEFT, padx=(4, 12))
        self._date_entry.insert(0, date.today().strftime("%Y%m%d"))

        tk.Label(
            row1, text="폴더:", bg=BG_FRAME, fg=FG_DIM, font=FONT_NORMAL,
        ).pack(side=tk.LEFT)

        self._folder_combo = ttk.Combobox(
            row1,
            values=["-1 (전체)"],
            state="readonly",
            width=40,
            font=FONT_NORMAL,
        )
        self._folder_combo.set("-1 (전체)")
        self._folder_combo.pack(side=tk.LEFT, padx=(4, 0))

        # Row 2: 버튼 + 상태
        row2 = tk.Frame(ctrl_frame, bg=BG_FRAME)
        row2.pack(fill=tk.X)

        self._download_btn = Button(
            row2,
            text="다운로드",
            command=self._on_download,
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
        self._download_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._analyze_btn = Button(
            row2,
            text="분석",
            command=self._on_analyze,
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
        self._analyze_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._status_label = tk.Label(
            row2, text="", bg=BG_FRAME, fg=FG_DIM, font=FONT_SMALL,
        )
        self._status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # ── 하단: 보고서 뷰어 ──
        report_frame = tk.Frame(self, bg=BG_DARK)
        report_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(4, 4))

        # 헤더
        header = tk.Frame(report_frame, bg=BG_FRAME, padx=8, pady=4)
        header.pack(fill=tk.X)

        tk.Label(
            header, text="분석 보고서", bg=BG_FRAME, fg=FG_TEXT, font=FONT_HEADING,
        ).pack(side=tk.LEFT)

        # 텍스트 + 스크롤바
        text_frame = tk.Frame(report_frame, bg=BG_DARK)
        text_frame.pack(fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._report_text = tk.Text(
            text_frame,
            wrap=tk.WORD,
            bg=BG_FRAME,
            fg=FG_TEXT,
            font=FONT_MONO,
            padx=8,
            pady=4,
            state=tk.DISABLED,
            borderwidth=0,
            highlightthickness=0,
            yscrollcommand=scrollbar.set,
        )
        self._report_text.pack(fill=tk.BOTH, expand=True)
        scrollbar.config(command=self._report_text.yview)

        # 하단 버튼
        btn_frame = tk.Frame(report_frame, bg=BG_DARK)
        btn_frame.pack(fill=tk.X, pady=(4, 0))

        Button(
            btn_frame,
            text="저장",
            command=self._on_save,
            bg=BG_BTN,
            fg=FG_TEXT,
            relief=tk.FLAT,
            font=FONT_NORMAL,
            padx=12,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 4))

        Button(
            btn_frame,
            text="복사",
            command=self._on_copy,
            bg=BG_BTN,
            fg=FG_TEXT,
            relief=tk.FLAT,
            font=FONT_NORMAL,
            padx=12,
            cursor="hand2",
        ).pack(side=tk.LEFT)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _refresh_agents(self, event: Any = None) -> None:
        """콤보박스 클릭 시 접속 에이전트 목록 갱신."""
        agents = self._app.get_connected_agent_ids()
        self._agent_combo["values"] = agents if agents else ["(연결된 에이전트 없음)"]

    def _get_agent_id(self) -> str | None:
        """선택된 에이전트 ID 반환. 유효하지 않으면 None."""
        val = self._agent_combo.get().strip()
        if not val or val == "(연결된 에이전트 없음)":
            return None
        return val

    def _get_date_str(self) -> str | None:
        """날짜 입력값 검증 후 반환. 유효하지 않으면 None."""
        val = self._date_entry.get().strip()
        if not re.fullmatch(r"\d{8}", val):
            return None
        return val

    def _on_agent_selected(self, event: Any = None) -> None:
        """에이전트 선택 시 log_folders 목록 갱신."""
        self._fetch_log_folders()

    def _fetch_log_folders(self) -> None:
        """선택된 에이전트의 log_folders 설정을 가져와 폴더 콤보를 갱신."""
        agent_id = self._get_agent_id()
        if not agent_id:
            return

        self._set_status("폴더 정보 조회 중...", "#cca700")

        def _do() -> None:
            result = self._app.api_get(f"/api/config/{agent_id}")
            self.after(0, _on_done, result)

        def _on_done(result: dict[str, Any] | None) -> None:
            if not result:
                self._set_status("설정 조회 실패: 응답 없음", "#f44747")
                return
            if "error" in result:
                self._set_status(f"설정 조회 실패: {result['error']}", "#f44747")
                return

            # API 응답: {"config": {"monitoring": {"log_folders": [...]}}}
            config = result.get("config", result)
            monitoring = config.get("monitoring", {})
            log_folders: list[str] = monitoring.get("log_folders", [])

            self._log_folders = log_folders
            if not log_folders:
                self._set_status("에이전트에 log_folders 설정 없음", "#cca700")
                return

            values = ["-1 (전체)"] + [
                f"{i}: {path}" for i, path in enumerate(log_folders)
            ]
            self._folder_combo["values"] = values
            self._folder_combo.set("-1 (전체)")
            self._set_status(
                f"{len(log_folders)}개 폴더 조회 완료", "#51cf66",
            )

        threading.Thread(target=_do, daemon=True).start()

    def _get_folder_index(self) -> int:
        """폴더 콤보에서 folder_index 추출."""
        text = self._folder_combo.get().strip()
        if text.startswith("-1"):
            return -1
        try:
            return int(text.split(":")[0])
        except ValueError:
            return -1

    def _set_status(self, text: str, color: str = FG_DIM) -> None:
        self._status_label.configure(text=text, fg=color)

    def _set_report(self, text: str) -> None:
        """보고서 텍스트 위젯에 내용 설정."""
        self._report_text.configure(state=tk.NORMAL)
        self._report_text.delete("1.0", tk.END)
        self._report_text.insert("1.0", text)
        self._report_text.configure(state=tk.DISABLED)

    def _get_report(self) -> str:
        return self._report_text.get("1.0", tk.END).strip()

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def _on_download(self) -> None:
        agent_id = self._get_agent_id()
        if agent_id is None:
            self._set_status("에이전트를 선택하세요", "#f44747")
            return

        date_str = self._get_date_str()
        if date_str is None:
            self._set_status("날짜를 8자리 숫자로 입력하세요 (YYYYMMDD)", "#f44747")
            return

        folder_index = self._get_folder_index()

        self._download_btn.configure(state=tk.DISABLED)
        self._set_status("다운로드 요청 중...", "#cca700")

        def _do() -> None:
            result = self._app.api_post(f"/api/logs/{agent_id}/history", {
                "date": date_str,
                "folder_index": folder_index,
                "clear_existing": False,
            })
            self.after(0, _on_done, result)

        def _on_done(result: dict[str, Any] | None) -> None:
            if result and "error" not in result:
                self._set_status(
                    f"다운로드 중... {date_str} (파일 수신 대기)",
                    "#cca700",
                )
                self._start_download_poll(agent_id, date_str)
            else:
                self._download_btn.configure(state=tk.NORMAL)
                err = (result or {}).get("error", "서버 응답 없음")
                self._set_status(f"실패: {err}", "#f44747")

        threading.Thread(target=_do, daemon=True).start()

    def _start_download_poll(self, agent_id: str, date_str: str) -> None:
        """다운로드 완료를 폴링으로 감지 (2초 간격, 최대 60회=2분)."""
        self._stop_download_poll()
        log_dir = _ROOT_DIR / "storage" / "logs" / agent_id / date_str
        self._poll_count = 0
        self._poll_prev_count = 0

        def _tick() -> None:
            self._poll_count += 1
            files = list(log_dir.glob("*.txt")) if log_dir.exists() else []
            cur = len(files)

            if cur > 0 and cur == self._poll_prev_count:
                # 파일 수 변화 없으면 완료로 판단
                self._download_btn.configure(state=tk.NORMAL)
                self._set_status(
                    f"다운로드 완료: {cur}개 파일 ({log_dir.name}/)",
                    "#51cf66",
                )
                self._download_poll_id = None
                return

            self._poll_prev_count = cur
            if self._poll_count >= 60:
                self._download_btn.configure(state=tk.NORMAL)
                self._set_status(
                    f"다운로드 타임아웃 ({cur}개 파일 수신됨)",
                    "#cca700",
                )
                self._download_poll_id = None
                return

            status = f"다운로드 중... {cur}개 파일 수신" if cur > 0 else "다운로드 중... (파일 수신 대기)"
            self._set_status(status, "#cca700")
            self._download_poll_id = self.after(2000, _tick)

        self._download_poll_id = self.after(2000, _tick)

    def _stop_download_poll(self) -> None:
        if self._download_poll_id is not None:
            self.after_cancel(self._download_poll_id)
            self._download_poll_id = None

    # ------------------------------------------------------------------
    # Analyze
    # ------------------------------------------------------------------

    def _on_analyze(self) -> None:
        agent_id = self._get_agent_id()
        if agent_id is None:
            self._set_status("에이전트를 선택하세요", "#f44747")
            return

        date_str = self._get_date_str()
        if date_str is None:
            self._set_status("날짜를 8자리 숫자로 입력하세요 (YYYYMMDD)", "#f44747")
            return

        log_dir = _ROOT_DIR / "storage" / "logs" / agent_id / date_str
        if not log_dir.exists():
            self._set_status(
                f"로그 파일 없음: {log_dir}", "#f44747",
            )
            return

        files = sorted(log_dir.glob("*.txt"))
        if not files:
            self._set_status(
                f"로그 파일 없음: {log_dir}/*.txt", "#f44747",
            )
            return

        self._analyze_btn.configure(state=tk.DISABLED)
        self._set_status(f"분석 중... ({len(files)}개 파일)", "#cca700")

        def _do() -> None:
            try:
                from server.analysis.log_analyzer import LogAnalyzer
                from server.analysis.report_generator import generate_report
                from server.analysis.rtp_first_analyzer import (
                    analyze_rtp_first,
                    generate_rtp_first_report,
                )

                # 1) SMDR-FC 매칭 분석
                analyzer = LogAnalyzer(agent_id, date_str)
                result = analyzer.analyze_files(files)
                report_md = generate_report(result)

                # 2) RTP-First Safety Net 분석 (동일 파일)
                rtp_stats = analyze_rtp_first(agent_id, date_str, files)
                rtp_report = generate_rtp_first_report(rtp_stats)

                # 통합 보고서
                combined = report_md + "\n---\n\n" + rtp_report
                self.after(0, _on_done, result, combined)
            except Exception as exc:
                self.after(0, _on_error, str(exc))

        def _on_done(result: Any, report_md: str) -> None:
            self._analyze_btn.configure(state=tk.NORMAL)
            self._set_report(report_md)
            total_calls = result.inbound_count + result.outbound_count
            self._set_status(
                f"분석 완료: {result.total_lines:,}줄, {total_calls:,}건 통화",
                "#51cf66",
            )

        def _on_error(msg: str) -> None:
            self._analyze_btn.configure(state=tk.NORMAL)
            self._set_status(f"분석 실패: {msg}", "#f44747")

        threading.Thread(target=_do, daemon=True).start()

    # ------------------------------------------------------------------
    # Save / Copy
    # ------------------------------------------------------------------

    def _on_save(self) -> None:
        text = self._get_report()
        if not text:
            return

        agent_id = self._get_agent_id() or "unknown"
        date_str = self._get_date_str() or "nodate"

        filepath = filedialog.asksaveasfilename(
            defaultextension=".md",
            filetypes=[("Markdown", "*.md")],
            initialfile=f"{agent_id}_{date_str}_analysis.md",
        )
        if not filepath:
            return

        try:
            Path(filepath).write_text(text, encoding="utf-8")
            self._set_status(f"저장 완료: {filepath}", "#51cf66")
        except OSError as exc:
            self._set_status(f"저장 실패: {exc}", "#f44747")

    def _on_copy(self) -> None:
        text = self._get_report()
        if not text:
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self._set_status("클립보드에 복사됨", "#51cf66")
