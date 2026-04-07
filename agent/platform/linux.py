"""Linux platform implementation using systemd."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import psutil

from agent.platform.base import PlatformHelper
from shared.utils import setup_logging

_logger = setup_logging("platform.linux")

_SYSTEMD_SYSTEM_DIR = Path("/etc/systemd/system")
_SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"


class LinuxPlatform(PlatformHelper):
    """Linux-specific operations: systemd, sudo, /proc."""

    # -- Process management --

    def start_elevated(self, exe: Path, args: list[str] | None, cwd: str) -> int:
        """Start process with elevated privileges via sudo."""
        cmd = ["sudo", str(exe)]
        if args:
            cmd.extend(args)

        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            start_new_session=True,
        )
        _logger.info("started '%s' elevated via sudo, pid=%d", exe.name, proc.pid)
        return proc.pid

    def detached_process_flags(self) -> int:
        return 0  # Unix does not use creationflags

    def hide_console(self) -> None:
        pass  # No console window concept on Linux

    # -- Autostart / systemd --

    def _unit_name(self, task_name: str) -> str:
        return f"ailogops-{task_name}.service"

    def _unit_path(self, task_name: str) -> Path:
        """Return systemd unit file path.

        Uses system-level (/etc/systemd/system) if running as root,
        otherwise user-level (~/.config/systemd/user).
        """
        if os.geteuid() == 0:
            return _SYSTEMD_SYSTEM_DIR / self._unit_name(task_name)
        return _SYSTEMD_USER_DIR / self._unit_name(task_name)

    def _systemctl(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Run systemctl with appropriate user/system flag."""
        cmd = ["systemctl"]
        if os.geteuid() != 0:
            cmd.append("--user")
        cmd.extend(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def register_autostart(
        self,
        task_name: str,
        exe: Path,
        args: list[str] | None,
        cwd: str,
    ) -> bool:
        """Create a systemd unit file and enable it."""
        unit_path = self._unit_path(task_name)
        unit_path.parent.mkdir(parents=True, exist_ok=True)

        exec_start_parts = [str(exe)]
        if args:
            exec_start_parts.extend(args)
        exec_start = " ".join(exec_start_parts)

        log_dir = Path(cwd) / "log"
        log_dir.mkdir(parents=True, exist_ok=True)

        unit_content = f"""\
[Unit]
Description=AILogOps Agent ({task_name})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={cwd}
ExecStart={exec_start}
Restart=on-failure
RestartSec=10
StandardOutput=append:{log_dir}/stdout.log
StandardError=append:{log_dir}/stderr.log

[Install]
WantedBy=multi-user.target
"""

        try:
            unit_path.write_text(unit_content, encoding="utf-8")

            result = self._systemctl("daemon-reload")
            if result.returncode != 0:
                _logger.warning("systemctl daemon-reload failed: %s", result.stderr.strip())

            result = self._systemctl("enable", self._unit_name(task_name))
            if result.returncode == 0:
                _logger.info("systemd unit '%s' registered and enabled", task_name)
                return True

            _logger.warning("systemctl enable failed: %s", result.stderr.strip())
            return False
        except Exception as exc:
            _logger.warning("failed to register systemd unit: %s", exc)
            return False

    def unregister_autostart(self, task_name: str) -> bool:
        """Disable and remove the systemd unit file."""
        unit_name = self._unit_name(task_name)
        unit_path = self._unit_path(task_name)

        try:
            self._systemctl("stop", unit_name)
            self._systemctl("disable", unit_name)
            unit_path.unlink(missing_ok=True)
            self._systemctl("daemon-reload")
            _logger.info("systemd unit '%s' removed", task_name)
            return True
        except Exception as exc:
            _logger.warning("failed to unregister systemd unit: %s", exc)
            return False

    def autostart_exists(self, task_name: str) -> bool:
        return self._unit_path(task_name).exists()

    def start_via_autostart(self, task_name: str, process_name: str) -> int:
        """Start via systemctl start."""
        unit_name = self._unit_name(task_name)

        result = self._systemctl("start", unit_name)
        if result.returncode != 0:
            raise OSError(f"systemctl start failed: {result.stderr.strip()}")

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
                        "started '%s' via systemd, pid=%d", process_name, pid
                    )
                    return pid

        raise OSError(f"process '{process_name}' not found after systemctl start")

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
        return "linux"
