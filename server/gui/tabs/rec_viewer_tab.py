"""녹취 뷰어 탭: 녹취 이력 조회 및 상세 분석 결과 확인."""

from __future__ import annotations

import ctypes
import json
import os
import tempfile
import threading
import tkinter as tk
import wave
from datetime import datetime
from tkinter import ttk
from typing import Any
from urllib.request import Request, urlopen

from server.gui.constants import (
    BG_BTN,
    BG_DARK,
    BG_FRAME,
    FG_DIM,
    FG_TEXT,
    FG_WHITE,
    FONT_NORMAL,
    ServerAppLike,
)

# ── Windows MCI 오디오 제어 ──
_MCI_ALIAS = "logops_play"


def _mci(cmd: str) -> str:
    """MCI 명령 전송. 성공 시 결과 문자열, 실패 시 빈 문자열."""
    buf = ctypes.create_unicode_buffer(256)
    err = ctypes.windll.winmm.mciSendStringW(cmd, buf, 255, 0)
    return buf.value if err == 0 else ""


class RecViewerTab(tk.Frame):
    """녹취 이력 조회 및 상세 분석 뷰어 탭."""

    def __init__(self, parent: ttk.Notebook, app: ServerAppLike) -> None:
        super().__init__(parent, bg=BG_DARK)
        self._app = app
        self._current_agent_id: str | None = None

        # Playback state
        self._selected_filename: str = ""
        self._play_tmp_path: str = ""
        self._is_playing: bool = False
        self._mci_opened: bool = False
        self._play_duration_ms: int = 0
        self._update_timer_id: str = ""
        self._user_seeking: bool = False

        # UI Components
        self._date_entry: tk.Entry
        self._agent_entry: tk.Entry
        self._search_status: tk.Label
        self._tree: ttk.Treeview
        self._detail_frame: tk.Frame

        # Detail Section Components
        self._left_col: tk.Frame
        self._right_col: tk.Frame
        self._info_section: tk.Frame
        self._info_labels: dict[str, tk.Label] = {}
        self._score_section: tk.Frame
        self._lbl_total_score: tk.Label
        self._sub_score_widgets: dict[str, tuple[tk.Label, tk.Frame]] = {}
        self._ratio_bar_frame: tk.Frame
        self._ratio_bars: list[tk.Frame] = []
        self._lbl_keywords: tk.Label
        self._stt_section: tk.Frame
        self._txt_stt: tk.Text
        self._aq_section: tk.Frame
        self._aq_labels: dict[str, tk.Label] = {}

        self._build_ui()

    def _build_ui(self) -> None:
        # 1. Search Bar
        search_frame = tk.Frame(self, bg=BG_FRAME, padx=10, pady=8)
        search_frame.pack(fill=tk.X, padx=0, pady=0)

        # Date Entry
        tk.Label(
            search_frame, text="날짜:", bg=BG_FRAME, fg=FG_TEXT, font=FONT_NORMAL
        ).pack(side=tk.LEFT, padx=(0, 5))

        self._date_entry = tk.Entry(
            search_frame,
            width=12,
            bg="#1e1e1e",
            fg=FG_TEXT,
            insertbackground=FG_TEXT,
            font=FONT_NORMAL,
            relief=tk.FLAT,
        )
        self._date_entry.pack(side=tk.LEFT)
        self._date_entry.insert(0, datetime.now().strftime("%Y%m%d"))

        # Agent Entry
        tk.Label(
            search_frame, text="Agent:", bg=BG_FRAME, fg=FG_TEXT, font=FONT_NORMAL
        ).pack(side=tk.LEFT, padx=(15, 5))

        self._agent_entry = tk.Entry(
            search_frame,
            width=20,
            bg="#1e1e1e",
            fg=FG_TEXT,
            insertbackground=FG_TEXT,
            font=FONT_NORMAL,
            relief=tk.FLAT,
        )
        self._agent_entry.pack(side=tk.LEFT)
        self._agent_entry.insert(0, "(auto)")
        self._agent_entry.bind(
            "<FocusIn>",
            lambda e: (
                self._agent_entry.delete(0, tk.END)
                if self._agent_entry.get() == "(auto)"
                else None
            ),
        )

        # Search Button
        btn_search = tk.Button(
            search_frame,
            text="조회",
            bg=BG_BTN,
            fg=FG_WHITE,
            font=FONT_NORMAL,
            relief=tk.FLAT,
            padx=12,
            pady=2,
            command=self._search,
        )
        btn_search.pack(side=tk.LEFT, padx=(15, 0))

        # Status label (검색 결과 / 에러 표시)
        self._search_status = tk.Label(
            search_frame,
            text="",
            bg=BG_FRAME,
            fg=FG_DIM,
            font=("Segoe UI", 9),
        )
        self._search_status.pack(side=tk.LEFT, padx=(12, 0))

        # 1.5. Playback Bar
        play_frame = tk.Frame(self, bg=BG_FRAME, padx=10, pady=4)
        play_frame.pack(fill=tk.X, padx=0, pady=0)

        self._btn_play = tk.Button(
            play_frame,
            text="\u25b6 재생",
            bg=BG_BTN,
            fg=FG_WHITE,
            font=FONT_NORMAL,
            relief=tk.FLAT,
            padx=8,
            pady=2,
            command=self._play,
            state=tk.DISABLED,
        )
        self._btn_play.pack(side=tk.LEFT)

        self._btn_stop = tk.Button(
            play_frame,
            text="\u25a0 정지",
            bg=BG_BTN,
            fg=FG_WHITE,
            font=FONT_NORMAL,
            relief=tk.FLAT,
            padx=8,
            pady=2,
            command=self._stop,
            state=tk.DISABLED,
        )
        self._btn_stop.pack(side=tk.LEFT, padx=(5, 0))

        self._play_slider = tk.Scale(
            play_frame,
            from_=0,
            to=1000,
            orient=tk.HORIZONTAL,
            showvalue=False,
            bg=BG_FRAME,
            fg=FG_TEXT,
            troughcolor="#404040",
            activebackground="#007acc",
            highlightthickness=0,
            bd=0,
            sliderrelief=tk.FLAT,
            sliderlength=14,
            width=10,
        )
        self._play_slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 10))
        self._play_slider.bind(
            "<Button-1>", lambda _e: setattr(self, "_user_seeking", True)
        )
        self._play_slider.bind("<ButtonRelease-1>", self._on_seek)

        self._lbl_play_time = tk.Label(
            play_frame,
            text="0:00 / 0:00",
            bg=BG_FRAME,
            fg=FG_DIM,
            font=("Consolas", 9),
        )
        self._lbl_play_time.pack(side=tk.LEFT)

        # 2. PanedWindow
        paned = tk.PanedWindow(
            self,
            orient=tk.VERTICAL,
            bg=BG_DARK,
            sashrelief=tk.FLAT,
            sashwidth=4,
            sashpad=0,
            opaqueresize=False,
        )
        paned.pack(fill=tk.BOTH, expand=True, padx=0, pady=(5, 0))

        # Top: Treeview
        tree_frame = tk.Frame(paned, bg=BG_DARK)
        paned.add(tree_frame, height=200)

        # Scrollbar for tree
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical")
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Treeview Style
        style = ttk.Style()
        style.configure(
            "RecViewer.Treeview",
            background="#1e1e1e",
            foreground=FG_TEXT,
            fieldbackground="#1e1e1e",
            font=FONT_NORMAL,
            rowheight=24,
        )
        style.configure(
            "RecViewer.Treeview.Heading",
            background=BG_BTN,
            foreground=FG_TEXT,
            font=("Segoe UI Semibold", 9),
            relief="flat",
        )
        style.map("RecViewer.Treeview", background=[("selected", "#264f78")])

        cols = ("filename", "model", "status", "duration", "score", "analyzed_at")
        self._tree = ttk.Treeview(
            tree_frame,
            columns=cols,
            show="headings",
            style="RecViewer.Treeview",
            yscrollcommand=tree_scroll.set,
            height=10,
        )
        self._tree.heading("filename", text="파일명")
        self._tree.heading("model", text="STT 모델")
        self._tree.heading("status", text="상태")
        self._tree.heading("duration", text="시간")
        self._tree.heading("score", text="점수")
        self._tree.heading("analyzed_at", text="분석일시")

        self._tree.column("filename", width=260)
        self._tree.column("model", width=160, anchor="center")
        self._tree.column("status", width=80, anchor="center")
        self._tree.column("duration", width=80, anchor="center")
        self._tree.column("score", width=80, anchor="center")
        self._tree.column("analyzed_at", width=160, anchor="center")

        self._tree.pack(fill=tk.BOTH, expand=True)
        tree_scroll.config(command=self._tree.yview)
        self._tree.bind("<<TreeviewSelect>>", self._on_record_selected)

        # Bottom: Detail View (스크롤 없이 화면 크기에 맞춤)
        self._detail_frame = tk.Frame(paned, bg=BG_DARK)
        paned.add(self._detail_frame)

        # Build Detail Sections
        self._build_detail_sections()

    def _build_detail_sections(self) -> None:
        # ── 2단 레이아웃: 좌측(정보+점수+음질) 고정폭 / 우측(STT 대화) 확장 ──
        self._left_col = tk.Frame(self._detail_frame, bg=BG_DARK, width=420)
        self._left_col.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 2))
        self._left_col.pack_propagate(False)

        self._right_col = tk.Frame(self._detail_frame, bg=BG_DARK)
        self._right_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(2, 0))

        # ── 좌측: Section A — 녹취 정보 ──
        self._info_section = self._create_section("녹취 정보", self._left_col)

        # Row 0: 파일명(3칸) + 에이전트(2칸)
        row0_keys = [("filename", "파일명", 3), ("agent", "에이전트", 2)]
        col = 0
        for key, label_text, span in row0_keys:
            tk.Label(
                self._info_section,
                text=label_text,
                bg=BG_FRAME,
                fg=FG_DIM,
                font=("Segoe UI", 8),
            ).grid(row=0, column=col, columnspan=span, sticky="w", padx=10, pady=(2, 0))
            lbl = tk.Label(
                self._info_section,
                text="-",
                bg=BG_FRAME,
                fg=FG_TEXT,
                font=FONT_NORMAL,
            )
            lbl.grid(
                row=1, column=col, columnspan=span, sticky="w", padx=10, pady=(0, 4)
            )
            self._info_labels[key] = lbl
            col += span

        # Row 1: 음질/채널/통화시간/분석일시/단어수 (5칸)
        row1_keys = [
            ("status", "음질"),
            ("channel", "채널"),
            ("duration", "통화시간"),
            ("analyzed", "분석일시"),
            ("words", "단어수"),
        ]
        for c, (key, label_text) in enumerate(row1_keys):
            tk.Label(
                self._info_section,
                text=label_text,
                bg=BG_FRAME,
                fg=FG_DIM,
                font=("Segoe UI", 8),
            ).grid(row=2, column=c, sticky="w", padx=10, pady=(2, 0))
            lbl = tk.Label(
                self._info_section,
                text="-",
                bg=BG_FRAME,
                fg=FG_TEXT,
                font=FONT_NORMAL,
            )
            lbl.grid(row=3, column=c, sticky="w", padx=10, pady=(0, 4))
            self._info_labels[key] = lbl

        # ── 좌측: Section B — 점수 ──
        self._score_section = self._create_section("점수", self._left_col)

        # Total Score
        score_frame = tk.Frame(self._score_section, bg=BG_FRAME)
        score_frame.pack(fill=tk.X, padx=10, pady=(2, 0))

        tk.Label(
            score_frame, text="총점", bg=BG_FRAME, fg=FG_DIM, font=("Segoe UI", 9)
        ).pack(side=tk.LEFT)

        self._lbl_total_score = tk.Label(
            score_frame, text="-", bg=BG_FRAME, fg=FG_TEXT, font=("Segoe UI Bold", 16)
        )
        self._lbl_total_score.pack(side=tk.LEFT, padx=8)

        # Sub Scores
        sub_frame = tk.Frame(self._score_section, bg=BG_FRAME)
        sub_frame.pack(fill=tk.X, padx=10, pady=2)

        self._sub_score_widgets = {}
        for key, title in [
            ("response", "응답속도"),
            ("phrase", "필수문구"),
            ("silence", "침묵비율"),
        ]:
            f = tk.Frame(sub_frame, bg=BG_FRAME)
            f.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

            tk.Label(f, text=title, bg=BG_FRAME, fg=FG_DIM, font=("Segoe UI", 8)).pack(
                anchor="w"
            )

            val_row = tk.Frame(f, bg=BG_FRAME)
            val_row.pack(fill=tk.X, pady=2)

            lbl_val = tk.Label(
                val_row, text="-", bg=BG_FRAME, fg=FG_TEXT, font=FONT_NORMAL
            )
            lbl_val.pack(side=tk.LEFT)

            bar_bg = tk.Frame(val_row, bg="#404040", height=6, width=100)
            bar_bg.pack(side=tk.RIGHT, padx=5)
            bar_bg.pack_propagate(False)

            bar_fg = tk.Frame(bar_bg, bg=FG_DIM, height=6, width=0)
            bar_fg.pack(side=tk.LEFT)

            self._sub_score_widgets[key] = (lbl_val, bar_fg)

        # Talk Ratios
        ratio_frame = tk.Frame(self._score_section, bg=BG_FRAME)
        ratio_frame.pack(fill=tk.X, padx=10, pady=(2, 4))

        tk.Label(
            ratio_frame,
            text="대화 비율 (상담원/고객/침묵)",
            bg=BG_FRAME,
            fg=FG_DIM,
            font=("Segoe UI", 8),
        ).pack(anchor="w", pady=(0, 2))

        self._ratio_bar_frame = tk.Frame(ratio_frame, bg="#404040", height=12)
        self._ratio_bar_frame.pack(fill=tk.X)
        self._ratio_bars = []
        for color in ["#569cd6", "#4ec9b0", "#606060"]:
            b = tk.Frame(self._ratio_bar_frame, bg=color, height=12)
            b.pack(side=tk.LEFT, fill=tk.Y)
            self._ratio_bars.append(b)

        # Keywords
        kw_frame = tk.Frame(self._score_section, bg=BG_FRAME)
        kw_frame.pack(fill=tk.X, padx=10, pady=(0, 2))
        self._lbl_keywords = tk.Label(
            kw_frame,
            text="",
            bg=BG_FRAME,
            fg=FG_TEXT,
            font=FONT_NORMAL,
            justify=tk.LEFT,
        )
        self._lbl_keywords.pack(anchor="w")

        # ── 좌측: Section D — 음질 분석 ──
        self._aq_section = self._create_section("음질 분석", self._left_col)

        aq_keys = [
            ("l_rms", "L채널 RMS"),
            ("r_rms", "R채널 RMS"),
            ("l_silence", "L채널 침묵"),
            ("r_silence", "R채널 침묵"),
            ("dropout", "드롭아웃"),
            ("duration_wav", "WAV 길이"),
        ]

        for i, (key, label_text) in enumerate(aq_keys):
            r, c = divmod(i, 4)
            tk.Label(
                self._aq_section,
                text=label_text,
                bg=BG_FRAME,
                fg=FG_DIM,
                font=("Segoe UI", 8),
            ).grid(row=r * 2, column=c, sticky="w", padx=10, pady=(5, 0))

            lbl = tk.Label(
                self._aq_section, text="-", bg=BG_FRAME, fg=FG_TEXT, font=FONT_NORMAL
            )
            lbl.grid(row=r * 2 + 1, column=c, sticky="w", padx=10, pady=(0, 10))
            self._aq_labels[key] = lbl

        # ── 우측: Section C — STT 대화 ──
        stt_outer = tk.Frame(self._right_col, bg=BG_FRAME)
        stt_outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        tk.Label(
            stt_outer,
            text="STT 대화",
            bg=BG_FRAME,
            fg="#007acc",
            font=("Segoe UI Semibold", 10),
        ).pack(anchor="w", padx=10, pady=5)

        stt_body = tk.Frame(stt_outer, bg="#1e1e1e")
        stt_body.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0, 5))

        self._txt_stt = tk.Text(
            stt_body,
            bg="#1e1e1e",
            fg=FG_TEXT,
            font=FONT_NORMAL,
            state=tk.DISABLED,
            padx=10,
            pady=10,
            relief=tk.FLAT,
            wrap=tk.WORD,
            borderwidth=0,
            highlightthickness=0,
        )
        stt_scroll = ttk.Scrollbar(
            stt_body, orient=tk.VERTICAL, command=self._txt_stt.yview
        )
        self._txt_stt.configure(yscrollcommand=stt_scroll.set)
        stt_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._txt_stt.pack(fill=tk.BOTH, expand=True)

        # Text Tags
        self._txt_stt.tag_configure(
            "agent", foreground="#569cd6", lmargin1=10, lmargin2=10
        )
        self._txt_stt.tag_configure(
            "customer", foreground="#4ec9b0", lmargin1=150, lmargin2=150
        )
        self._txt_stt.tag_configure(
            "timestamp", foreground="#888888", font=("Consolas", 8)
        )

    def _create_section(self, title: str, parent: tk.Frame) -> tk.Frame:
        frame = tk.Frame(parent, bg=BG_FRAME, pady=2)
        frame.pack(fill=tk.X, padx=10, pady=2)

        tk.Label(
            frame, text=title, bg=BG_FRAME, fg="#007acc", font=("Segoe UI Semibold", 9)
        ).pack(anchor="w", padx=10, pady=(2, 0))

        content = tk.Frame(frame, bg=BG_FRAME)
        content.pack(fill=tk.X)
        return content

    def _search(self) -> None:
        date = self._date_entry.get().strip()
        agent_id = self._agent_entry.get().strip()
        if agent_id in ("(auto)", ""):
            agent_id = ""

        # Save agent_id for subsequent detail calls
        self._current_agent_id = agent_id

        self._search_status.config(text="조회 중...", fg="#cca700")

        params = []
        if date:
            params.append(f"date={date}")
        if agent_id:
            params.append(f"agent_id={agent_id}")
        query = "&".join(params)
        path = f"/api/rec/list?{query}" if query else "/api/rec/list"

        threading.Thread(target=self._do_search, args=(path,), daemon=True).start()

    def _do_search(self, path: str) -> None:
        resp = self._app.api_get(path)
        self.after(0, self._update_list, resp)

    def _update_list(self, resp: dict[str, Any] | None) -> None:
        # Clear existing
        for item in self._tree.get_children():
            self._tree.delete(item)

        if resp is None:
            self._search_status.config(
                text="서버 응답 없음 (서버 실행 여부 확인)", fg="#f44747"
            )
            return

        if "error" in resp:
            self._search_status.config(text=f"오류: {resp['error']}", fg="#f44747")
            return

        records = resp.get("records", [])
        if not records:
            self._search_status.config(text="결과 없음", fg=FG_DIM)
            return

        self._search_status.config(text=f"{len(records)}건 조회됨", fg="#51cf66")

        for r in records:
            filename = r.get("filename", "")
            model = r.get("model_name") or "-"
            status = r.get("status", "-")
            duration = self._fmt_duration(r.get("duration_wav"))
            score = self._fmt_score(r.get("score_total"))
            analyzed_at = r.get("aq_analyzed_at", "").replace("T", " ")

            self._tree.insert(
                "",
                tk.END,
                values=(filename, model, status, duration, score, analyzed_at),
            )

    def _on_record_selected(self, _event: Any) -> None:
        selection = self._tree.selection()
        if not selection:
            return
        item = self._tree.item(selection[0])
        filename = str(item["values"][0])
        agent_id = self._current_agent_id or ""

        # 선택된 파일 저장 + 재생 버튼 활성화
        self._selected_filename = filename
        self._btn_play.config(state=tk.NORMAL)

        path = f"/api/rec/detail/{filename}"
        if agent_id:
            path += f"?agent_id={agent_id}"

        threading.Thread(target=self._do_detail, args=(path,), daemon=True).start()

    def _do_detail(self, path: str) -> None:
        resp = self._app.api_get(path)
        if resp and "record" in resp:
            self.after(0, self._update_detail, resp)

    def _update_detail(self, resp: dict[str, Any]) -> None:
        r = resp.get("record", {})
        if not r:
            return

        # 1. Info Section
        self._info_labels["filename"].config(text=r.get("filename", "-"))
        self._info_labels["agent"].config(text=r.get("agent_id", "-"))

        status = r.get("status", "-")
        self._info_labels["status"].config(text=status, fg=self._status_color(status))
        self._info_labels["duration"].config(
            text=self._fmt_duration(r.get("duration_wav"))
        )

        is_stereo = r.get("is_stereo")
        self._info_labels["channel"].config(
            text="Stereo" if is_stereo == 1 else "Mono" if is_stereo == 0 else "-"
        )
        self._info_labels["analyzed"].config(
            text=r.get("aq_analyzed_at", "").replace("T", " ")
        )
        self._info_labels["words"].config(text=str(r.get("word_count", "-")))

        # 2. Score Section
        total = r.get("score_total")
        self._lbl_total_score.config(
            text=self._fmt_score(total), fg=self._score_color(total)
        )

        # Sub Scores
        self._update_sub_score("response", r.get("score_response"))
        self._update_sub_score("phrase", r.get("score_phrase"))
        self._update_sub_score("silence", r.get("score_silence"))

        # Ratios
        ag = r.get("agent_talk_ratio", 0) or 0
        cu = r.get("customer_talk_ratio", 0) or 0
        si = r.get("silence_ratio", 0) or 0

        # Ensure values are valid
        if ag < 0:
            ag = 0
        if cu < 0:
            cu = 0
        if si < 0:
            si = 0

        total_ratio = ag + cu + si

        # Normalize if total > 1.0 (though API should return 0.0-1.0)
        if total_ratio > 1.0:
            ag /= total_ratio
            cu /= total_ratio
            si /= total_ratio

        # Use place for relative width
        self._ratio_bars[0].place(relx=0, rely=0, relheight=1, relwidth=ag)
        self._ratio_bars[1].place(relx=ag, rely=0, relheight=1, relwidth=cu)
        self._ratio_bars[2].place(relx=ag + cu, rely=0, relheight=1, relwidth=si)

        # Keywords
        hit_req = r.get("required_phrase_hit", False)
        req_list = r.get("required_phrase_list", "")
        hit_forb = r.get("forbidden_word_hit", False)
        forb_list = r.get("forbidden_word_list", "")

        req_txt = f"필수문구: {'O' if hit_req else 'X'} ({req_list})"
        forb_txt = f"금지어: {'O' if hit_forb else 'X'} ({forb_list})"
        self._lbl_keywords.config(text=f"{req_txt}\n{forb_txt}")

        # 3. STT Section
        self._txt_stt.config(state=tk.NORMAL)
        self._txt_stt.delete("1.0", tk.END)

        seg_json = r.get("segments_json", "[]")
        try:
            segments = json.loads(seg_json)
            # Sort by time
            # order 필드 우선 정렬 (기존 데이터는 order 없음 → time 폴백)
            segments.sort(key=lambda x: (x.get("order", 999999), x.get("time", 0)))

            for s in segments:
                start = self._fmt_duration(s.get("time", 0))
                end = self._fmt_duration(s.get("end", 0))
                spk = s.get("speaker", "").lower()
                text = s.get("text", "")

                is_agent = "agent" in spk or "left" in spk or spk == "l"
                tag = "agent" if is_agent else "customer"
                role = "상담원" if is_agent else "고객"

                line_header = f"[{start}~{end}] {role}: "

                self._txt_stt.insert(tk.END, line_header, "timestamp")
                self._txt_stt.insert(tk.END, f"{text}\n\n", tag)

        except json.JSONDecodeError:
            self._txt_stt.insert(tk.END, "(대화 내용 파싱 실패)")

        self._txt_stt.config(state=tk.DISABLED)

        # 4. Audio Quality
        def fmt_rms(v: float | None) -> str:
            return f"{v:.1f} dB" if v is not None else "-"

        def fmt_pct(v: float | None) -> str:
            return f"{round(v * 100)}%" if v is not None else "-"

        self._aq_labels["l_rms"].config(text=fmt_rms(r.get("left_rms_db")))
        self._aq_labels["r_rms"].config(text=fmt_rms(r.get("right_rms_db")))
        self._aq_labels["l_silence"].config(text=fmt_pct(r.get("left_silence")))
        self._aq_labels["r_silence"].config(text=fmt_pct(r.get("right_silence")))
        self._aq_labels["dropout"].config(text=str(r.get("dropout_count", "-")))
        self._aq_labels["duration_wav"].config(
            text=self._fmt_duration(r.get("duration_wav"))
        )

    def _update_sub_score(self, key: str, val: float | None) -> None:
        lbl, bar = self._sub_score_widgets[key]
        lbl.config(text=self._fmt_score(val))

        # Color bar
        c = self._score_color(val)
        bar.config(bg=c)

        # Width (max 150)
        # val is 0-100 usually
        if val is None:
            val = 0
        w = min(int(val * 1.5), 150)
        bar.config(width=w)

    @staticmethod
    def _fmt_duration(sec: float | None) -> str:
        if not sec or sec <= 0:
            return "-"
        m, s = divmod(int(sec), 60)
        return f"{m}:{s:02d}"

    @staticmethod
    def _fmt_score(score: float | None) -> str:
        if score is None:
            return "-"
        return str(round(score))

    @staticmethod
    def _score_color(score: float | None) -> str:
        if score is None:
            return FG_DIM
        if score >= 80:
            return "#51cf66"
        if score >= 60:
            return "#cca700"
        return "#f44747"

    @staticmethod
    def _status_color(status: str) -> str:
        colors = {
            "OK": "#51cf66",
            "DROPOUT": "#f44747",
            "EMPTY": FG_DIM,
            "MUTED_L": "#cca700",
            "MUTED_R": "#cca700",
            "MISMATCH": "#e89b47",
        }
        return colors.get(status, FG_DIM)

    # ------------------------------------------------------------------
    # WAV 재생 (Windows MCI API)
    # ------------------------------------------------------------------

    def _play(self) -> None:
        """선택된 녹취 파일 재생."""
        if not self._selected_filename:
            return

        # 이미 재생 중이면 먼저 정지
        if self._is_playing:
            self._stop()

        self._btn_play.config(state=tk.DISABLED, text="\u25b6 다운로드중...")
        self._search_status.config(text="녹취 파일 다운로드 중...", fg="#cca700")
        threading.Thread(
            target=self._do_play, args=(self._selected_filename,), daemon=True
        ).start()

    def _do_play(self, filename: str) -> None:
        """백그라운드: WAV 다운로드 → PCM 변환 → UI 스레드에서 MCI 재생."""
        try:
            url = f"{self._app.dashboard_url}/api/rec/stream/{filename}"
            req = Request(url)

            token = self._app.auth_token
            if token:
                req.add_header("Authorization", f"Bearer {token}")

            with urlopen(req, timeout=30) as resp:
                wav_data = resp.read()

            if len(wav_data) < 44:
                raise RuntimeError(f"WAV 데이터 너무 작음: {len(wav_data)} bytes")

            self._cleanup_tmp()

            # WAV 헤더 파싱 → PCM 변환 (백그라운드 스레드)
            pcm_path = self._ensure_pcm_wav(wav_data)
            self._play_tmp_path = pcm_path

            self.after(0, self._start_playback, pcm_path, filename)
        except Exception as exc:
            self.after(0, self._play_error, str(exc))

    def _ensure_pcm_wav(self, wav_data: bytes) -> str:
        """WAV 바이트 → 표준 PCM WAV 파일로 저장. 항상 PCM 보장."""
        import struct as _st

        # RIFF 헤더 파싱
        if wav_data[:4] != b"RIFF" or wav_data[8:12] != b"WAVE":
            raise RuntimeError("RIFF/WAVE 헤더 없음")

        fmt_code = 1
        sample_rate = 8000
        n_channels = 1
        bits_per_sample = 16
        raw_data = b""
        pos = 12

        while pos + 8 <= len(wav_data):
            chunk_id = wav_data[pos : pos + 4]
            chunk_size = _st.unpack_from("<I", wav_data, pos + 4)[0]
            pos += 8

            if chunk_id == b"fmt ":
                fmt_code = _st.unpack_from("<H", wav_data, pos)[0]
                n_channels = _st.unpack_from("<H", wav_data, pos + 2)[0]
                sample_rate = _st.unpack_from("<I", wav_data, pos + 4)[0]
                bits_per_sample = _st.unpack_from("<H", wav_data, pos + 14)[0]
            elif chunk_id == b"data":
                raw_data = wav_data[pos : pos + chunk_size]
                break

            pos += chunk_size
            # RIFF 청크는 2바이트 정렬
            if chunk_size % 2:
                pos += 1

        if not raw_data:
            raise RuntimeError("data 청크 없음")

        # μ-law(7) → PCM16 디코딩
        if fmt_code == 7:
            pcm = bytearray(len(raw_data) * 2)
            for i in range(len(raw_data)):
                b = (~raw_data[i]) & 0xFF
                sign = b & 0x80
                exp = (b >> 4) & 0x07
                man = b & 0x0F
                s = ((man << 3) + 0x84) << exp
                s -= 0x84
                if sign:
                    s = -s
                s = max(-32768, min(32767, s))
                pcm[i * 2] = s & 0xFF
                pcm[i * 2 + 1] = (s >> 8) & 0xFF
            raw_data = bytes(pcm)
            bits_per_sample = 16
        elif fmt_code == 6:
            # A-law → PCM16
            pcm = bytearray(len(raw_data) * 2)
            for i in range(len(raw_data)):
                b = raw_data[i] ^ 0x55
                sign = b & 0x80
                exp = (b >> 4) & 0x07
                man = b & 0x0F
                if exp == 0:
                    s = (man << 4) + 8
                else:
                    s = ((man << 4) + 0x108) << (exp - 1)
                if sign:
                    s = -s
                s = max(-32768, min(32767, s))
                pcm[i * 2] = s & 0xFF
                pcm[i * 2 + 1] = (s >> 8) & 0xFF
            raw_data = bytes(pcm)
            bits_per_sample = 16
        elif fmt_code != 1:
            raise RuntimeError(f"지원하지 않는 WAV 포맷: {fmt_code}")

        # 표준 PCM WAV 파일로 저장
        fd, pcm_path = tempfile.mkstemp(suffix=".wav", prefix="logops_pcm_")
        os.close(fd)
        sample_width = bits_per_sample // 8
        with wave.open(pcm_path, "wb") as wf:
            wf.setnchannels(n_channels)
            wf.setsampwidth(sample_width)
            wf.setframerate(sample_rate)
            wf.writeframes(raw_data)

        # 검증: wave.open으로 읽을 수 있는지
        with wave.open(pcm_path, "rb") as wf:
            if wf.getnframes() == 0:
                raise RuntimeError("변환된 PCM 파일에 프레임 없음")

        return pcm_path

    def _start_playback(self, wav_path: str, filename: str) -> None:
        """변환된 PCM WAV를 MCI로 재생."""
        try:
            _mci(f"close {_MCI_ALIAS}")
            err = ctypes.windll.winmm.mciSendStringW(
                f'open "{wav_path}" type waveaudio alias {_MCI_ALIAS}',
                ctypes.create_unicode_buffer(256),
                255,
                0,
            )
            if err != 0:
                err_buf = ctypes.create_unicode_buffer(256)
                ctypes.windll.winmm.mciGetErrorStringW(err, err_buf, 255)
                raise RuntimeError(f"MCI: {err_buf.value}")

            length_str = _mci(f"status {_MCI_ALIAS} length")
            dur = int(length_str) if length_str.isdigit() else 0
            if dur == 0:
                _mci(f"close {_MCI_ALIAS}")
                raise RuntimeError("MCI: 재생 길이 0")

            self._play_duration_ms = dur
            _mci(f"play {_MCI_ALIAS}")
            self._mci_opened = True
            self._is_playing = True
            self._btn_play.config(state=tk.NORMAL, text="\u25b6 재생")
            self._btn_stop.config(state=tk.NORMAL)
            self._play_slider.set(0)
            self._lbl_play_time.config(text=f"0:00 / {self._fmt_ms(dur)}")
            self._search_status.config(text=f"\u25b6 재생 중: {filename}", fg="#51cf66")
            self._tick_playback()
        except Exception as exc:
            self._play_error(str(exc))

    def _tick_playback(self) -> None:
        """200ms 주기 위치 업데이트."""
        if not self._is_playing or not self._mci_opened:
            return

        pos_str = _mci(f"status {_MCI_ALIAS} position")
        pos_ms = int(pos_str) if pos_str.isdigit() else 0

        if self._play_duration_ms > 0 and pos_ms >= self._play_duration_ms:
            self._on_playback_finished()
            return

        if not self._user_seeking:
            slider_val = (
                pos_ms * 1000 // self._play_duration_ms if self._play_duration_ms else 0
            )
            self._play_slider.set(slider_val)

        self._lbl_play_time.config(
            text=f"{self._fmt_ms(pos_ms)} / {self._fmt_ms(self._play_duration_ms)}"
        )
        self._update_timer_id = self.after(200, self._tick_playback)

    def _on_seek(self, _event: Any) -> None:
        """슬라이더 드래그 완료 시 해당 위치로 이동."""
        self._user_seeking = False
        if not self._mci_opened or not self._play_duration_ms:
            return
        val = self._play_slider.get()
        seek_ms = val * self._play_duration_ms // 1000
        _mci(f"seek {_MCI_ALIAS} to {seek_ms}")
        _mci(f"play {_MCI_ALIAS}")

    def _on_playback_finished(self) -> None:
        """재생 완료 시 상태 정리."""
        self._is_playing = False
        if self._mci_opened:
            _mci(f"stop {_MCI_ALIAS}")
            _mci(f"close {_MCI_ALIAS}")
            self._mci_opened = False
        self._btn_stop.config(state=tk.DISABLED)
        self._play_slider.set(1000)
        self._lbl_play_time.config(
            text=(
                f"{self._fmt_ms(self._play_duration_ms)} / "
                f"{self._fmt_ms(self._play_duration_ms)}"
            )
        )
        self._search_status.config(text="재생 완료", fg=FG_DIM)

    def _play_error(self, msg: str) -> None:
        """재생 실패 시 UI 업데이트."""
        self._btn_play.config(state=tk.NORMAL, text="\u25b6 재생")
        self._search_status.config(text=f"재생 실패: {msg}", fg="#f44747")
        self._cleanup_tmp()

    def _stop(self) -> None:
        """재생 정지."""
        if self._update_timer_id:
            self.after_cancel(self._update_timer_id)
            self._update_timer_id = ""
        if self._mci_opened:
            _mci(f"stop {_MCI_ALIAS}")
            _mci(f"close {_MCI_ALIAS}")
            self._mci_opened = False
        else:
            # winsound 폴백 정지
            try:
                import winsound

                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass
        self._is_playing = False
        self._btn_stop.config(state=tk.DISABLED)
        self._play_slider.set(0)
        self._lbl_play_time.config(text="0:00 / 0:00")
        self._search_status.config(text="정지됨", fg=FG_DIM)
        self._cleanup_tmp()

    def _cleanup_tmp(self) -> None:
        """임시 WAV 파일 삭제."""
        if self._play_tmp_path and os.path.exists(self._play_tmp_path):
            try:
                os.unlink(self._play_tmp_path)
            except OSError:
                pass
            self._play_tmp_path = ""

    @staticmethod
    def _fmt_ms(ms: int) -> str:
        """밀리초를 M:SS 형식으로 변환."""
        total_sec = ms // 1000
        m, s = divmod(total_sec, 60)
        return f"{m}:{s:02d}"
