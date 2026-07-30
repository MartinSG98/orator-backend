from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlmodel import Session, select

from app.config import get_settings
from app.db import get_session
from app.models import (
    Document,
    DocumentDetail,
    DocumentOverview,
    DocumentSummary,
    SynthesisJob,
    SynthesisJobOut,
    Translation,
    TranslationWithJobs,
)
from app.services.extract import SUPPORTED_EXTENSIONS, ExtractionError, extract_text

router = APIRouter(prefix="/api/documents", tags=["documents"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


@router.post("", response_model=DocumentDetail, status_code=201)
async def upload_document(
    file: UploadFile, session: Session = Depends(get_session)
) -> Document:
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise HTTPException(415, f"unsupported file type, expected one of: {supported}")

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"file exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
    if not data:
        raise HTTPException(400, "file is empty")

    try:
        text = extract_text(filename, data)
    except ExtractionError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not text.strip():
        raise HTTPException(400, "no extractable text in file")

    documents_dir = get_settings().media_dir / "documents"
    documents_dir.mkdir(parents=True, exist_ok=True)
    stored_path = documents_dir / f"{uuid4().hex}{suffix}"
    stored_path.write_bytes(data)

    document = Document(
        filename=filename,
        stored_path=str(stored_path),
        text=text,
        word_count=len(text.split()),
    )
    session.add(document)
    session.commit()
    session.refresh(document)
    return document


@router.get("", response_model=list[DocumentSummary])
def list_documents(session: Session = Depends(get_session)) -> list[Document]:
    return list(session.exec(select(Document).order_by(Document.id.desc())).all())


@router.get("/{document_id}", response_model=DocumentDetail)
def get_document(document_id: int, session: Session = Depends(get_session)) -> Document:
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(404, "document not found")
    return document


@router.get("/{document_id}/overview", response_model=DocumentOverview)
def get_document_overview(
    document_id: int, session: Session = Depends(get_session)
) -> DocumentOverview:
    """The document with its translations and their jobs, in one response."""
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(404, "document not found")

    translations = session.exec(
        select(Translation)
        .where(Translation.document_id == document_id)
        .order_by(Translation.id.desc())
    ).all()

    with_jobs = []
    for translation in translations:
        jobs = session.exec(
            select(SynthesisJob)
            .where(SynthesisJob.translation_id == translation.id)
            .order_by(SynthesisJob.id.desc())
        ).all()
        with_jobs.append(
            TranslationWithJobs.model_validate(
                translation,
                update={"jobs": [SynthesisJobOut.model_validate(j) for j in jobs]},
            )
        )
    return DocumentOverview.model_validate(document, update={"translations": with_jobs})


@router.delete("/{document_id}", status_code=204)
def delete_document(document_id: int, session: Session = Depends(get_session)) -> None:
    """Delete the document and everything hanging off it, files included."""
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(404, "document not found")

    translations = session.exec(
        select(Translation).where(Translation.document_id == document_id)
    ).all()
    for translation in translations:
        jobs = session.exec(
            select(SynthesisJob).where(SynthesisJob.translation_id == translation.id)
        ).all()
        for job in jobs:
            if job.audio_path:
                Path(job.audio_path).unlink(missing_ok=True)
            session.delete(job)
        session.delete(translation)

    Path(document.stored_path).unlink(missing_ok=True)
    session.delete(document)
    session.commit()
