"""Persistence behind one interface, per ADR 0007.

The methods mirror the app's access patterns rather than exposing a query
language, which is what lets a key-value store implement them later. The
SQLite implementation opens a short session per call and returns detached
objects, so callers never hold a database handle.
"""

from abc import ABC, abstractmethod
from functools import lru_cache

from sqlalchemy import func
from sqlmodel import Session, select

from app.config import get_settings
from app.db import get_engine
from app.models import Document, DocumentSummary, SynthesisJob, Translation


class Repository(ABC):
    # Documents
    @abstractmethod
    def add_document(self, document: Document) -> Document: ...

    @abstractmethod
    def get_document(self, document_id: int) -> Document | None: ...

    @abstractmethod
    def list_documents(self) -> list[DocumentSummary]:
        """Newest first, with translation and completed-audio counts."""

    @abstractmethod
    def delete_document(self, document_id: int) -> None: ...

    # Translations
    @abstractmethod
    def add_translation(self, translation: Translation) -> Translation: ...

    @abstractmethod
    def get_translation(self, translation_id: int) -> Translation | None: ...

    @abstractmethod
    def find_translation(
        self, document_id: int, language_code: str
    ) -> Translation | None: ...

    @abstractmethod
    def list_translations(self, document_id: int) -> list[Translation]:
        """Newest first."""

    @abstractmethod
    def save_translation(self, translation: Translation) -> Translation: ...

    @abstractmethod
    def delete_translation(self, translation_id: int) -> None: ...

    # Synthesis jobs
    @abstractmethod
    def add_job(self, job: SynthesisJob) -> SynthesisJob: ...

    @abstractmethod
    def get_job(self, job_id: int) -> SynthesisJob | None: ...

    @abstractmethod
    def list_jobs(self, translation_id: int | None = None) -> list[SynthesisJob]:
        """Newest first, optionally filtered by translation."""

    @abstractmethod
    def save_job(self, job: SynthesisJob) -> SynthesisJob: ...

    @abstractmethod
    def delete_job(self, job_id: int) -> None: ...


class SqliteRepository(Repository):
    def __init__(self) -> None:
        self._engine = get_engine()

    def _session(self) -> Session:
        return Session(self._engine)

    def _add(self, obj):
        with self._session() as session:
            session.add(obj)
            session.commit()
            session.refresh(obj)
        return obj

    def _save(self, obj):
        with self._session() as session:
            merged = session.merge(obj)
            session.commit()
            session.refresh(merged)
        return merged

    def _delete(self, model, obj_id: int) -> None:
        with self._session() as session:
            obj = session.get(model, obj_id)
            if obj is not None:
                session.delete(obj)
                session.commit()

    # Documents

    def add_document(self, document: Document) -> Document:
        return self._add(document)

    def get_document(self, document_id: int) -> Document | None:
        with self._session() as session:
            return session.get(Document, document_id)

    def list_documents(self) -> list[DocumentSummary]:
        with self._session() as session:
            documents = session.exec(
                select(Document).order_by(Document.id.desc())
            ).all()
            translation_counts = dict(
                session.exec(
                    select(Translation.document_id, func.count(Translation.id))
                    .group_by(Translation.document_id)
                ).all()
            )
            audio_counts = dict(
                session.exec(
                    select(Translation.document_id, func.count(SynthesisJob.id))
                    .join(SynthesisJob, SynthesisJob.translation_id == Translation.id)
                    .where(SynthesisJob.status == "completed")
                    .group_by(Translation.document_id)
                ).all()
            )
            return [
                DocumentSummary.model_validate(
                    document,
                    update={
                        "translation_count": translation_counts.get(document.id, 0),
                        "audio_count": audio_counts.get(document.id, 0),
                    },
                )
                for document in documents
            ]

    def delete_document(self, document_id: int) -> None:
        self._delete(Document, document_id)

    # Translations

    def add_translation(self, translation: Translation) -> Translation:
        return self._add(translation)

    def get_translation(self, translation_id: int) -> Translation | None:
        with self._session() as session:
            return session.get(Translation, translation_id)

    def find_translation(
        self, document_id: int, language_code: str
    ) -> Translation | None:
        with self._session() as session:
            return session.exec(
                select(Translation)
                .where(Translation.document_id == document_id)
                .where(Translation.language_code == language_code)
            ).first()

    def list_translations(self, document_id: int) -> list[Translation]:
        with self._session() as session:
            return list(
                session.exec(
                    select(Translation)
                    .where(Translation.document_id == document_id)
                    .order_by(Translation.id.desc())
                ).all()
            )

    def save_translation(self, translation: Translation) -> Translation:
        return self._save(translation)

    def delete_translation(self, translation_id: int) -> None:
        self._delete(Translation, translation_id)

    # Synthesis jobs

    def add_job(self, job: SynthesisJob) -> SynthesisJob:
        return self._add(job)

    def get_job(self, job_id: int) -> SynthesisJob | None:
        with self._session() as session:
            return session.get(SynthesisJob, job_id)

    def list_jobs(self, translation_id: int | None = None) -> list[SynthesisJob]:
        with self._session() as session:
            query = select(SynthesisJob).order_by(SynthesisJob.id.desc())
            if translation_id is not None:
                query = query.where(SynthesisJob.translation_id == translation_id)
            return list(session.exec(query).all())

    def save_job(self, job: SynthesisJob) -> SynthesisJob:
        return self._save(job)

    def delete_job(self, job_id: int) -> None:
        self._delete(SynthesisJob, job_id)


@lru_cache
def get_repository() -> Repository:
    settings = get_settings()
    if settings.runtime == "aws":
        raise RuntimeError("DynamoDB repository lands with the next commit")
    return SqliteRepository()
