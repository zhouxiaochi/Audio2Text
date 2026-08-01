from pathlib import Path

import pytest

from backend.storage import (
    LocalStorage,
    artifact_object_key,
    job_prefix,
    source_object_key,
)


def test_local_storage_upload_download_and_delete(tmp_path: Path):
    storage = LocalStorage(tmp_path / "objects")
    source = tmp_path / "sample.wav"
    source.write_bytes(b"audio")
    key = source_object_key("user-1", "job-1", ".wav")

    storage.put_file(key, source, "user-1", "job-1")
    assert storage.exists(key, "user-1", "job-1")
    assert storage.get_bytes(key, "user-1", "job-1") == b"audio"

    destination = tmp_path / "download.wav"
    storage.download_file(key, destination, "user-1", "job-1")
    assert destination.read_bytes() == b"audio"

    storage.put_bytes(
        artifact_object_key("user-1", "job-1", "md"),
        b"# transcript",
        "user-1",
        "job-1",
    )
    storage.delete_prefix("user-1", "job-1")
    assert not storage.exists(key, "user-1", "job-1")


def test_keys_are_server_generated_and_isolated(tmp_path: Path):
    assert job_prefix("user-1", "job-1") == "v1/users/user-1/jobs/job-1/"
    assert source_object_key("user-1", "job-1", ".mp3").endswith(
        "/source/original.mp3"
    )
    assert artifact_object_key("user-1", "job-1", "docx").endswith(
        "/artifacts/transcript.docx"
    )

    storage = LocalStorage(tmp_path)
    key = source_object_key("user-1", "job-1", ".wav")
    with pytest.raises(ValueError):
        storage.put_bytes(key, b"audio", "user-2", "job-1")
    with pytest.raises(ValueError):
        storage.put_bytes(
            "v1/users/user-1/jobs/job-1/../../secret",
            b"audio",
            "user-1",
            "job-1",
        )
