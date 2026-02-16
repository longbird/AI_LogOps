from __future__ import annotations

# pyright: reportAttributeAccessIssue=false, reportMissingImports=false

from dataclasses import asdict

import pytest

from shared.protocol import (
    CHUNK_SIZE,
    HEADER_SIZE,
    MAX_PAYLOAD_SIZE,
    AuthAckPayload,
    AuthPayload,
    AuthStatus,
    CmdCtrlPayload,
    CmdCtrlAckPayload,
    CmdDeployPayload,
    CtrlAckStatus,
    CtrlAction,
    DisconnectPayload,
    DisconnectReason,
    FileAckPayload,
    FileChunkPayload,
    HeartbeatPayload,
    LogHistPayload,
    LogRealPayload,
    Packet,
    PacketHeader,
    PacketType,
)


def test_header_size_constant() -> None:
    assert HEADER_SIZE == 5


def test_protocol_size_constants() -> None:
    assert CHUNK_SIZE == 4096
    assert MAX_PAYLOAD_SIZE == 10 * 1024 * 1024


def test_packet_type_values_match_spec() -> None:
    assert PacketType.AUTH == 0x01
    assert PacketType.AUTH_ACK == 0x02
    assert PacketType.LOG_HIST == 0x03
    assert PacketType.LOG_REAL == 0x04
    assert PacketType.CMD_DEPLOY == 0x10
    assert PacketType.FILE_CHUNK == 0x11
    assert PacketType.FILE_ACK == 0x12
    assert PacketType.CMD_CTRL == 0x13
    assert PacketType.CMD_CTRL_ACK == 0x14
    assert PacketType.AGENT_UPDATE == 0x20
    assert PacketType.HEARTBEAT == 0xFE
    assert PacketType.DISCONNECT == 0xFF


def test_packet_type_values_are_unique() -> None:
    values = [int(packet_type) for packet_type in PacketType]
    assert len(values) == len(set(values))


def test_status_and_action_enums_match_spec() -> None:
    assert AuthStatus.SUCCESS == 0x00
    assert AuthStatus.FAILED == 0x01
    assert AuthStatus.VERSION_MISMATCH == 0x02

    assert CtrlAction.STOP == 0x00
    assert CtrlAction.START == 0x01
    assert CtrlAction.RESTART == 0x02

    assert CtrlAckStatus.SUCCESS == 0x00
    assert CtrlAckStatus.FAILED == 0x01
    assert CtrlAckStatus.DEPLOY_VERIFIED == 0x10
    assert CtrlAckStatus.DEPLOY_ROLLBACK == 0x11

    assert DisconnectReason.NORMAL == 0x00
    assert DisconnectReason.ERROR == 0x01
    assert DisconnectReason.HEARTBEAT_TIMEOUT == 0x02
    assert DisconnectReason.SERVER_SHUTDOWN == 0x03


@pytest.mark.parametrize("packet_type", list(PacketType))
@pytest.mark.parametrize("payload_length", [0, 1, 4096, MAX_PAYLOAD_SIZE])
def test_packet_header_pack_unpack_roundtrip(
    packet_type: PacketType, payload_length: int
) -> None:
    packed = PacketHeader.pack(packet_type=packet_type, payload_length=payload_length)
    unpacked_type, unpacked_len = PacketHeader.unpack(packed)
    assert unpacked_type == packet_type
    assert unpacked_len == payload_length


def test_packet_header_rejects_oversized_payload() -> None:
    with pytest.raises(ValueError):
        _ = PacketHeader.pack(
            packet_type=PacketType.AUTH, payload_length=MAX_PAYLOAD_SIZE + 1
        )


def test_packet_header_rejects_negative_payload() -> None:
    with pytest.raises(ValueError):
        _ = PacketHeader.pack(packet_type=PacketType.AUTH, payload_length=-1)


@pytest.mark.parametrize("size", [0, 1, 2, 3, 4])
def test_packet_header_unpack_rejects_wrong_header_size(size: int) -> None:
    with pytest.raises(ValueError):
        _ = PacketHeader.unpack(b"\x00" * size)


def test_packet_header_unpack_rejects_unknown_packet_type() -> None:
    invalid_header = b"\x05" + (0).to_bytes(4, byteorder="big")
    with pytest.raises(ValueError):
        _ = PacketHeader.unpack(invalid_header)


@pytest.mark.parametrize(
    "payload",
    [
        AuthPayload(agent_id="agent-01", version="1.0.0", token="t" * 64),
        AuthPayload(agent_id="a", version="1", token="x" * 64),
        AuthPayload(agent_id="a" * 32, version="v" * 8, token="z" * 64),
        AuthPayload(agent_id="", version="", token="k" * 64),
    ],
)
def test_auth_payload_pack_unpack_roundtrip(payload: AuthPayload) -> None:
    packed = payload.pack()
    unpacked = AuthPayload.unpack(packed)
    assert asdict(unpacked) == asdict(payload)


def test_auth_payload_packs_to_fixed_104_bytes() -> None:
    payload = AuthPayload(agent_id="agent-01", version="1.0.0", token="t" * 64)
    assert len(payload.pack()) == 104


def test_auth_payload_accepts_short_token_and_pads() -> None:
    payload = AuthPayload(agent_id="agent-01", version="1.0.0", token="shorttoken")
    packed = payload.pack()
    unpacked = AuthPayload.unpack(packed)
    assert len(packed) == 104
    assert unpacked.token == "shorttoken"


def test_auth_ack_payload_pack_unpack_success() -> None:
    payload = AuthAckPayload(status=AuthStatus.SUCCESS, session_id="session-abc")
    packed = payload.pack()
    unpacked = AuthAckPayload.unpack(packed)
    assert unpacked.status == AuthStatus.SUCCESS
    assert unpacked.session_id == "session-abc"
    assert len(packed) == 17


def test_auth_ack_payload_pack_unpack_version_mismatch() -> None:
    payload = AuthAckPayload(status=AuthStatus.VERSION_MISMATCH, session_id="sid")
    packed = payload.pack()
    unpacked = AuthAckPayload.unpack(packed)
    assert unpacked.status == AuthStatus.VERSION_MISMATCH
    assert unpacked.session_id == "sid"
    assert len(packed) == 17


def test_heartbeat_payload_pack_unpack_roundtrip() -> None:
    payload = HeartbeatPayload(timestamp=1_700_000_000, cpu_percent=45, mem_percent=72)
    packed = payload.pack()
    unpacked = HeartbeatPayload.unpack(packed)
    assert unpacked == payload
    assert len(packed) == 10


@pytest.mark.parametrize(
    ("cpu_percent", "mem_percent"),
    [(150, 10), (10, 150), (101, 100), (100, 101)],
)
def test_heartbeat_payload_rejects_out_of_range_values(
    cpu_percent: int, mem_percent: int
) -> None:
    payload = HeartbeatPayload(
        timestamp=1_700_000_000,
        cpu_percent=cpu_percent,
        mem_percent=mem_percent,
    )
    with pytest.raises(ValueError):
        _ = payload.pack()


@pytest.mark.parametrize("reason", list(DisconnectReason))
def test_disconnect_payload_pack_unpack_roundtrip(reason: DisconnectReason) -> None:
    payload = DisconnectPayload(reason=reason)
    packed = payload.pack()
    unpacked = DisconnectPayload.unpack(packed)
    assert unpacked.reason == reason
    assert len(packed) == 1


@pytest.mark.parametrize("packet_type", list(PacketType))
def test_packet_build_and_parse_header_roundtrip(packet_type: PacketType) -> None:
    payload = b"test-payload"
    packet = Packet.build(packet_type=packet_type, payload_bytes=payload)
    header = packet[:HEADER_SIZE]
    body = packet[HEADER_SIZE:]

    parsed_type, parsed_length = Packet.parse_header(header)
    assert parsed_type == packet_type
    assert parsed_length == len(payload)
    assert body == payload


def test_log_hist_payload_roundtrip() -> None:
    payload = LogHistPayload(filename="app.log", data=b"line1\nline2\n")
    packed = payload.pack()
    unpacked = LogHistPayload.unpack(packed)
    assert unpacked.filename == "app.log"
    assert unpacked.data == b"line1\nline2\n"


def test_log_real_payload_roundtrip() -> None:
    payload = LogRealPayload(filename="app.log", line="service started")
    packed = payload.pack()
    unpacked = LogRealPayload.unpack(packed)
    assert unpacked.filename == "app.log"
    assert unpacked.line == "service started"


def test_log_hist_large_data() -> None:
    data_size = MAX_PAYLOAD_SIZE - 260
    payload = LogHistPayload(filename="large.log", data=b"x" * data_size)
    packed = payload.pack()
    unpacked = LogHistPayload.unpack(packed)
    assert len(packed) == MAX_PAYLOAD_SIZE
    assert unpacked.filename == "large.log"
    assert unpacked.data == b"x" * data_size


def test_log_real_unicode_line() -> None:
    payload = LogRealPayload(filename="korean.log", line="로그 수집 시작")
    packed = payload.pack()
    unpacked = LogRealPayload.unpack(packed)
    assert unpacked.filename == "korean.log"
    assert unpacked.line == "로그 수집 시작"


def test_cmd_deploy_payload_pack_unpack_roundtrip() -> None:
    payload = CmdDeployPayload(
        file_size=8192,
        sha256="a" * 64,
        filename="agent_update.bin",
    )
    packed = payload.pack()
    unpacked = CmdDeployPayload.unpack(packed)
    assert unpacked == payload
    assert len(packed) == 292


def test_file_chunk_payload_pack_unpack_roundtrip() -> None:
    payload = FileChunkPayload(seq_num=2, data=b"abc123")
    packed = payload.pack()
    unpacked = FileChunkPayload.unpack(packed)
    assert unpacked.seq_num == 2
    assert unpacked.data == b"abc123"
    assert len(packed) == 12


def test_file_chunk_payload_variable_size() -> None:
    payload = FileChunkPayload(seq_num=1, data=b"x" * CHUNK_SIZE)
    packed = payload.pack()
    unpacked = FileChunkPayload.unpack(packed)
    assert unpacked.seq_num == 1
    assert len(unpacked.data) == CHUNK_SIZE
    assert unpacked.data == b"x" * CHUNK_SIZE


def test_file_ack_payload_pack_unpack() -> None:
    payload = FileAckPayload(seq_num=42, status=0)
    packed = payload.pack()
    unpacked = FileAckPayload.unpack(packed)
    assert unpacked.seq_num == 42
    assert unpacked.status == 0
    assert len(packed) == 5


def test_cmd_ctrl_ack_payload_pack_unpack() -> None:
    payload = CmdCtrlAckPayload(
        action=CtrlAction.RESTART,
        pid=12345,
        status=CtrlAckStatus.DEPLOY_VERIFIED,
    )
    packed = payload.pack()
    unpacked = CmdCtrlAckPayload.unpack(packed)
    assert unpacked.action == CtrlAction.RESTART
    assert unpacked.pid == 12345
    assert unpacked.status == CtrlAckStatus.DEPLOY_VERIFIED
    assert len(packed) == 6


def test_cmd_ctrl_payload_pack_unpack() -> None:
    payload = CmdCtrlPayload(action=CtrlAction.RESTART)
    packed = payload.pack()
    unpacked = CmdCtrlPayload.unpack(packed)
    assert unpacked.action == CtrlAction.RESTART
    assert len(packed) == 1
