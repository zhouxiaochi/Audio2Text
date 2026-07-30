from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    data_dir: Path = Path("data")
    database_path: Path | None = None
    remote_base_url: str = "https://openrouter.ai/api/v1"
    remote_api_key: str = ""
    transcription_model: str = "openai/whisper-large-v3"
    speaker_model: str = "openai/gpt-5-mini"
    llm_model: str = "openai/gpt-5"
    frontend_origin: str = "http://localhost:3000"
    llm_batch_characters: int = 24000
    max_upload_bytes: int = 500 * 1024 * 1024
    allowed_extensions: set[str] = Field(
        default_factory=lambda: {
            ".mp3",
            ".wav",
            ".m4a",
            ".mp4",
            ".mov",
            ".webm",
            ".ogg",
            ".flac",
            ".aac",
            ".mkv",
        }
    )
    chunk_seconds: int = 480
    overlap_seconds: int = 5
    request_timeout_seconds: float = 180.0
    remote_max_retries: int = 4
    worker_poll_seconds: float = 0.5
    max_active_jobs: int = 1
    retention_hours: int = 24

    @property
    def db_path(self) -> Path:
        return self.database_path or self.data_dir / "audio2text.sqlite3"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    def prepare(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.prepare()
    return settings
