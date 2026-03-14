from __future__ import annotations

import asyncio
import os
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from logging import Logger
from pathlib import Path

from agent.core.process_mgr import ProcessManager
from shared.utils import setup_logging


@dataclass(slots=True)
class DeployResult:
    """프로세스 배포 결과."""

    success: bool = False
    pid: int = 0
    backup_path: str = ""
    error: str = ""
    replaced_files: list[str] = field(default_factory=list)


class ProcessDeployer:
    """대상 프로세스 Telegram 경유 업데이트 처리.

    SelfUpdater의 스테이징 디렉토리(temp/update/)를 공유하며,
    /deploy 명령 시 대상 프로세스 디렉토리로 파일을 배포한다.

    흐름: zip 수신(SelfUpdater staging) → 백업 → 프로세스 종료 → 파일 교체 → 시작 → 검증
    """

    def __init__(
        self,
        process_mgr: ProcessManager,
        update_dir: Path,
        log_folders: list[str] | None = None,
    ) -> None:
        self.process_mgr: ProcessManager = process_mgr
        self.update_dir: Path = update_dir  # SelfUpdater.update_dir 과 동일
        self.log_folders: list[str] = log_folders or []
        self._logger: Logger = setup_logging("process_deploy")

    @property
    def has_staged_files(self) -> bool:
        """스테이징된 파일이 존재하는지 확인."""
        return self.update_dir.exists() and any(self.update_dir.iterdir())

    def staged_file_summary(self) -> str:
        """스테이징된 파일 목록 요약."""
        if not self.has_staged_files:
            return "(없음)"
        items: list[str] = []
        for item in sorted(self.update_dir.iterdir()):
            if item.is_dir():
                count = sum(1 for _ in item.rglob("*") if _.is_file())
                items.append(f"  📁 {item.name}/ ({count} files)")
            else:
                size_kb = item.stat().st_size / 1024
                items.append(f"  📄 {item.name} ({size_kb:.1f}KB)")
        return "\n".join(items)

    async def execute_deploy(
        self,
        process_args: list[str] | None = None,
        deploy_path: str | None = None,
    ) -> DeployResult:
        """대상 프로세스에 스테이징된 파일을 배포한다.

        1. 현재 프로세스 백업
        2. 프로세스 종료
        3. 파일 교체 (스테이징 → 대상 디렉토리)
        4. 프로세스 시작
        5. 헬스체크 (30초)
        6. 실패 시 롤백
        """
        result = DeployResult()

        if not self.has_staged_files:
            result.error = "스테이징된 파일이 없습니다. zip/exe 파일을 먼저 전송하세요."
            return result

        if deploy_path:
            target_dir = Path(deploy_path)
        else:
            target_dir = self.process_mgr.process_path.parent
        self._logger.info(
            "deploying to %s (process: %s)",
            target_dir,
            self.process_mgr.process_name,
        )

        # 1. Backup
        try:
            backup_path = self.process_mgr.backup_current()
            result.backup_path = backup_path
            self._logger.info("backup created: %s", backup_path)
        except FileNotFoundError:
            self._logger.info("no existing file to backup (new deploy)")

        # 2. Kill all matching processes
        killed = self.process_mgr.kill_all()
        self._logger.info("process kill result: %s", killed)

        # 3. Clear log folders
        cleared = self._clear_log_folders()
        self._logger.info("cleared %d log folders", cleared)

        # 4. Copy staged files to target directory
        try:
            replaced = self._copy_staged_files(target_dir)
            result.replaced_files = replaced
            self._logger.info("replaced %d items", len(replaced))
        except OSError as exc:
            result.error = f"파일 교체 실패: {exc}"
            self._logger.error("file copy failed: %s", exc)
            # 롤백 시도
            _ = self._rollback_and_start(process_args)
            return result

        # 5. Start process
        try:
            pid = self.process_mgr.start(args=process_args)
            result.pid = pid
            self._logger.info("process started: pid=%d", pid)
        except OSError as exc:
            result.error = f"프로세스 시작 실패: {exc}"
            self._logger.error("process start failed: %s", exc)
            _ = self._rollback_and_start(process_args)
            return result

        # 6. Health check
        healthy = await self.process_mgr.health_check(timeout=30)
        if healthy:
            result.success = True
            self._cleanup_staging()
            self._logger.info("deploy verified: pid=%d", pid)
            return result

        # 7. Rollback
        self._logger.warning("health check failed, rolling back")
        rollback_ok = self._rollback_and_start(process_args)
        result.error = (
            "헬스체크 실패. 롤백 완료."
            if rollback_ok
            else "헬스체크 실패. 롤백도 실패."
        )
        return result

    def _detect_version(self) -> str | None:
        """Try to detect version from staged version.h file."""
        version_h = self.update_dir / "version.h"
        if version_h.exists():
            content = version_h.read_text(encoding="utf-8", errors="ignore")
            match = re.search(r'#define\s+VERSION_STRING\s+"([^"]+)"', content)
            if match:
                return match.group(1)
        return None

    def write_update_flag(self) -> None:
        """Write update-ready.flag for RecSvrManager to detect."""
        flag_path = self.update_dir / "update-ready.flag"
        version = self._detect_version()
        if version:
            flag_content = f"version={version}\n"
        else:
            flag_content = f"version=v{datetime.now().strftime('%Y%m%d_%H%M%S')}\n"
        flag_path.write_text(flag_content, encoding="utf-8")
        self._logger.info(
            "update flag written: %s (%s)", flag_path, flag_content.strip()
        )

    async def extract_staged_files(self, zip_path: Path) -> None:
        """Extract zip to staging directory."""
        if self.update_dir.exists():
            shutil.rmtree(self.update_dir)
        self.update_dir.mkdir(parents=True, exist_ok=True)

        def _extract() -> None:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(self.update_dir)

        await asyncio.to_thread(_extract)
        self._logger.info("extracted %s to %s", zip_path.name, self.update_dir)

    def _clear_log_folders(self) -> int:
        """프로세스와 공통 경로가 가장 많이 겹치는 로그 폴더만 삭제한다.

        로그 폴더가 여러 개일 때, 모니터링 프로세스 경로와 공통 경로 부분(parts)이
        가장 많이 매칭되는 폴더만 삭제 대상으로 선택한다.

        예) process = D:/AirSoft/Server2/bin/REC_SVR2.exe
            로그1: D:/AirSoft/Server2/Log/Record  → 공통: D:/AirSoft/Server2 (3 parts) ✅
            로그2: D:/AirSoft/Server1/Log/Record  → 공통: D:/AirSoft        (2 parts) ❌
        """
        process_dir = str(self.process_mgr.process_path.resolve().parent)
        cleared = 0

        # 1) 각 로그 폴더별 공통 경로 깊이 계산
        scored: list[tuple[int, Path]] = []
        for folder_str in self.log_folders:
            folder = Path(folder_str).resolve()
            if not folder.is_dir():
                continue
            try:
                common = Path(os.path.commonpath([process_dir, str(folder)]))
                depth = len(common.parts)
            except ValueError:
                # 서로 다른 드라이브
                self._logger.info("skip log folder (different drive): %s", folder)
                continue

            if depth < 2:
                # 드라이브 루트만 공유 → 무관한 폴더
                self._logger.info(
                    "skip log folder (only drive root shared): %s", folder
                )
                continue

            scored.append((depth, folder))
            self._logger.info("log folder match depth %d: %s", depth, folder)

        if not scored:
            return 0

        # 2) 최대 매칭 깊이 기준으로 필터
        max_depth = max(d for d, _ in scored)
        best_folders = [f for d, f in scored if d == max_depth]

        self._logger.info(
            "clearing %d log folder(s) with match depth %d",
            len(best_folders),
            max_depth,
        )

        # 3) 삭제
        for folder in best_folders:
            self._logger.info("clearing log folder: %s", folder)
            for item in folder.iterdir():
                try:
                    if item.is_file():
                        item.unlink()
                        cleared += 1
                    elif item.is_dir():
                        shutil.rmtree(item)
                        cleared += 1
                except OSError as exc:
                    self._logger.warning("failed to delete %s: %s", item, exc)
        return cleared

    def _copy_staged_files(self, target_dir: Path) -> list[str]:
        """스테이징 디렉토리의 파일을 대상 디렉토리로 복사."""
        target_dir.mkdir(parents=True, exist_ok=True)
        replaced: list[str] = []

        for item in self.update_dir.iterdir():
            dest = target_dir / item.name
            if item.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                _ = shutil.copytree(str(item), str(dest))
                replaced.append(f"{item.name}/")
            else:
                _ = shutil.copy2(str(item), str(dest))
                replaced.append(item.name)

        return replaced

    def _rollback_and_start(self, process_args: list[str] | None = None) -> bool:
        """롤백 후 프로세스 재시작 시도."""
        _ = self.process_mgr.kill_all()
        rolled_back = self.process_mgr.rollback()
        if rolled_back:
            try:
                _ = self.process_mgr.start(args=process_args)
                self._logger.info("rollback + restart succeeded")
            except OSError:
                self._logger.error("rollback succeeded but restart failed")
                return False
        else:
            self._logger.error("rollback failed: no backup available")
        return rolled_back

    def _cleanup_staging(self) -> None:
        """배포 성공 후 스테이징 디렉토리 정리."""
        if self.update_dir.exists():
            shutil.rmtree(self.update_dir, ignore_errors=True)
            self._logger.info("staging directory cleaned")
