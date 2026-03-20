"""Windows platform implementation."""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from pathlib import Path

import psutil

from agent.platform.base import PlatformHelper
from shared.utils import setup_logging

_logger = setup_logging("platform.windows")


class WindowsPlatform(PlatformHelper):
    """Windows-specific operations: pywin32, ctypes.windll, schtasks."""

    # -- Process management --

    def start_elevated(self, exe: Path, args: list[str] | None, cwd: str) -> int:
        """Start process with UAC elevation via ShellExecuteExW."""
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
        sei.lpFile = str(exe)
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

        _logger.info("started '%s' elevated, pid=%d", exe.name, pid)
        return pid

    def detached_process_flags(self) -> int:
        return subprocess.DETACHED_PROCESS

    def hide_console(self) -> None:
        from typing import cast

        hwnd = cast(int, ctypes.windll.kernel32.GetConsoleWindow())
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE

    # -- Autostart / scheduled task --

    def register_autostart(
        self,
        task_name: str,
        exe: Path,
        args: list[str] | None,
        cwd: str,
    ) -> bool:
        """Register a Windows scheduled task with highest run level."""
        import subprocess as sp

        arg_str = " ".join(args) if args else ""

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
            _logger.info("schtask '%s' registered", task_name)
            return True

        _logger.warning("schtask registration failed: %s", result.stderr.strip())
        return False

    def unregister_autostart(self, task_name: str) -> bool:
        import subprocess as sp

        r = sp.run(
            ["schtasks", "/Delete", "/TN", task_name, "/F"],
            capture_output=True,
        )
        return r.returncode == 0

    def autostart_exists(self, task_name: str) -> bool:
        import subprocess as sp

        r = sp.run(
            ["schtasks", "/Query", "/TN", task_name],
            capture_output=True,
        )
        return r.returncode == 0

    def start_via_autostart(self, task_name: str, process_name: str) -> int:
        """Run via schtasks (no UAC). Returns PID."""
        import subprocess as sp

        r = sp.run(
            ["schtasks", "/Run", "/TN", task_name],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            raise OSError(f"schtasks /Run failed: {r.stderr.strip()}")

        for _ in range(10):
            time.sleep(0.5)
            for proc in psutil.process_iter(["name", "pid"]):
                name = proc.info.get("name")
                pid = proc.info.get("pid")
                if (
                    isinstance(name, str)
                    and name.lower() == process_name.lower()
                    and isinstance(pid, int)
                ):
                    _logger.info(
                        "started '%s' via schtask, pid=%d", process_name, pid
                    )
                    return pid

        raise OSError(f"process '{process_name}' not found after schtasks /Run")

    # -- System monitoring --

    def get_gdi_count(self, pid: int) -> int:
        try:
            kernel32 = ctypes.windll.kernel32
            user32 = ctypes.windll.user32
            h_process = kernel32.OpenProcess(0x1000, False, pid)
            if not h_process:
                return -1
            try:
                return int(user32.GetGuiResources(h_process, 0))
            finally:
                kernel32.CloseHandle(h_process)
        except (OSError, AttributeError):
            return -1

    def get_handle_count(self, proc: object) -> int:
        try:
            p = proc  # type: psutil.Process
            return int(p.num_handles())  # type: ignore[attr-defined]
        except (AttributeError, OSError, psutil.Error):
            return -1

    def get_total_handles(self) -> int:
        total = 0
        has_value = False
        for proc in psutil.process_iter(["pid"]):
            try:
                value = self.get_handle_count(proc)
                if value >= 0:
                    total += value
                    has_value = True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except OSError:
                continue
        return total if has_value else -1

    def get_total_gdi(self) -> int:
        total = 0
        has_value = False
        for proc in psutil.process_iter(["pid"]):
            try:
                pid = proc.info.get("pid")
                if not isinstance(pid, int):
                    continue
                value = self.get_gdi_count(pid)
                if value >= 0:
                    total += value
                    has_value = True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except OSError:
                continue
        return total if has_value else -1

    # -- Service / daemon --

    @property
    def supports_service_mode(self) -> bool:
        return True

    @property
    def platform_name(self) -> str:
        return "windows"
