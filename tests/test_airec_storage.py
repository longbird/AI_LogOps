"""Tests for AirREC storage and upload endpoint."""

from __future__ import annotations

import os
import tempfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.airec.config import AirRecConfig
from server.airec.routers.recordings import create_recordings_router
from server.airec.routers.stream import create_stream_router
from server.airec.routers.upload import create_upload_router
from server.airec.storage import RecordingStorage


class TestRecordingStorage:
    def test_store_and_find(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = RecordingStorage(tmpdir)
            data = b"RIFF" + b"\x00" * 100
            path = storage.store("agent-01", data, "rec_12345.wav")
            assert path.exists()
            assert path.read_bytes() == data

            found = storage.find_by_filename("rec_12345.wav")
            assert found is not None
            assert found.name == "rec_12345.wav"

    def test_find_nonexistent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = RecordingStorage(tmpdir)
            assert storage.find_by_filename("rec_99999.wav") is None

    def test_find_file_by_agent_and_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = RecordingStorage(tmpdir)
            storage.store("agent-01", b"data", "rec_100.wav")
            found = storage.find_file("agent-01", "rec_100.wav")
            assert found is not None
            assert found.name == "rec_100.wav"

    def test_list_recordings_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = RecordingStorage(tmpdir)
            assert storage.list_recordings() == []

    def test_list_recordings_with_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = RecordingStorage(tmpdir)
            storage.store("agent-01", b"d1", "rec_1.wav")
            storage.store("agent-01", b"d2", "rec_2.wav")
            storage.store("agent-02", b"d3", "rec_3.wav")

            all_recs = storage.list_recordings()
            assert len(all_recs) == 3

            agent1 = storage.list_recordings(agent_id="agent-01")
            assert len(agent1) == 2

    def test_base_dir_property(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = RecordingStorage(tmpdir)
            assert str(storage.base_dir) == tmpdir


class TestAirRecConfig:
    def test_defaults(self) -> None:
        cfg = AirRecConfig()
        assert cfg.storage_dir == "server/storage/recordings"
        assert cfg.max_upload_size_mb == 100
        assert cfg.allowed_extensions == [".wav"]

    def test_ensure_storage_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = os.path.join(tmpdir, "test_storage")
            cfg = AirRecConfig(storage_dir=subdir)
            path = cfg.ensure_storage_dir()
            assert path.exists()


class TestUploadEndpoint:
    def _create_app(self, storage: RecordingStorage) -> FastAPI:
        app = FastAPI()
        app.include_router(create_upload_router(storage))
        return app

    def test_upload_wav(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = RecordingStorage(tmpdir)
            app = self._create_app(storage)
            client = TestClient(app)

            wav_data = b"RIFF" + b"\x00" * 100

            resp = client.post(
                "/api/rec/upload",
                files={"file": ("test_001.wav", wav_data, "audio/wav")},
                data={"filename": "rec_001.wav", "agent_id": "test-agent"},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "ok"
            assert body["filename"] == "rec_001.wav"
            assert body["agent_id"] == "test-agent"
            assert body["size"] == len(wav_data)

    def test_upload_non_wav_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = RecordingStorage(tmpdir)
            app = self._create_app(storage)
            client = TestClient(app)

            resp = client.post(
                "/api/rec/upload",
                files={"file": ("readme.txt", b"hello", "text/plain")},
                data={"filename": "readme.txt", "agent_id": "test-agent"},
            )
            assert resp.status_code == 400

    def test_upload_empty_file_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = RecordingStorage(tmpdir)
            app = self._create_app(storage)
            client = TestClient(app)

            resp = client.post(
                "/api/rec/upload",
                files={"file": ("empty.wav", b"", "audio/wav")},
                data={"filename": "empty.wav", "agent_id": "test-agent"},
            )
            assert resp.status_code == 400

    def test_upload_missing_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = RecordingStorage(tmpdir)
            app = self._create_app(storage)
            client = TestClient(app)

            resp = client.post(
                "/api/rec/upload",
                files={"file": ("test.wav", b"RIFF\x00" * 10, "audio/wav")},
                data={"agent_id": "test-agent"},
            )
            assert resp.status_code == 422


class TestStreamEndpoint:
    def _create_app(self, storage: RecordingStorage) -> FastAPI:
        app = FastAPI()
        app.include_router(create_stream_router(storage))
        return app

    def test_stream_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = RecordingStorage(tmpdir)
            app = self._create_app(storage)
            client = TestClient(app)

            resp = client.get("/api/rec/stream/99999")
            assert resp.status_code == 404

    def test_stream_full_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = RecordingStorage(tmpdir)
            wav_data = b"RIFF" + b"\xab" * 200
            storage.store("agent-01", wav_data, "rec_42.wav")

            app = self._create_app(storage)
            client = TestClient(app)

            resp = client.get("/api/rec/stream/rec_42.wav")
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "audio/wav"
            assert len(resp.content) == len(wav_data)

    def test_stream_range_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = RecordingStorage(tmpdir)
            wav_data = b"RIFF" + b"\xcd" * 500
            storage.store("agent-01", wav_data, "rec_55.wav")

            app = self._create_app(storage)
            client = TestClient(app)

            resp = client.get(
                "/api/rec/stream/rec_55.wav",
                headers={"Range": "bytes=0-99"},
            )
            assert resp.status_code == 206
            assert len(resp.content) == 100
            assert "Content-Range" in resp.headers


class TestRecordingsEndpoint:
    def _create_app(self, storage: RecordingStorage) -> FastAPI:
        app = FastAPI()
        app.include_router(create_recordings_router(storage))
        return app

    def test_list_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = RecordingStorage(tmpdir)
            app = self._create_app(storage)
            client = TestClient(app)

            resp = client.get("/api/rec/recordings")
            assert resp.status_code == 200
            body = resp.json()
            assert body["total"] == 0
            assert body["items"] == []

    def test_list_with_recordings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = RecordingStorage(tmpdir)
            storage.store("a1", b"d1", "rec_1.wav")
            storage.store("a1", b"d2", "rec_2.wav")

            app = self._create_app(storage)
            client = TestClient(app)

            resp = client.get("/api/rec/recordings")
            assert resp.status_code == 200
            body = resp.json()
            assert body["total"] == 2

    def test_list_filter_by_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = RecordingStorage(tmpdir)
            storage.store("a1", b"d1", "rec_1.wav")
            storage.store("a2", b"d2", "rec_2.wav")

            app = self._create_app(storage)
            client = TestClient(app)

            resp = client.get("/api/rec/recordings?agent_id=a1")
            assert resp.status_code == 200
            body = resp.json()
            assert body["total"] == 1
