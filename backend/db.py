import json
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from backend.models import JobRecord, JobStatus, Stage, utc_now


class JobStore:
    """SQLite-backed persistent job and checkpoint store."""

    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    original_filename TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    progress REAL NOT NULL DEFAULT 0,
                    error TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_status_created
                    ON jobs(status, created_at);
                CREATE TABLE IF NOT EXISTS checkpoints (
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(job_id, key)
                );
                """
            )

    @staticmethod
    def _job(row: sqlite3.Row) -> JobRecord:
        data = dict(row)
        data["metadata"] = json.loads(data.pop("metadata_json"))
        return JobRecord.model_validate(data)

    def create_job(self, job_id: str, filename: str, source_path: Path) -> JobRecord:
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO jobs
                   (id, original_filename, source_path, status, stage, progress, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 0, ?, ?)""",
                (
                    job_id,
                    filename,
                    str(source_path),
                    JobStatus.QUEUED,
                    Stage.QUEUED,
                    now,
                    now,
                ),
            )
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> JobRecord:
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._job(row)

    def list_jobs(self, limit: int = 100, offset: int = 0) -> tuple[list[JobRecord], int]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
            total = db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        return [self._job(row) for row in rows], total

    def count_active(self) -> int:
        with self.connect() as db:
            return int(
                db.execute(
                    "SELECT COUNT(*) FROM jobs WHERE status IN (?, ?)",
                    (JobStatus.QUEUED, JobStatus.PROCESSING),
                ).fetchone()[0]
            )

    def cleanup_expired(self, jobs_dir: Path, uploads_dir: Path, retention_hours: int) -> int:
        """Delete terminal jobs and artifacts older than the configured demo retention."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=retention_hours)).isoformat()
        with self.connect() as db:
            rows = db.execute(
                """SELECT id, source_path FROM jobs
                   WHERE status IN (?, ?) AND updated_at < ?""",
                (JobStatus.COMPLETED, JobStatus.FAILED, cutoff),
            ).fetchall()
            for row in rows:
                Path(row["source_path"]).unlink(missing_ok=True)
                shutil.rmtree(jobs_dir / row["id"], ignore_errors=True)
            if rows:
                db.executemany("DELETE FROM jobs WHERE id = ?", [(row["id"],) for row in rows])
        return len(rows)

    def update_job(self, job_id: str, **values: Any) -> None:
        allowed = {"status", "stage", "progress", "error", "retry_count", "metadata_json"}
        values = {key: value for key, value in values.items() if key in allowed}
        if not values:
            return
        values["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self.connect() as db:
            db.execute(
                f"UPDATE jobs SET {assignments} WHERE id = ?",
                (*values.values(), job_id),
            )

    def claim_next(self) -> JobRecord | None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id FROM jobs WHERE status = ? ORDER BY created_at LIMIT 1",
                (JobStatus.QUEUED,),
            ).fetchone()
            if row is None:
                db.execute("COMMIT")
                return None
            now = utc_now()
            changed = db.execute(
                """UPDATE jobs SET status = ?, error = NULL, updated_at = ?
                   WHERE id = ? AND status = ?""",
                (JobStatus.PROCESSING, now, row["id"], JobStatus.QUEUED),
            ).rowcount
            db.execute("COMMIT")
        return self.get_job(row["id"]) if changed else None

    def recover_incomplete(self) -> int:
        with self.connect() as db:
            return db.execute(
                """UPDATE jobs SET status = ?, updated_at = ?
                   WHERE status = ?""",
                (JobStatus.QUEUED, utc_now(), JobStatus.PROCESSING),
            ).rowcount

    def retry(self, job_id: str) -> JobRecord:
        job = self.get_job(job_id)
        if job.status not in {JobStatus.FAILED, JobStatus.COMPLETED}:
            raise ValueError("only failed or completed jobs can be retried")
        self.update_job(
            job_id,
            status=JobStatus.QUEUED,
            error=None,
            retry_count=job.retry_count + 1,
        )
        return self.get_job(job_id)

    def put_checkpoint(self, job_id: str, key: str, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False)
        with self.connect() as db:
            db.execute(
                """INSERT INTO checkpoints(job_id, key, value_json, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(job_id, key) DO UPDATE SET
                     value_json = excluded.value_json, updated_at = excluded.updated_at""",
                (job_id, key, payload, utc_now()),
            )

    def get_checkpoint(self, job_id: str, key: str) -> Any | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT value_json FROM checkpoints WHERE job_id = ? AND key = ?", (job_id, key)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def delete_checkpoints(self, job_id: str, prefix: str | None = None) -> None:
        with self.connect() as db:
            if prefix is None:
                db.execute("DELETE FROM checkpoints WHERE job_id = ?", (job_id,))
            else:
                db.execute(
                    "DELETE FROM checkpoints WHERE job_id = ? AND key LIKE ?",
                    (job_id, f"{prefix}%"),
                )
