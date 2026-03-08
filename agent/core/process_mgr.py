from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime
from logging import Logger
from pathlib import Path

import psutil

from shared.utils import setup_logging


class ProcessManager:
    """대상 애플리케이션 프로세스 관리. 스펙 섹션 2.4 참조."""

    def __init__(self, process_name: str, process_path: str, backup_dir: str):
        """
        process_name: 프로세스 이름 (예: "target_app.exe")
        process_path: 실행 파일 경로 (예: "C:/Apps/target_app.exe")
        backup_dir: 백업 디렉토리 (예: "C:/Apps/backups/")
        """
        self.process_name: str = process_name
        self.process_path: Path = Path(process_path)
        self.backup_dir: Path = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self._logger: Logger = setup_logging("process_mgr")

    def find_pid(self) -> int | None:
        """프로세스 이름으로 PID 검색. 없으면 None."""
        for proc in psutil.process_iter(["name", "pid"]):
            name = proc.info.get("name")
            pid = proc.info.get("pid")
            if isinstance(name, str) and name.lower() == self.process_name.lower():
                if isinstance(pid, int):
                    return pid
        return None

    def find_all_pids(self) -> list[int]:
        """프로세스 이름으로 매칭되는 모든 PID 검색."""
        pids: list[int] = []
        for proc in psutil.process_iter(["name", "pid"]):
            name = proc.info.get("name")
            pid = proc.info.get("pid")
            if isinstance(name, str) and name.lower() == self.process_name.lower():
                if isinstance(pid, int):
                    pids.append(pid)
        return pids

    def kill(self) -> bool:
        """프로세스 종료. 성공 시 True."""
        pid = self.find_pid()
        if pid is None:
            return True

        proc: psutil.Process | None = None
        try:
            proc = psutil.Process(pid)
            proc.terminate()
            _ = proc.wait(timeout=10)
            return True
        except (psutil.NoSuchProcess, psutil.TimeoutExpired):
            if proc is None:
                return True
            try:
                proc.kill()
                return True
            except psutil.NoSuchProcess:
                return True

    def kill_all(self) -> bool:
        """동일 이름의 모든 프로세스를 종료한다. 모두 성공 시 True.

        kill()과 달리 첫 번째 매칭 PID만이 아닌,
        동일 이름의 모든 프로세스를 terminate → wait → force kill 한다.
        """
        pids = self.find_all_pids()
        if not pids:
            return True

        self._logger.info(
            "kill_all: terminating %d process(es) '%s' (pids=%s)",
            len(pids),
            self.process_name,
            pids,
        )

        # 1차: terminate (graceful)
        procs: list[psutil.Process] = []
        for pid in pids:
            try:
                p = psutil.Process(pid)
                p.terminate()
                procs.append(p)
            except psutil.NoSuchProcess:
                pass

        # wait for all (최대 10초)
        _, alive = psutil.wait_procs(procs, timeout=10)

        # 2차: 아직 살아있는 프로세스 force kill
        if alive:
            self._logger.warning(
                "kill_all: %d process(es) survived terminate, force killing",
                len(alive),
            )
            for p in alive:
                try:
                    p.kill()
                except psutil.NoSuchProcess:
                    pass
            _, still_alive = psutil.wait_procs(alive, timeout=5)
            if still_alive:
                self._logger.error(
                    "kill_all: %d process(es) could not be killed: %s",
                    len(still_alive),
                    [p.pid for p in still_alive],
                )
                return False

        self._logger.info("kill_all: all processes terminated successfully")
        return True

    # ── Task Scheduler 기반 권한 상승 (UAC 우회) ──────────────

    def _schtask_name(self) -> str:
        """예약 작업 이름 생성."""
        safe = self.process_name.replace(".", "_").replace(" ", "_")
        return f"AILogOps_{safe}"

    def register_schtask(self, args: list[str] | None = None) -> bool:
        """관리자 권한 실행용 예약 작업 등록.

        **최초 1회, 관리자 권한 콘솔에서 실행 필요.**
        등록 후에는 비관리자 콘솔에서도 UAC 없이 프로세스 시작 가능.
        """
        import subprocess as sp

        task_name = self._schtask_name()
        exe = str(self.process_path)
        cwd = str(self.process_path.parent)
        arg_str = " ".join(args) if args else ""

        # PowerShell: WorkingDirectory 지원
        action_parts = [
            f"New-ScheduledTaskAction -Execute '{exe}'",
            f"-WorkingDirectory '{cwd}'",
        ]
        if arg_str:
            action_parts.insert(1, f"-Argument '{arg_str}'")

        ps_script = (
            f"$action = {' '.join(action_parts)}; "
            f"$principal = New-ScheduledTaskPrincipal"
            f" -UserId $env:USERNAME -RunLevel Highest"
            f" -LogonType Interactive; "
            f"Register-ScheduledTask -TaskName '{task_name}'"
            f" -Action $action -Principal $principal -Force"
        )

        result = sp.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            self._logger.info("schtask '%s' registered", task_name)
            return True

        self._logger.warning("schtask registration failed: %s", result.stderr.strip())
        return False

    def _schtask_exists(self) -> bool:
        """예약 작업 존재 여부 확인."""
        import subprocess as sp

        r = sp.run(
            ["schtasks", "/Query", "/TN", self._schtask_name()],
            capture_output=True,
        )
        return r.returncode == 0

    def _start_via_schtask(self) -> int:
        """예약 작업으로 프로세스 시작 (UAC 없음). PID 반환."""
        import subprocess as sp
        import time

        task_name = self._schtask_name()
        r = sp.run(
            ["schtasks", "/Run", "/TN", task_name],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            raise OSError(f"schtasks /Run failed: {r.stderr.strip()}")

        # 프로세스 기동 대기 후 PID 확인
        for _ in range(10):
            time.sleep(0.5)
            pid = self.find_pid()
            if pid is not None:
                self._logger.info(
                    "started '%s' via schtask, pid=%d",
                    self.process_name,
                    pid,
                )
                return pid

        raise OSError(f"process '{self.process_name}' not found after schtasks /Run")

    # ── 프로세스 시작 (fallback chain) ─────────────────────

    def start(self, args: list[str] | None = None) -> int:
        """프로세스 시작. PID 반환.

        Fallback chain:
        1. subprocess.Popen (일반 실행)
        2. Task Scheduler (UAC 없이 관리자 실행, 사전 등록 필요)
        3. ShellExecuteEx runas (UAC 프롬프트 발생)
        """
        import subprocess

        cmd = [str(self.process_path)]
        if args:
            cmd.extend(args)

        cwd = str(self.process_path.parent)

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                creationflags=(
                    subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0
                ),
            )
            return proc.pid
        except OSError as exc:
            # Windows ERROR_ELEVATION_REQUIRED (740)
            if sys.platform == "win32" and getattr(exc, "winerror", 0) == 740:
                self._logger.info("elevation required for '%s'", self.process_name)
                # 1순위: Task Scheduler (UAC 없음)
                if self._schtask_exists():
                    return self._start_via_schtask()
                # 2순위: ShellExecuteEx runas (UAC 발생)
                self._logger.warning(
                    "schtask '%s' not registered — "
                    "falling back to ShellExecuteEx runas (UAC)",
                    self._schtask_name(),
                )
                return self._start_elevated(args, cwd)
            raise

    def _start_elevated(self, args: list[str] | None, cwd: str) -> int:
        """Windows ShellExecuteEx runas로 관리자 권한 프로세스 시작 (UAC 발생)."""
        import ctypes
        from ctypes import wintypes

        SEE_MASK_NOCLOSEPROCESS = 0x00000040

        class SHELLEXECUTEINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("fMask", wintypes.ULONG),
                ("hwnd", wintypes.HWND),
                ("lpVerb", wintypes.LPCWSTR),
                ("lpFile", wintypes.LPCWSTR),
                ("lpParameters", wintypes.LPCWSTR),
                ("lpDirectory", wintypes.LPCWSTR),
                ("nShow", ctypes.c_int),
                ("hInstApp", wintypes.HINSTANCE),
                ("lpIDList", ctypes.c_void_p),
                ("lpClass", wintypes.LPCWSTR),
                ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", wintypes.DWORD),
                ("hIconOrMonitor", wintypes.HANDLE),
                ("hProcess", wintypes.HANDLE),
            ]

        params = " ".join(args) if args else ""

        sei = SHELLEXECUTEINFO()
        sei.cbSize = ctypes.sizeof(sei)
        sei.fMask = SEE_MASK_NOCLOSEPROCESS
        sei.hwnd = None
        sei.lpVerb = "runas"
        sei.lpFile = str(self.process_path)
        sei.lpParameters = params or None
        sei.lpDirectory = cwd
        sei.nShow = 1  # SW_SHOWNORMAL

        if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
            err = ctypes.windll.kernel32.GetLastError()
            raise OSError(f"ShellExecuteExW failed (error={err})")

        pid = 0
        if sei.hProcess:
            pid = ctypes.windll.kernel32.GetProcessId(sei.hProcess)
            ctypes.windll.kernel32.CloseHandle(sei.hProcess)

        if not pid:
            raise OSError("ShellExecuteExW: failed to obtain PID")

        self._logger.info("started '%s' elevated, pid=%d", self.process_name, pid)
        return pid

    async def health_check(self, timeout: int = 30) -> bool:
        """프로세스 기동 확인. timeout초 내에 PID 발견 시 True."""
        import asyncio

        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            pid = self.find_pid()
            if pid is not None:
                return True
            await asyncio.sleep(1)
        return False

    def backup_current(self) -> str:
        """현재 실행 파일 백업. 백업 경로 반환."""
        if not self.process_path.exists():
            raise FileNotFoundError(f"Process file not found: {self.process_path}")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = (
            self.backup_dir
            / f"{self.process_path.stem}_{timestamp}{self.process_path.suffix}"
        )
        _ = shutil.copy2(str(self.process_path), str(backup_path))
        return str(backup_path)

    def rollback(self) -> bool:
        """최신 백업으로 복원. 성공 시 True."""
        backups = sorted(
            self.backup_dir.glob(
                f"{self.process_path.stem}_*{self.process_path.suffix}"
            ),
            reverse=True,
        )
        if not backups:
            return False

        latest = backups[0]
        _ = shutil.copy2(str(latest), str(self.process_path))
        return True

    def get_backup_count(self) -> int:
        """백업 파일 수 반환."""
        return len(
            list(
                self.backup_dir.glob(
                    f"{self.process_path.stem}_*{self.process_path.suffix}"
                )
            )
        )

    def cleanup_old_backups(self, max_backups: int = 5) -> int:
        """오래된 백업 삭제. 삭제 개수 반환."""
        backups = sorted(
            self.backup_dir.glob(
                f"{self.process_path.stem}_*{self.process_path.suffix}"
            )
        )
        removed = 0
        while len(backups) - removed > max_backups:
            backups[removed].unlink()
            removed += 1
        return removed


def acquire_instance_lock(lock_dir: Path) -> bool:
    """PID 파일 기반 단일 인스턴스 잠금.

    이미 동일 에이전트가 실행 중이면 False를 반환한다.
    이전 프로세스가 비정상 종료되어 PID 파일만 남은 경우에는
    해당 PID가 실제로 살아있는지 확인하여 안전하게 처리한다.
    """
    pid_file = lock_dir / ".agent.pid"
    current_pid = os.getpid()
    logger = setup_logging("instance_lock")

    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text(encoding="utf-8").strip())
            if old_pid != current_pid:
                try:
                    proc = psutil.Process(old_pid)
                    if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                        logger.warning(
                            "another instance already running: pid=%d", old_pid
                        )
                        return False
                except psutil.NoSuchProcess:
                    pass  # 이전 프로세스 이미 종료됨
        except (ValueError, OSError):
            pass  # PID 파일 파싱 실패 → 무시하고 진행

    try:
        pid_file.write_text(str(current_pid), encoding="utf-8")
    except OSError as exc:
        logger.warning("failed to write PID file: %s", exc)

    return True


def release_instance_lock(lock_dir: Path) -> None:
    """PID 파일 잠금 해제."""
    pid_file = lock_dir / ".agent.pid"
    try:
        if pid_file.exists():
            pid_file.unlink()
    except OSError:
        pass
