import asyncio
import re
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse

from backend.config import Settings, get_settings
from backend.db import JobStore
from backend.documents import render_docx
from backend.auth import COOKIE_NAME, create_session, current_user, password_hash, require_admin
from backend.models import (
    AuthCredentials,
    CreditTopUp,
    JobCreateResponse,
    JobList,
    JobPublic,
    JobRecord,
    JobStatus,
    MarkdownPayload,
    Message,
    UserPublic,
)
from backend.pipeline import Pipeline
from backend.remote import RemoteClient
from backend.storage import (
    LocalStorage,
    SpacesStorage,
    Storage,
    artifact_object_key,
    source_object_key,
)
from backend.worker import Worker


def safe_filename(filename: str) -> str:
    name = Path(filename).name
    return re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]", "_", name)[:180] or "upload"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    settings.prepare()
    store = getattr(app.state, "store", None) or JobStore(
        settings.mongodb_uri, settings.mongodb_database
    )
    store.initialize()
    storage = getattr(app.state, "storage", None)
    if storage is None:
        if settings.spaces_enabled:
            storage = SpacesStorage(
                settings.spaces_endpoint,
                settings.spaces_region,
                settings.spaces_bucket,
                settings.spaces_access_key_id,
                settings.spaces_secret_access_key,
            )
        else:
            storage = LocalStorage(settings.data_dir / "storage")
    if settings.admin_initial_username and settings.admin_initial_password:
        if store.get_user_by_username(settings.admin_initial_username) is None:
            store.create_user(
                settings.admin_initial_username,
                password_hash.hash(settings.admin_initial_password),
                role="admin",
            )
    remote = RemoteClient(settings)
    pipeline = Pipeline(settings, store, remote, storage)
    worker = Worker(settings, store, pipeline, storage)
    app.state.store = store
    app.state.storage = storage
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
        store.close()


def create_app(
    settings: Settings | None = None,
    storage: Storage | None = None,
) -> FastAPI:
    app = FastAPI(title="Audio2Text API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings or get_settings()
    if storage is not None:
        app.state.storage = storage
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[app.state.settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def store(request: Request) -> JobStore:
        return request.app.state.store

    def get_job_or_404(job_id: str, db: JobStore, user: dict) -> JobRecord:
        try:
            job = (
                db.get_job(job_id)
                if user["role"] == "admin"
                else db.get_user_job(str(user["_id"]), job_id)
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="任务不存在或已过期。") from exc
        return job

    @app.get("/health", response_model=Message)
    async def health() -> Message:
        return Message(message="ok")

    @app.get("/deployment", response_model=dict[str, str | bool | int])
    async def deployment(request: Request) -> dict[str, str | bool | int]:
        settings: Settings = request.app.state.settings
        storage: Storage = request.app.state.storage
        return {
            "storage": storage.name,
            "persistent": storage.persistent,
            "retention_hours": settings.retention_hours,
            "max_active_jobs": settings.max_active_jobs,
        }

    @app.post("/auth/register", response_model=UserPublic, status_code=201)
    async def register(credentials: AuthCredentials, request: Request) -> UserPublic:
        try:
            user = request.app.state.store.create_user(
                credentials.username, password_hash.hash(credentials.password)
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return UserPublic.model_validate(user)

    @app.post("/auth/login", response_model=UserPublic)
    async def login(credentials: AuthCredentials, request: Request, response: Response) -> UserPublic:
        db: JobStore = request.app.state.store
        user = db.get_user_by_username(credentials.username)
        if user is None or not password_hash.verify(credentials.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="用户名或密码错误。")
        token, expires_at = create_session(db, str(user["_id"]), request.app.state.settings)
        response.set_cookie(
            COOKIE_NAME,
            token,
            httponly=True,
            secure=bool(request.app.state.settings.session_secret),
            samesite="lax",
            expires=expires_at,
            path="/",
        )
        return UserPublic.model_validate(db._public_user(user))

    @app.post("/auth/logout", response_model=Message)
    async def logout(request: Request, response: Response) -> Message:
        token = request.cookies.get(COOKIE_NAME)
        if token:
            request.app.state.store.revoke_session(token)
        response.delete_cookie(COOKIE_NAME, path="/")
        return Message(message="已退出登录。")

    @app.get("/auth/me", response_model=UserPublic)
    async def me(request: Request) -> UserPublic:
        return UserPublic.model_validate(request.app.state.store._public_user(current_user(request)))

    @app.get("/admin/summary")
    async def admin_summary(request: Request) -> dict:
        require_admin(request)
        return request.app.state.store.admin_summary()

    @app.get("/admin/users")
    async def admin_users(
        request: Request, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)
    ) -> dict:
        require_admin(request)
        items, total = request.app.state.store.list_users(limit, offset)
        return {"items": items, "total": total}

    @app.get("/admin/usage")
    async def admin_usage(
        request: Request, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)
    ) -> dict:
        require_admin(request)
        items, total = request.app.state.store.list_usage_events(limit, offset)
        return {"items": items, "total": total}

    @app.post("/admin/credits", response_model=UserPublic)
    async def admin_top_up(payload: CreditTopUp, request: Request) -> UserPublic:
        admin = require_admin(request)
        try:
            user = request.app.state.store.top_up(
                payload.username,
                payload.amount_usd,
                payload.note,
                str(admin["_id"]),
                payload.idempotency_key,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="用户不存在。") from exc
        return UserPublic.model_validate(user)

    @app.post("/jobs", response_model=JobCreateResponse, status_code=201)
    async def create_job(
        file: UploadFile = File(...),
        db: JobStore = Depends(store),
        request: Request = None,
    ) -> JobCreateResponse:
        settings: Settings = request.app.state.settings
        user = current_user(request)
        user_id = str(user["_id"])
        if not db.has_minimum_credit(user_id, settings.minimum_upload_credit_usd):
            raise HTTPException(
                status_code=402,
                detail=f"余额不足，上传任务至少需要 ${settings.minimum_upload_credit_usd:.2f} 额度。",
            )
        if db.count_active(user_id) >= settings.max_active_jobs:
            raise HTTPException(
                status_code=429,
                detail="another audio job is already queued or processing; try again later",
            )
        filename = safe_filename(file.filename or "")
        extension = Path(filename).suffix.lower()
        if extension not in settings.allowed_extensions:
            raise HTTPException(status_code=415, detail="unsupported media extension")
        job_id = str(uuid.uuid4())
        destination = settings.uploads_dir / f"{job_id}{extension}.tmp"
        key = source_object_key(user_id, job_id, extension)
        storage: Storage = request.app.state.storage
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
            storage.put_file(key, destination, user_id, job_id)
            try:
                job = db.create_job(job_id, user_id, filename, key)
            except Exception:
                storage.delete_prefix(user_id, job_id)
                raise
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        finally:
            destination.unlink(missing_ok=True)
            await file.close()
        return JobCreateResponse(id=job.id, status=job.status)

    @app.get("/jobs", response_model=JobList)
    async def list_jobs(
        request: Request,
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        db: JobStore = Depends(store),
    ) -> JobList:
        user = current_user(request)
        items, total = db.list_jobs(
            None if user["role"] == "admin" else str(user["_id"]), limit, offset
        )
        return JobList(items=items, total=total)

    @app.get("/jobs/{job_id}", response_model=JobPublic)
    async def get_job(
        job_id: str, request: Request, db: JobStore = Depends(store)
    ) -> JobRecord:
        return get_job_or_404(job_id, db, current_user(request))

    @app.get("/jobs/{job_id}/markdown", response_class=PlainTextResponse)
    async def get_markdown(
        job_id: str, request: Request, db: JobStore = Depends(store)
    ) -> str:
        job = get_job_or_404(job_id, db, current_user(request))
        key = job.artifacts.get("md") or artifact_object_key(job.user_id, job.id, "md")
        storage: Storage = request.app.state.storage
        if not storage.exists(key, job.user_id, job.id):
            raise HTTPException(status_code=409, detail="Markdown is not available")
        return storage.get_bytes(key, job.user_id, job.id).decode("utf-8")

    @app.put("/jobs/{job_id}/markdown", response_model=Message)
    async def save_markdown(
        job_id: str,
        payload: MarkdownPayload,
        request: Request,
        db: JobStore = Depends(store),
    ) -> Message:
        job = get_job_or_404(job_id, db, current_user(request))
        key = artifact_object_key(job.user_id, job.id, "md")
        request.app.state.storage.put_bytes(
            key, payload.markdown.encode("utf-8"), job.user_id, job.id
        )
        db.update_job(job.id, artifacts={**job.artifacts, "md": key})
        return Message(message="Markdown saved")

    @app.post("/jobs/{job_id}/retry", response_model=JobPublic)
    async def retry_job(job_id: str, request: Request, db: JobStore = Depends(store)) -> JobRecord:
        get_job_or_404(job_id, db, current_user(request))
        try:
            return db.retry(job_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/jobs/{job_id}/docx", response_model=Message)
    async def regenerate_docx(
        job_id: str, request: Request, db: JobStore = Depends(store)
    ) -> Message:
        job = get_job_or_404(job_id, db, current_user(request))
        storage: Storage = request.app.state.storage
        markdown_key = job.artifacts.get("md") or artifact_object_key(
            job.user_id, job.id, "md"
        )
        if not storage.exists(markdown_key, job.user_id, job.id):
            raise HTTPException(status_code=409, detail="Markdown is not available")
        job_dir = request.app.state.settings.jobs_dir / job_id
        docx_path = job_dir / "transcript.docx"
        render_docx(
            storage.get_bytes(markdown_key, job.user_id, job.id).decode("utf-8"),
            docx_path,
        )
        docx_key = artifact_object_key(job.user_id, job.id, "docx")
        storage.put_file(docx_key, docx_path, job.user_id, job.id)
        db.update_job(job.id, artifacts={**job.artifacts, "docx": docx_key})
        return Message(message="DOCX regenerated")

    @app.get("/jobs/{job_id}/download/{format}")
    async def download(
        job_id: str,
        format: str,
        request: Request,
        db: JobStore = Depends(store),
    ) -> StreamingResponse:
        job = get_job_or_404(job_id, db, current_user(request))
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
        _, media_type = formats[format]
        key = job.artifacts.get(format) or artifact_object_key(job.user_id, job.id, format)
        storage: Storage = request.app.state.storage
        if not storage.exists(key, job.user_id, job.id):
            raise HTTPException(status_code=409, detail=f"{format} artifact is not available")
        stem = Path(job.original_filename).stem
        download_name = f"{stem}.{format}"
        return StreamingResponse(
            iter([storage.get_bytes(key, job.user_id, job.id)]),
            media_type=media_type,
            headers={
                "Content-Disposition": (
                    f"attachment; filename=transcript.{format}; "
                    f"filename*=UTF-8''{quote(download_name)}"
                )
            },
        )

    @app.delete("/jobs/{job_id}", response_model=Message)
    async def delete_job(
        job_id: str, request: Request, db: JobStore = Depends(store)
    ) -> Message:
        job = get_job_or_404(job_id, db, current_user(request))
        if job.status in {JobStatus.QUEUED, JobStatus.PROCESSING}:
            raise HTTPException(status_code=409, detail="任务处理中，无法删除。")
        db.update_job(job.id, status=JobStatus.DELETING)
        try:
            request.app.state.storage.delete_prefix(job.user_id, job.id)
            db.delete_job_records(job.id)
            shutil.rmtree(request.app.state.settings.jobs_dir / job.id, ignore_errors=True)
        except Exception:
            db.update_job(job.id, status=job.status)
            raise
        return Message(message="任务已删除。")

    return app


app = create_app()
