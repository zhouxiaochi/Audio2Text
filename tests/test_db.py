from pathlib import Path

import mongomock
import pytest

from backend.db import JobStore
from backend.models import JobStatus


def test_job_lifecycle_and_checkpoint(tmp_path: Path):
    store = JobStore("mongodb://test", "audio2text", client=mongomock.MongoClient())
    store.initialize()
    source = tmp_path / "sample.wav"
    source.write_bytes(b"audio")
    job = store.create_job("job-1", "user-1", "sample.wav", source)

    assert job.status == JobStatus.QUEUED
    assert store.claim_next().id == job.id
    assert store.get_job(job.id).status == JobStatus.PROCESSING

    store.put_checkpoint(job.id, "chunk:0", {"text": "hello"})
    assert store.get_checkpoint(job.id, "chunk:0") == {"text": "hello"}

    assert store.recover_incomplete() == 1
    assert store.get_job(job.id).status == JobStatus.QUEUED


def test_retry_rejects_active_job(tmp_path: Path):
    store = JobStore("mongodb://test", "audio2text", client=mongomock.MongoClient())
    store.initialize()
    source = tmp_path / "sample.wav"
    source.write_bytes(b"audio")
    store.create_job("job-1", "user-1", "sample.wav", source)

    with pytest.raises(ValueError):
        store.retry("job-1")
