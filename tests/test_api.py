from pathlib import Path

from fastapi.testclient import TestClient

from backend.api import create_app
from backend.config import Settings


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        database_path=tmp_path / "test.sqlite3",
        worker_poll_seconds=60,
        max_upload_bytes=20,
    )


def test_create_list_and_get_job(tmp_path: Path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        response = client.post(
            "/jobs", files={"file": ("recording.wav", b"not-a-real-wave", "audio/wav")}
        )
        assert response.status_code == 201
        job_id = response.json()["id"]

        listing = client.get("/jobs")
        assert listing.status_code == 200
        assert listing.json()["total"] == 1
        assert client.get(f"/jobs/{job_id}").status_code == 200


def test_cors_allows_configured_frontend(tmp_path: Path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        response = client.options(
            "/jobs",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_deployment_discloses_ephemeral_storage(tmp_path: Path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        response = client.get("/deployment")
        assert response.status_code == 200
        assert response.json()["persistent"] is False
        assert response.json()["storage"] == "ephemeral"


def test_upload_validation(tmp_path: Path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        assert (
            client.post("/jobs", files={"file": ("notes.txt", b"text", "text/plain")}).status_code
            == 415
        )
        assert (
            client.post("/jobs", files={"file": ("empty.wav", b"", "audio/wav")}).status_code
            == 400
        )
        assert (
            client.post(
                "/jobs", files={"file": ("large.wav", b"x" * 21, "audio/wav")}
            ).status_code
            == 413
        )


def test_active_job_limit(tmp_path: Path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        first = client.post(
            "/jobs", files={"file": ("first.wav", b"audio", "audio/wav")}
        )
        assert first.status_code == 201
        second = client.post(
            "/jobs", files={"file": ("second.wav", b"audio", "audio/wav")}
        )
        assert second.status_code == 429


def test_markdown_save_read_and_docx(tmp_path: Path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        created = client.post(
            "/jobs", files={"file": ("recording.wav", b"content", "audio/wav")}
        ).json()
        job_id = created["id"]
        assert client.put(
            f"/jobs/{job_id}/markdown", json={"markdown": "# Edited\n\nHello"}
        ).status_code == 200
        assert client.get(f"/jobs/{job_id}/markdown").text == "# Edited\n\nHello"
        assert client.post(f"/jobs/{job_id}/docx").status_code == 200
        assert client.get(f"/jobs/{job_id}/download/docx").status_code == 200
