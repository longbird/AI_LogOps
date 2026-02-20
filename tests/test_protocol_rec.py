from __future__ import annotations

from shared.protocol import (
    HEADER_SIZE,
    Packet,
    PacketType,
    RecAnalysisPayload,
    RecUploadAckPayload,
    RecUploadReqPayload,
)


def test_packet_type_values_for_recording_messages() -> None:
    assert PacketType.REC_ANALYSIS_RESULT == 0x30
    assert PacketType.REC_UPLOAD_REQ == 0x31
    assert PacketType.REC_UPLOAD_ACK == 0x32


def test_rec_analysis_payload_roundtrip_with_realistic_values() -> None:
    payload = RecAnalysisPayload(
        rec_no=2026022001,
        status="ok",
        left_rms_db=-24.7,
        right_rms_db=-25.1,
        left_silence_ratio=0.08,
        right_silence_ratio=0.11,
        dropout_count=3,
        duration_wav=302.56,
        duration_smdr=301.97,
        is_stereo=True,
    )

    packed = payload.pack()
    unpacked = RecAnalysisPayload.unpack(packed)

    assert unpacked == payload


def test_rec_upload_req_payload_roundtrip_with_https_url() -> None:
    payload = RecUploadReqPayload(
        rec_no=2026022002,
        upload_url="https://upload.example.com/recordings/2026022002.wav?token=abc123",
    )

    packed = payload.pack()
    unpacked = RecUploadReqPayload.unpack(packed)

    assert unpacked == payload


def test_rec_upload_ack_payload_roundtrip_and_exact_size() -> None:
    payload = RecUploadAckPayload(rec_no=2026022003, status=1, file_size=4_294_967_296)

    packed = payload.pack()
    unpacked = RecUploadAckPayload.unpack(packed)

    assert len(packed) == 13
    assert unpacked == payload


def test_packet_build_for_new_recording_packet_types() -> None:
    analysis_payload = RecAnalysisPayload(
        rec_no=1,
        status="warn",
        left_rms_db=-32.5,
        right_rms_db=-33.2,
        left_silence_ratio=0.15,
        right_silence_ratio=0.14,
        dropout_count=0,
        duration_wav=30.5,
        duration_smdr=30.4,
        is_stereo=False,
    ).pack()
    req_payload = RecUploadReqPayload(
        rec_no=2,
        upload_url="https://upload.example.com/recordings/2.wav",
    ).pack()
    ack_payload = RecUploadAckPayload(rec_no=3, status=0, file_size=12345).pack()

    packets = [
        (PacketType.REC_ANALYSIS_RESULT, analysis_payload),
        (PacketType.REC_UPLOAD_REQ, req_payload),
        (PacketType.REC_UPLOAD_ACK, ack_payload),
    ]

    for packet_type, payload in packets:
        packet = Packet.build(packet_type=packet_type, payload_bytes=payload)
        parsed_type, parsed_length = Packet.parse_header(packet[:HEADER_SIZE])

        assert parsed_type == packet_type
        assert parsed_length == len(payload)
        assert packet[HEADER_SIZE:] == payload
