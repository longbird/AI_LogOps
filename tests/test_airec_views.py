import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from server.airec.routers.views import create_views_router
from server.airec.storage import RecordingStorage


# Setup for tests
@pytest.fixture
def test_env():
    # Create temp storage
    storage_dir = tempfile.mkdtemp()
    storage = RecordingStorage(storage_dir)

    # Create app
    app = FastAPI()
    views_router = create_views_router(storage)
    app.include_router(views_router)

    # Mount static (optional for tests but good for completeness)
    static_dir = Path(__file__).parent.parent / "server" / "airec" / "static"
    if static_dir.exists():
        app.mount(
            "/airec/static", StaticFiles(directory=str(static_dir)), name="airec-static"
        )

    client = TestClient(app)

    yield client, storage, storage_dir

    # Cleanup
    shutil.rmtree(storage_dir)


def test_index_redirect(test_env):
    client, _, _ = test_env
    response = client.get("/airec/")
    assert response.status_code == 200  # Follows redirect to /airec/recordings
    assert "녹취 목록" in response.text
    # Check if we landed on recordings page
    assert response.url.path == "/airec/recordings"


def test_recordings_empty(test_env):
    client, _, _ = test_env
    response = client.get("/airec/recordings")
    assert response.status_code == 200
    assert "녹취 데이터가 없습니다" in response.text


def test_recordings_with_data(test_env):
    client, storage, _ = test_env

    # Create dummy recording
    agent_id = "agent_007"
    rec_no = 12345
    filename = f"rec_{rec_no}.wav"
    storage.store(agent_id, rec_no, b"dummy wav content", filename)

    # Query with today's date (storage.store uses today)
    today = datetime.now().strftime("%Y%m%d")
    response = client.get(f"/airec/recordings?date={today}")

    assert response.status_code == 200
    assert agent_id in response.text
    assert filename in response.text
    assert "12345" in response.text


def test_recording_detail_found(test_env):
    client, storage, _ = test_env

    agent_id = "agent_008"
    rec_no = 999
    filename = f"rec_{rec_no}.wav"
    storage.store(agent_id, rec_no, b"dummy wav content", filename)

    response = client.get(f"/airec/recording/{rec_no}")
    assert response.status_code == 200
    assert f"녹취 상세 #{rec_no}" in response.text
    assert agent_id in response.text


def test_recording_detail_not_found(test_env):
    client, _, _ = test_env
    response = client.get("/airec/recording/999999")
    # Our implementation returns HTML 404
    assert response.status_code == 404
    assert "Recording not found" in response.text


def test_dashboard_empty(test_env):
    client, _, _ = test_env
    response = client.get("/airec/dashboard")
    assert response.status_code == 200
    assert "대시보드" in response.text
    assert "0" in response.text  # Zero total


def test_dashboard_stats(test_env):
    client, storage, _ = test_env

    # Store multiple files
    storage.store("agent_A", 1, b"data", "rec_1.wav")
    storage.store("agent_A", 2, b"data", "rec_2.wav")
    storage.store("agent_B", 3, b"data", "rec_3.wav")

    today = datetime.now().strftime("%Y%m%d")
    response = client.get(f"/airec/dashboard?date={today}")

    assert response.status_code == 200
    # Check total count (3)
    assert "3" in response.text  # 3 total recordings
    # Check agent stats
    assert "agent_A" in response.text
    assert "agent_B" in response.text
    # agent_A has 2, agent_B has 1
    # We can check if "2" appears near agent_A or just generally in the table


def test_pagination(test_env):
    client, storage, _ = test_env

    # Create 60 recordings
    agent_id = "bulk_agent"
    for i in range(60):
        storage.store(agent_id, 1000 + i, b"x", f"rec_{1000 + i}.wav")

    today = datetime.now().strftime("%Y%m%d")

    # Page 1 (default 50)
    response = client.get(f"/airec/recordings?date={today}&per_page=50")
    assert response.status_code == 200
    assert "총 60건" in response.text
    assert "1/2 페이지" in response.text

    # Page 2
    response = client.get(f"/airec/recordings?date={today}&page=2&per_page=50")
    assert response.status_code == 200
    assert "rec_1050.wav" in response.text  # Assuming order
