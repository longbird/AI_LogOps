"""macOS (Darwin) platform implementation."""

from __future__ import annotations

import os
import plistlib
import subprocess
import time
from pathlib import Path

import psutil

from agent.platform.base import PlatformHelper
from shared.utils import setup_logging

_logger = setup_logging("platform.darwin")

_LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"


class DarwinPlatform(PlatformHelper):
    """macOS-specific operations: launchd, osascript, lsof."""

    # -- Process management --

    def start_elevated(self, exe: Path, args: list[str] | None, cwd: str) -> int:
        """Start process with elevated privileges via osascript (AppleScript).

        This triggers the macOS authorization dialog (equivalent to UAC).
        """
        cmd_parts = [str(exe)]
        if args:
            cmd_parts.extend(args)
        shell_cmd = " ".join(f'\\"{p}\\"' for p in cmd_parts)

        apple_script = (
            f'do shell script "cd \\"{cwd}\\" && {shell_cmd} &"'
            f" with administrator privileges"
        )

        result = subprocess.run(
            ["osascript", "-e", apple_script],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise OSError(
                f"osascript elevation failed: {result.stderr.strip()}"
            )

        # Wait for process to appear
        process_name = exe.name
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
                    _logger.info("started '%s' elevated, pid=%d", process_name, pid)
                    return pid

        raise OSError(f"process '{process_name}' not found after elevation")

    def detached_process_flags(self) -> int:
        return 0  # Unix does not use creationflags

    def hide_console(self) -> None:
        pass  # No console window concept on macOS

    # -- Autostart / launchd --

    def _plist_path(self, task_name: str) -> Path:
        return _LAUNCH_AGENTS_DIR / f"com.ailogops.{task_name}.plist"

    def register_autostart(
        self,
        task_name: str,
        exe: Path,
        args: list[str] | None,
        cwd: str,
    ) -> bool:
        """Create a launchd plist in ~/Library/LaunchAgents."""
        _LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
        plist_path = self._plist_path(task_name)

        program_args = [str(exe)]
        if args:
            program_args.extend(args)

        plist_data = {
            "Label": f"com.ailogops.{task_name}",
            "ProgramArguments": program_args,
            "WorkingDirectory": cwd,
            "RunAtLoad": True,
            "KeepAlive": {
                "SuccessfulExit": False,  # Restart on crash
            },
            "StandardOutPath": str(Path(cwd) / "log" / "stdout.log"),
            "StandardErrorPath": str(Path(cwd) / "log" / "stderr.log"),
        }

        try:
            with open(plist_path, "wb") as f:
                plistlib.dump(plist_data, f)

            # Load the plist
            result = subprocess.run(
                ["launchctl", "load", str(plist_path)],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                _logger.info("launchd plist '%s' registered", task_name)
                return True

            _logger.warning(
                "launchctl load failed: %s", result.stderr.strip()
            )
            return False
        except Exception as exc:
            _logger.warning("failed to register launchd plist: %s", exc)
            return False

    def unregister_autostart(self, task_name: str) -> bool:
        plist_path = self._plist_path(task_name)
        if not plist_path.exists():
            return True

        try:
            subprocess.run(
                ["launchctl", "unload", str(plist_path)],
                capture_output=True,
            )
            plist_path.unlink(missing_ok=True)
            _logger.info("launchd plist '%s' removed", task_name)
            return True
        except Exception as exc:
            _logger.warning("failed to unregister launchd plist: %s", exc)
            return False

    def autostart_exists(self, task_name: str) -> bool:
        return self._plist_path(task_name).exists()

    def start_via_autostart(self, task_name: str, process_name: str) -> int:
        """Start via launchctl kickstart."""
        label = f"com.ailogops.{task_name}"
        uid = os.getuid()

        result = subprocess.run(
            ["launchctl", "kickstart", f"gui/{uid}/{label}"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise OSError(f"launchctl kickstart failed: {result.stderr.strip()}")

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
                        "started '%s' via launchd, pid=%d", process_name, pid
                    )
                    return pid

        raise OSError(f"process '{process_name}' not found after launchctl kickstart")

    # -- System monitoring --

    def get_gdi_count(self, pid: int) -> int:
        return -1  # GDI is a Windows-only concept

    def get_handle_count(self, proc: object) -> int:
        """Get open file descriptor count via psutil.num_fds()."""
        try:
            p = proc  # type: psutil.Process
            return int(p.num_fds())  # type: ignore[attr-defined]
        except (AttributeError, OSError, psutil.Error):
            return -1

    def get_total_handles(self) -> int:
        """Get system-wide FD count."""
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
        return -1  # GDI is a Windows-only concept

    # -- Service / daemon --

    @property
    def supports_service_mode(self) -> bool:
        return True

    @property
    def platform_name(self) -> str:
        return "darwin"
