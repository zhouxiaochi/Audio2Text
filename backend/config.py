from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, populate_by_name=True
    )

    data_dir: Path = Path("data")
    mongodb_uri: str = ""
    mongodb_database: str = "audio2text"
    session_secret: str = ""
    session_days: int = 14
    minimum_upload_credit_usd: float = 1.0
    admin_initial_username: str = ""
    admin_initial_password: str = ""
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
    spaces_endpoint: str = ""
    spaces_region: str = ""
    spaces_bucket: str = ""
    spaces_access_key_id: str = ""
    spaces_secret_access_key: str = ""
    spaces_presigned_url_ttl_seconds: int = 600

    @property
    def spaces_enabled(self) -> bool:
        return all(
            (
                self.spaces_endpoint,
                self.spaces_region,
                self.spaces_bucket,
                self.spaces_access_key_id,
                self.spaces_secret_access_key,
            )
        )

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


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.prepare()
    return settings
