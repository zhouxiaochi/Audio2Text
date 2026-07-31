import hashlib
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.collection import Collection

from backend.models import JobRecord, JobStatus, Stage, utc_now


class JobStore:
    """MongoDB-backed store for jobs, checkpoints, accounts, and billing records."""

    def __init__(self, uri: str, database: str, client: Any | None = None):
        if not uri:
            raise RuntimeError("MONGODB_URI is not configured")
        self.client = client or MongoClient(uri, serverSelectionTimeoutMS=10_000)
        self.db = self.client[database]
        self.jobs: Collection = self.db.jobs
        self.checkpoints: Collection = self.db.checkpoints
        self.users: Collection = self.db.users
        self.sessions: Collection = self.db.sessions
        self.usage_events: Collection = self.db.usage_events
        self.credit_ledger: Collection = self.db.credit_ledger

    def close(self) -> None:
        self.client.close()

    def initialize(self) -> None:
        self.users.create_index("username_normalized", unique=True)
        self.sessions.create_index("token_hash", unique=True)
        self.sessions.create_index("expires_at", expireAfterSeconds=0)
        self.jobs.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
        self.jobs.create_index([("status", ASCENDING), ("created_at", ASCENDING)])
        self.checkpoints.create_index([("job_id", ASCENDING), ("key", ASCENDING)], unique=True)
        self.usage_events.create_index([("job_id", ASCENDING), ("request_id", ASCENDING)], unique=True, sparse=True)
        self.credit_ledger.create_index("idempotency_key", unique=True, sparse=True)
        self.credit_ledger.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])

    @staticmethod
    def _job(data: dict[str, Any]) -> JobRecord:
        data = dict(data)
        data.pop("_id", None)
        return JobRecord.model_validate(data)

    def create_job(self, job_id: str, user_id: str, filename: str, source_path: Path) -> JobRecord:
        now = utc_now()
        self.jobs.insert_one(
            {
                "id": job_id,
                "user_id": user_id,
                "original_filename": filename,
                "source_path": str(source_path),
                "status": JobStatus.QUEUED,
                "stage": Stage.QUEUED,
                "progress": 0,
                "error": None,
                "retry_count": 0,
                "metadata": {},
                "created_at": now,
                "updated_at": now,
            }
        )
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> JobRecord:
        row = self.jobs.find_one({"id": job_id})
        if row is None:
            raise KeyError(job_id)
        return self._job(row)

    def list_jobs(
        self, user_id: str | None = None, limit: int = 100, offset: int = 0
    ) -> tuple[list[JobRecord], int]:
        query = {"user_id": user_id} if user_id else {}
        rows = list(self.jobs.find(query).sort("created_at", DESCENDING).skip(offset).limit(limit))
        total = self.jobs.count_documents(query)
        return [self._job(row) for row in rows], total

    def count_active(self, user_id: str | None = None) -> int:
        query: dict[str, Any] = {"status": {"$in": [JobStatus.QUEUED, JobStatus.PROCESSING]}}
        if user_id:
            query["user_id"] = user_id
        return self.jobs.count_documents(query)

    def cleanup_expired(self, jobs_dir: Path, uploads_dir: Path, retention_hours: int) -> int:
        """Delete terminal jobs and artifacts older than the configured demo retention."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=retention_hours)).isoformat()
        rows = list(self.jobs.find({"status": {"$in": [JobStatus.COMPLETED, JobStatus.FAILED]}, "updated_at": {"$lt": cutoff}}))
        for row in rows:
            Path(row["source_path"]).unlink(missing_ok=True)
            shutil.rmtree(jobs_dir / row["id"], ignore_errors=True)
        if rows:
            ids = [row["id"] for row in rows]
            self.checkpoints.delete_many({"job_id": {"$in": ids}})
            self.jobs.delete_many({"id": {"$in": ids}})
        return len(rows)

    def update_job(self, job_id: str, **values: Any) -> None:
        allowed = {"status", "stage", "progress", "error", "retry_count", "metadata"}
        values = {key: value for key, value in values.items() if key in allowed}
        if not values:
            return
        values["updated_at"] = utc_now()
        self.jobs.update_one({"id": job_id}, {"$set": values})

    def claim_next(self) -> JobRecord | None:
        row = self.jobs.find_one_and_update(
            {"status": JobStatus.QUEUED},
            {"$set": {"status": JobStatus.PROCESSING, "error": None, "updated_at": utc_now()}},
            sort=[("created_at", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )
        return self._job(row) if row else None

    def recover_incomplete(self) -> int:
        rows = list(self.jobs.find({"status": {"$in": [JobStatus.QUEUED, JobStatus.PROCESSING]}}))
        recovered = 0
        for row in rows:
            source = Path(row["source_path"])
            if source.exists():
                self.jobs.update_one({"_id": row["_id"]}, {"$set": {"status": JobStatus.QUEUED, "updated_at": utc_now()}})
                recovered += 1
            else:
                self.jobs.update_one(
                    {"_id": row["_id"]},
                    {"$set": {"status": JobStatus.FAILED, "error": "服务重启后临时源文件已丢失，请重新上传。", "updated_at": utc_now()}},
                )
        return recovered

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
        self.checkpoints.update_one(
            {"job_id": job_id, "key": key},
            {"$set": {"value": value, "updated_at": utc_now()}},
            upsert=True,
        )

    def get_checkpoint(self, job_id: str, key: str) -> Any | None:
        row = self.checkpoints.find_one({"job_id": job_id, "key": key})
        return row["value"] if row else None

    def delete_checkpoints(self, job_id: str, prefix: str | None = None) -> None:
        query: dict[str, Any] = {"job_id": job_id}
        if prefix is not None:
            query["key"] = {"$regex": f"^{prefix}"}
        self.checkpoints.delete_many(query)

    @staticmethod
    def _public_user(user: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(user["_id"]),
            "username": user["username"],
            "role": user["role"],
            "balance_usd": float(user.get("balance_usd", 0)),
            "created_at": user["created_at"],
        }

    def create_user(self, username: str, password_hash: str, role: str = "user") -> dict[str, Any]:
        now = utc_now()
        try:
            result = self.users.insert_one(
                {
                    "username": username,
                    "username_normalized": username.lower(),
                    "password_hash": password_hash,
                    "role": role,
                    "balance_usd": 0.0,
                    "created_at": now,
                    "last_login_at": None,
                }
            )
        except DuplicateKeyError as exc:
            raise ValueError("用户名已被使用。") from exc
        return self._public_user(self.users.find_one({"_id": result.inserted_id}))

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        return self.users.find_one({"username_normalized": username.lower()})

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        from bson import ObjectId

        if not ObjectId.is_valid(user_id):
            return None
        return self.users.find_one({"_id": ObjectId(user_id)})

    def list_users(self, limit: int = 100, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        rows = list(self.users.find().sort("created_at", DESCENDING).skip(offset).limit(limit))
        return [self._public_user(row) for row in rows], self.users.count_documents({})

    def create_session(self, user_id: str, token: str, expires_at: datetime) -> None:
        self.sessions.insert_one(
            {
                "token_hash": hashlib.sha256(token.encode()).hexdigest(),
                "user_id": user_id,
                "expires_at": expires_at,
                "created_at": utc_now(),
                "revoked_at": None,
            }
        )

    def get_session_user(self, token: str) -> dict[str, Any] | None:
        session = self.sessions.find_one(
            {
                "token_hash": hashlib.sha256(token.encode()).hexdigest(),
                "revoked_at": None,
                "expires_at": {"$gt": datetime.now(timezone.utc)},
            }
        )
        return self.get_user(session["user_id"]) if session else None

    def revoke_session(self, token: str) -> None:
        self.sessions.update_one(
            {"token_hash": hashlib.sha256(token.encode()).hexdigest()},
            {"$set": {"revoked_at": utc_now()}},
        )

    def has_minimum_credit(self, user_id: str, minimum: float) -> bool:
        user = self.get_user(user_id)
        return bool(user and float(user.get("balance_usd", 0)) >= minimum)

    def record_usage(
        self,
        job_id: str,
        user_id: str,
        endpoint: str,
        model: str,
        usage: dict[str, Any] | None,
        raw_cost_usd: float | None,
        request_id: str | None,
    ) -> None:
        event = {
            "job_id": job_id,
            "user_id": user_id,
            "endpoint": endpoint,
            "model": model,
            "usage": usage or {},
            "raw_cost_usd": raw_cost_usd,
            "billable_usd": raw_cost_usd * 5 if raw_cost_usd is not None else None,
            "cost_status": "settled" if raw_cost_usd is not None else "pending",
            "request_id": request_id,
            "created_at": utc_now(),
        }
        try:
            self.usage_events.insert_one(event)
        except DuplicateKeyError:
            return

    def settle_job_cost(self, job_id: str, user_id: str) -> float:
        ledger_key = f"job-cost:{job_id}"
        if self.credit_ledger.find_one({"idempotency_key": ledger_key}):
            return 0.0
        amount = sum(
            float(row["billable_usd"] or 0)
            for row in self.usage_events.find({"job_id": job_id, "billable_usd": {"$ne": None}})
        )
        if amount <= 0:
            return 0.0
        self.credit_ledger.insert_one(
            {
                "user_id": user_id,
                "amount_usd": -amount,
                "kind": "usage",
                "job_id": job_id,
                "idempotency_key": ledger_key,
                "created_at": utc_now(),
            }
        )
        from bson import ObjectId

        self.users.update_one({"_id": ObjectId(user_id)}, {"$inc": {"balance_usd": -amount}})
        return amount

    def top_up(self, username: str, amount: float, note: str, admin_id: str, idempotency_key: str) -> dict[str, Any]:
        user = self.get_user_by_username(username)
        if user is None:
            raise KeyError(username)
        existing = self.credit_ledger.find_one({"idempotency_key": idempotency_key})
        if existing:
            return self._public_user(user)
        self.credit_ledger.insert_one(
            {
                "user_id": str(user["_id"]),
                "amount_usd": amount,
                "kind": "top_up",
                "note": note,
                "admin_id": admin_id,
                "idempotency_key": idempotency_key,
                "created_at": utc_now(),
            }
        )
        self.users.update_one({"_id": user["_id"]}, {"$inc": {"balance_usd": amount}})
        return self._public_user(self.users.find_one({"_id": user["_id"]}))

    def admin_summary(self) -> dict[str, Any]:
        usage = list(
            self.usage_events.aggregate(
                [{"$group": {"_id": None, "raw": {"$sum": {"$ifNull": ["$raw_cost_usd", 0]}}, "billed": {"$sum": {"$ifNull": ["$billable_usd", 0]}}, "pending": {"$sum": {"$cond": [{"$eq": ["$cost_status", "pending"]}, 1, 0]}}}}]
            )
        )
        totals = usage[0] if usage else {"raw": 0, "billed": 0, "pending": 0}
        return {
            "users": self.users.count_documents({}),
            "jobs": self.jobs.count_documents({}),
            "raw_cost_usd": float(totals["raw"]),
            "billable_usd": float(totals["billed"]),
            "pending_cost_events": int(totals["pending"]),
        }

    def list_usage_events(self, limit: int = 100, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        rows = list(
            self.usage_events.find(
                {},
                {
                    "_id": 0,
                    "user_id": 1,
                    "job_id": 1,
                    "endpoint": 1,
                    "model": 1,
                    "usage": 1,
                    "raw_cost_usd": 1,
                    "billable_usd": 1,
                    "cost_status": 1,
                    "created_at": 1,
                },
            )
            .sort("created_at", DESCENDING)
            .skip(offset)
            .limit(limit)
        )
        return rows, self.usage_events.count_documents({})
