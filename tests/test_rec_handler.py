"""Unit tests for server.airec.rec_handler.RecHandler."""

from __future__ import annotations

import pytest

from server.airec.rec_handler import RecHandler
from shared.protocol import RecAnalysisPayload, RecUploadReqPayload


def _make_payload(
    filename: str = "rec_001.wav",
    status: str = "OK",
    is_stereo: bool = True,
    duration_wav: float = 10.0,
    left_rms_db: float = -20.0,
    right_rms_db: float = -22.0,
    left_silence_ratio: float = 0.1,
    right_silence_ratio: float = 0.15,
    dropout_count: int = 0,
    duration_smdr: float = 10.0,
) -> RecAnalysisPayload:
    return RecAnalysisPayload(
        filename=filename,
        status=status,
        left_rms_db=left_rms_db,
        right_rms_db=right_rms_db,
        left_silence_ratio=left_silence_ratio,
        right_silence_ratio=right_silence_ratio,
        dropout_count=dropout_count,
        duration_wav=duration_wav,
        duration_smdr=duration_smdr,
        is_stereo=is_stereo,
    )


class TestHandleAnalysisResult:
    def test_handle_analysis_result_ok_stereo(self) -> None:
        """OK + stereo + duration >= 3s → returns RecUploadReqPayload."""
        handler = RecHandler(upload_base_url="http://server:8000")
        payload = _make_payload(
            filename="rec_042.wav", status="OK", is_stereo=True, duration_wav=10.0
        )

        result = handler.handle_analysis_result("agent1", payload)

        assert isinstance(result, RecUploadReqPayload)
        assert result.filename == "rec_042.wav"
        assert result.upload_url == "http://server:8000/api/rec/upload"

    def test_handle_analysis_result_empty(self) -> None:
        """EMPTY status → returns None (no upload)."""
        handler = RecHandler()
        payload = _make_payload(
            filename="rec_001.wav", status="EMPTY", is_stereo=True, duration_wav=10.0
        )

        result = handler.handle_analysis_result("agent1", payload)

        assert result is None

    def test_handle_analysis_result_mono(self) -> None:
        """OK + mono + duration >= 3s → returns upload request (mono is allowed)."""
        handler = RecHandler()
        payload = _make_payload(
            filename="rec_001.wav", status="OK", is_stereo=False, duration_wav=10.0
        )

        result = handler.handle_analysis_result("agent1", payload)

        assert result is not None
        assert result.filename == "rec_001.wav"

    def test_handle_analysis_result_short(self) -> None:
        """OK + duration < 3s → returns None."""
        handler = RecHandler()
        payload = _make_payload(
            filename="rec_001.wav", status="OK", is_stereo=True, duration_wav=2.9
        )

        result = handler.handle_analysis_result("agent1", payload)

        assert result is None

    def test_handle_analysis_result_stores_record(self) -> None:
        """Analysis result is stored in records dict."""
        handler = RecHandler()
        payload = _make_payload(
            filename="rec_007.wav", status="OK", is_stereo=True, duration_wav=5.0
        )

        handler.handle_analysis_result("agentX", payload)

        assert ("agentX", "rec_007.wav") in handler.records
        record = handler.records[("agentX", "rec_007.wav")]
        assert record.status == "OK"
        assert record.filename == "rec_007.wav"
        assert record.agent_id == "agentX"

    def test_handle_analysis_result_adds_to_pending(self) -> None:
        """Upload-eligible result adds filename to pending_uploads."""
        handler = RecHandler()
        payload = _make_payload(
            filename="rec_005.wav", status="OK", is_stereo=True, duration_wav=10.0
        )

        handler.handle_analysis_result("agent1", payload)

        assert "agent1" in handler.pending_uploads
        assert "rec_005.wav" in handler.pending_uploads["agent1"]


class TestHandleUploadAck:
    def test_handle_upload_ack_success(self) -> None:
        """status=0 → record.uploaded = True."""
        handler = RecHandler()
        payload = _make_payload(
            filename="rec_001.wav", status="OK", is_stereo=True, duration_wav=10.0
        )
        handler.handle_analysis_result("agent1", payload)

        handler.handle_upload_ack(
            agent_id="agent1", filename="rec_001.wav", status=0, file_size=1024
        )

        record = handler.records[("agent1", "rec_001.wav")]
        assert record.uploaded is True

    def test_handle_upload_ack_failure(self) -> None:
        """status=2 → record.uploaded = False."""
        handler = RecHandler()
        payload = _make_payload(
            filename="rec_001.wav", status="OK", is_stereo=True, duration_wav=10.0
        )
        handler.handle_analysis_result("agent1", payload)

        handler.handle_upload_ack(
            agent_id="agent1", filename="rec_001.wav", status=2, file_size=0
        )

        record = handler.records[("agent1", "rec_001.wav")]
        assert record.uploaded is False

    def test_handle_upload_ack_removes_from_pending(self) -> None:
        """Upload ack (success or failure) removes filename from pending_uploads."""
        handler = RecHandler()
        payload = _make_payload(
            filename="rec_003.wav", status="OK", is_stereo=True, duration_wav=10.0
        )
        handler.handle_analysis_result("agent1", payload)
        assert "rec_003.wav" in handler.pending_uploads.get("agent1", set())

        handler.handle_upload_ack(
            agent_id="agent1", filename="rec_003.wav", status=0, file_size=512
        )

        assert "rec_003.wav" not in handler.pending_uploads.get("agent1", set())

    def test_handle_upload_ack_unknown_record(self) -> None:
        """Upload ack for unknown filename does not raise."""
        handler = RecHandler()
        # Should not raise even if record doesn't exist
        handler.handle_upload_ack(
            agent_id="agent1", filename="rec_999.wav", status=0, file_size=0
        )


class TestGetAgentStats:
    def test_get_agent_stats(self) -> None:
        """Verify stats computation across multiple records."""
        handler = RecHandler()

        # 3 records for agent1: 2 OK (1 uploaded), 1 EMPTY
        p1 = _make_payload(
            filename="rec_001.wav", status="OK", is_stereo=True, duration_wav=10.0
        )
        p2 = _make_payload(
            filename="rec_002.wav", status="OK", is_stereo=True, duration_wav=10.0
        )
        p3 = _make_payload(
            filename="rec_003.wav", status="EMPTY", is_stereo=True, duration_wav=10.0
        )

        handler.handle_analysis_result("agent1", p1)
        handler.handle_analysis_result("agent1", p2)
        handler.handle_analysis_result("agent1", p3)

        # Mark rec_001.wav as uploaded
        handler.handle_upload_ack(
            agent_id="agent1", filename="rec_001.wav", status=0, file_size=100
        )

        stats = handler.get_agent_stats("agent1")

        assert stats["total"] == 3
        assert stats["ok"] == 2
        assert stats["uploaded"] == 1

    def test_get_agent_stats_empty(self) -> None:
        """Stats for agent with no records returns zeros."""
        handler = RecHandler()
        stats = handler.get_agent_stats("unknown_agent")
        assert stats == {"total": 0, "ok": 0, "uploaded": 0}

    def test_get_agent_stats_isolates_agents(self) -> None:
        """Stats for one agent don't include records from another."""
        handler = RecHandler()
        p1 = _make_payload(
            filename="rec_001.wav", status="OK", is_stereo=True, duration_wav=10.0
        )
        p2 = _make_payload(
            filename="rec_001.wav", status="OK", is_stereo=True, duration_wav=10.0
        )

        handler.handle_analysis_result("agent1", p1)
        handler.handle_analysis_result("agent2", p2)

        stats1 = handler.get_agent_stats("agent1")
        stats2 = handler.get_agent_stats("agent2")

        assert stats1["total"] == 1
        assert stats2["total"] == 1


class TestDuplicateAnalysis:
    def test_duplicate_analysis_overwrites(self) -> None:
        """Same (agent_id, filename) overwrites previous record."""
        handler = RecHandler()

        p1 = _make_payload(
            filename="rec_001.wav", status="OK", is_stereo=True, duration_wav=10.0
        )
        p2 = _make_payload(
            filename="rec_001.wav", status="EMPTY", is_stereo=False, duration_wav=1.0
        )

        handler.handle_analysis_result("agent1", p1)
        handler.handle_analysis_result("agent1", p2)

        # Only one record should exist
        assert len(handler.records) == 1
        record = handler.records[("agent1", "rec_001.wav")]
        assert record.status == "EMPTY"
        assert record.is_stereo is False
        assert record.duration_wav == 1.0
