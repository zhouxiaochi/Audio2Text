import asyncio
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse

from backend.config import Settings, get_settings
from backend.db import JobStore
from backend.documents import render_docx
from backend.models import JobCreateResponse, JobList, JobRecord, MarkdownPayload, Message
from backend.pipeline import Pipeline
from backend.remote import RemoteClient
from backend.worker import Worker


def safe_filename(filename: str) -> str:
    name = Path(filename).name
    return re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]", "_", name)[:180] or "upload"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    settings.prepare()
    store = JobStore(settings.db_path)
    store.initialize()
    remote = RemoteClient(settings)
    pipeline = Pipeline(settings, store, remote)
    worker = Worker(settings, store, pipeline)
    app.state.store = store
    app.state.remote = remote
    app.state.worker = worker
    task = asyncio.create_task(worker.run(), name="audio2text-worker")
    try:
        yield
    finally:
        worker.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await remote.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="Audio2Text API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings or get_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[app.state.settings.frontend_origin],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def store(request: Request) -> JobStore:
        return request.app.state.store

    def get_job_or_404(job_id: str, db: JobStore) -> JobRecord:
        try:
            return db.get_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc

    @app.get("/health", response_model=Message)
    async def health() -> Message:
        return Message(message="ok")

    @app.get("/deployment", response_model=dict[str, str | bool | int])
    async def deployment(request: Request) -> dict[str, str | bool | int]:
        settings: Settings = request.app.state.settings
        return {
            "storage": "ephemeral",
            "persistent": False,
            "retention_hours": settings.retention_hours,
            "max_active_jobs": settings.max_active_jobs,
        }

    @app.post("/jobs", response_model=JobCreateResponse, status_code=201)
    async def create_job(
        file: UploadFile = File(...),
        db: JobStore = Depends(store),
        request: Request = None,
    ) -> JobCreateResponse:
        settings: Settings = request.app.state.settings
        db.cleanup_expired(settings.jobs_dir, settings.uploads_dir, settings.retention_hours)
        if db.count_active() >= settings.max_active_jobs:
            raise HTTPException(
                status_code=429,
                detail="another audio job is already queued or processing; try again later",
            )
        filename = safe_filename(file.filename or "")
        extension = Path(filename).suffix.lower()
        if extension not in settings.allowed_extensions:
            raise HTTPException(status_code=415, detail="unsupported media extension")
        job_id = str(uuid.uuid4())
        destination = settings.uploads_dir / f"{job_id}{extension}"
        total = 0
        try:
            with destination.open("xb") as output:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > settings.max_upload_bytes:
                        raise HTTPException(status_code=413, detail="upload exceeds size limit")
                    output.write(chunk)
            if total == 0:
                raise HTTPException(status_code=400, detail="empty upload")
            job = db.create_job(job_id, filename, destination)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        finally:
            await file.close()
        return JobCreateResponse(id=job.id, status=job.status)

    @app.get("/jobs", response_model=JobList)
    async def list_jobs(
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        db: JobStore = Depends(store),
    ) -> JobList:
        items, total = db.list_jobs(limit, offset)
        return JobList(items=items, total=total)

    @app.get("/jobs/{job_id}", response_model=JobRecord)
    async def get_job(job_id: str, db: JobStore = Depends(store)) -> JobRecord:
        return get_job_or_404(job_id, db)

    @app.get("/jobs/{job_id}/markdown", response_class=PlainTextResponse)
    async def get_markdown(
        job_id: str, request: Request, db: JobStore = Depends(store)
    ) -> str:
        get_job_or_404(job_id, db)
        path = request.app.state.settings.jobs_dir / job_id / "transcript.md"
        if not path.exists():
            raise HTTPException(status_code=409, detail="Markdown is not available")
        return path.read_text(encoding="utf-8")

    @app.put("/jobs/{job_id}/markdown", response_model=Message)
    async def save_markdown(
        job_id: str,
        payload: MarkdownPayload,
        request: Request,
        db: JobStore = Depends(store),
    ) -> Message:
        get_job_or_404(job_id, db)
        path = request.app.state.settings.jobs_dir / job_id / "transcript.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload.markdown, encoding="utf-8")
        return Message(message="Markdown saved")

    @app.post("/jobs/{job_id}/retry", response_model=JobRecord)
    async def retry_job(job_id: str, db: JobStore = Depends(store)) -> JobRecord:
        get_job_or_404(job_id, db)
        try:
            return db.retry(job_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/jobs/{job_id}/docx", response_model=Message)
    async def regenerate_docx(
        job_id: str, request: Request, db: JobStore = Depends(store)
    ) -> Message:
        get_job_or_404(job_id, db)
        job_dir = request.app.state.settings.jobs_dir / job_id
        markdown_path = job_dir / "transcript.md"
        if not markdown_path.exists():
            raise HTTPException(status_code=409, detail="Markdown is not available")
        render_docx(markdown_path.read_text(encoding="utf-8"), job_dir / "transcript.docx")
        return Message(message="DOCX regenerated")

    @app.get("/jobs/{job_id}/download/{format}")
    async def download(
        job_id: str,
        format: str,
        request: Request,
        db: JobStore = Depends(store),
    ) -> FileResponse:
        job = get_job_or_404(job_id, db)
        formats = {
            "json": ("transcript.json", "application/json"),
            "md": ("transcript.md", "text/markdown"),
            "docx": (
                "transcript.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
        }
        if format not in formats:
            raise HTTPException(status_code=404, detail="unsupported download format")
        filename, media_type = formats[format]
        path = request.app.state.settings.jobs_dir / job_id / filename
        if not path.exists():
            raise HTTPException(status_code=409, detail=f"{format} artifact is not available")
        stem = Path(job.original_filename).stem
        return FileResponse(path, media_type=media_type, filename=f"{stem}.{format}")

    return app


app = create_app()
