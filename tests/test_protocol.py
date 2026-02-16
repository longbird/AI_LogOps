from __future__ import annotations

from dataclasses import asdict

import pytest

from shared.protocol import (
    HEADER_SIZE,
    MAX_PAYLOAD_SIZE,
    AuthAckPayload,
    AuthPayload,
    AuthStatus,
    CtrlAckStatus,
    CtrlAction,
    DisconnectPayload,
    DisconnectReason,
    HeartbeatPayload,
    Packet,
    PacketHeader,
    PacketType,
)


def test_header_size_constant() -> None:
    assert HEADER_SIZE == 5


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
