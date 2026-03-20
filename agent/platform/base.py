"""Abstract base class for platform-specific operations."""

from __future__ import annotations

import abc
from pathlib import Path


class PlatformHelper(abc.ABC):
    """Interface for platform-dependent agent operations.

    Each platform (Windows, macOS) implements this interface
    to handle OS-specific tasks like privilege elevation,
    autostart registration, and system monitoring.
    """

    # -- Process management --

    @abc.abstractmethod
    def start_elevated(self, exe: Path, args: list[str] | None, cwd: str) -> int:
        """Start a process with elevated privileges. Returns PID.

        Windows: ShellExecuteEx runas (UAC prompt).
        macOS: osascript / Authorization Services.
        """

    @abc.abstractmethod
    def detached_process_flags(self) -> int:
        """Return subprocess.Popen creationflags for detached processes.

        Windows: subprocess.DETACHED_PROCESS
        macOS/Linux: 0 (not needed)
        """

    @abc.abstractmethod
    def hide_console(self) -> None:
        """Hide the console window. No-op on platforms without console concept."""

    # -- Autostart / scheduled task --

    @abc.abstractmethod
    def register_autostart(
        self,
        task_name: str,
        exe: Path,
        args: list[str] | None,
        cwd: str,
    ) -> bool:
        """Register the process to run at system startup with elevated privileges.

        Windows: schtasks / Task Scheduler.
        macOS: launchd plist in ~/Library/LaunchAgents.
        """

    @abc.abstractmethod
    def unregister_autostart(self, task_name: str) -> bool:
        """Remove the autostart registration."""

    @abc.abstractmethod
    def autostart_exists(self, task_name: str) -> bool:
        """Check whether autostart registration exists."""

    @abc.abstractmethod
    def start_via_autostart(self, task_name: str, process_name: str) -> int:
        """Start the process via the registered autostart mechanism. Returns PID.

        Windows: schtasks /Run.
        macOS: launchctl kickstart.
        """

    # -- System monitoring --

    @abc.abstractmethod
    def get_gdi_count(self, pid: int) -> int:
        """Get GDI object count for a process. Returns -1 if unsupported."""

    @abc.abstractmethod
    def get_handle_count(self, proc: object) -> int:
        """Get handle/FD count for a psutil.Process. Returns -1 if unsupported."""

    @abc.abstractmethod
    def get_total_handles(self) -> int:
        """Get system-wide handle count. Returns -1 if unsupported."""

    @abc.abstractmethod
    def get_total_gdi(self) -> int:
        """Get system-wide GDI count. Returns -1 if unsupported."""

    # -- Service / daemon --

    @property
    @abc.abstractmethod
    def supports_service_mode(self) -> bool:
        """Whether this platform supports background service/daemon mode."""

    @property
    @abc.abstractmethod
    def platform_name(self) -> str:
        """Short platform identifier: 'windows', 'darwin'."""
