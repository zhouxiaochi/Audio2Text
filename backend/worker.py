import asyncio
import logging

from backend.config import Settings
from backend.db import JobStore
from backend.models import JobStatus
from backend.pipeline import Pipeline
from backend.storage import Storage

logger = logging.getLogger(__name__)


class Worker:
    """Single cooperative worker consuming persistent MongoDB jobs."""

    def __init__(
        self, settings: Settings, store: JobStore, pipeline: Pipeline, storage: Storage
    ):
        self.settings = settings
        self.store = store
        self.pipeline = pipeline
        self.storage = storage
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        for job in self.store.list_incomplete():
            source_exists = bool(
                job.source_object_key
                and self.storage.exists(job.source_object_key, job.user_id, job.id)
            )
            self.store.update_job(
                job.id,
                status=JobStatus.QUEUED if source_exists else JobStatus.FAILED,
                error=None
                if source_exists
                else "持久存储中的源文件已丢失，请重新上传。",
            )
        while not self._stop.is_set():
            job = self.store.claim_next()
            if job is None:
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self.settings.worker_poll_seconds
                    )
                except TimeoutError:
                    pass
                continue
            try:
                await self.pipeline.run(job)
            except asyncio.CancelledError:
                self.store.update_job(job.id, status=JobStatus.QUEUED)
                raise
            except Exception as exc:
                logger.exception("job %s failed", job.id)
                self.store.update_job(
                    job.id,
                    status=JobStatus.FAILED,
                    error=str(exc)[:4000],
                )
