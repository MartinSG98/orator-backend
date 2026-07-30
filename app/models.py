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
