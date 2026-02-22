from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
from pathlib import Path

from agent.core.config_view import ConfigView
from agent.core.tcp_client import TCPClient
from agent.recording.uploader import RecordingUploader
from agent.recording.watcher import RecordingWatcher
from shared.protocol import (
    CmdRecAckPayload,
    CmdRecPayload,
    PacketType,
    RecAckStatus,
    RecAction,
    RecAnalysisPayload,
    RecDataReqPayload,
    RecDataRespPayload,
    RecUploadAckPayload,
    RecUploadReqPayload,
    SttResultPayload,
)


class RecordingController:
    def __init__(
        self,
        tcp_client: TCPClient,
        recording_cfg: ConfigView,
        logger: logging.Logger,
    ) -> None:
        self._tcp_client: TCPClient = tcp_client
        self._cfg: ConfigView = recording_cfg
        self._logger: logging.Logger = logger
        self._rec_watcher: RecordingWatcher | None = None
        self._rec_watcher_task: asyncio.Task[None] | None = None
        self._uploader: RecordingUploader | None = RecordingUploader(
            agent_id=tcp_client.agent_id
        )

    async def handle_cmd_rec(self, payload_data: bytes) -> None:
        try:
            cmd = CmdRecPayload.unpack(payload_data)
        except ValueError:
            self._logger.warning("invalid CMD_REC payload")
            return

        if cmd.action == RecAction.START:
            await self._handle_cmd_rec_start(cmd)
            return

        if cmd.action == RecAction.NEXT:
            if self._rec_watcher is not None:
                self._rec_watcher.resume_next()
                self._logger.info("RecordingWatcher: server NEXT received")
            else:
                self._logger.warning("RecordingWatcher not running, ignoring NEXT")
            return

        if cmd.action == RecAction.STOP:
            await self._stop_watcher()
            self._logger.info("RecordingWatcher stopped")
            ack = CmdRecAckPayload(action=RecAction.STOP, status=RecAckStatus.SUCCESS)
            await self._tcp_client.send_packet(PacketType.CMD_REC_ACK, ack.pack())

    async def handle_rec_upload_req(self, payload_data: bytes) -> None:
        try:
            req = RecUploadReqPayload.unpack(payload_data)
        except (ValueError, KeyError):
            self._logger.warning("invalid REC_UPLOAD_REQ payload")
            return

        watch_dir = self._cfg.s("watch_dir", "")
        target: str | None = None
        for wav_file in Path(watch_dir).rglob("*.wav"):
            if wav_file.name == req.filename:
                target = str(wav_file)
                break

        if target is None:
            self._logger.warning("filename=%s not found in %s", req.filename, watch_dir)
            fail_ack = RecUploadAckPayload(
                filename=req.filename,
                status=-1,
                file_size=0,
            )
            with contextlib.suppress(ConnectionError, OSError):
                await self._tcp_client.send_packet(
                    PacketType.REC_UPLOAD_ACK,
                    fail_ack.pack(),
                )
            return

        if self._uploader is None:
            self._uploader = RecordingUploader(agent_id=self._tcp_client.agent_id)

        filename_out, status, file_size = await self._uploader.upload(
            filename=req.filename,
            filepath=target,
            upload_url=req.upload_url,
        )
        ack = RecUploadAckPayload(
            filename=filename_out,
            status=status,
            file_size=file_size,
        )
        with contextlib.suppress(ConnectionError, OSError):
            await self._tcp_client.send_packet(PacketType.REC_UPLOAD_ACK, ack.pack())

    async def handle_stt_result(self, payload_data: bytes) -> None:
        try:
            stt = SttResultPayload.unpack(payload_data)
        except (ValueError, KeyError):
            self._logger.warning("invalid STT_RESULT payload")
            return

        self._logger.info(
            "STT result received: filename=%s dur=%.1fs words=%d score=%.1f talk_agent=%.0f%% talk_cust=%.0f%% silence=%.0f%% first_resp=%.1fs phrase=%d forbidden=%d",
            stt.filename,
            stt.duration_sec,
            stt.word_count,
            stt.score_total,
            stt.agent_talk_ratio * 100,
            stt.customer_talk_ratio * 100,
            stt.silence_ratio * 100,
            stt.first_response_sec,
            stt.required_phrase_hit,
            stt.forbidden_word_hit,
        )

        db_cfg = dict(self._cfg.sub("db").raw())
        if not db_cfg:
            self._logger.warning("recording.db not configured, cannot save STT result")
            return

        try:
            from agent.db.connection import get_connection
            from agent.db.helpers import (
                upsert_audio_quality,
                upsert_call_quality,
                upsert_transcript,
            )

            conn = await asyncio.to_thread(get_connection, db_cfg)

            if stt.aq_status:
                _ = await asyncio.to_thread(
                    upsert_audio_quality,
                    conn,
                    stt.filename,
                    stt.agent_id,
                    stt.aq_status,
                    stt.aq_left_rms_db,
                    stt.aq_right_rms_db,
                    stt.aq_left_silence,
                    stt.aq_right_silence,
                    stt.aq_dropout_count,
                    stt.aq_duration_wav,
                    None,
                    stt.aq_is_stereo,
                )
                self._logger.info(
                    "Audio quality saved to DB: filename=%s status=%s",
                    stt.filename,
                    stt.aq_status,
                )

            transcript_id = await asyncio.to_thread(
                upsert_transcript,
                conn,
                stt.filename,
                stt.full_text,
                stt.agent_text,
                stt.customer_text,
                stt.segments_json,
                stt.duration_sec,
                stt.word_count,
            )
            _ = await asyncio.to_thread(
                upsert_call_quality,
                conn,
                stt.filename,
                transcript_id,
                stt.first_response_sec,
                stt.agent_talk_ratio,
                stt.customer_talk_ratio,
                stt.silence_ratio,
                stt.required_phrase_hit,
                stt.required_phrase_list,
                stt.forbidden_word_hit,
                stt.forbidden_word_list,
                stt.score_total,
                stt.score_response,
                stt.score_phrase,
                stt.score_silence,
            )
            self._logger.info(
                "STT result saved to DB: filename=%s transcript_id=%s",
                stt.filename,
                transcript_id,
            )
        except Exception:
            self._logger.exception(
                "Failed to save STT result to DB: filename=%s",
                stt.filename,
            )

    async def handle_rec_data_req(self, payload_data: bytes) -> None:
        try:
            req = RecDataReqPayload.unpack(payload_data)
        except (ValueError, KeyError):
            self._logger.warning("invalid REC_DATA_REQ payload")
            return

        db_cfg = dict(self._cfg.sub("db").raw())
        if not db_cfg:
            self._logger.warning("recording.db not configured, cannot query recordings")
            return

        try:
            from agent.db.connection import get_connection
            from agent.db.helpers import query_recording_detail, query_recordings_list

            def _db_query() -> list[dict[str, object]]:
                conn = get_connection(db_cfg)
                if req.query_type == "detail":
                    row = query_recording_detail(conn, req.filename)
                    return [row] if row else []
                return query_recordings_list(conn, req.date_str)

            records = await asyncio.to_thread(_db_query)
            serializable = self._to_serializable_records(records)
            resp = RecDataRespPayload(query_type=req.query_type, records=serializable)
            with contextlib.suppress(ConnectionError, OSError):
                await self._tcp_client.send_packet(
                    PacketType.REC_DATA_RESP, resp.pack()
                )
            self._logger.info(
                "REC_DATA_RESP sent: query_type=%s records=%d",
                req.query_type,
                len(serializable),
            )
        except Exception:
            self._logger.exception("Failed to handle REC_DATA_REQ")

    async def on_connection_lost(self) -> None:
        await self._stop_watcher()
        self._logger.info("connection lost: rec_watcher stopped")

    async def cleanup(self) -> None:
        await self._stop_watcher()

    async def _handle_cmd_rec_start(self, cmd: CmdRecPayload) -> None:
        if self._rec_watcher is not None:
            self._logger.info("RecordingWatcher already running, ignoring START")
            ack = CmdRecAckPayload(action=RecAction.START, status=RecAckStatus.SUCCESS)
            await self._tcp_client.send_packet(PacketType.CMD_REC_ACK, ack.pack())
            return

        watch_dir = self._cfg.s("watch_dir", "")
        if not watch_dir:
            self._logger.warning("recording.watch_dir not configured")
            ack = CmdRecAckPayload(action=RecAction.START, status=RecAckStatus.FAILED)
            await self._tcp_client.send_packet(PacketType.CMD_REC_ACK, ack.pack())
            return

        async def _on_new_recording(
            rec_no: int,
            filename: str,
            filepath: str,
            duration: float,
            in_out: int = 0,
        ) -> None:
            self._logger.info(
                "recording callback: rec_no=%d filename=%s dur=%.1fs file=%s in_out=%d",
                rec_no,
                filename,
                duration,
                filepath,
                in_out,
            )
            if not self._tcp_client.is_connected:
                self._logger.warning("not connected - skipping filename=%s", filename)
                return

            payload = RecAnalysisPayload(
                filename=filename,
                status="OK",
                left_rms_db=0.0,
                right_rms_db=0.0,
                left_silence_ratio=0.0,
                right_silence_ratio=0.0,
                dropout_count=0,
                duration_wav=duration,
                duration_smdr=0.0,
                is_stereo=False,
                in_out=in_out,
            )
            with contextlib.suppress(ConnectionError, OSError):
                await self._tcp_client.send_packet(
                    PacketType.REC_ANALYSIS_RESULT,
                    payload.pack(),
                )

        self._rec_watcher = RecordingWatcher(
            watch_dir=watch_dir,
            extensions=self._cfg.ls("extensions", [".wav"]),
            on_new_recording=_on_new_recording,
            date_filter=cmd.date,
            db_config=self._cfg.sub("db").raw(),
        )
        self._rec_watcher_task = asyncio.create_task(self._rec_watcher.start())
        self._logger.info(
            "RecordingWatcher started: watch_dir=%s date_filter=%s",
            watch_dir,
            cmd.date or "(all)",
        )

        ack = CmdRecAckPayload(action=RecAction.START, status=RecAckStatus.SUCCESS)
        await self._tcp_client.send_packet(PacketType.CMD_REC_ACK, ack.pack())

    async def _stop_watcher(self) -> None:
        if self._rec_watcher is not None:
            with contextlib.suppress(Exception):
                await self._rec_watcher.stop()
            self._rec_watcher = None

        if self._rec_watcher_task is not None:
            _ = self._rec_watcher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._rec_watcher_task
            self._rec_watcher_task = None

    def _to_serializable_records(
        self,
        records: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        serializable: list[dict[str, object]] = []
        for rec in records:
            row_dict: dict[str, object] = {}
            for key, value in rec.items():
                if isinstance(value, dt.datetime):
                    row_dict[key] = value.isoformat()
                elif isinstance(value, bytes):
                    row_dict[key] = value.decode("utf-8", errors="replace")
                else:
                    row_dict[key] = value
            serializable.append(row_dict)
        return serializable
