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

    def start(self, args: list[str] | None = None) -> int:
        """프로세스 시작. PID 반환. args: 추가 실행 인수."""
        import subprocess

        cmd = [str(self.process_path)]
        if args:
            cmd.extend(args)

        proc = subprocess.Popen(
            cmd,
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
