from __future__ import annotations

import shutil
import subprocess
import sys
from logging import Logger
from pathlib import Path

from shared.utils import compute_sha256, setup_logging


class SelfUpdater:
    def __init__(self, backup_dir: str, current_version: str):
        self.backup_dir: Path = Path(backup_dir)
        self.current_version: str = current_version
        self.work_dir: Path = self.backup_dir.parent
        self.service_name: str = "AILogOps-Agent"

        self.temp_dir: Path = self.work_dir / "temp"
        self.new_binary_path: Path = self.temp_dir / "agent_new.exe"
        self.current_exe_path: Path = self.work_dir / "agent.exe"
        self.updater_bat_path: Path = self.work_dir / "updater.bat"
        self.version_file_path: Path = self.work_dir / ".version"
        self.template_path: Path
        if getattr(sys, "frozen", False):
            self.template_path = (
                Path(sys.executable).resolve().parent / "updater_template.bat"
            )
        else:
            self.template_path = Path(__file__).with_name("updater_template.bat")

        self._logger: Logger = setup_logging("self_update")

        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    async def receive_update(self, data: bytes, sha256: str) -> bool:
        """새 바이너리 수신 + SHA-256 검증. True=verified, False=failed."""
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        _ = self.new_binary_path.write_bytes(data)

        actual_sha = compute_sha256(str(self.new_binary_path))
        if actual_sha.lower() != sha256.lower():
            self._logger.error("update binary sha256 mismatch")
            self.new_binary_path.unlink(missing_ok=True)
            return False
        return True

    def generate_updater_bat(self) -> str:
        """updater.bat 생성. 롤백 로직 포함. Returns path to generated .bat."""
        template = self.template_path.read_text(encoding="utf-8")
        backup_path = self.backup_dir / f"agent_{self.current_version}.exe"
        content = template.format(
            service_name=self.service_name,
            current_exe=str(self.current_exe_path),
            backup_path=str(backup_path),
            new_exe=str(self.new_binary_path),
            version=self.current_version,
        )

        _ = self.updater_bat_path.write_text(content, encoding="utf-8")
        return str(self.updater_bat_path)

    def execute_update(self) -> None:
        """bat 실행 후 프로세스 종료. subprocess.Popen + sys.exit."""
        if not self.updater_bat_path.exists():
            _ = self.generate_updater_bat()

        command = ["cmd", "/c", str(self.updater_bat_path)]
        creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        _ = subprocess.Popen(command, creationflags=creationflags)
        sys.exit(0)

    def verify_version_on_boot(self) -> bool:
        """.version 파일과 actual version 비교. 불일치 시 rollback. True=ok, False=rolled back."""
        if not self.version_file_path.exists():
            self.write_version_file(self.current_version)
            return True

        recorded_version = self.version_file_path.read_text(encoding="utf-8").strip()
        if recorded_version == self.current_version:
            return True

        latest_backup = self._latest_backup()
        if latest_backup is None:
            self._logger.error("version mismatch but no backup file available")
            return False

        _ = shutil.copy2(str(latest_backup), str(self.current_exe_path))
        self.write_version_file(self.current_version)
        self._logger.warning(
            "version mismatch detected; rolled back using backup %s",
            latest_backup,
        )
        return False

    def write_version_file(self, version: str) -> None:
        """.version 파일 작성."""
        _ = self.version_file_path.write_text(f"{version}\n", encoding="utf-8")

    def _latest_backup(self) -> Path | None:
        backups = sorted(
            self.backup_dir.glob("agent_*.exe"),
            key=lambda path: path.name,
            reverse=True,
        )
        if not backups:
            return None
        return backups[0]
