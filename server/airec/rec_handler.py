"""Server-side recording message handler.

Receives analysis results from agents via TCP,
stores them in-memory, and orchestrates WAV upload requests.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from shared.protocol import RecAnalysisPayload, RecUploadReqPayload, SttResultPayload

_logger = logging.getLogger(__name__)


@dataclass
class AnalysisRecord:
    """In-memory record of an agent's analysis result."""

    filename: str
    agent_id: str
    status: str
    left_rms_db: float
    right_rms_db: float
    left_silence_ratio: float
    right_silence_ratio: float
    dropout_count: int
    duration_wav: float
    duration_smdr: float
    is_stereo: bool
    received_at: datetime = field(default_factory=datetime.now)
    uploaded: bool = False


class RecHandler:
    """Handle recording-related TCP messages on the server side."""

    def __init__(self, upload_base_url: str = "http://localhost:8000") -> None:
        self._upload_base_url = upload_base_url.rstrip("/")
        self._records: dict[tuple[str, str], AnalysisRecord] = {}
        # Track filenames pending upload per agent
        self._pending_uploads: dict[str, set[str]] = {}

    @property
    def records(self) -> dict[tuple[str, str], AnalysisRecord]:
        """All stored analysis records keyed by (agent_id, filename)."""
        return self._records

    @property
    def pending_uploads(self) -> dict[str, set[str]]:
        """Pending upload requests per agent."""
        return self._pending_uploads

    def handle_analysis_result(
        self, agent_id: str, payload: RecAnalysisPayload
    ) -> RecUploadReqPayload | None:
        """Process an analysis result from an agent.

        Stores the result and determines if a WAV upload should be requested.

        Returns RecUploadReqPayload if upload needed, None otherwise.
        """
        record = AnalysisRecord(
            filename=payload.filename,
            agent_id=agent_id,
            status=payload.status,
            left_rms_db=payload.left_rms_db,
            right_rms_db=payload.right_rms_db,
            left_silence_ratio=payload.left_silence_ratio,
            right_silence_ratio=payload.right_silence_ratio,
            dropout_count=payload.dropout_count,
            duration_wav=payload.duration_wav,
            duration_smdr=payload.duration_smdr,
            is_stereo=payload.is_stereo,
        )
        key = (agent_id, payload.filename)
        self._records[key] = record

        _logger.info(
            "analysis result stored: agent_id=%s filename=%s status=%s",
            agent_id,
            payload.filename,
            payload.status,
        )

        # Request upload if recording is OK and stereo (suitable for STT)
        if self._should_request_upload(record):
            upload_url = f"{self._upload_base_url}/api/rec/upload"
            pending = self._pending_uploads.setdefault(agent_id, set())
            pending.add(payload.filename)
            _logger.info(
                "requesting upload: agent_id=%s filename=%s url=%s",
                agent_id,
                payload.filename,
                upload_url,
            )
            return RecUploadReqPayload(filename=payload.filename, upload_url=upload_url)
        return None

    def handle_upload_ack(
        self, agent_id: str, filename: str, status: int, file_size: int
    ) -> None:
        """Process upload acknowledgement from agent.

        Args:
            agent_id: Agent identifier.
            filename: Recording filename.
            status: 0=success, 1=file_not_found, 2=upload_failed.
            file_size: Size of uploaded file.
        """
        key = (agent_id, filename)
        record = self._records.get(key)

        # Remove from pending
        pending = self._pending_uploads.get(agent_id)
        if pending is not None:
            pending.discard(filename)

        if status == 0:
            if record is not None:
                record.uploaded = True
            _logger.info(
                "upload success: agent_id=%s filename=%s size=%d",
                agent_id,
                filename,
                file_size,
            )
        else:
            _logger.warning(
                "upload failed: agent_id=%s filename=%s status=%d",
                agent_id,
                filename,
                status,
            )

    def _should_request_upload(self, record: AnalysisRecord) -> bool:
        """Determine if a recording should be uploaded for STT.

        Upload if:
        - Status is OK (not EMPTY, MUTED, etc.)
        - Recording is stereo
        - Duration is at least 3 seconds
        - Not already uploaded
        """
        if record.uploaded:
            return False
        if record.status != "OK":
            return False
        if not record.is_stereo:
            return False
        if record.duration_wav < 3.0:
            return False
        return True

    def run_stt_pipeline(
        self, agent_id: str, filename: str, wav_path: str
    ) -> SttResultPayload | None:
        """Run STT pipeline on uploaded WAV and return result payload.

        Returns None if pipeline fails.
        """
        try:
            from server.airec.analyzer.pipeline import run_pipeline

            result = run_pipeline(filename, wav_path)
        except Exception:
            _logger.exception(
                "STT pipeline failed: agent_id=%s filename=%s", agent_id, filename
            )
            return None

        return SttResultPayload(
            filename=result.filename,
            agent_id=agent_id,
            full_text=result.full_text,
            agent_text=result.agent_text,
            customer_text=result.customer_text,
            segments_json=result.segments_json,
            duration_sec=result.duration_sec,
            word_count=result.word_count,
            score_total=result.score_total,
            score_response=result.score_response,
            score_phrase=result.score_phrase,
            score_silence=result.score_silence,
            first_response_sec=result.first_response_sec,
            agent_talk_ratio=result.agent_talk_ratio,
            customer_talk_ratio=result.customer_talk_ratio,
            silence_ratio=result.silence_ratio,
            required_phrase_hit=result.required_phrase_hit,
            required_phrase_list=result.required_phrase_list,
            forbidden_word_hit=result.forbidden_word_hit,
            forbidden_word_list=result.forbidden_word_list,
        )

    def get_agent_stats(self, agent_id: str) -> dict[str, int]:
        """Get analysis statistics for an agent."""
        total = 0
        ok = 0
        uploaded = 0
        for (aid, _), rec in self._records.items():
            if aid != agent_id:
                continue
            total += 1
            if rec.status == "OK":
                ok += 1
            if rec.uploaded:
                uploaded += 1
        return {"total": total, "ok": ok, "uploaded": uploaded}
