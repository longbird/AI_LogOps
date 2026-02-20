"""WAV file upload endpoint.

Receives multipart WAV uploads from agents and stores them via RecordingStorage.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from server.airec.storage import RecordingStorage


def create_upload_router(storage: RecordingStorage) -> APIRouter:
    """Create upload router with storage dependency."""
    router = APIRouter(prefix="/api/rec", tags=["airec-upload"])

    async def upload_recording(
        file: Annotated[UploadFile, File(...)],
        filename: Annotated[str, Form(...)],
        agent_id: Annotated[str, Form(...)],
    ) -> dict[str, object]:
        """Receive a WAV file upload from an agent.

        Multipart form fields:
            - file: WAV file
            - filename: recording filename
            - agent_id: agent identifier
        """
        if file.filename is None or not file.filename.lower().endswith(".wav"):
            raise HTTPException(400, "Only WAV files are accepted")

        data = await file.read()
        if len(data) == 0:
            raise HTTPException(400, "Empty file")

        saved_path = storage.store(
            agent_id=agent_id,
            data=data,
            filename=filename,
        )

        return {
            "status": "ok",
            "filename": filename,
            "agent_id": agent_id,
            "size": len(data),
            "path": str(saved_path),
        }

    router.add_api_route("/upload", upload_recording, methods=["POST"])

    return router
