from __future__ import annotations

import pytest

from shared.protocol import (
    HEADER_SIZE,
    MAX_PAYLOAD_SIZE,
    AuthStatus,
    CtrlAckStatus,
    CtrlAction,
    DisconnectReason,
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
