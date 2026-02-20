"""HTTPS multipart WAV file uploader.

Agent uploads WAV files to the command server when requested via TCP
(REC_UPLOAD_REQ). Uses httpx for async HTTP with streaming upload.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

_logger = logging.getLogger(__name__)


class RecordingUploader:
    """Upload WAV files to command server via HTTPS multipart POST."""

    def __init__(self, agent_id: str, timeout: float = 120.0) -> None:
        self._agent_id: str = agent_id
        self._timeout: float = timeout

    async def upload(
        self, rec_no: int, filepath: str, upload_url: str
    ) -> tuple[int, int, int]:
        """Upload a WAV file to the command server.

        Args:
            rec_no: Recording number identifier.
            filepath: Absolute path to the WAV file.
            upload_url: HTTPS URL for multipart upload.

        Returns:
            (rec_no, status, file_size)
            status: 0=success, 1=file_not_found, 2=upload_failed
        """
        path = Path(filepath)
        if not path.is_file():
            _logger.warning("rec_no=%s file not found: %s", rec_no, filepath)
            return rec_no, 1, 0

        file_size = path.stat().st_size
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                verify=False,  # Allow self-signed certs for internal servers
            ) as client:
                with path.open("rb") as f:
                    resp = await client.post(
                        upload_url,
                        files={"file": (path.name, f, "audio/wav")},
                        data={"rec_no": str(rec_no), "agent_id": self._agent_id},
                    )
                if resp.status_code == 200:
                    _logger.info("rec_no=%s uploaded (%d bytes)", rec_no, file_size)
                    return rec_no, 0, file_size
                _logger.error(
                    "rec_no=%s upload HTTP %d: %s",
                    rec_no,
                    resp.status_code,
                    resp.text[:200],
                )
                return rec_no, 2, file_size
        except Exception:
            _logger.exception("rec_no=%s upload failed", rec_no)
            return rec_no, 2, file_size
