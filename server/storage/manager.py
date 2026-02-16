from __future__ import annotations

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
        경로: {base_dir}/logs/{agent_id}/{filename}"""

        target = self.base_dir / "logs" / agent_id / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = target.write_bytes(data)
        return str(target)

    def append_realtime_log(self, agent_id: str, filename: str, line: str) -> None:
        """실시간 로그 라인 append.
        경로: {base_dir}/logs/{agent_id}/realtime/{filename}"""

        target = self.base_dir / "logs" / agent_id / "realtime" / filename
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
