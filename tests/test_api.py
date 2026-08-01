from pathlib import Path

import mongomock
import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.config import Settings
from backend.db import JobStore
from backend.models import JobStatus
from backend.storage import LocalStorage


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        mongodb_uri="mongodb://test",
        mongodb_database="audio2text",
        session_secret="test-secret",
        worker_poll_seconds=60,
        max_upload_bytes=20,
        minimum_upload_credit_usd=0,
    )


def make_app(tmp_path: Path):
    app = create_app(make_settings(tmp_path), storage=LocalStorage(tmp_path / "objects"))
    store = JobStore("mongodb://test", "audio2text", client=mongomock.MongoClient())
    store.initialize()
    app.state.store = store
    return app


def authenticate(client: TestClient) -> None:
    assert client.post(
        "/auth/register", json={"username": "tester", "password": "strong-password"}
    ).status_code == 201
    assert client.post(
        "/auth/login", json={"username": "tester", "password": "strong-password"}
    ).status_code == 200


def test_create_list_and_get_job(tmp_path: Path):
    app = make_app(tmp_path)
    with TestClient(app, base_url="https://testserver") as client:
        authenticate(client)
        response = client.post(
            "/jobs", files={"file": ("recording.wav", b"not-a-real-wave", "audio/wav")}
        )
        assert response.status_code == 201
        job_id = response.json()["id"]

        listing = client.get("/jobs")
        assert listing.status_code == 200
        assert listing.json()["total"] == 1
        detail = client.get(f"/jobs/{job_id}")
        assert detail.status_code == 200
        assert "source_object_key" not in detail.json()
        assert "source_path" not in detail.json()
        assert "artifacts" not in detail.json()
        assert "source_object_key" not in listing.json()["items"][0]
        assert "artifacts" not in listing.json()["items"][0]


def test_cors_allows_configured_frontend(tmp_path: Path):
    app = make_app(tmp_path)
    with TestClient(app, base_url="https://testserver") as client:
        response = client.options(
            "/jobs",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_deployment_discloses_local_storage(tmp_path: Path):
    app = make_app(tmp_path)
    with TestClient(app, base_url="https://testserver") as client:
        response = client.get("/deployment")
        assert response.status_code == 200
        assert response.json()["persistent"] is False
        assert response.json()["storage"] == "local"


def test_upload_validation(tmp_path: Path):
    app = make_app(tmp_path)
    with TestClient(app, base_url="https://testserver") as client:
        authenticate(client)
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
    app = make_app(tmp_path)
    with TestClient(app, base_url="https://testserver") as client:
        authenticate(client)
        first = client.post(
            "/jobs", files={"file": ("first.wav", b"audio", "audio/wav")}
        )
        assert first.status_code == 201
        second = client.post(
            "/jobs", files={"file": ("second.wav", b"audio", "audio/wav")}
        )
        assert second.status_code == 429


def test_markdown_save_read_and_docx(tmp_path: Path):
    app = make_app(tmp_path)
    with TestClient(app, base_url="https://testserver") as client:
        authenticate(client)
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


def test_cross_user_cannot_access_or_delete_job(tmp_path: Path):
    app = make_app(tmp_path)
    with TestClient(app, base_url="https://testserver") as owner:
        authenticate(owner)
        job_id = owner.post(
            "/jobs", files={"file": ("recording.wav", b"content", "audio/wav")}
        ).json()["id"]
        owner.post("/auth/logout")
        assert owner.post(
            "/auth/register", json={"username": "other", "password": "strong-password"}
        ).status_code == 201
        assert owner.post(
            "/auth/login", json={"username": "other", "password": "strong-password"}
        ).status_code == 200

        assert owner.get(f"/jobs/{job_id}").status_code == 404
        assert owner.get(f"/jobs/{job_id}/markdown").status_code == 404
        assert owner.put(
            f"/jobs/{job_id}/markdown", json={"markdown": "stolen"}
        ).status_code == 404
        assert owner.get(f"/jobs/{job_id}/download/md").status_code == 404
        assert owner.delete(f"/jobs/{job_id}").status_code == 404


def test_delete_completed_job_removes_storage_and_records(tmp_path: Path):
    app = make_app(tmp_path)
    with TestClient(app, base_url="https://testserver") as client:
        authenticate(client)
        job_id = client.post(
            "/jobs", files={"file": ("recording.wav", b"content", "audio/wav")}
        ).json()["id"]
        job = app.state.store.get_job(job_id)
        app.state.store.update_job(job_id, status=JobStatus.COMPLETED)

        assert client.delete(f"/jobs/{job_id}").status_code == 200
        assert client.get(f"/jobs/{job_id}").status_code == 404
        assert not app.state.storage.exists(
            job.source_object_key, job.user_id, job.id
        )


def test_upload_db_failure_compensates_storage(tmp_path: Path, monkeypatch):
    app = make_app(tmp_path)
    with TestClient(app, base_url="https://testserver") as client:
        authenticate(client)
        deleted: list[tuple[str, str]] = []
        original_delete = app.state.storage.delete_prefix

        def record_delete(user_id: str, job_id: str) -> None:
            deleted.append((user_id, job_id))
            original_delete(user_id, job_id)

        monkeypatch.setattr(app.state.storage, "delete_prefix", record_delete)

        def fail_create(*args, **kwargs):
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(app.state.store, "create_job", fail_create)
        with pytest.raises(RuntimeError):
            client.post(
                "/jobs", files={"file": ("recording.wav", b"content", "audio/wav")}
            )
        assert len(deleted) == 1


def test_delete_rejects_active_job(tmp_path: Path):
    app = make_app(tmp_path)
    with TestClient(app, base_url="https://testserver") as client:
        authenticate(client)
        job_id = client.post(
            "/jobs", files={"file": ("recording.wav", b"content", "audio/wav")}
        ).json()["id"]

        assert client.delete(f"/jobs/{job_id}").status_code == 409
        assert client.get(f"/jobs/{job_id}").status_code == 200
