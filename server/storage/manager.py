from __future__ import annotations

import hashlib
import time
from pathlib import Path


class StorageManager:
    """로그/리포트/백업 파일 관리. 스펙 섹션 3.1 참조."""

    def __init__(
        self,
        base_dir: str,
        max_retention_days: int = 30,
        max_backups: int = 5,
    ):
        """base_dir: 저장 루트 (e.g., "./storage")"""

        self.base_dir: Path = Path(base_dir)
        self.max_retention_days: int = max_retention_days
        self.max_backups: int = max_backups

    def save_log_history(self, agent_id: str, filename: str, data: bytes) -> str:
        """누적 로그 저장. 반환: 저장 경로.
        파일명에서 날짜(YYYYMMDD) 추출 → 일자별 폴더에 저장.
        경로: {base_dir}/logs/{agent_id}/{date_str}/{filename}
        날짜 추출 실패 시: {base_dir}/logs/{agent_id}/{filename} (레거시)"""

        import re

        date_match = re.match(r"^(\d{8})_", filename)
        if date_match:
            date_str = date_match.group(1)
            target = self.base_dir / "logs" / agent_id / date_str / filename
        else:
            target = self.base_dir / "logs" / agent_id / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = target.write_bytes(data)
        return str(target)

    def append_realtime_log(
        self, agent_id: str, filename: str, line: str, folder_index: int = 0
    ) -> None:
        """실시간 로그 라인 append.
        경로: {base_dir}/logs/{agent_id}/realtime/folder_{N}/{filename}"""

        target = (
            self.base_dir
            / "logs"
            / agent_id
            / "realtime"
            / f"folder_{folder_index}"
            / filename
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as stream:
            if line.endswith("\n"):
                _ = stream.write(line)
            else:
                _ = stream.write(f"{line}\n")

    def get_agent_logs(self, agent_id: str) -> list[str]:
        """에이전트의 모든 로그 파일 경로 반환."""

        root = self.base_dir / "logs" / agent_id
        if not root.exists():
            return []
        return sorted(str(path) for path in root.rglob("*") if path.is_file())

    def get_agent_log_content(self, agent_id: str, filename: str) -> str:
        """특정 로그 파일 내용 반환."""

        target = self.base_dir / "logs" / agent_id / filename
        return target.read_text(encoding="utf-8")

    def get_stored_file_metadata(
        self, agent_id: str, date_str: str = ""
    ) -> dict[str, tuple[int, bytes]]:
        """에이전트 로그 디렉토리의 파일별 (size, md5) 반환. realtime/ 제외.

        date_str 지정 시 해당 날짜 폴더 + flat 경로 모두 검색 (레거시 호환).
        Returns: {filename: (file_size, md5_digest)}
        """
        root = self.base_dir / "logs" / agent_id
        if not root.exists():
            return {}

        result: dict[str, tuple[int, bytes]] = {}

        # flat 경로 검색 (레거시)
        for path in root.iterdir():
            if not path.is_file():
                continue
            if date_str and not path.name.startswith(date_str + "_"):
                continue
            data = path.read_bytes()
            result[path.name] = (len(data), hashlib.md5(data).digest())

        # 일자별 폴더 검색
        if date_str:
            date_dir = root / date_str
            if date_dir.exists() and date_dir.is_dir():
                for path in date_dir.iterdir():
                    if path.is_file() and path.name not in result:
                        data = path.read_bytes()
                        result[path.name] = (len(data), hashlib.md5(data).digest())
        else:
            # date_str 미지정 시 모든 하위 날짜 폴더도 검색
            for sub in root.iterdir():
                if not sub.is_dir() or sub.name == "realtime":
                    continue
                for path in sub.iterdir():
                    if path.is_file() and path.name not in result:
                        data = path.read_bytes()
                        result[path.name] = (len(data), hashlib.md5(data).digest())

        return result

    def save_report(self, agent_id: str, report_name: str, content: str) -> str:
        """AI 리포트 저장. 경로: {base_dir}/reports/{agent_id}/{report_name}"""

        target = self.base_dir / "reports" / agent_id / report_name
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = target.write_text(content, encoding="utf-8")
        return str(target)

    def get_agent_reports(self, agent_id: str) -> list[str]:
        """에이전트의 모든 리포트 파일 목록."""

        root = self.base_dir / "reports" / agent_id
        if not root.exists():
            return []
        return sorted(str(path) for path in root.rglob("*") if path.is_file())

    def cleanup_old_logs(self) -> int:
        """max_retention_days 초과 로그 삭제. 반환: 삭제된 파일 수."""

        logs_root = self.base_dir / "logs"
        if not logs_root.exists():
            return 0

        cutoff_ts = time.time() - (self.max_retention_days * 24 * 60 * 60)
        removed_count = 0

        for path in logs_root.rglob("*"):
            if not path.is_file():
                continue
            if path.stat().st_mtime > cutoff_ts:
                continue
            path.unlink()
            removed_count += 1

        for path in sorted(logs_root.rglob("*"), reverse=True):
            if path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass

        return removed_count
