"""WAV streaming endpoint with HTTP Range support.

Adapted from AirREC airec_api/routers/stream.py.
Uses RecordingStorage instead of direct DB/filesystem access.
µ-law (format_code=7) WAV files are transcoded to 16-bit PCM on the fly
for browser compatibility (Python 3.13 — audioop unavailable).

If a WAV file is not found locally, it is fetched from the agent via TCP
(query_type="wav_file") and cached in local storage for subsequent requests.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import struct
from collections.abc import Generator

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from server.airec.storage import RecordingStorage

CHUNK_SIZE = 64 * 1024  # 64KB
_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# µ-law → 16-bit PCM 변환 (ITU-T G.711 lookup table)
# ---------------------------------------------------------------------------

_ULAW_TABLE = np.array(
    [
        -32124,
        -31100,
        -30076,
        -29052,
        -28028,
        -27004,
        -25980,
        -24956,
        -23932,
        -22908,
        -21884,
        -20860,
        -19836,
        -18812,
        -17788,
        -16764,
        -15996,
        -15484,
        -14972,
        -14460,
        -13948,
        -13436,
        -12924,
        -12412,
        -11900,
        -11388,
        -10876,
        -10364,
        -9852,
        -9340,
        -8828,
        -8316,
        -7932,
        -7676,
        -7420,
        -7164,
        -6908,
        -6652,
        -6396,
        -6140,
        -5884,
        -5628,
        -5372,
        -5116,
        -4860,
        -4604,
        -4348,
        -4092,
        -3900,
        -3772,
        -3644,
        -3516,
        -3388,
        -3260,
        -3132,
        -3004,
        -2876,
        -2748,
        -2620,
        -2492,
        -2364,
        -2236,
        -2108,
        -1980,
        -1884,
        -1820,
        -1756,
        -1692,
        -1628,
        -1564,
        -1500,
        -1436,
        -1372,
        -1308,
        -1244,
        -1180,
        -1116,
        -1052,
        -988,
        -924,
        -876,
        -844,
        -812,
        -780,
        -748,
        -716,
        -684,
        -652,
        -620,
        -588,
        -556,
        -524,
        -492,
        -460,
        -428,
        -396,
        -372,
        -356,
        -340,
        -324,
        -308,
        -292,
        -276,
        -260,
        -244,
        -228,
        -212,
        -196,
        -180,
        -164,
        -148,
        -132,
        -120,
        -112,
        -104,
        -96,
        -88,
        -80,
        -72,
        -64,
        -56,
        -48,
        -40,
        -32,
        -24,
        -16,
        -8,
        0,
        32124,
        31100,
        30076,
        29052,
        28028,
        27004,
        25980,
        24956,
        23932,
        22908,
        21884,
        20860,
        19836,
        18812,
        17788,
        16764,
        15996,
        15484,
        14972,
        14460,
        13948,
        13436,
        12924,
        12412,
        11900,
        11388,
        10876,
        10364,
        9852,
        9340,
        8828,
        8316,
        7932,
        7676,
        7420,
        7164,
        6908,
        6652,
        6396,
        6140,
        5884,
        5628,
        5372,
        5116,
        4860,
        4604,
        4348,
        4092,
        3900,
        3772,
        3644,
        3516,
        3388,
        3260,
        3132,
        3004,
        2876,
        2748,
        2620,
        2492,
        2364,
        2236,
        2108,
        1980,
        1884,
        1820,
        1756,
        1692,
        1628,
        1564,
        1500,
        1436,
        1372,
        1308,
        1244,
        1180,
        1116,
        1052,
        988,
        924,
        876,
        844,
        812,
        780,
        748,
        716,
        684,
        652,
        620,
        588,
        556,
        524,
        492,
        460,
        428,
        396,
        372,
        356,
        340,
        324,
        308,
        292,
        276,
        260,
        244,
        228,
        212,
        196,
        180,
        164,
        148,
        132,
        120,
        112,
        104,
        96,
        88,
        80,
        72,
        64,
        56,
        48,
        40,
        32,
        24,
        16,
        8,
        0,
    ],
    dtype=np.int16,
)


def _is_ulaw_wav(file_path: str) -> bool:
    """WAV 파일이 µ-law 포맷(format_code=7)인지 빠르게 확인."""
    try:
        with open(file_path, "rb") as f:
            header = f.read(12)
            if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
                return False
            while True:
                chunk_hdr = f.read(8)
                if len(chunk_hdr) < 8:
                    return False
                chunk_id = chunk_hdr[:4]
                chunk_size = struct.unpack("<I", chunk_hdr[4:8])[0]
                if chunk_id == b"fmt ":
                    fmt_data = f.read(min(chunk_size, 2))
                    if len(fmt_data) < 2:
                        return False
                    return struct.unpack("<H", fmt_data)[0] == 7
                f.seek(chunk_size + (chunk_size % 2), 1)
    except (OSError, struct.error):
        return False


def _transcode_ulaw_to_pcm(src_path: str) -> bytes:
    """µ-law WAV → 16-bit PCM WAV 전체 변환. 메모리에서 처리."""
    with open(src_path, "rb") as f:
        riff = f.read(4)
        if riff != b"RIFF":
            raise ValueError("Not a RIFF file")
        f.read(4)  # file size (skip)
        if f.read(4) != b"WAVE":
            raise ValueError("Not a WAVE file")

        num_channels = 1
        sample_rate = 8000
        data_bytes = b""
        fmt_found = False

        while True:
            chunk_hdr = f.read(8)
            if len(chunk_hdr) < 8:
                break
            chunk_id = chunk_hdr[:4]
            chunk_size = struct.unpack("<I", chunk_hdr[4:8])[0]

            if chunk_id == b"fmt ":
                fmt_data = f.read(chunk_size)
                audio_format = struct.unpack("<H", fmt_data[0:2])[0]
                num_channels = struct.unpack("<H", fmt_data[2:4])[0]
                sample_rate = struct.unpack("<I", fmt_data[4:8])[0]
                fmt_found = True
                if audio_format != 7:
                    raise ValueError(f"Not µ-law: format={audio_format}")
            elif chunk_id == b"data":
                data_bytes = f.read(chunk_size)
            else:
                f.seek(chunk_size, 1)
            if chunk_size % 2 == 1:
                f.read(1)

    if not fmt_found or not data_bytes:
        raise ValueError("Missing fmt or data chunk")

    # µ-law → 16-bit PCM via lookup table (vectorized)
    pcm_samples = _ULAW_TABLE[np.frombuffer(data_bytes, dtype=np.uint8)]
    pcm_data = pcm_samples.tobytes()

    # PCM WAV 헤더 작성
    bits_per_sample = 16
    block_align = num_channels * (bits_per_sample // 8)
    byte_rate = sample_rate * block_align
    data_size = len(pcm_data)

    buf = io.BytesIO()
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + data_size))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(struct.pack("<I", 16))
    buf.write(
        struct.pack(
            "<HHIIHH",
            1,
            num_channels,
            sample_rate,
            byte_rate,
            block_align,
            bits_per_sample,
        )
    )
    buf.write(b"data")
    buf.write(struct.pack("<I", data_size))
    buf.write(pcm_data)
    return buf.getvalue()


async def _fetch_wav_from_agent(
    request: Request, filename: str, storage: RecordingStorage, agent_id: str = ""
) -> str | None:
    """Fetch WAV file from agent via TCP and cache locally. Returns local path or None."""
    tcp_server = getattr(request.app.state, "tcp_server", None)
    session_mgr = getattr(request.app.state, "session_mgr", None)
    if tcp_server is None or session_mgr is None:
        return None

    # agent_id 지정 시 해당 에이전트, 없으면 첫 번째 사용
    if not agent_id:
        sessions = session_mgr.get_all_sessions()
        if not sessions:
            return None
        agent_id = sessions[0].agent_info.agent_id

    try:
        resp = await tcp_server.send_rec_data_req(
            agent_id=agent_id,
            query_type="wav_file",
            filename=filename,
            timeout=120.0,
        )
    except Exception:
        _logger.exception("TCP wav_file request failed: %s", filename)
        return None

    if resp is None or not resp.records:
        return None

    rec = resp.records[0]
    if "error" in rec:
        _logger.warning("Agent wav_file error: %s", rec)
        return None

    wav_b64 = rec.get("wav_b64")
    if not wav_b64:
        return None

    wav_bytes = base64.b64decode(wav_b64)
    saved_path = storage.store(agent_id, wav_bytes, filename)
    _logger.info(
        "WAV cached from agent: filename=%s size=%d path=%s",
        filename,
        len(wav_bytes),
        saved_path,
    )
    return str(saved_path)


def create_stream_router(storage: RecordingStorage) -> APIRouter:
    """Create stream router with storage dependency."""
    router = APIRouter(prefix="/api/rec", tags=["airec-stream"])

    async def stream_recording(filename: str, request: Request) -> Response:
        """Stream a WAV file with HTTP Range support for browser playback.

        µ-law WAV files are automatically transcoded to 16-bit PCM.
        If not found locally, fetches from agent via TCP and caches.
        """
        file_path = storage.find_by_filename(filename)

        # 로컬에 없으면 에이전트에서 TCP로 가져와 캐시
        if file_path is None:
            req_agent_id = request.query_params.get("agent_id", "")
            cached = await _fetch_wav_from_agent(request, filename, storage, agent_id=req_agent_id)
            if cached is not None:
                file_path = storage.find_by_filename(filename)

        if file_path is None:
            raise HTTPException(404, f"Recording {filename} not found")

        full_path = str(file_path)
        file_name = file_path.name

        # download=1 이면 Content-Disposition: attachment (파일 다운로드)
        disposition = (
            "attachment"
            if request.query_params.get("download") == "1"
            else "inline"
        )

        # µ-law → PCM 트랜스코딩 (브라우저 호환)
        if _is_ulaw_wav(full_path):
            try:
                pcm_data = _transcode_ulaw_to_pcm(full_path)
            except (ValueError, OSError) as exc:
                _logger.warning("µ-law transcode failed: %s — %s", filename, exc)
            else:
                pcm_size = len(pcm_data)
                range_header = request.headers.get("range")

                if range_header:
                    range_spec = range_header.replace("bytes=", "")
                    parts = range_spec.split("-")
                    start = int(parts[0]) if parts[0] else 0
                    end = int(parts[1]) if len(parts) > 1 and parts[1] else pcm_size - 1
                    end = min(end, pcm_size - 1)
                    length = end - start + 1
                    return Response(
                        content=pcm_data[start : end + 1],
                        status_code=206,
                        media_type="audio/wav",
                        headers={
                            "Content-Range": f"bytes {start}-{end}/{pcm_size}",
                            "Accept-Ranges": "bytes",
                            "Content-Length": str(length),
                            "Content-Disposition": f'{disposition}; filename="{file_name}"',
                        },
                    )

                return Response(
                    content=pcm_data,
                    media_type="audio/wav",
                    headers={
                        "Accept-Ranges": "bytes",
                        "Content-Length": str(pcm_size),
                        "Content-Disposition": f'{disposition}; filename="{file_name}"',
                    },
                )

        # 일반 WAV (PCM) — 기존 스트리밍 로직
        file_size = os.path.getsize(full_path)
        range_header = request.headers.get("range")

        if range_header:
            range_spec = range_header.replace("bytes=", "")
            parts = range_spec.split("-")
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1
            end = min(end, file_size - 1)
            length = end - start + 1

            def iter_range() -> Generator[bytes, None, None]:
                with open(full_path, "rb") as stream_file:
                    _ = stream_file.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = stream_file.read(min(CHUNK_SIZE, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk

            return StreamingResponse(
                iter_range(),
                status_code=206,
                media_type="audio/wav",
                headers={
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(length),
                    "Content-Disposition": f'{disposition}; filename="{file_name}"',
                },
            )

        def iter_file() -> Generator[bytes, None, None]:
            with open(full_path, "rb") as stream_file:
                while True:
                    chunk = stream_file.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    yield chunk

        return StreamingResponse(
            iter_file(),
            media_type="audio/wav",
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
                "Content-Disposition": f'{disposition}; filename="{file_name}"',
            },
        )

    router.add_api_route("/stream/{filename:path}", stream_recording, methods=["GET"])

    return router
