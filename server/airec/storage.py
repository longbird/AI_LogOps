"""WAV file storage manager.

Stores uploaded WAV files organized by agent_id and date.
Directory structure: {storage_dir}/{agent_id}/{YYYYMMDD}/{filename}
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

_logger = logging.getLogger(__name__)


class RecordingStorage:
    """Manage WAV file storage on the command server."""

    def __init__(self, base_dir: str) -> None:
        self._base: Path = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)

    @property
    def base_dir(self) -> Path:
        return self._base

    def store(self, agent_id: str, data: bytes, filename: str) -> Path:
        """Store a WAV file and return the saved path.

        Files are saved to: {base_dir}/{agent_id}/{YYYYMMDD}/{filename}
        """
        today = date.today().strftime("%Y%m%d")
        target_dir = self._base / agent_id / today
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        _ = target.write_bytes(data)
        _logger.info(
            "stored filename=%s agent=%s path=%s size=%d",
            filename,
            agent_id,
            target,
            len(data),
        )
        return target

    def find_file(self, agent_id: str, filename: str) -> Path | None:
        """Find a stored WAV file by agent_id and filename.

        Searches date-based directories in reverse order (most recent first).
        """
        agent_dir = self._base / agent_id
        if not agent_dir.is_dir():
            return None
        for date_dir in sorted(agent_dir.iterdir(), reverse=True):
            if not date_dir.is_dir():
                continue
            candidate = date_dir / filename
            if candidate.is_file():
                return candidate
        return None

    def find_by_filename(self, filename: str) -> Path | None:
        """Find a WAV file by filename across all agents.

        Searches for exact filename match.
        """
        if not self._base.exists():
            return None
        for agent_dir in self._base.iterdir():
            if not agent_dir.is_dir():
                continue
            for date_dir in sorted(agent_dir.iterdir(), reverse=True):
                if not date_dir.is_dir():
                    continue
                for file_path in date_dir.iterdir():
                    if not file_path.is_file() or file_path.suffix.lower() != ".wav":
                        continue
                    if file_path.name == filename:
                        return file_path
        return None

    def list_recordings(
        self,
        agent_id: str | None = None,
        date_str: str | None = None,
    ) -> list[dict[str, object]]:
        """List stored recordings with metadata.

        Returns list of dicts with: agent_id, date, filename, file_size, path
        """
        if not self._base.exists():
            return []

        results: list[dict[str, object]] = []
        dirs = (
            [self._base / agent_id]
            if agent_id and (self._base / agent_id).is_dir()
            else [d for d in self._base.iterdir() if d.is_dir()]
        )

        for agent_dir in dirs:
            aid = agent_dir.name
            date_dirs = (
                [agent_dir / date_str]
                if date_str and (agent_dir / date_str).is_dir()
                else sorted(agent_dir.iterdir(), reverse=True)
            )
            for date_dir in date_dirs:
                if not date_dir.is_dir():
                    continue
                for file_path in sorted(date_dir.iterdir()):
                    if not file_path.is_file() or file_path.suffix.lower() != ".wav":
                        continue
                    results.append(
                        {
                            "agent_id": aid,
                            "date": date_dir.name,
                            "filename": file_path.name,
                            "file_size": file_path.stat().st_size,
                            "path": str(file_path),
                        }
                    )
        return results
