"""WAV streaming endpoint with HTTP Range support.

Adapted from AirREC airec_api/routers/stream.py.
Uses RecordingStorage instead of direct DB/filesystem access.
"""

from __future__ import annotations

import os
from collections.abc import Generator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from server.airec.storage import RecordingStorage

CHUNK_SIZE = 64 * 1024  # 64KB


def create_stream_router(storage: RecordingStorage) -> APIRouter:
    """Create stream router with storage dependency."""
    router = APIRouter(prefix="/api/rec", tags=["airec-stream"])

    def stream_recording(filename: str, request: Request) -> Response:
        """Stream a WAV file with HTTP Range support for browser playback."""
        file_path = storage.find_by_filename(filename)
        if file_path is None:
            raise HTTPException(404, f"Recording {filename} not found")

        full_path = str(file_path)
        file_name = file_path.name
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
                    "Content-Disposition": f'inline; filename="{file_name}"',
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
                "Content-Disposition": f'inline; filename="{file_name}"',
            },
        )

    router.add_api_route("/stream/{filename:path}", stream_recording, methods=["GET"])

    return router
