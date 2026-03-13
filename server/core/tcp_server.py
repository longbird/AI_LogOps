from __future__ import annotations

import asyncio
import logging
import socket
from pathlib import Path
from typing import TYPE_CHECKING, Any
from typing import Protocol, cast

from server.core.session_mgr import SessionManager
from shared.protocol import (
    HEADER_SIZE,
    AuthAckPayload,
    AuthPayload,
    AuthStatus,
    CmdLogPayload,
    CmdLogAckPayload,
    CmdRecPayload,
    CmdRecAckPayload,
    CtrlAction,
    LogAction,
    RecAction,
    LogFileListPayload,
    LogFileSelectPayload,
    Packet,
    PacketHeader,
    PacketType,
    CHUNK_SIZE,
    CmdConfigPayload,
    CmdCtrlAckPayload,
    CmdDeployPayload,
    CmdExecAckPayload,
    CmdExecPayload,
    ConfigAction,
    CtrlAckStatus,
    DeployTarget,
    FileAckPayload,
    FileChunkPayload,
    LogHistPayload,
    LogRealPayload,
    RecAnalysisPayload,
    RecDataReqPayload,
    RecDataRespPayload,
    RecUploadAckPayload,
    HeartbeatPayload,
    SttResultPayload,
)
from shared.utils import compute_sha256, setup_logging

if TYPE_CHECKING:
    from server.storage.manager import StorageManager


class _NotifyCallback(Protocol):
    """TCPServer 이벤트 알림 콜백 프로토콜."""

    async def __call__(self, message: str) -> None: ...


class TCPServer:
    def __init__(
        self,
        host: str,
        port: int,
        session_mgr: SessionManager,
        auth_token: str,
        storage_mgr: StorageManager | None = None,
        rec_handler: Any | None = None,  # server.airec.rec_handler.RecHandler
        rec_storage: Any | None = None,  # server.airec.storage.RecordingStorage
        notify_callback: _NotifyCallback | None = None,
    ):
        self.host: str = host
        self.port: int = port
        self.session_mgr: SessionManager = session_mgr
        self.auth_token: str = auth_token
        self.storage_mgr: StorageManager | None = storage_mgr
        self.rec_handler: Any | None = rec_handler
        self.rec_storage: Any | None = rec_storage
        self._notify_callback: _NotifyCallback | None = notify_callback
        self._logger: logging.Logger = setup_logging(self.__class__.__name__)
        self._server: asyncio.base_events.Server | None = None
        self._is_running: bool = False
        self._deploy_results: dict[str, asyncio.Future[CmdCtrlAckPayload]] = {}
        self._rec_data_futures: dict[str, asyncio.Future[RecDataRespPayload]] = {}
        self._config_futures: dict[str, asyncio.Future[dict]] = {}
        self._exec_futures: dict[str, asyncio.Future] = {}

        # ── 녹취 분석 flow control ──
        self._rec_in_flight: int = 0  # 현재 서버에서 처리 중인 녹취 건수
        self._rec_max_concurrent: int = 3  # 최대 동시 처리 건수
        self._rec_waiting_agents: list[str] = []  # NEXT 대기 중인 에이전트 목록

        # ── STT 엔진 설정 ──
        self._stt_engine: str = "local"  # local | openai-whisper | openai-gpt4o
        self._openai_prompt: str = ""  # OpenAI 모델 도메인 힌트

        # ── 실시간 로그 분석 상태 ──
        # agent_id -> {folder_index -> MonitorState} (실시간 수신 시 레이지 초기화)
        self._monitor_states: dict[str, dict[int, Any]] = {}

        # ── HIST 전송 진행률 추적 ──
        # agent_id -> {"total": int, "received": int, "total_bytes": int}
        self._hist_progress: dict[str, dict[str, int]] = {}

    @property
    def rec_max_concurrent(self) -> int:
        """최대 동시 녹취 분석 건수."""
        return self._rec_max_concurrent

    async def set_rec_max_concurrent(self, value: int) -> None:
        """최대 동시 녹취 분석 건수 변경. 슬롯 여유 시 즉시 대기 에이전트에 NEXT 전송."""
        self._rec_max_concurrent = max(1, min(value, 10))
        self._logger.info(
            "rec_max_concurrent changed to %d (in_flight=%d, waiting=%d)",
            self._rec_max_concurrent,
            self._rec_in_flight,
            len(self._rec_waiting_agents),
        )
        # 슬롯이 늘어났으면 대기 에이전트에 즉시 NEXT
        await self._try_dispatch_next()

    @property
    def stt_engine(self) -> str:
        """현재 STT 엔진."""
        return self._stt_engine

    async def set_stt_engine(self, engine: str) -> None:
        """STT 엔진 변경."""
        valid = ("local", "openai-whisper", "openai-gpt4o", "openai-diarize", "rtzr")
        if engine not in valid:
            self._logger.warning("invalid stt_engine: %s (valid: %s)", engine, valid)
            return
        self._stt_engine = engine
        self._logger.info("stt_engine changed to %s", engine)

    @property
    def openai_prompt(self) -> str:
        """OpenAI STT 모델 도메인 힌트."""
        return self._openai_prompt

    async def set_openai_prompt(self, prompt: str) -> None:
        """OpenAI STT 모델 도메인 힌트 변경."""
        self._openai_prompt = prompt
        self._logger.info("openai_prompt changed (len=%d)", len(prompt))

    async def _notify(self, message: str) -> None:
        """이벤트 알림 콜백 호출. 실패 시 무시."""
        if self._notify_callback is not None:
            try:
                await self._notify_callback(message)
            except Exception:
                self._logger.debug("notify callback failed: %s", message[:80])

    def _rec_log(self, agent_id: str, message: str) -> None:
        """녹취 분석 진행 로그를 세션의 rec_log_buffer에 추가."""
        import datetime

        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {message}"
        session = self.session_mgr.get_session(agent_id)
        if session is not None:
            session.rec_log_buffer.append(line)
            if len(session.rec_log_buffer) > 500:
                session.rec_log_buffer = session.rec_log_buffer[-300:]

    @property
    def is_running(self) -> bool:
        return self._is_running

    async def start(self) -> int:
        """서버 시작. asyncio.start_server 사용. 바인딩된 포트 반환."""

        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        sockets = self._server.sockets or []
        if not sockets:
            raise RuntimeError("server failed to bind socket")
        socket_addr = cast(tuple[str, int], sockets[0].getsockname())
        bound_port = socket_addr[1]
        self._is_running = True
        self._logger.info("tcp server started: host=%s port=%s", self.host, bound_port)
        return bound_port

    async def stop(self) -> None:
        if self._server is None:
            self._is_running = False
            return

        self._is_running = False
        for session in self.session_mgr.get_all_sessions():
            if session.writer is None:
                continue
            writer = cast(_WriterLike, session.writer)
            try:
                if not writer.is_closing():
                    writer.close()
                await writer.wait_closed()
            except Exception:
                self._logger.debug(
                    "failed to close writer during stop: agent_id=%s",
                    session.agent_info.agent_id,
                )
            self.session_mgr.remove_session(session.agent_info.agent_id)

        self._server.close()
        await self._server.wait_closed()
        self._server = None
        self._logger.info("tcp server stopped")

    async def _send_auth_ack(
        self,
        writer: _WriterLike,
        status: AuthStatus,
        session_id: str,
    ) -> None:
        writer.write(
            Packet.build(
                PacketType.AUTH_ACK,
                AuthAckPayload(status=status, session_id=session_id).pack(),
            )
        )
        await writer.drain()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: _WriterLike,
    ) -> None:
        """클라이언트 핸들러: AUTH → 메인 루프."""

        peer = writer.get_extra_info("peername")
        agent_id: str | None = None
        session_id: str | None = None
        self._logger.info("client connected: peer=%s", peer)

        # TCP keepalive 설정 — 유휴 연결 감지
        sock = cast(socket.socket | None, writer.get_extra_info("socket"))
        if sock is not None:
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                sock.ioctl(  # type: ignore[attr-defined]
                    socket.SIO_KEEPALIVE_VALS,  # type: ignore[attr-defined]
                    (1, 30_000, 10_000),
                )
            except (AttributeError, OSError):
                self._logger.debug("TCP keepalive setup skipped")

        try:
            agent_id = await self._handle_auth(reader, writer)
            if agent_id is None:
                return
            # 세션 교체 경쟁조건 방지: 현재 세션 ID 기록
            cur_session = self.session_mgr.get_session(agent_id)
            session_id = cur_session.session_id if cur_session else None
            await self._recv_loop(reader, writer, agent_id)
        except Exception:
            self._logger.exception("client handler error: peer=%s", peer)
        finally:
            if agent_id is not None:
                # 세션 교체 경쟁조건 방지: 현재 세션이 우리 세션일 때만 제거
                # (에이전트가 재접속하여 새 세션이 생성된 경우 제거하지 않음)
                cur = self.session_mgr.get_session(agent_id)
                if cur is not None and (
                    session_id is None or cur.session_id == session_id
                ):
                    self.session_mgr.remove_session(agent_id)
                    # ── Flow control cleanup: 끊어진 에이전트 정리 ──
                    if agent_id in self._rec_waiting_agents:
                        self._rec_waiting_agents.remove(agent_id)
                        self._logger.info(
                            "removed disconnected agent from waiting list: %s", agent_id
                        )
                    await self._notify(f"🔌 에이전트 연결 해제: {agent_id}")
                else:
                    self._logger.info(
                        "skipping session removal — already replaced: agent_id=%s "
                        "old_session=%s current_session=%s",
                        agent_id,
                        session_id,
                        cur.session_id if cur else "none",
                    )
            try:
                if not writer.is_closing():
                    writer.close()
                await writer.wait_closed()
            except Exception:
                self._logger.debug("writer close failed: peer=%s", peer)
            self._logger.info("client disconnected: peer=%s", peer)

    async def _handle_auth(
        self,
        reader: asyncio.StreamReader,
        writer: _WriterLike,
    ) -> str | None:
        """AUTH 패킷 처리. 성공 시 agent_id 반환, 실패 시 None."""

        try:
            header_bytes = await reader.readexactly(HEADER_SIZE)
            packet_type, payload_length = PacketHeader.unpack(header_bytes)
            payload = await reader.readexactly(payload_length)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            self._logger.info("connection lost before auth complete")
            return None
        except ValueError:
            self._logger.warning("invalid auth header")
            await self._send_auth_ack(writer, AuthStatus.FAILED, "")
            return None

        if packet_type != PacketType.AUTH:
            self._logger.warning("first packet is not auth: type=%s", packet_type)
            await self._send_auth_ack(writer, AuthStatus.FAILED, "")
            return None

        try:
            auth_payload = AuthPayload.unpack(payload)
        except ValueError:
            self._logger.warning("invalid auth payload")
            await self._send_auth_ack(writer, AuthStatus.FAILED, "")
            return None

        if auth_payload.token != self.auth_token:
            self._logger.warning(
                "auth failed: invalid token for agent_id=%s (received='%s')",
                auth_payload.agent_id,
                auth_payload.token,
            )
            await self._send_auth_ack(writer, AuthStatus.FAILED, "")
            return None

        session = self.session_mgr.create_session(
            agent_id=auth_payload.agent_id,
            version=auth_payload.version,
            writer=writer,
        )
        if session is None:
            self._logger.warning(
                "auth failed: session limit reached for agent_id=%s",
                auth_payload.agent_id,
            )
            await self._send_auth_ack(writer, AuthStatus.FAILED, "")
            return None

        await self._send_auth_ack(writer, AuthStatus.SUCCESS, session.session_id)
        self._logger.info(
            "auth success: agent_id=%s session_id=%s",
            auth_payload.agent_id,
            session.session_id,
        )
        await self._notify(
            f"✅ 에이전트 접속: {auth_payload.agent_id} (v{auth_payload.version})"
        )
        return auth_payload.agent_id

    async def _recv_loop(
        self,
        reader: asyncio.StreamReader,
        writer: _WriterLike,
        agent_id: str,
    ) -> None:
        """인증 후 패킷 수신 루프.

        read_timeout 동안 데이터가 없으면 연결 끊김으로 간주.
        에이전트 heartbeat 간격(30초)의 3배 = 90초.
        """
        read_timeout = 90  # heartbeat_interval(30) * 3

        while True:
            try:
                header_bytes = await asyncio.wait_for(
                    reader.readexactly(HEADER_SIZE),
                    timeout=read_timeout,
                )
                packet_type, payload_length = PacketHeader.unpack(header_bytes)
                payload = await reader.readexactly(payload_length)
            except asyncio.TimeoutError:
                self._logger.warning(
                    "no data for %ds — connection presumed dead: agent_id=%s",
                    read_timeout,
                    agent_id,
                )
                break
            except (asyncio.IncompleteReadError, ConnectionResetError):
                self._logger.info("connection lost in recv loop: agent_id=%s", agent_id)
                break
            except ValueError:
                self._logger.warning("invalid packet header: agent_id=%s", agent_id)
                break

            if packet_type == PacketType.HEARTBEAT:
                hb = HeartbeatPayload.unpack(payload)
                self.session_mgr.update_heartbeat(
                    agent_id, process_status=hb.process_status
                )
                writer.write(Packet.build(PacketType.HEARTBEAT, payload))
                await writer.drain()
                self._logger.debug("heartbeat echoed: agent_id=%s", agent_id)
                continue

            if packet_type == PacketType.DISCONNECT:
                self._logger.info("disconnect received: agent_id=%s", agent_id)
                break

            if packet_type == PacketType.LOG_HIST:
                self._handle_log_hist(agent_id, payload)
                continue

            if packet_type == PacketType.LOG_REAL:
                self._handle_log_real(agent_id, payload)
                continue

            if packet_type in {PacketType.FILE_ACK, PacketType.CMD_CTRL_ACK}:
                if packet_type == PacketType.FILE_ACK:
                    self._handle_file_ack(agent_id, payload)
                else:
                    self._handle_cmd_ctrl_ack(agent_id, payload)
                continue

            if packet_type == PacketType.LOG_FILE_LIST:
                await self._handle_log_file_list(agent_id, payload, writer)
                continue

            if packet_type == PacketType.CMD_LOG_ACK:
                self._handle_cmd_log_ack(agent_id, payload)
                continue

            if packet_type == PacketType.CMD_REC_ACK:
                self._handle_cmd_rec_ack(agent_id, payload)
                continue

            if packet_type == PacketType.REC_ANALYSIS_RESULT:
                await self._handle_rec_analysis(agent_id, payload, writer)
                continue

            if packet_type == PacketType.REC_UPLOAD_ACK:
                await self._handle_rec_upload_ack(agent_id, payload, writer)
                continue

            if packet_type == PacketType.REC_DATA_RESP:
                self._handle_rec_data_resp(agent_id, payload)
                continue

            if packet_type == PacketType.CMD_CONFIG_ACK:
                self._handle_config_ack(agent_id, payload)
                continue

            if packet_type == PacketType.CMD_EXEC_ACK:
                ack = CmdExecAckPayload.unpack(payload)
                self._logger.info("CMD_EXEC_ACK from %s: %s success=%s", agent_id, ack.command_name, ack.success)
                fut = self._exec_futures.pop(agent_id, None)
                if fut and not fut.done():
                    fut.set_result(ack)
                continue

            self._logger.warning(
                "unexpected packet type: agent_id=%s type=%s payload_len=%s",
                agent_id,
                int(packet_type),
                payload_length,
            )

    def _handle_log_hist(self, agent_id: str, payload: bytes) -> None:
        if self.storage_mgr is None:
            return

        try:
            message = LogHistPayload.unpack(payload)
            saved_path = self.storage_mgr.save_log_history(
                agent_id=agent_id,
                filename=message.filename,
                data=message.data,
            )
            self._logger.info(
                "log history stored: agent_id=%s filename=%s path=%s size=%s",
                agent_id,
                message.filename,
                saved_path,
                len(message.data),
            )
            # 진행 상황을 log_buffer에 추가
            session = self.session_mgr.get_session(agent_id)
            if session is not None:
                size_bytes = len(message.data)
                progress = self._hist_progress.get(agent_id)
                if progress is not None:
                    progress["received"] += 1
                    progress["total_bytes"] += size_bytes
                    rcv = progress["received"]
                    total = progress["total"]
                    total_kb = progress["total_bytes"] / 1024
                    pct = int(rcv * 100 / total) if total > 0 else 100
                    session.log_buffer.append(
                        f"[HIST] {rcv}/{total} ({pct}%) {message.filename} "
                        f"({size_bytes / 1024:.1f} KB) 누적: {total_kb:.0f} KB"
                    )
                    if rcv >= total:
                        session.log_buffer.append(
                            f"[HIST] 전송 완료: {total}개 파일, {total_kb:.0f} KB"
                        )
                        del self._hist_progress[agent_id]
                else:
                    session.log_buffer.append(
                        f"[HIST] {message.filename} ({size_bytes / 1024:.1f} KB)"
                    )
        except ValueError:
            self._logger.warning("invalid LOG_HIST payload: agent_id=%s", agent_id)

    def _handle_log_real(self, agent_id: str, payload: bytes) -> None:
        try:
            message = LogRealPayload.unpack(payload)
            # In-memory buffer for dashboard WebSocket streaming
            session = self.session_mgr.get_session(agent_id)
            if session is not None:
                buf_len = len(session.log_buffer)
                buffered_line = f"[F{message.folder_index}] {message.line}"
                session.log_buffer.append(buffered_line)
                if len(session.log_buffer) > 1000:
                    session.log_buffer = session.log_buffer[-500:]
                # 최초 수신 또는 100건마다 INFO 로그
                if buf_len == 0 or (buf_len + 1) % 100 == 0:
                    self._logger.info(
                        "LOG_REAL: agent=%s file=%s folder=%d buffer=%d",
                        agent_id,
                        message.filename,
                        message.folder_index,
                        buf_len + 1,
                    )

            # 실시간 분석 — 폴더별 MonitorState에 라인 피드
            folder_idx = message.folder_index
            if agent_id not in self._monitor_states:
                self._monitor_states[agent_id] = {}
            if folder_idx not in self._monitor_states[agent_id]:
                from server.analysis.monitor_state import MonitorState

                self._monitor_states[agent_id][folder_idx] = MonitorState()
            self._monitor_states[agent_id][folder_idx].process_line(
                message.line, filename=message.filename
            )
            # Persist to disk
            if self.storage_mgr is not None:
                self.storage_mgr.append_realtime_log(
                    agent_id=agent_id,
                    filename=message.filename,
                    line=message.line,
                    folder_index=message.folder_index,
                )
        except ValueError:
            self._logger.warning("invalid LOG_REAL payload: agent_id=%s", agent_id)

    def _handle_file_ack(self, agent_id: str, payload: bytes) -> None:
        try:
            ack = FileAckPayload.unpack(payload)
        except ValueError:
            self._logger.warning("invalid FILE_ACK payload: agent_id=%s", agent_id)
            return

        self._logger.info(
            "file ack received: agent_id=%s seq=%s status=%s",
            agent_id,
            ack.seq_num,
            ack.status,
        )

    def _handle_cmd_ctrl_ack(self, agent_id: str, payload: bytes) -> None:
        try:
            ack = CmdCtrlAckPayload.unpack(payload)
        except ValueError:
            self._logger.warning("invalid CMD_CTRL_ACK payload: agent_id=%s", agent_id)
            return

        self._logger.info(
            "cmd ctrl ack received: agent_id=%s action=%s pid=%s status=%s",
            agent_id,
            ack.action.name,
            ack.pid,
            ack.status.name,
        )

        future = self._deploy_results.get(agent_id)
        if future is not None and not future.done():
            future.set_result(ack)

        if ack.status == CtrlAckStatus.DEPLOY_VERIFIED:
            self._logger.info("deploy verified: agent_id=%s pid=%s", agent_id, ack.pid)
            asyncio.create_task(
                self._notify(f"✅ 배포 완료: {agent_id} (pid={ack.pid})")
            )
        elif ack.status == CtrlAckStatus.DEPLOY_ROLLBACK:
            self._logger.warning("deploy rolled back: agent_id=%s", agent_id)
            asyncio.create_task(self._notify(f"⚠️ 배포 롤백: {agent_id}"))

    def _handle_config_ack(self, agent_id: str, payload: bytes) -> None:
        """CMD_CONFIG_ACK 처리: 설정 조회/업데이트 응답."""
        try:
            ack = CmdConfigPayload.unpack(payload)
        except (ValueError, Exception):
            self._logger.warning("invalid CMD_CONFIG_ACK payload: agent_id=%s", agent_id)
            return

        import json
        try:
            data = json.loads(ack.config_data) if ack.config_data else {}
        except json.JSONDecodeError:
            data = {"error": "invalid JSON in config response"}

        # Store in session for polling
        session = self.session_mgr.get_session(agent_id)
        if session is not None:
            if ack.action == ConfigAction.GET:
                session.config_data = data
            elif ack.action == ConfigAction.UPDATE:
                session.config_update_result = data

        # Resolve pending future
        fut = self._config_futures.pop(agent_id, None)
        if fut is not None and not fut.done():
            fut.set_result(data)

        self._logger.info(
            "config ack: agent_id=%s action=%s",
            agent_id,
            ack.action.name,
        )

    async def send_config_command(
        self,
        agent_id: str,
        action: ConfigAction,
        config_data: str = "",
    ) -> bool:
        """CMD_CONFIG 패킷을 에이전트에 전송."""
        session = self.session_mgr.get_session(agent_id)
        if session is None or session.writer is None:
            return False
        writer = cast(_WriterLike, session.writer)
        cmd = CmdConfigPayload(action=action, config_data=config_data)
        writer.write(Packet.build(PacketType.CMD_CONFIG, cmd.pack()))
        await writer.drain()
        self._logger.info(
            "CMD_CONFIG sent: agent_id=%s action=%s",
            agent_id,
            action.name,
        )
        return True

    def get_config_future(self, agent_id: str) -> asyncio.Future[dict]:
        """설정 응답을 기다리기 위한 Future 생성."""
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[dict] = loop.create_future()
        self._config_futures[agent_id] = fut
        return fut

    async def send_exec_command(
        self,
        agent_id: str,
        command_name: str,
    ) -> bool:
        """CMD_EXEC 패킷을 에이전트에 전송."""
        session = self.session_mgr.get_session(agent_id)
        if session is None or session.writer is None:
            return False
        writer = cast(_WriterLike, session.writer)
        cmd = CmdExecPayload(command_name=command_name)
        writer.write(Packet.build(PacketType.CMD_EXEC, cmd.pack()))
        await writer.drain()
        self._logger.info(
            "CMD_EXEC sent: agent_id=%s command=%s",
            agent_id,
            command_name,
        )
        return True

    def get_exec_future(self, agent_id: str) -> asyncio.Future[CmdExecAckPayload]:
        """커맨드 실행 결과를 기다리기 위한 Future 생성."""
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[CmdExecAckPayload] = loop.create_future()
        self._exec_futures[agent_id] = fut
        return fut

    async def send_ctrl_command(
        self,
        agent_id: str,
        action: CtrlAction,
        target: int = 1,
    ) -> bool:
        """CMD_CTRL 패킷을 에이전트에 전송.

        target: DeployTarget 값 (0=AGENT, 1=PROCESS, 2=REC_CLIENT).
        """
        session = self.session_mgr.get_session(agent_id)
        if session is None or session.writer is None:
            return False
        writer = cast(_WriterLike, session.writer)
        from shared.protocol import CmdCtrlPayload

        cmd = CmdCtrlPayload(action=action, target=target)
        writer.write(Packet.build(PacketType.CMD_CTRL, cmd.pack()))
        await writer.drain()
        self._logger.info(
            "CMD_CTRL sent: agent_id=%s action=%s target=%d",
            agent_id,
            action.name,
            target,
        )
        return True

    async def send_log_command(
        self,
        agent_id: str,
        action: LogAction,
        date: str = "",
        folder_index: int = -1,
    ) -> bool:
        """CMD_LOG 패킷을 에이전트에 전송."""
        session = self.session_mgr.get_session(agent_id)
        if session is None or session.writer is None:
            return False
        writer = cast(_WriterLike, session.writer)
        cmd = CmdLogPayload(action=action, date=date, folder_index=folder_index)
        writer.write(Packet.build(PacketType.CMD_LOG, cmd.pack()))
        await writer.drain()
        return True

    def _handle_cmd_log_ack(self, agent_id: str, payload: bytes) -> None:
        try:
            ack = CmdLogAckPayload.unpack(payload)
        except ValueError:
            self._logger.warning("invalid CMD_LOG_ACK payload: agent_id=%s", agent_id)
            return
        self._logger.info(
            "cmd log ack: agent_id=%s action=%s status=%s file_count=%s",
            agent_id,
            ack.action.name,
            ack.status.name,
            ack.file_count,
        )

    async def send_rec_command(
        self, agent_id: str, action: RecAction, date: str = ""
    ) -> bool:
        """CMD_REC 패킷을 에이전트에 전송."""
        session = self.session_mgr.get_session(agent_id)
        if session is None:
            self._logger.warning(
                "send_rec_command: session not found for agent_id=%s "
                "(connected agents: %s)",
                agent_id,
                [s.agent_info.agent_id for s in self.session_mgr.get_all_sessions()],
            )
            return False
        if session.writer is None:
            self._logger.warning(
                "send_rec_command: writer is None for agent_id=%s", agent_id
            )
            return False
        writer = cast(_WriterLike, session.writer)
        cmd = CmdRecPayload(action=action, date=date)
        writer.write(Packet.build(PacketType.CMD_REC, cmd.pack()))
        await writer.drain()
        action_label = {
            RecAction.START: "분석 시작",
            RecAction.STOP: "분석 중지",
            RecAction.NEXT: "다음 분석",
        }.get(action, action.name)
        date_label = date or "(today)"
        self._rec_log(agent_id, f"{action_label} 명령 전송 (날짜={date_label})")
        return True

    async def _send_rec_next(self, agent_id: str) -> bool:
        """에이전트에 NEXT 명령 전송 (다음 1건 분석 허가)."""
        return await self.send_rec_command(agent_id, RecAction.NEXT)

    async def _try_dispatch_next(self) -> None:
        """대기 중인 에이전트에 NEXT 전송 (슬롯 여유 시)."""
        while (
            self._rec_waiting_agents and self._rec_in_flight < self._rec_max_concurrent
        ):
            agent_id = self._rec_waiting_agents.pop(0)
            # 에이전트가 아직 연결되어 있는지 확인
            if self.session_mgr.get_session(agent_id) is None:
                self._logger.debug("skip waiting agent (disconnected): %s", agent_id)
                continue
            ok = await self._send_rec_next(agent_id)
            if ok:
                self._rec_log(
                    agent_id,
                    f"NEXT 전송 (in_flight={self._rec_in_flight}/{self._rec_max_concurrent})",
                )
                self._logger.info(
                    "rec NEXT dispatched: agent_id=%s in_flight=%d/%d",
                    agent_id,
                    self._rec_in_flight,
                    self._rec_max_concurrent,
                )
                self._logger.info(
                    "rec NEXT dispatched: agent_id=%s in_flight=%d/%d",
                    agent_id,
                    self._rec_in_flight,
                    self._rec_max_concurrent,
                )

    def _handle_cmd_rec_ack(self, agent_id: str, payload: bytes) -> None:
        try:
            ack = CmdRecAckPayload.unpack(payload)
        except ValueError:
            self._logger.warning("invalid CMD_REC_ACK payload: agent_id=%s", agent_id)
            return
        self._rec_log(
            agent_id,
            f"에이전트 응답: {ack.action.name} → {ack.status.name}",
        )
        self._logger.info(
            "cmd rec ack: agent_id=%s action=%s status=%s",
            agent_id,
            ack.action.name,
            ack.status.name,
        )

    async def _handle_log_file_list(
        self, agent_id: str, payload: bytes, writer: _WriterLike
    ) -> None:
        """LOG_FILE_LIST 수신: 서버 저장소와 비교 후 LOG_FILE_SELECT 전송."""
        if self.storage_mgr is None:
            return

        try:
            file_list = LogFileListPayload.unpack(payload)
        except ValueError:
            self._logger.warning("invalid LOG_FILE_LIST payload: agent_id=%s", agent_id)
            return

        # 파일명에서 날짜 추출하여 날짜별 메타데이터 조회
        import re

        date_str = ""
        if file_list.entries:
            m = re.match(r"^(\d{8})_", file_list.entries[0].filename)
            if m:
                date_str = m.group(1)

        stored = self.storage_mgr.get_stored_file_metadata(agent_id, date_str)
        selected: list[str] = []

        for entry in file_list.entries:
            local = stored.get(entry.filename)
            if local is None:
                selected.append(entry.filename)
            else:
                local_size, local_md5 = local
                if local_size != entry.file_size or local_md5 != entry.md5:
                    selected.append(entry.filename)

        self._logger.info(
            "file list comparison: agent_id=%s total=%d selected=%d skipped=%d",
            agent_id,
            len(file_list.entries),
            len(selected),
            len(file_list.entries) - len(selected),
        )

        # HIST 진행률 초기화
        self._hist_progress[agent_id] = {
            "total": len(selected),
            "received": 0,
            "total_bytes": 0,
        }

        # 진행 메시지를 log_buffer에 추가
        session = self.session_mgr.get_session(agent_id)
        if session is not None:
            skipped = len(file_list.entries) - len(selected)
            session.log_buffer.append(
                f"[HIST] 전송 시작: {len(selected)}개 파일 "
                f"(이미 저장: {skipped}개, 전체: {len(file_list.entries)}개)"
            )

        select = LogFileSelectPayload(filenames=selected)
        writer.write(Packet.build(PacketType.LOG_FILE_SELECT, select.pack()))
        await writer.drain()

    async def send_deploy(
        self,
        agent_id: str,
        file_path: str,
        deploy_target: str = "agent",
        original_filename: str = "",
        deploy_path: str = "",
    ) -> bool:
        """에이전트에 파일 배포. CMD_DEPLOY + FILE_CHUNKs 전송.

        대용량 파일(zip 등)을 위해 64KB 청크와 배치 drain을 사용한다.
        original_filename이 지정되면 에이전트에 해당 이름으로 전송한다.
        """
        session = self.session_mgr.get_session(agent_id)
        if session is None or session.writer is None:
            return False
        writer = cast(_WriterLike, session.writer)

        path = Path(file_path)
        if not path.exists():
            return False

        data = path.read_bytes()
        sha256_hash = compute_sha256(file_path)

        loop = asyncio.get_running_loop()
        self._deploy_results[agent_id] = loop.create_future()

        target = (
            DeployTarget.PROCESS if deploy_target == "process" else DeployTarget.AGENT
        )

        cmd = CmdDeployPayload(
            file_size=len(data),
            sha256=sha256_hash,
            filename=original_filename or path.name,
            deploy_target=target,
            deploy_path=deploy_path,
        )
        writer.write(Packet.build(PacketType.CMD_DEPLOY, cmd.pack()))
        await writer.drain()

        deploy_chunk_size = 65535  # max protocol chunk size (2-byte H field)
        drain_interval = 8  # 8청크마다 flush (≈512KB 단위)
        total_chunks = (len(data) + deploy_chunk_size - 1) // deploy_chunk_size
        seq = 0
        for offset in range(0, len(data), deploy_chunk_size):
            chunk_data = data[offset : offset + deploy_chunk_size]
            chunk = FileChunkPayload(seq_num=seq, data=chunk_data)
            writer.write(Packet.build(PacketType.FILE_CHUNK, chunk.pack()))
            seq += 1
            if seq % drain_interval == 0:
                await writer.drain()
                # 진행률 로깅 (25% 단위)
                pct = seq * 100 // total_chunks
                if (
                    pct in (25, 50, 75)
                    and (seq - drain_interval) * 100 // total_chunks < pct
                ):
                    self._logger.info(
                        "deploy progress: agent_id=%s %d%% (%d/%d chunks)",
                        agent_id,
                        pct,
                        seq,
                        total_chunks,
                    )

        # 잔여 데이터 flush
        await writer.drain()
        self._logger.info(
            "deploy transfer complete: agent_id=%s file=%s chunks=%d size=%.1f MB",
            agent_id,
            path.name,
            seq,
            len(data) / (1024 * 1024),
        )
        return True

    def get_deploy_result_future(
        self, agent_id: str
    ) -> asyncio.Future[CmdCtrlAckPayload] | None:
        return self._deploy_results.get(agent_id)

    async def _handle_rec_analysis(
        self, agent_id: str, payload: bytes, writer: _WriterLike
    ) -> None:
        """Handle REC_ANALYSIS_RESULT: store result, optionally request upload."""
        if self.rec_handler is None:
            self._logger.debug("rec_handler not configured, ignoring analysis result")
            # flow control: rec_handler 없어도 에이전트 대기 해제
            await self._send_rec_next(agent_id)
            return

        try:
            analysis = RecAnalysisPayload.unpack(payload)
        except (ValueError, KeyError):
            self._logger.warning(
                "invalid REC_ANALYSIS_RESULT payload: agent_id=%s", agent_id
            )
            # flow control: 파싱 실패해도 에이전트 대기 해제
            await self._send_rec_next(agent_id)
            return

        # Import handle method dynamically to avoid coupling
        self._rec_log(
            agent_id,
            f"분석결과 수신: {analysis.filename} "
            f"상태={analysis.status} "
            f"L={analysis.left_rms_db:.1f}dB R={analysis.right_rms_db:.1f}dB "
            f"시간={analysis.duration_wav:.1f}초",
        )
        upload_req = self.rec_handler.handle_analysis_result(agent_id, analysis)  # type: ignore[union-attr]
        needs_upload = upload_req is not None
        if needs_upload:
            writer.write(Packet.build(PacketType.REC_UPLOAD_REQ, upload_req.pack()))
            await writer.drain()
            self._rec_log(agent_id, f"업로드 요청: {analysis.filename}")
            self._logger.info(
                "sent REC_UPLOAD_REQ: agent_id=%s filename=%s",
                agent_id,
                analysis.filename,
            )
            # 업로드 진행 중 → in_flight 유지, UPLOAD_ACK에서 감소
            self._rec_in_flight += 1
            self._rec_log(
                agent_id,
                f"파이프라인 진행 중 (in_flight={self._rec_in_flight}/{self._rec_max_concurrent})",
            )
        else:
            self._rec_log(
                agent_id,
                f"업로드 스킵 (상태={analysis.status}): {analysis.filename}",
            )

        # ── Flow control: 에이전트에 다음 분석 허가 ──
        if not needs_upload:
            # 업로드 불필요 → 즉시 슬롯 사용 없음, 바로 NEXT 가능
            if self._rec_in_flight < self._rec_max_concurrent:
                await self._send_rec_next(agent_id)
                self._rec_log(
                    agent_id,
                    f"NEXT 즉시 전송 (in_flight={self._rec_in_flight}/{self._rec_max_concurrent})",
                )
            else:
                self._rec_waiting_agents.append(agent_id)
                self._rec_log(
                    agent_id,
                    f"NEXT 대기 (in_flight={self._rec_in_flight}/{self._rec_max_concurrent})",
                )
        else:
            # 업로드 필요 → UPLOAD_ACK 수신 후 NEXT 전송
            self._rec_waiting_agents.append(agent_id)

    async def _handle_rec_upload_ack(
        self, agent_id: str, payload: bytes, writer: _WriterLike
    ) -> None:
        """Handle REC_UPLOAD_ACK from agent. Trigger STT if upload succeeded."""
        if self.rec_handler is None:
            # flow control: rec_handler 없어도 슬롯 해제 + 대기 에이전트 처리
            if self._rec_in_flight > 0:
                self._rec_in_flight -= 1
            await self._try_dispatch_next()
            return

        try:
            ack = RecUploadAckPayload.unpack(payload)
        except ValueError:
            self._logger.warning(
                "invalid REC_UPLOAD_ACK payload: agent_id=%s", agent_id
            )
            # flow control: 파싱 실패해도 슬롯 해제 + 대기 에이전트 처리
            if self._rec_in_flight > 0:
                self._rec_in_flight -= 1
            await self._try_dispatch_next()
            return

        self.rec_handler.handle_upload_ack(  # type: ignore[union-attr]
            agent_id=agent_id,
            filename=ack.filename,
            status=ack.status,
            file_size=ack.file_size,
        )
        _fsize = ack.file_size
        _fsize_str = (
            f"{_fsize / 1024:.1f}KB"
            if _fsize < 1024 * 1024
            else f"{_fsize / 1024 / 1024:.1f}MB"
        )
        self._rec_log(
            agent_id,
            f"업로드 완료: {ack.filename} ({_fsize_str})",
        )
        self._logger.info(
            "rec upload ack: agent_id=%s filename=%s status=%s size=%s",
            agent_id,
            ack.filename,
            ack.status,
            ack.file_size,
        )

        # Trigger STT pipeline on successful upload (비동기 태스크로 분리)
        # ※ _recv_loop을 블록하지 않기 위해 create_task 사용
        #    — STT 실행 중에도 heartbeat 에코가 가능해짐
        if ack.status == 0 and self.rec_storage is not None:
            wav_path = self.rec_storage.find_by_filename(ack.filename)  # type: ignore[union-attr]
            if wav_path is not None:
                # AnalysisRecord에서 in_out 값 조회
                _rec_key = (agent_id, ack.filename)
                _rec_record = self.rec_handler.records.get(_rec_key)  # type: ignore[union-attr]
                _in_out = _rec_record.in_out if _rec_record is not None else 0
                self._rec_log(
                    agent_id, f"STT 분석 시작: {ack.filename} (in_out={_in_out})"
                )
                self._logger.info(
                    "triggering STT pipeline: agent_id=%s filename=%s path=%s in_out=%d",
                    agent_id,
                    ack.filename,
                    wav_path,
                    _in_out,
                )
                asyncio.create_task(
                    self._run_stt_and_finalize(
                        agent_id, ack.filename, str(wav_path), writer, _in_out
                    )
                )
                # STT 비동기 실행 중 — 슬롯 여유 있으면 대기 에이전트에 NEXT
                await self._try_dispatch_next()
                return
            else:
                self._rec_log(
                    agent_id,
                    f"STT 실패: {ack.filename} (파일 미발견)",
                )
                self._logger.warning(
                    "uploaded WAV not found in storage: agent_id=%s filename=%s",
                    agent_id,
                    ack.filename,
                )

        # ── Flow control: STT 불필요 또는 실패 → 슬롯 해제 → 대기 에이전트에 NEXT ──
        if self._rec_in_flight > 0:
            self._rec_in_flight -= 1
        self._rec_log(
            agent_id,
            f"파이프라인 완료 (in_flight={self._rec_in_flight}/{self._rec_max_concurrent})",
        )
        self._logger.info(
            "rec pipeline done: agent_id=%s in_flight=%d/%d waiting=%d",
            agent_id,
            self._rec_in_flight,
            self._rec_max_concurrent,
            len(self._rec_waiting_agents),
        )
        await self._try_dispatch_next()

    async def _run_stt_and_finalize(
        self,
        agent_id: str,
        filename: str,
        wav_path: str,
        writer: _WriterLike,
        in_out: int = 0,
    ) -> None:
        """음질 분석 + STT 파이프라인을 별도 태스크로 실행.

        _recv_loop 밖에서 실행되므로 처리 중에도 heartbeat 에코가 가능.
        음질 분석 결과와 관계없이 STT 분석은 항상 진행.
        *in_out* 1=수신, 2=발신 — STT 채널 매핑에 사용.
        """
        if self.rec_handler is None:
            return
        rec_handler = self.rec_handler
        try:
            # ── Step 1: 음질 분석 (서버에서 수행) ──
            quality = await asyncio.to_thread(
                rec_handler.run_audio_quality,
                agent_id,
                filename,
                wav_path,
            )
            if quality is not None:
                q_status = quality.get("status", "?")
                q_l_db = quality.get("left_rms_db", 0.0)
                q_r_db = quality.get("right_rms_db", 0.0)
                q_dur = quality.get("duration_wav", 0.0)
                self._rec_log(
                    agent_id,
                    f"음질분석 완료: {filename} "
                    f"상태={q_status} L={q_l_db:.1f}dB R={q_r_db:.1f}dB "
                    f"시간={q_dur:.1f}초",
                )
            else:
                self._rec_log(agent_id, f"음질분석 실패: {filename} (STT는 계속 진행)")

            # ── Step 2: STT 분석 (음질 결과와 무관하게 항상 진행) ──
            stt_payload: SttResultPayload | None = await asyncio.to_thread(
                lambda: rec_handler.run_stt_pipeline(
                    agent_id,
                    filename,
                    wav_path,
                    quality,
                    in_out,
                    stt_engine=self._stt_engine,
                    openai_prompt=self._openai_prompt,
                )
            )
            if stt_payload is not None:
                try:
                    writer.write(
                        Packet.build(PacketType.STT_RESULT, stt_payload.pack())
                    )
                    await writer.drain()
                except (ConnectionError, OSError):
                    self._logger.warning(
                        "failed to send STT_RESULT (connection lost): "
                        "agent_id=%s filename=%s",
                        agent_id,
                        filename,
                    )
                # 상세 결과 로그 (GUI 녹취탭에 표시)
                p = stt_payload
                phrase_mark = "✓" if p.required_phrase_hit else "✗"
                forbidden_mark = (
                    f"⚠ {p.forbidden_word_list}" if p.forbidden_word_hit else "없음"
                )
                self._rec_log(
                    agent_id,
                    f"STT 완료: {p.filename} "
                    f"({p.duration_sec:.1f}초, {p.word_count}단어)",
                )
                self._rec_log(
                    agent_id,
                    f"  점수={p.score_total:.1f} "
                    f"(응답={p.score_response:.0f} "
                    f"문구={p.score_phrase:.0f} "
                    f"침묵={p.score_silence:.0f})",
                )
                self._rec_log(
                    agent_id,
                    f"  상담원={p.agent_talk_ratio * 100:.0f}% "
                    f"고객={p.customer_talk_ratio * 100:.0f}% "
                    f"침묵={p.silence_ratio * 100:.0f}% "
                    f"첫응답={p.first_response_sec:.1f}초",
                )
                self._rec_log(
                    agent_id,
                    f"  필수문구({phrase_mark}): "
                    f"{p.required_phrase_list or '-'}  "
                    f"금칙어: {forbidden_mark}",
                )
                self._logger.info(
                    "sent STT_RESULT: agent_id=%s filename=%s "
                    "dur=%.1fs words=%d score=%.1f "
                    "talk_agent=%.0f%% talk_cust=%.0f%% silence=%.0f%% "
                    "first_resp=%.1fs phrase=%d/%s forbidden=%d/%s",
                    agent_id,
                    stt_payload.filename,
                    stt_payload.duration_sec,
                    stt_payload.word_count,
                    stt_payload.score_total,
                    stt_payload.agent_talk_ratio * 100,
                    stt_payload.customer_talk_ratio * 100,
                    stt_payload.silence_ratio * 100,
                    stt_payload.first_response_sec,
                    stt_payload.required_phrase_hit,
                    stt_payload.required_phrase_list,
                    stt_payload.forbidden_word_hit,
                    stt_payload.forbidden_word_list,
                )
            else:
                self._rec_log(
                    agent_id,
                    f"STT 실패: {filename} (결과 없음)",
                )
                self._logger.warning(
                    "STT pipeline returned no result: agent_id=%s filename=%s",
                    agent_id,
                    filename,
                )
        except Exception:
            self._logger.exception(
                "STT pipeline error: agent_id=%s filename=%s", agent_id, filename
            )
            self._rec_log(agent_id, f"STT 오류: {filename}")
        finally:
            # ── Flow control: STT 완료 → 슬롯 해제 → 대기 에이전트에 NEXT ──
            if self._rec_in_flight > 0:
                self._rec_in_flight -= 1
            self._rec_log(
                agent_id,
                f"파이프라인 완료 (in_flight={self._rec_in_flight}/{self._rec_max_concurrent})",
            )
            self._logger.info(
                "rec pipeline done: agent_id=%s in_flight=%d/%d waiting=%d",
                agent_id,
                self._rec_in_flight,
                self._rec_max_concurrent,
                len(self._rec_waiting_agents),
            )
            await self._try_dispatch_next()

    def _handle_rec_data_resp(self, agent_id: str, payload: bytes) -> None:
        """Handle REC_DATA_RESP from agent — resolve pending future."""
        try:
            resp = RecDataRespPayload.unpack(payload)
        except (ValueError, KeyError):
            self._logger.warning("invalid REC_DATA_RESP payload: agent_id=%s", agent_id)
            return

        future = self._rec_data_futures.pop(agent_id, None)
        if future is not None and not future.done():
            future.set_result(resp)
        self._logger.debug(
            "rec data resp: agent_id=%s query_type=%s records=%d",
            agent_id,
            resp.query_type,
            len(resp.records),
        )

    async def send_rec_data_req(
        self,
        agent_id: str,
        query_type: str,
        date_str: str = "",
        filename: str = "",
        search: str = "",
        timeout: float = 30.0,
    ) -> RecDataRespPayload | None:
        """Send REC_DATA_REQ to agent and wait for response."""
        session = self.session_mgr.get_session(agent_id)
        if session is None or session.writer is None:
            return None
        writer = cast(_WriterLike, session.writer)

        loop = asyncio.get_running_loop()
        future: asyncio.Future[RecDataRespPayload] = loop.create_future()
        self._rec_data_futures[agent_id] = future

        req = RecDataReqPayload(
            query_type=query_type,
            date_str=date_str,
            filename=filename,
            search=search,
        )
        writer.write(Packet.build(PacketType.REC_DATA_REQ, req.pack()))
        await writer.drain()

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._rec_data_futures.pop(agent_id, None)
            self._logger.warning("rec data req timed out: agent_id=%s", agent_id)
            return None


class _WriterLike(Protocol):
    def write(self, data: bytes) -> None: ...

    async def drain(self) -> None: ...

    def close(self) -> None: ...

    async def wait_closed(self) -> None: ...

    def is_closing(self) -> bool: ...

    def get_extra_info(self, name: str) -> object: ...
