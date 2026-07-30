from pathlib import Path

import pytest

from backend.db import JobStore
from backend.models import JobStatus


def test_job_lifecycle_and_checkpoint(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    job = store.create_job("job-1", "sample.wav", tmp_path / "sample.wav")

    assert job.status == JobStatus.QUEUED
    assert store.claim_next().id == job.id
    assert store.get_job(job.id).status == JobStatus.PROCESSING

    store.put_checkpoint(job.id, "chunk:0", {"text": "hello"})
    assert store.get_checkpoint(job.id, "chunk:0") == {"text": "hello"}

    assert store.recover_incomplete() == 1
    assert store.get_job(job.id).status == JobStatus.QUEUED


def test_retry_rejects_active_job(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_job("job-1", "sample.wav", tmp_path / "sample.wav")

    with pytest.raises(ValueError):
        store.retry("job-1")
