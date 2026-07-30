import asyncio
import logging

from backend.config import Settings
from backend.db import JobStore
from backend.models import JobStatus
from backend.pipeline import Pipeline

logger = logging.getLogger(__name__)


class Worker:
    """Single cooperative worker consuming persistent SQLite jobs."""

    def __init__(self, settings: Settings, store: JobStore, pipeline: Pipeline):
        self.settings = settings
        self.store = store
        self.pipeline = pipeline
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        self.store.recover_incomplete()
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
