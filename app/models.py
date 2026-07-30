from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Document(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    filename: str
    stored_path: str  # internal location of the original upload, never exposed
    text: str
    word_count: int
    created_at: datetime = Field(default_factory=_utcnow)


class DocumentSummary(SQLModel):
    id: int
    filename: str
    word_count: int
    created_at: datetime


class DocumentDetail(DocumentSummary):
    text: str


class Translation(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="document.id", index=True)
    language_code: str  # catalog code, e.g. fr-FR
    translate_code: str  # what AWS Translate was asked for, e.g. fr
    detected_source: str | None = None
    text: str
    edited: bool = False
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class TranslationSummary(SQLModel):
    id: int
    document_id: int
    language_code: str
    edited: bool
    created_at: datetime
    updated_at: datetime


class TranslationDetail(TranslationSummary):
    translate_code: str
    detected_source: str | None
    text: str


class TranslationCreate(SQLModel):
    language_code: str


class TranslationUpdate(SQLModel):
    text: str


class SynthesisJob(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    translation_id: int = Field(foreign_key="translation.id", index=True)
    voice_id: str
    engine: str
    language_code: str
    status: str = "queued"  # queued, running, completed, failed
    error: str | None = None
    total_chunks: int = 0
    done_chunks: int = 0
    audio_path: str | None = None  # internal location, never exposed
    duration_seconds: float | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class SynthesisJobOut(SQLModel):
    id: int
    translation_id: int
    voice_id: str
    engine: str
    language_code: str
    status: str
    error: str | None
    total_chunks: int
    done_chunks: int
    duration_seconds: float | None
    created_at: datetime
    updated_at: datetime


class SynthesisJobCreate(SQLModel):
    voice_id: str


class TranslationWithJobs(TranslationDetail):
    jobs: list[SynthesisJobOut] = []


class DocumentOverview(DocumentDetail):
    translations: list[TranslationWithJobs] = []
