from __future__ import annotations

# pyright: basic

import ctypes
import sys
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


def _get_gdi_count(pid: int) -> int:
    """Windows GDI object count via ctypes. Returns -1 on failure."""
    if sys.platform != "win32":
        return -1

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


class SystemMonitor:
    def __init__(self, target_process_name: str):
        self._target_process_name: str = target_process_name.lower()
        self._logger: Logger = setup_logging("system_monitor")

    def collect_system(self) -> SystemMetrics:
        """Collect system-wide CPU/Memory/Handle/GDI using psutil + ctypes."""

        cpu_percent = float(psutil.cpu_percent(interval=None))
        memory_percent = float(psutil.virtual_memory().percent)

        handle_count = self._collect_total_handles()
        gdi_count = self._collect_total_gdi()

        return SystemMetrics(
            cpu_percent=cpu_percent,
            memory_percent=memory_percent,
            handle_count=handle_count,
            gdi_count=gdi_count,
        )

    def collect_target_process(self) -> ProcessMetrics | None:
        """Collect target process metrics. Returns None if process not found."""

        for proc in psutil.process_iter(["name", "pid"]):
            try:
                name = proc.info.get("name")
                pid = proc.info.get("pid")
                if not isinstance(name, str) or not isinstance(pid, int):
                    continue
                if name.lower() != self._target_process_name:
                    continue

                cpu_percent = float(proc.cpu_percent(interval=None))
                memory_mb = float(proc.memory_info().rss) / (1024 * 1024)
                handle_count = self._safe_num_handles(proc)
                gdi_count = _get_gdi_count(pid)

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

    def format_status_report(self) -> str:
        """Format complete status report for Telegram response."""

        system_metrics = self.collect_system()
        process_metrics = self.collect_target_process()

        lines = [system_metrics.format()]
        if process_metrics is None:
            lines.append(f"Process {self._target_process_name}: not found")
        else:
            lines.append(process_metrics.format())

        return "\n".join(lines)

    def _collect_total_handles(self) -> int:
        if sys.platform != "win32":
            return -1

        total = 0
        has_value = False

        for proc in psutil.process_iter(["pid"]):
            try:
                value = self._safe_num_handles(proc)
                if value >= 0:
                    total += value
                    has_value = True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except OSError:
                continue

        return total if has_value else -1

    def _collect_total_gdi(self) -> int:
        if sys.platform != "win32":
            return -1

        total = 0
        has_value = False

        for proc in psutil.process_iter(["pid"]):
            try:
                pid = proc.info.get("pid")
                if not isinstance(pid, int):
                    continue

                value = _get_gdi_count(pid)
                if value >= 0:
                    total += value
                    has_value = True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except OSError:
                continue

        return total if has_value else -1

    @staticmethod
    def _safe_num_handles(proc: psutil.Process) -> int:
        if sys.platform != "win32":
            return -1

        try:
            return int(proc.num_handles())
        except (AttributeError, OSError, psutil.Error):
            return -1
