"""계정 관리 탭: 사용자 CRUD (Web 대시보드 /admin 기능과 동일)."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any
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
    ServerAppLike,
)


class AdminTab(tk.Frame):
    """계정 관리 탭."""

    def __init__(self, parent: ttk.Notebook, app: ServerAppLike) -> None:
        super().__init__(parent, bg=BG_DARK)
        self._app = app
        self._all_permissions: dict[str, str] = {}
        self._users: dict[str, Any] = {}

        # ── 상단 바 ──
        top = tk.Frame(self, bg=BG_DARK)
        top.pack(fill=tk.X, padx=10, pady=(10, 5))

        tk.Label(
            top,
            text="계정 관리",
            bg=BG_DARK,
            fg=FG_WHITE,
            font=("Segoe UI Semibold", 12),
        ).pack(side=tk.LEFT)

        tk.Button(
            top,
            text="+ 계정 추가",
            bg=BG_BTN_PRIMARY,
            fg=FG_WHITE,
            font=FONT_NORMAL,
            relief=tk.FLAT,
            padx=12,
            command=self._open_create_dialog,
        ).pack(side=tk.RIGHT, padx=(5, 0))

        tk.Button(
            top,
            text="새로고침",
            bg=BG_BTN,
            fg=FG_WHITE,
            font=FONT_NORMAL,
            relief=tk.FLAT,
            padx=10,
            command=self._refresh,
        ).pack(side=tk.RIGHT)

        self._status = tk.Label(
            top, text="", bg=BG_DARK, fg=FG_DIM, font=("Segoe UI", 8)
        )
        self._status.pack(side=tk.RIGHT, padx=10)

        # ── 사용자 목록 Treeview ──
        tree_frame = tk.Frame(self, bg=BG_FRAME)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        columns = ("username", "role", "permissions", "created_at")
        self._tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        self._tree.heading("username", text="사용자명")
        self._tree.heading("role", text="역할")
        self._tree.heading("permissions", text="권한")
        self._tree.heading("created_at", text="생성일")

        self._tree.column("username", width=120, minwidth=80)
        self._tree.column("role", width=80, minwidth=60)
        self._tree.column("permissions", width=300, minwidth=150)
        self._tree.column("created_at", width=160, minwidth=100)

        scrollbar = ttk.Scrollbar(
            tree_frame, orient=tk.VERTICAL, command=self._tree.yview
        )
        self._tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._tree.pack(fill=tk.BOTH, expand=True)

        # ── 하단 버튼 ──
        bottom = tk.Frame(self, bg=BG_DARK)
        bottom.pack(fill=tk.X, padx=10, pady=(0, 10))

        tk.Button(
            bottom,
            text="수정",
            bg=BG_BTN,
            fg=FG_WHITE,
            font=FONT_NORMAL,
            relief=tk.FLAT,
            padx=12,
            command=self._open_edit_dialog,
        ).pack(side=tk.LEFT, padx=(0, 5))

        tk.Button(
            bottom,
            text="삭제",
            bg="#6e1b1b",
            fg=FG_WHITE,
            font=FONT_NORMAL,
            relief=tk.FLAT,
            padx=12,
            command=self._delete_user,
        ).pack(side=tk.LEFT)

    # ------------------------------------------------------------------
    # 데이터 로드
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        self._status.config(text="로딩 중...", fg="#cca700")
        threading.Thread(target=self._do_refresh, daemon=True).start()

    def _do_refresh(self) -> None:
        resp = self._app.api_get("/api/admin/users")
        self.after(0, self._on_refresh, resp)

    def _on_refresh(self, resp: dict[str, Any] | None) -> None:
        for item in self._tree.get_children():
            self._tree.delete(item)

        if resp is None:
            self._status.config(text="서버 응답 없음", fg="#f44747")
            return

        if "error" in resp:
            self._status.config(text=f"오류: {resp['error']}", fg="#f44747")
            return

        self._users = resp.get("users", {})
        self._all_permissions = resp.get("permissions", {})

        for uname, u in self._users.items():
            role_text = "관리자" if u.get("role") == "admin" else "사용자"
            perms = u.get("permissions", [])
            perm_labels = ", ".join(
                self._all_permissions.get(p, p) for p in perms
            )
            created = u.get("created_at", "")
            if "T" in created:
                created = created.split("T")[0]

            self._tree.insert(
                "", tk.END, iid=uname,
                values=(uname, role_text, perm_labels, created),
            )

        count = len(self._users)
        self._status.config(text=f"{count}명", fg="#51cf66")

    def _get_selected_username(self) -> str | None:
        sel = self._tree.selection()
        if not sel:
            messagebox.showinfo("알림", "사용자를 선택하세요.", parent=self)
            return None
        return str(sel[0])

    # ------------------------------------------------------------------
    # 계정 추가
    # ------------------------------------------------------------------

    def _open_create_dialog(self) -> None:
        self._open_user_dialog(mode="create")

    def _open_edit_dialog(self) -> None:
        username = self._get_selected_username()
        if not username:
            return
        self._open_user_dialog(mode="edit", username=username)

    def _open_user_dialog(
        self, mode: str = "create", username: str = ""
    ) -> None:
        dlg = tk.Toplevel(self)
        dlg.title("계정 추가" if mode == "create" else f"계정 수정 — {username}")
        dlg.geometry("400x420")
        dlg.configure(bg=BG_DARK)
        dlg.transient(self)
        dlg.grab_set()

        # 사용자명
        tk.Label(
            dlg, text="사용자명", bg=BG_DARK, fg=FG_TEXT, font=FONT_NORMAL
        ).pack(anchor="w", padx=15, pady=(15, 2))
        name_entry = tk.Entry(dlg, font=FONT_NORMAL, width=30)
        name_entry.pack(padx=15, anchor="w")
        if mode == "edit":
            name_entry.insert(0, username)
            name_entry.config(state=tk.DISABLED)

        # 비밀번호
        pw_label_text = "비밀번호" if mode == "create" else "비밀번호 (변경 시 입력)"
        tk.Label(
            dlg, text=pw_label_text, bg=BG_DARK, fg=FG_TEXT, font=FONT_NORMAL
        ).pack(anchor="w", padx=15, pady=(10, 2))
        pw_entry = tk.Entry(dlg, font=FONT_NORMAL, width=30, show="*")
        pw_entry.pack(padx=15, anchor="w")

        # 역할
        tk.Label(
            dlg, text="역할", bg=BG_DARK, fg=FG_TEXT, font=FONT_NORMAL
        ).pack(anchor="w", padx=15, pady=(10, 2))
        role_var = tk.StringVar(value="user")
        role_frame = tk.Frame(dlg, bg=BG_DARK)
        role_frame.pack(anchor="w", padx=15)
        tk.Radiobutton(
            role_frame, text="사용자", variable=role_var, value="user",
            bg=BG_DARK, fg=FG_TEXT, selectcolor=BG_FRAME,
            activebackground=BG_DARK, activeforeground=FG_TEXT,
        ).pack(side=tk.LEFT, padx=(0, 15))
        tk.Radiobutton(
            role_frame, text="관리자", variable=role_var, value="admin",
            bg=BG_DARK, fg=FG_TEXT, selectcolor=BG_FRAME,
            activebackground=BG_DARK, activeforeground=FG_TEXT,
        ).pack(side=tk.LEFT)

        if mode == "edit":
            u = self._users.get(username, {})
            role_var.set(u.get("role", "user"))

        # 권한 체크박스
        tk.Label(
            dlg, text="권한", bg=BG_DARK, fg=FG_TEXT, font=FONT_NORMAL
        ).pack(anchor="w", padx=15, pady=(10, 2))
        perm_frame = tk.Frame(dlg, bg=BG_DARK)
        perm_frame.pack(anchor="w", padx=15)

        existing_perms = (
            self._users.get(username, {}).get("permissions", [])
            if mode == "edit"
            else []
        )

        perm_vars: dict[str, tk.BooleanVar] = {}
        for perm_key, perm_label in self._all_permissions.items():
            var = tk.BooleanVar(value=perm_key in existing_perms)
            perm_vars[perm_key] = var
            tk.Checkbutton(
                perm_frame, text=perm_label, variable=var,
                bg=BG_DARK, fg=FG_TEXT, selectcolor=BG_FRAME,
                activebackground=BG_DARK, activeforeground=FG_TEXT,
                font=("Segoe UI", 8),
            ).pack(anchor="w")

        # 상태 라벨
        status_lbl = tk.Label(
            dlg, text="", bg=BG_DARK, fg=FG_DIM, font=("Segoe UI", 8)
        )
        status_lbl.pack(anchor="w", padx=15, pady=(5, 0))

        # 버튼
        btn_frame = tk.Frame(dlg, bg=BG_DARK)
        btn_frame.pack(fill=tk.X, padx=15, pady=(10, 15))

        def _save() -> None:
            uname = name_entry.get().strip()
            pw = pw_entry.get().strip()
            role = role_var.get()
            perms = [k for k, v in perm_vars.items() if v.get()]

            if mode == "create":
                if not uname or not pw:
                    status_lbl.config(
                        text="사용자명과 비밀번호는 필수입니다.", fg="#f44747"
                    )
                    return
                if len(uname) < 2:
                    status_lbl.config(text="사용자명은 2자 이상.", fg="#f44747")
                    return
                if len(pw) < 4:
                    status_lbl.config(text="비밀번호는 4자 이상.", fg="#f44747")
                    return

            status_lbl.config(text="저장 중...", fg="#cca700")
            save_btn.config(state=tk.DISABLED)

            def _do() -> None:
                if mode == "create":
                    body = {
                        "username": uname,
                        "password": pw,
                        "role": role,
                        "permissions": perms,
                    }
                    resp = self._app.api_post("/api/admin/users", body)
                else:
                    body: dict[str, Any] = {
                        "role": role,
                        "permissions": perms,
                    }
                    if pw:
                        body["password"] = pw
                    resp = self._app.api_put(
                        f"/api/admin/users/{quote(uname)}", body
                    )

                self.after(0, _on_done, resp)

            def _on_done(resp: dict[str, Any] | None) -> None:
                save_btn.config(state=tk.NORMAL)
                if resp is None:
                    status_lbl.config(text="서버 응답 없음", fg="#f44747")
                    return
                if "error" in resp:
                    status_lbl.config(text=resp["error"], fg="#f44747")
                    return
                dlg.destroy()
                self._refresh()

            threading.Thread(target=_do, daemon=True).start()

        save_btn = tk.Button(
            btn_frame,
            text="저장",
            bg=BG_BTN_PRIMARY,
            fg=FG_WHITE,
            font=FONT_NORMAL,
            relief=tk.FLAT,
            padx=15,
            command=_save,
        )
        save_btn.pack(side=tk.LEFT, padx=(0, 5))

        tk.Button(
            btn_frame,
            text="취소",
            bg=BG_BTN,
            fg=FG_WHITE,
            font=FONT_NORMAL,
            relief=tk.FLAT,
            padx=15,
            command=dlg.destroy,
        ).pack(side=tk.LEFT)

    # ------------------------------------------------------------------
    # 삭제
    # ------------------------------------------------------------------

    def _delete_user(self) -> None:
        username = self._get_selected_username()
        if not username:
            return

        if not messagebox.askyesno(
            "계정 삭제",
            f"'{username}' 계정을 삭제하시겠습니까?\n이 작업은 되돌릴 수 없습니다.",
            parent=self,
        ):
            return

        self._status.config(text="삭제 중...", fg="#cca700")

        def _do() -> None:
            resp = self._app.api_delete(
                f"/api/admin/users/{quote(username)}"
            )
            self.after(0, _on_done, resp)

        def _on_done(resp: dict[str, Any] | None) -> None:
            if resp and "error" in resp:
                self._status.config(text=f"삭제 실패: {resp['error']}", fg="#f44747")
            else:
                self._refresh()

        threading.Thread(target=_do, daemon=True).start()
