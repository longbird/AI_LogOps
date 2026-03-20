from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

import psutil

from shared.utils import setup_logging


@dataclass(slots=True)
class SystemMetrics:
    """전체 시스템 메트릭."""

    cpu_percent: float
    memory_percent: float
    handle_count: int
    gdi_count: int

    def format(self) -> str:
        return (
            "System "
            f"CPU={self.cpu_percent:.1f}% "
            f"Memory={self.memory_percent:.1f}% "
            f"Handles={self.handle_count} "
            f"GDI={self.gdi_count}"
        )


@dataclass(slots=True)
class ProcessMetrics:
    """대상 프로세스 메트릭."""

    name: str
    pid: int
    cpu_percent: float
    memory_mb: float
    handle_count: int
    gdi_count: int

    def format(self) -> str:
        return (
            f"Process {self.name} (PID={self.pid}) "
            f"CPU={self.cpu_percent:.1f}% "
            f"Memory={self.memory_mb:.1f}MB "
            f"Handles={self.handle_count} "
            f"GDI={self.gdi_count}"
        )


class SystemMonitor:
    def __init__(
        self,
        target_process_names: list[str] | None = None,
        *,
        target_process_name: str = "",
    ):
        # Multi-process: list of names
        if target_process_names:
            self._target_names: list[str] = [n.lower() for n in target_process_names if n]
        elif target_process_name:
            self._target_names = [target_process_name.lower()]
        else:
            self._target_names = []
        # Backward compat: first name as default
        self._target_process_name: str = self._target_names[0] if self._target_names else ""
        self._logger: Logger = setup_logging("system_monitor")

    @staticmethod
    def _get_platform():
        """Lazy-load platform helper."""
        from agent.platform import get_platform

        return get_platform()

    def collect_system(self) -> SystemMetrics:
        """Collect system-wide CPU/Memory/Handle/GDI metrics."""
        platform = self._get_platform()

        cpu_percent = float(psutil.cpu_percent(interval=None))
        memory_percent = float(psutil.virtual_memory().percent)

        handle_count = platform.get_total_handles()
        gdi_count = platform.get_total_gdi()

        return SystemMetrics(
            cpu_percent=cpu_percent,
            memory_percent=memory_percent,
            handle_count=handle_count,
            gdi_count=gdi_count,
        )

    def collect_target_process(self, target_name: str = "") -> ProcessMetrics | None:
        """Collect target process metrics. Returns None if process not found.

        Args:
            target_name: specific process name to collect. If empty, uses
                         the first (default) target process name.
        """
        platform = self._get_platform()
        lookup = (target_name.lower() if target_name else self._target_process_name)
        if not lookup:
            return None

        for proc in psutil.process_iter(["name", "pid"]):
            try:
                name = proc.info.get("name")
                pid = proc.info.get("pid")
                if not isinstance(name, str) or not isinstance(pid, int):
                    continue
                if name.lower() != lookup:
                    continue

                cpu_percent = float(proc.cpu_percent(interval=None))
                memory_mb = float(proc.memory_info().rss) / (1024 * 1024)
                handle_count = platform.get_handle_count(proc)
                gdi_count = platform.get_gdi_count(pid)

                return ProcessMetrics(
                    name=name,
                    pid=pid,
                    cpu_percent=cpu_percent,
                    memory_mb=memory_mb,
                    handle_count=handle_count,
                    gdi_count=gdi_count,
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except OSError:
                continue

        return None

    def collect_all_target_processes(self) -> dict[str, ProcessMetrics | None]:
        """Collect metrics for all target processes."""
        return {name: self.collect_target_process(name) for name in self._target_names}

    def format_status_report(self) -> str:
        """Format complete status report for Telegram response."""
        system_metrics = self.collect_system()
        lines = [system_metrics.format()]

        if not self._target_names:
            lines.append("Process: no target configured")
        else:
            for tname in self._target_names:
                metrics = self.collect_target_process(tname)
                if metrics is None:
                    lines.append(f"Process {tname}: not found")
                else:
                    lines.append(metrics.format())

        return "\n".join(lines)
