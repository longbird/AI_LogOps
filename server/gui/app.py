"""AI-LogOps Server Management GUI.

tkinter 기반 서버 관리 도구.
서버 프로세스를 subprocess로 관리하고, 대시보드 API를 통해 에이전트/배포/녹취를 제어한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from queue import Empty, SimpleQueue
from tkinter import ttk
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from server.gui.constants import (
    BG_BTN,
    BG_BTN_PRIMARY,
    BG_DARK,
    BG_FRAME,
    BG_HEADER,
    FG_DIM,
    FG_TEXT,
    FG_WHITE,
    FONT_FAMILY,
    FONT_NORMAL,
    FONT_SMALL,
    FONT_TITLE,
)

logger = logging.getLogger("server.gui")

ROOT_DIR = Path(__file__).resolve().parent.parent.parent  # AI-LogOps/


class ServerGUI:
    """서버 관리 GUI 메인 애플리케이션."""

    def __init__(self) -> None:
        self._root = tk.Tk()
        self._root.title("AI-LogOps Server Manager")
        self._root.geometry("960x680")
        self._root.minsize(800, 560)
        self._root.configure(bg=BG_DARK)

        # 설정
        self._config = self._load_config()
        self._dashboard_port: int = int(
            self._config.get("dashboard", {}).get("port", 8080)
        )
        self._dashboard_url = f"http://localhost:{self._dashboard_port}"
        self._tcp_port: int = int(self._config.get("tcp", {}).get("port", 9500))
        self._auth_token: str = str(
            self._config.get("tcp", {}).get("auth_token", "")
        ) or os.environ.get("TCP_AUTH_TOKEN", "default-auth-token")
        self._jwt_cookie: str = ""
        self._auth_failed: bool = False  # 로그인 실패 시 반복 시도 방지

        # 서버 프로세스
        self._server_proc: subprocess.Popen[str] | None = None
        self._server_log_queue: SimpleQueue[str] = SimpleQueue()
        self._server_start_time: float = 0.0

        # 백그라운드 asyncio 루프
        self._bg_loop: asyncio.AbstractEventLoop | None = None
        self._bg_thread: threading.Thread | None = None

        self._build_ui()
        self._start_bg_loop()

        self._root.protocol("WM_DELETE_WINDOW", self._quit_app)

        # 서버 자동 시작
        self._root.after(500, self._auto_start_server)

    # ── Config ──

    @staticmethod
    def _load_config() -> dict[str, Any]:
        config_path = ROOT_DIR / "server" / "config.yaml"
        if not config_path.exists():
            return {}
        try:
            import yaml  # type: ignore[import-untyped]

            with open(config_path, encoding="utf-8") as f:
                return dict(yaml.safe_load(f) or {})
        except Exception:
            return {}

    # ── Background asyncio loop ──

    def _start_bg_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._bg_loop = loop
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()
        self._bg_thread = t

    def run_async(self, coro: Any) -> Any:
        """백그라운드 루프에서 코루틴 실행. Future 반환."""
        if self._bg_loop is None:
            raise RuntimeError("background loop not started")
        return asyncio.run_coroutine_threadsafe(coro, self._bg_loop)

    # ── UI 구성 ──

    def _build_ui(self) -> None:
        root = self._root

        # ── 헤더 ──
        header = tk.Frame(root, bg=BG_HEADER, height=44)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        tk.Label(
            header,
            text="AI-LogOps Server Manager",
            bg=BG_HEADER,
            fg=FG_WHITE,
            font=FONT_TITLE,
        ).pack(side=tk.LEFT, padx=12, pady=8)

        self._status_label = tk.Label(
            header,
            text="● Stopped",
            bg=BG_HEADER,
            fg="#ff6b6b",
            font=(FONT_FAMILY, 10),
        )
        self._status_label.pack(side=tk.RIGHT, padx=12)

        # ── 탭 노트북 ──
        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "Dark.TNotebook",
            background=BG_DARK,
            borderwidth=0,
        )
        style.configure(
            "Dark.TNotebook.Tab",
            background=BG_BTN,
            foreground=FG_DIM,
            padding=[12, 6],
            font=FONT_NORMAL,
        )
        style.map(
            "Dark.TNotebook.Tab",
            background=[("selected", BG_HEADER)],
            foreground=[("selected", FG_WHITE)],
        )

        self._notebook = ttk.Notebook(root, style="Dark.TNotebook")
        self._notebook.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # 탭 초기화 (지연 import)
        from server.gui.tabs.server_tab import ServerTab
        from server.gui.tabs.agents_tab import AgentsTab
        from server.gui.tabs.deploy_tab import DeployTab
        from server.gui.tabs.recording_tab import RecordingTab
        from server.gui.tabs.rec_viewer_tab import RecViewerTab
        from server.gui.tabs.admin_tab import AdminTab

        self._server_tab = ServerTab(self._notebook, self)
        self._agents_tab = AgentsTab(self._notebook, self)
        self._deploy_tab = DeployTab(self._notebook, self)
        self._recording_tab = RecordingTab(self._notebook, self)
        self._rec_viewer_tab = RecViewerTab(self._notebook, self)
        self._admin_tab = AdminTab(self._notebook, self)

        self._notebook.add(self._server_tab, text=" 서버 ")
        self._notebook.add(self._agents_tab, text=" 에이전트 ")
        self._notebook.add(self._deploy_tab, text=" 배포 ")
        self._notebook.add(self._recording_tab, text=" 녹취 ")
        self._notebook.add(self._rec_viewer_tab, text=" 녹취 조회 ")
        self._notebook.add(self._admin_tab, text=" 계정 관리 ")

        # ── 하단 바 ──
        footer = tk.Frame(root, bg="#1e1e1e", height=24)
        footer.pack(fill=tk.X, side=tk.BOTTOM)
        footer.pack_propagate(False)

        tk.Label(
            footer,
            text=f"Dashboard: {self._dashboard_url}  |  TCP: 0.0.0.0:{self._tcp_port}",
            bg="#1e1e1e",
            fg="#555555",
            font=FONT_SMALL,
        ).pack(side=tk.LEFT, padx=8)

        self._uptime_label = tk.Label(
            footer,
            text="",
            bg="#1e1e1e",
            fg="#555555",
            font=FONT_SMALL,
        )
        self._uptime_label.pack(side=tk.RIGHT, padx=8)

    # ── 서버 프로세스 관리 ──

    def start_server(self) -> bool:
        """서버 프로세스 시작. 성공 시 True."""
        if self._server_proc is not None and self._server_proc.poll() is None:
            return False  # 이미 실행 중

        try:
            env = {**__import__("os").environ, "PYTHONIOENCODING": "utf-8"}
            self._server_proc = subprocess.Popen(
                [sys.executable, "-u", "run_server.py"],
                cwd=str(ROOT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            self._server_start_time = time.time()

            # stdout 읽기 스레드
            threading.Thread(
                target=self._read_server_output,
                daemon=True,
            ).start()

            # 상태 갱신 타이머
            self._root.after(1000, self._update_server_status)

            self._status_label.configure(text="● Running", fg="#51cf66")
            logger.info("server process started: PID %d", self._server_proc.pid)
            return True
        except Exception as e:
            self._server_log_queue.put(f"[ERROR] 서버 시작 실패: {e}")
            return False

    def stop_server(self) -> bool:
        """서버 프로세스 중지. 성공 시 True."""
        proc = self._server_proc
        if proc is None or proc.poll() is not None:
            return False

        try:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

            self._server_proc = None
            self._status_label.configure(text="● Stopped", fg="#ff6b6b")
            self._uptime_label.configure(text="")
            logger.info("server process stopped")
            return True
        except Exception as e:
            self._server_log_queue.put(f"[ERROR] 서버 중지 실패: {e}")
            return False

    def is_server_running(self) -> bool:
        return self._server_proc is not None and self._server_proc.poll() is None

    def get_connected_agent_ids(self) -> list[str]:
        """에이전트 탭에서 현재 접속된 에이전트 ID 목록 반환."""
        try:
            return self._agents_tab.get_agent_ids()
        except Exception:
            return []

    def _read_server_output(self) -> None:
        """서버 프로세스의 stdout을 읽어서 큐에 넣기. 별도 스레드."""
        proc = self._server_proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                self._server_log_queue.put(line.rstrip("\n"))
        except Exception:
            pass
        finally:
            self._server_log_queue.put("[INFO] 서버 프로세스 종료됨")
            self._root.after(0, self._on_server_stopped)

    def _on_server_stopped(self) -> None:
        self._server_proc = None
        self._status_label.configure(text="● Stopped", fg="#ff6b6b")
        self._uptime_label.configure(text="")
        self._server_tab.on_server_stopped()

    def _update_server_status(self) -> None:
        if not self.is_server_running():
            return
        elapsed = int(time.time() - self._server_start_time)
        h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
        self._uptime_label.configure(text=f"Uptime: {h:02d}:{m:02d}:{s:02d}")
        self._root.after(1000, self._update_server_status)

    # ── HTTP 클라이언트 ──

    def _ensure_auth(self) -> None:
        """대시보드 로그인 (JWT 쿠키 획득)."""
        if self._jwt_cookie or self._auth_failed:
            return

        # config.yaml → dashboard.users 또는 auth.py 기본값 사용
        users = self._config.get("dashboard", {}).get("users", {})
        if users:
            username = next(iter(users))
            password = users[username]
        else:
            # auth.py 하드코딩 기본값
            username, password = "admin", "admin1234"

        try:
            import urllib.parse
            from urllib.request import HTTPRedirectHandler, build_opener

            data = urllib.parse.urlencode(
                {"username": username, "password": password}
            ).encode()
            req = Request(
                f"{self._dashboard_url}/login",
                data=data,
                method="POST",
            )
            req.add_header("Content-Type", "application/x-www-form-urlencoded")

            # 303 리다이렉트를 따라가지 않고 Set-Cookie 캡처
            class _NoRedirect(HTTPRedirectHandler):
                def redirect_request(  # type: ignore[override]
                    self, *_args: Any, **_kwargs: Any
                ) -> None:
                    return None  # type: ignore[return-value]

            opener = build_opener(_NoRedirect)
            try:
                resp = opener.open(req, timeout=5)
                # 200인데 쿠키 없으면 실패 (401 등)
                if resp.status == 401:
                    self._auth_failed = True
                    logger.warning("대시보드 로그인 실패 (비밀번호 불일치)")
            except HTTPError as e:
                if e.code == 401:
                    self._auth_failed = True
                    logger.warning("대시보드 로그인 실패 (비밀번호 불일치)")
                else:
                    # 303 리다이렉트는 HTTPError로 도착 — 여기서 쿠키 추출
                    for header_val in e.headers.get_all("Set-Cookie") or []:
                        if "access_token=" in header_val:
                            token = header_val.split("access_token=")[1].split(";")[0]
                            self._jwt_cookie = token
                            break
                    if not self._jwt_cookie:
                        self._auth_failed = True
        except Exception:
            pass  # 서버 미실행 시 무시

    def _do_request(self, req: Request) -> dict[str, Any] | None:
        """HTTP 요청 실행. 401 시 재로그인 1회 재시도."""
        for attempt in range(2):
            try:
                with urlopen(req, timeout=30) as resp:
                    return dict(json.loads(resp.read().decode()))
            except HTTPError as e:
                if e.code == 401 and attempt == 0:
                    # JWT 만료 → 쿠키/실패 플래그 초기화 후 재로그인
                    self._jwt_cookie = ""
                    self._auth_failed = False
                    self._ensure_auth()
                    if self._jwt_cookie:
                        req.remove_header("Cookie")
                        req.add_header("Cookie", f"access_token={self._jwt_cookie}")
                        continue
                # 4xx/5xx 에러 body 읽기 (실제 에러 메시지 보존)
                try:
                    return dict(json.loads(e.read().decode()))
                except Exception:
                    return None
            except Exception:
                return None
        return None

    def api_get(self, path: str) -> dict[str, Any] | None:
        """대시보드 API GET 요청."""
        self._ensure_auth()
        req = Request(f"{self._dashboard_url}{path}")
        if self._jwt_cookie:
            req.add_header("Cookie", f"access_token={self._jwt_cookie}")
        return self._do_request(req)

    def api_post(self, path: str, body: dict[str, Any]) -> dict[str, Any] | None:
        """대시보드 API POST (JSON) 요청."""
        self._ensure_auth()
        data = json.dumps(body).encode()
        req = Request(
            f"{self._dashboard_url}{path}",
            data=data,
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        if self._jwt_cookie:
            req.add_header("Cookie", f"access_token={self._jwt_cookie}")
        return self._do_request(req)

    def api_put(self, path: str, body: dict[str, Any]) -> dict[str, Any] | None:
        """대시보드 API PUT (JSON) 요청."""
        self._ensure_auth()
        data = json.dumps(body).encode()
        req = Request(
            f"{self._dashboard_url}{path}",
            data=data,
            method="PUT",
        )
        req.add_header("Content-Type", "application/json")
        if self._jwt_cookie:
            req.add_header("Cookie", f"access_token={self._jwt_cookie}")
        return self._do_request(req)

    def api_delete(self, path: str) -> dict[str, Any] | None:
        """대시보드 API DELETE 요청."""
        self._ensure_auth()
        req = Request(f"{self._dashboard_url}{path}", method="DELETE")
        if self._jwt_cookie:
            req.add_header("Cookie", f"access_token={self._jwt_cookie}")
        return self._do_request(req)

    def api_deploy_upload(
        self, file_path: str, agent_id: str, target: str,
        deploy_path: str = "",
    ) -> dict[str, Any] | None:
        """배포 파일 업로드 (multipart/form-data)."""
        try:
            import mimetypes
            import uuid

            boundary = uuid.uuid4().hex
            file_name = Path(file_path).name
            mime_type = mimetypes.guess_type(file_name)[0] or "application/zip"

            file_data = Path(file_path).read_bytes()

            body_parts: list[bytes] = []
            # file field
            body_parts.append(f"--{boundary}\r\n".encode())
            body_parts.append(
                f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'.encode()
            )
            body_parts.append(f"Content-Type: {mime_type}\r\n\r\n".encode())
            body_parts.append(file_data)
            body_parts.append(b"\r\n")
            # agent_id field
            body_parts.append(f"--{boundary}\r\n".encode())
            body_parts.append(
                b'Content-Disposition: form-data; name="agent_id"\r\n\r\n'
            )
            body_parts.append(f"{agent_id}\r\n".encode())
            # target field
            body_parts.append(f"--{boundary}\r\n".encode())
            body_parts.append(b'Content-Disposition: form-data; name="target"\r\n\r\n')
            body_parts.append(f"{target}\r\n".encode())
            # deploy_path field
            if deploy_path:
                body_parts.append(f"--{boundary}\r\n".encode())
                body_parts.append(b'Content-Disposition: form-data; name="deploy_path"\r\n\r\n')
                body_parts.append(f"{deploy_path}\r\n".encode())
            # end
            body_parts.append(f"--{boundary}--\r\n".encode())

            body = b"".join(body_parts)

            req = Request(
                f"{self._dashboard_url}/api/deploy/upload",
                data=body,
                method="POST",
            )
            req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
            req.add_header("Authorization", f"Bearer {self._auth_token}")

            with urlopen(req, timeout=30) as resp:
                return dict(json.loads(resp.read().decode()))
        except Exception as e:
            return {"error": str(e)}

    def _quit_app(self) -> None:
        import os

        if self.is_server_running():
            self.stop_server()

        if self._bg_loop is not None:
            self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)

        try:
            self._root.destroy()
        except Exception:
            pass

        def _force_exit() -> None:
            time.sleep(3)
            os._exit(0)

        threading.Thread(target=_force_exit, daemon=True).start()

    # ── Properties ──

    @property
    def root(self) -> tk.Tk:
        return self._root

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    @property
    def dashboard_url(self) -> str:
        return self._dashboard_url

    @property
    def auth_token(self) -> str:
        return self._auth_token

    @property
    def server_log_queue(self) -> SimpleQueue[str]:
        return self._server_log_queue

    # ── Auto Start ──

    def _auto_start_server(self) -> None:
        """GUI 시작 시 서버 자동 실행."""
        if self.is_server_running():
            return
        logger.info("auto-starting server on GUI launch")
        # 서버 탭의 _start_server()가 start_server() + UI 갱신을 함께 처리
        self._server_tab._start_server()

    # ── Run ──

    def run(self) -> None:
        self._root.mainloop()
