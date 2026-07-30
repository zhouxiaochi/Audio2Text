from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Stage(StrEnum):
    QUEUED = "queued"
    PROBING = "probing"
    NORMALIZING = "normalizing"
    CHUNKING = "chunking"
    TRANSCRIBING = "transcribing"
    MERGING = "merging"
    SPEAKERS = "speakers"
    TRANSLATING = "translating"
    RENDERING = "rendering"
    COMPLETED = "completed"


class WordTimestamp(BaseModel):
    word: str
    start: float = Field(ge=0)
    end: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_interval(self) -> "WordTimestamp":
        if self.end < self.start:
            raise ValueError("word end must be greater than or equal to start")
        return self


class TranscriptSegment(BaseModel):
    id: int | None = None
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str = Field(min_length=1)
    speaker: str | None = None
    translation_zh: str | None = None
    words: list[WordTimestamp] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_interval(self) -> "TranscriptSegment":
        if self.end < self.start:
            raise ValueError("segment end must be greater than or equal to start")
        return self


class SpeakerTurn(BaseModel):
    segment_id: int = Field(ge=0)
    speaker: str = Field(min_length=1, max_length=80)


class SpeakerInference(BaseModel):
    turns: list[SpeakerTurn]


class TranslationItem(BaseModel):
    segment_id: int = Field(ge=0)
    translation_zh: str = Field(min_length=1)


class TranslationResult(BaseModel):
    translations: list[TranslationItem]


class JobRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    original_filename: str
    source_path: str
    status: JobStatus
    stage: str
    progress: float = Field(ge=0, le=1)
    error: str | None = None
    retry_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class JobList(BaseModel):
    items: list[JobRecord]
    total: int


class MarkdownPayload(BaseModel):
    markdown: str


class JobCreateResponse(BaseModel):
    id: str
    status: JobStatus


class Message(BaseModel):
    message: str
