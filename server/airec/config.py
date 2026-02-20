"""AirREC server-side configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AirRecConfig:
    """Configuration for AirREC server components."""

    storage_dir: str = "server/storage/recordings"
    """Base directory for uploaded WAV files."""

    max_upload_size_mb: int = 100
    """Maximum upload file size in MB."""

    allowed_extensions: list[str] = field(default_factory=lambda: [".wav"])
    """Allowed file extensions for upload."""

    def ensure_storage_dir(self) -> Path:
        """Create storage directory if it doesn't exist."""
        path = Path(self.storage_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path
