from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from logging import Logger
from pathlib import Path

from shared.utils import setup_logging

_SERVICE_TEMPLATE = r"""@echo off
setlocal enabledelayedexpansion

:: === AI-LogOps Agent Updater (Service Mode) ===

:: Step 1: 서비스 정지
net stop {service_name}

:: Step 1b: 서비스 종료 확인 (최대 30초 대기)
set WAIT_COUNT=0
:WAIT_SVC
sc query {service_name} | find "STOPPED" >nul
if not errorlevel 1 goto SVC_STOPPED
set /a WAIT_COUNT+=1
if !WAIT_COUNT! GEQ 30 (
    echo [WARN] Service still running after 30s, forcing kill...
    taskkill /IM {exe_name} /F >nul 2>&1
    timeout /t 3 >nul
    goto SVC_STOPPED
)
timeout /t 1 >nul
goto WAIT_SVC
:SVC_STOPPED

:: Step 2: 현재 폴더 백업
if exist "{backup_dir}" rmdir /S /Q "{backup_dir}"
mkdir "{backup_dir}"
xcopy "{install_dir}\*" "{backup_dir}\" /E /H /Y /Q >nul

:: Step 3: 새 파일 배치 (config.yaml 제외)
for /f "delims=" %%F in ('dir /b /a-d "{update_dir}"') do (
    if /I NOT "%%F"=="config.yaml" copy /Y "{update_dir}\%%F" "{install_dir}\%%F" >nul
)
for /d %%D in ("{update_dir}\*") do (
    xcopy "%%D" "{install_dir}\%%~nxD\" /E /H /Y /Q >nul
)

:: Step 4: 인스턴스 잠금 파일 삭제 (sys.exit 후 taskkill 경합 방지)
if exist "{install_dir}\.agent.pid" del /F /Q "{install_dir}\.agent.pid"

:: Step 5: 서비스 시작
net start {service_name}

:: Step 6: 시작 검증
timeout /t 15 >nul
sc query {service_name} | find "RUNNING" >nul
if errorlevel 1 (
    echo [ROLLBACK] Service failed to start. Restoring backup...
    xcopy "{backup_dir}\*" "{install_dir}\" /E /H /Y /Q >nul
    net start {service_name}
)

:: Step 7: 정리
if exist "{update_dir}" rmdir /S /Q "{update_dir}"

endlocal
"""

_DEBUG_TEMPLATE = r"""@echo off
setlocal enabledelayedexpansion

:: === AI-LogOps Agent Updater (Debug Mode) ===

:: Step 1: 프로세스 종료 (/F: 강제, /T 사용 금지 — bat 자신이 자식 프로세스라 함께 죽음)
taskkill /IM {exe_name} /F >nul 2>&1

:: Step 1b: 프로세스 종료 확인 (최대 30초 대기)
set WAIT_COUNT=0
:WAIT_KILL
tasklist /FI "IMAGENAME eq {exe_name}" 2>nul | find /I "{exe_name}" >nul
if errorlevel 1 goto KILL_DONE
set /a WAIT_COUNT+=1
if !WAIT_COUNT! GEQ 30 (
    echo [WARN] Process still alive after 30s, retrying force kill...
    taskkill /IM {exe_name} /F >nul 2>&1
    timeout /t 3 >nul
    goto KILL_DONE
)
timeout /t 1 >nul
goto WAIT_KILL
:KILL_DONE

:: Step 2: 현재 폴더 백업
if exist "{backup_dir}" rmdir /S /Q "{backup_dir}"
mkdir "{backup_dir}"
xcopy "{install_dir}\*" "{backup_dir}\" /E /H /Y /Q >nul

:: Step 3: 새 파일 배치 (config.yaml 제외)
for /f "delims=" %%F in ('dir /b /a-d "{update_dir}"') do (
    if /I NOT "%%F"=="config.yaml" copy /Y "{update_dir}\%%F" "{install_dir}\%%F" >nul
)
for /d %%D in ("{update_dir}\*") do (
    xcopy "%%D" "{install_dir}\%%~nxD\" /E /H /Y /Q >nul
)

:: Step 4: 인스턴스 잠금 파일 삭제 (sys.exit 후 taskkill 경합 방지)
if exist "{install_dir}\.agent.pid" del /F /Q "{install_dir}\.agent.pid"

:: Step 5: 프로세스 시작
start "" "{exe_path}"

:: Step 6: 시작 검증 (10초 대기)
timeout /t 10 >nul
tasklist /FI "IMAGENAME eq {exe_name}" | find "{exe_name}" >nul
if errorlevel 1 (
    echo [ROLLBACK] Process failed to start. Restoring backup...
    xcopy "{backup_dir}\*" "{install_dir}\" /E /H /Y /Q >nul
    start "" "{exe_path}"
)

:: Step 7: 정리
if exist "{update_dir}" rmdir /S /Q "{update_dir}"

endlocal
"""


class SelfUpdater:
    """Telegram 경유 zip 업데이트를 처리한다.

    흐름: zip 수신 → 압축 해제 → updater.bat 생성 → bat 실행 (정지→교체→시작)
    """

    def __init__(
        self,
        install_dir: Path | None = None,
        is_service_mode: bool = False,
    ) -> None:
        if install_dir is None:
            if getattr(sys, "frozen", False):
                install_dir = Path(sys.executable).resolve().parent
            else:
                install_dir = Path(__file__).resolve().parents[1]

        self.install_dir: Path = install_dir
        self.service_name: str = "AILogOps-Agent"
        self.is_service_mode: bool = is_service_mode

        self.temp_dir: Path = self.install_dir / "temp"
        self.parts_dir: Path = self.temp_dir / "parts"
        self.update_dir: Path = self.temp_dir / "update"
        self.backup_dir: Path = self.install_dir / "backups"
        self.updater_bat_path: Path = self.install_dir / "updater.bat"

        self._expected_parts: int = 0
        self._received_parts: dict[int, Path] = {}

        self._logger: Logger = setup_logging("self_update")

    # ── 단일 zip 수신 ──────────────────────────────────

    async def receive_zip(self, data: bytes) -> bool:
        """zip 데이터를 수신하여 temp/update/ 에 압축 해제한다.

        Returns True if extraction succeeded.
        """
        self._reset_parts()
        return await self._extract_zip_data(data)

    # ── 단일 exe/파일 수신 ─────────────────────────────

    async def receive_file(self, data: bytes, filename: str) -> bool:
        """exe 등 단일 파일을 temp/update/ 에 직접 저장한다.

        zip이 아닌 파일(exe 등)은 추출 없이 그대로 스테이징한다.
        Returns True if save succeeded.
        """
        self._reset_parts()

        self.temp_dir.mkdir(parents=True, exist_ok=True)
        if self.update_dir.exists():
            shutil.rmtree(self.update_dir, ignore_errors=True)
        self.update_dir.mkdir(parents=True, exist_ok=True)

        target_path = self.update_dir / filename
        try:
            _ = target_path.write_bytes(data)
            self._logger.info(
                "file saved to staging: %s (%d bytes)", filename, len(data)
            )
            return True
        except OSError as exc:
            self._logger.error("failed to save file: %s", exc)
            return False

    # ── 분할 파트 수신 ─────────────────────────────────

    def receive_part(self, part_num: int, total_parts: int, data: bytes) -> str:
        """분할 파트를 저장한다. 상태 메시지를 반환."""
        self.parts_dir.mkdir(parents=True, exist_ok=True)

        if self._expected_parts == 0:
            self._expected_parts = total_parts
            self._received_parts.clear()
        elif self._expected_parts != total_parts:
            # 새 업로드 시작 — 기존 파트 초기화
            self._reset_parts()
            self._expected_parts = total_parts

        part_path = self.parts_dir / f"part{part_num:02d}"
        _ = part_path.write_bytes(data)
        self._received_parts[part_num] = part_path
        self._logger.info(
            "part %d/%d saved: %d bytes", part_num, total_parts, len(data)
        )

        received = len(self._received_parts)
        return f"파트 {part_num}/{total_parts} 수신 완료 ({received}/{total_parts})"

    @property
    def all_parts_received(self) -> bool:
        if self._expected_parts == 0:
            return False
        return len(self._received_parts) == self._expected_parts

    async def merge_parts(self) -> bool:
        """수신된 파트를 병합하여 zip으로 조립 + 압축 해제."""
        if not self.all_parts_received:
            return False

        merged = bytearray()
        for i in sorted(self._received_parts.keys()):
            merged.extend(self._received_parts[i].read_bytes())

        self._logger.info(
            "merged %d parts: %d bytes total", len(self._received_parts), len(merged)
        )
        self._reset_parts()
        return await self._extract_zip_data(bytes(merged))

    def _reset_parts(self) -> None:
        self._expected_parts = 0
        self._received_parts.clear()
        if self.parts_dir.exists():
            shutil.rmtree(self.parts_dir, ignore_errors=True)

    # ── 공통: zip 압축 해제 ────────────────────────────

    async def _extract_zip_data(self, data: bytes) -> bool:
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        if self.update_dir.exists():
            shutil.rmtree(self.update_dir, ignore_errors=True)

        zip_path = self.temp_dir / "update.zip"
        _ = zip_path.write_bytes(data)
        self._logger.info("update zip saved: %d bytes", len(data))

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(self.update_dir)
            self._logger.info("update zip extracted to %s", self.update_dir)
        except (zipfile.BadZipFile, OSError) as exc:
            self._logger.error("failed to extract update zip: %s", exc)
            zip_path.unlink(missing_ok=True)
            return False

        zip_path.unlink(missing_ok=True)
        return True

    def generate_updater_bat(self) -> str:
        """updater.bat 생성. 실행 모드에 따라 service/debug 템플릿 선택."""
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        exe_path = (
            str(Path(sys.executable).resolve())
            if getattr(sys, "frozen", False)
            else str(self.install_dir / "AILogOps-Agent.exe")
        )
        exe_name = Path(exe_path).name

        if self.is_service_mode:
            content = _SERVICE_TEMPLATE.format(
                service_name=self.service_name,
                install_dir=str(self.install_dir),
                backup_dir=str(self.backup_dir),
                update_dir=str(self.update_dir),
                exe_name=exe_name,
            )
        else:
            content = _DEBUG_TEMPLATE.format(
                install_dir=str(self.install_dir),
                backup_dir=str(self.backup_dir),
                update_dir=str(self.update_dir),
                exe_path=exe_path,
                exe_name=exe_name,
            )

        _ = self.updater_bat_path.write_text(content, encoding="utf-8")
        self._logger.info(
            "updater.bat generated (mode=%s): %s",
            "service" if self.is_service_mode else "debug",
            self.updater_bat_path,
        )
        return str(self.updater_bat_path)

    def _merge_config_version(self) -> None:
        """update_dir의 config.yaml에서 version을 읽어 설치된 config.yaml에 반영한다.

        updater.bat는 config.yaml을 덮어쓰지 않으므로(사용자 설정 보호),
        version 필드만 선택적으로 갱신한다.
        """
        # PyInstaller 6.x는 config.yaml을 _internal/에 배치하므로 양쪽 확인
        new_config = self.update_dir / "config.yaml"
        if not new_config.exists():
            new_config = self.update_dir / "_internal" / "config.yaml"
        installed_config = self.install_dir / "config.yaml"

        if not new_config.exists() or not installed_config.exists():
            self._logger.warning(
                "config merge skipped: new=%s(%s) installed=%s(%s)",
                new_config,
                new_config.exists(),
                installed_config,
                installed_config.exists(),
            )
            return

        try:
            import yaml  # noqa: PLC0415

            with new_config.open("r", encoding="utf-8") as f:
                new_cfg: dict[str, object] = yaml.safe_load(f) or {}
            agent_section = new_cfg.get("agent")
            new_version: str = ""
            if isinstance(agent_section, dict):
                v = agent_section.get("version", "")
                if isinstance(v, str):
                    new_version = v
            if not new_version:
                return

            with installed_config.open("r", encoding="utf-8") as f:
                installed_text = f.read()

            # 정규식으로 version 필드만 교체 (YAML 구조 보존)
            import re  # noqa: PLC0415

            updated_text, count = re.subn(
                r'(^\s*version:\s*)"[^"]*"',
                rf'\g<1>"{new_version}"',
                installed_text,
                count=1,
                flags=re.MULTILINE,
            )
            if count > 0:
                _ = installed_config.write_text(updated_text, encoding="utf-8")
                self._logger.info("config.yaml version updated to %s", new_version)
            else:
                self._logger.warning("config.yaml version field not found")
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("failed to merge config version: %s", exc)

    def execute_update(self) -> None:
        """bat 실행 후 프로세스 종료."""
        self._merge_config_version()
        _ = self.generate_updater_bat()

        # 인스턴스 잠금 파일을 먼저 삭제 — taskkill이 프로세스를 강제 종료하면
        # finally 블록의 release_instance_lock()이 실행되지 않아
        # .agent.pid가 남아 새 exe가 "이미 실행 중"으로 판단하고 종료되는 문제 방지
        pid_file = self.install_dir / ".agent.pid"
        try:
            pid_file.unlink(missing_ok=True)
        except OSError:
            pass

        command = ["cmd", "/c", str(self.updater_bat_path)]
        creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        self._logger.info("launching updater.bat and exiting...")
        _ = subprocess.Popen(command, creationflags=creationflags)
        sys.exit(0)
