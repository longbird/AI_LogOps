"""AI-LogOps Agent GUI.

tkinter 메인 윈도우 + pystray 시스템 트레이.
에이전트 asyncio 루프는 백그라운드 스레드에서 실행된다.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import tkinter as tk
from pathlib import Path
from queue import SimpleQueue
from tkinter import ttk
from typing import Any

from agent.core.agent_runtime import AgentHooks, AgentRuntime
from agent.gui.log_viewer import LogViewer


class GUILogHandler(logging.Handler):
    """로그 레코드를 SimpleQueue로 전달하는 핸들러."""

    def __init__(self, queue: SimpleQueue[str]) -> None:
        super().__init__()
        self._queue: SimpleQueue[str] = queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._queue.put(msg)
        except Exception:
            self.handleError(record)


class AgentGUI:
    """에이전트 GUI 메인 애플리케이션."""

    def __init__(self) -> None:
        self._root = tk.Tk()
        self._root.title("AI-LogOps Agent")
        self._root.geometry("380x340")
        self._root.resizable(False, False)
        self._root.configure(bg="#252526")

        # 로그 큐 + 핸들러
        self._log_queue: SimpleQueue[str] = SimpleQueue()
        self._log_handler = GUILogHandler(self._log_queue)
        formatter = logging.Formatter(
            "[%(asctime)s] %(name)s %(levelname)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        self._log_handler.setFormatter(formatter)

        # 상태
        self._agent_thread: threading.Thread | None = None
        self._agent_loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._runtime: AgentRuntime | None = None
        self.tcp_client: Any | None = None
        self._log_viewer: LogViewer | None = None
        self._tray_icon: Any | None = None  # pystray.Icon (optional dep)
        self._running: bool = False

        # 상태 변수
        self._status_var = tk.StringVar(value="준비")
        self._agent_id_var = tk.StringVar(value="-")
        self._version_var = tk.StringVar(value="-")
        self._provider_var = tk.StringVar(value="-")
        self._uptime_var = tk.StringVar(value="00:00:00")
        self._log_count_var = tk.StringVar(value="0")

        self._start_time: float = 0.0

        self._build_ui()
        self._setup_tray()

        self._root.protocol("WM_DELETE_WINDOW", self._minimize_to_tray)

    def _build_ui(self) -> None:
        root = self._root

        # -- 헤더 --
        header = tk.Frame(root, bg="#007acc", height=44)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        tk.Label(
            header,
            text="AI-LogOps Agent",
            bg="#007acc",
            fg="white",
            font=("Segoe UI Semibold", 13),
        ).pack(side=tk.LEFT, padx=12, pady=8)

        self._status_dot = tk.Label(
            header,
            text="●",
            bg="#007acc",
            fg="#888888",
            font=("Segoe UI", 14),
        )
        self._status_dot.pack(side=tk.RIGHT, padx=12)

        # -- 상태 패널 --
        info_frame = tk.Frame(root, bg="#252526", padx=16, pady=12)
        info_frame.pack(fill=tk.X)

        rows = [
            ("상태", self._status_var),
            ("에이전트 ID", self._agent_id_var),
            ("버전", self._version_var),
            ("LLM Provider", self._provider_var),
            ("가동 시간", self._uptime_var),
            ("로그 수신", self._log_count_var),
        ]
        for i, (label_text, var) in enumerate(rows):
            tk.Label(
                info_frame,
                text=label_text,
                bg="#252526",
                fg="#888888",
                font=("Segoe UI", 9),
                anchor="w",
            ).grid(row=i, column=0, sticky="w", pady=2)

            tk.Label(
                info_frame,
                textvariable=var,
                bg="#252526",
                fg="#d4d4d4",
                font=("Segoe UI", 9),
                anchor="w",
            ).grid(row=i, column=1, sticky="w", padx=(16, 0), pady=2)

        info_frame.columnconfigure(1, weight=1)

        # -- 버튼 영역 --
        btn_frame = tk.Frame(root, bg="#252526", padx=16, pady=8)
        btn_frame.pack(fill=tk.X)

        self._start_btn = tk.Button(
            btn_frame,
            text="▶ 에이전트 시작",
            command=self._start_agent,
            bg="#0e639c",
            fg="white",
            activebackground="#1177bb",
            activeforeground="white",
            relief=tk.FLAT,
            font=("Segoe UI", 10),
            padx=12,
            pady=4,
            cursor="hand2",
        )
        self._start_btn.pack(fill=tk.X, pady=(0, 6))

        self._log_btn = tk.Button(
            btn_frame,
            text="📋 실시간 로그 보기",
            command=self._open_log_viewer,
            bg="#3c3c3c",
            fg="#cccccc",
            activebackground="#4c4c4c",
            activeforeground="white",
            relief=tk.FLAT,
            font=("Segoe UI", 10),
            padx=12,
            pady=4,
            cursor="hand2",
        )
        self._log_btn.pack(fill=tk.X, pady=(0, 6))

        self._reconnect_btn = tk.Button(
            btn_frame,
            text="🔄 강제 재접속",
            command=self._force_reconnect,
            bg="#3c3c3c",
            fg="#cccccc",
            activebackground="#4c4c4c",
            activeforeground="white",
            relief=tk.FLAT,
            font=("Segoe UI", 10),
            padx=12,
            pady=4,
            cursor="hand2",
            state=tk.DISABLED,
        )
        self._reconnect_btn.pack(fill=tk.X, pady=(0, 6))

        # -- 하단 바 --
        footer = tk.Frame(root, bg="#1e1e1e", height=24)
        footer.pack(fill=tk.X, side=tk.BOTTOM)
        footer.pack_propagate(False)

        tk.Label(
            footer,
            text="트레이로 최소화: 닫기 버튼 클릭",
            bg="#1e1e1e",
            fg="#555555",
            font=("Segoe UI", 8),
        ).pack(side=tk.LEFT, padx=8)

    def _setup_tray(self) -> None:
        """시스템 트레이 아이콘 설정."""
        try:
            import pystray
            from PIL import Image, ImageDraw

            # 간단한 아이콘 생성 (파란 원에 흰 A)
            image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.ellipse([4, 4, 60, 60], fill="#007acc")
            draw.text((20, 14), "A", fill="white")

            menu = pystray.Menu(
                pystray.MenuItem("열기", self._show_from_tray, default=True),
                pystray.MenuItem("로그 보기", self._tray_open_log),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("종료", self._quit_app),
            )

            self._tray_icon = pystray.Icon(
                "ai-logops",
                image,
                "AI-LogOps Agent",
                menu,
            )

            tray_thread = threading.Thread(
                target=self._tray_icon.run,
                daemon=True,
            )
            tray_thread.start()
        except ImportError:
            pass  # pystray 없으면 트레이 기능 비활성화

    def _minimize_to_tray(self) -> None:
        """창 닫기 시 트레이로 최소화. 트레이 없으면 완전 종료."""
        if self._tray_icon is not None:
            self._root.withdraw()
        else:
            self._quit_app()

    def _show_from_tray(self) -> None:
        """트레이에서 창 복원."""
        self._root.after(0, self._root.deiconify)
        self._root.after(10, self._root.lift)

    def _tray_open_log(self) -> None:
        self._root.after(0, self._open_log_viewer)

    def _quit_app(self) -> None:
        """완전 종료. 백그라운드 스레드가 남아있더라도 프로세스를 확실히 종료."""
        import os

        if self._stop_event is not None:
            self._stop_event.set()

        if self._agent_loop is not None:
            self._agent_loop.call_soon_threadsafe(self._agent_loop.stop)

        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()  # type: ignore[union-attr]
            except Exception:
                pass

        try:
            self._root.destroy()
        except Exception:
            pass

        # asyncio 루프/Telegram 폴링 등 데몬 스레드가 남아있을 수 있으므로
        # 일정 시간 후 강제 종료하여 좀비 프로세스를 방지한다.
        def _force_exit() -> None:
            import time

            time.sleep(3)
            os._exit(0)

        threading.Thread(target=_force_exit, daemon=True).start()

    # -- 에이전트 실행 --

    def _start_agent(self) -> None:
        """에이전트를 백그라운드 스레드에서 시작."""
        if self._running:
            return

        self._running = True
        self._start_btn.configure(text="⏹ 실행 중...", state=tk.DISABLED, bg="#3c3c3c")
        self._status_var.set("시작 중...")
        self._status_dot.configure(fg="#cca700")

        import time

        self._start_time = time.time()

        # 로그 핸들러를 루트 로거에 등록
        root_logger = logging.getLogger()
        root_logger.addHandler(self._log_handler)
        root_logger.setLevel(logging.INFO)

        self._agent_thread = threading.Thread(
            target=self._agent_thread_main,
            daemon=True,
        )
        self._agent_thread.start()

        # 업타임 갱신 타이머
        self._update_uptime()
        # 상태 폴링
        self._root.after(2000, self._update_agent_status)

    def _force_reconnect(self) -> None:
        """GUI에서 강제 재접속 트리거. 모든 서버에 재접속을 시도한다."""
        loop = self._agent_loop
        runtime = self._runtime
        if loop is None or runtime is None or not self._running:
            return

        self._reconnect_btn.configure(state=tk.DISABLED)
        self._status_var.set("재접속 중...")

        async def _do_reconnect() -> None:
            logger = logging.getLogger("agent.gui")
            success_count = 0
            total = len(runtime.connections)

            for conn in runtime.connections:
                try:
                    await conn.tcp_client.disconnect()
                    connected = await conn.tcp_client.connect()
                    if connected:
                        logger.info("force reconnect [%s]: success", conn.config.name)
                        success_count += 1
                    else:
                        logger.warning("force reconnect [%s]: failed", conn.config.name)
                except Exception:
                    logger.exception("force reconnect error [%s]", conn.config.name)

            if success_count == total and total > 0:
                msg = "전체 서버 재접속됨"
            elif success_count > 0:
                msg = f"서버 {success_count}/{total} 재접속됨"
            else:
                msg = "재접속 실패"

            self._root.after(0, lambda: self._status_var.set(msg))
            self._root.after(
                0,
                lambda: self._reconnect_btn.configure(state=tk.NORMAL),
            )

        loop.call_soon_threadsafe(asyncio.ensure_future, _do_reconnect())

    def _agent_thread_main(self) -> None:
        """백그라운드 스레드: asyncio 루프 + 에이전트 실행."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._agent_loop = loop

        try:
            loop.run_until_complete(self._run_agent_async())
        except Exception:
            logging.getLogger("gui").exception("에이전트 스레드 오류")
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.close()
            asyncio.set_event_loop(None)
            self._running = False

    async def _run_agent_async(self) -> None:
        from shared.utils import setup_file_logging

        base_dir = self._get_base_dir()
        setup_file_logging(base_dir / "log")

        self._stop_event = asyncio.Event()

        def _on_conn(connected: bool) -> None:
            def _update() -> None:
                self._status_var.set("서버 접속됨" if connected else "서버 연결 끊김")
                self._status_dot.configure(fg="#73c991" if connected else "#f44747")

            self._root.after(0, _update)

        def _on_status(status: str) -> None:
            self._root.after(0, lambda: self._status_var.set(status))

        def _on_log_sent() -> None:
            def _update() -> None:
                self._log_count_var.set(str(int(self._log_count_var.get()) + 1))

            self._root.after(0, _update)

        def _on_duplicate_instance(_message: str) -> None:
            self._root.after(
                0,
                lambda: self._status_var.set("중복 실행 감지 — 종료됨"),
            )

        def _on_provider_ready(provider: str) -> None:
            self._root.after(0, lambda: self._provider_var.set(provider))

        hooks = AgentHooks(
            on_status=_on_status,
            on_connection_change=_on_conn,
            on_log_sent=_on_log_sent,
            on_duplicate_instance=_on_duplicate_instance,
            on_provider_ready=_on_provider_ready,
            is_service_mode=False,
        )

        self._runtime = AgentRuntime(
            base_dir=base_dir,
            stop_event=self._stop_event,
            hooks=hooks,
        )
        self.tcp_client = self._runtime.tcp_client  # will be set during run()

        from agent import __version__ as agent_version

        self._root.after(0, lambda: self._agent_id_var.set("-"))
        self._root.after(0, lambda: self._version_var.set(agent_version))
        self._root.after(0, lambda: self._status_dot.configure(fg="#73c991"))
        self._root.after(0, lambda: self._reconnect_btn.configure(state=tk.NORMAL))

        try:
            await self._runtime.run()
        finally:
            self._root.after(0, self._on_agent_stopped)

    def _on_agent_stopped(self) -> None:
        self._status_var.set("정지됨")
        self._status_dot.configure(fg="#f44747")
        self._start_btn.configure(text="▶ 에이전트 시작", state=tk.NORMAL, bg="#0e639c")

    # -- 로그 뷰어 --

    def _open_log_viewer(self) -> None:
        if self._log_viewer is not None:
            try:
                self._log_viewer.deiconify()
                self._log_viewer.lift()
                return
            except tk.TclError:
                self._log_viewer = None

        self._log_viewer = LogViewer(self._root, self._log_queue)

    # -- 업타임 --

    def _update_uptime(self) -> None:
        if not self._running or self._start_time == 0:
            return
        import time

        elapsed = int(time.time() - self._start_time)
        h, rem = divmod(elapsed, 3600)
        m, s = divmod(rem, 60)
        self._uptime_var.set(f"{h:02d}:{m:02d}:{s:02d}")
        self._root.after(1000, self._update_uptime)

    def _update_agent_status(self) -> None:
        """에이전트 실행 중 상태 갱신."""
        if not self._running:
            return
        self._root.after(5000, self._update_agent_status)

    # -- 유틸 --

    @staticmethod
    def _get_base_dir() -> Path:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parents[1]

    # -- 실행 --

    def run(self) -> None:
        """tkinter 메인루프 시작. 에이전트 자동 시작."""
        self._root.after(100, self._start_agent)
        self._root.mainloop()


def run_gui() -> None:
    """GUI 엔트리포인트."""
    app = AgentGUI()
    app.run()


if __name__ == "__main__":
    run_gui()
