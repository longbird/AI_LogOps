from __future__ import annotations

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

    def start(self) -> int:
        """프로세스 시작. PID 반환."""
        import subprocess

        proc = subprocess.Popen(
            [str(self.process_path)],
            creationflags=subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0,
        )
        return proc.pid

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
