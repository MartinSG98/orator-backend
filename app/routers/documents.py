from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from app.models import (
    Document,
    DocumentDetail,
    DocumentOverview,
    DocumentSummary,
    SynthesisJobOut,
    TranslationWithJobs,
)
from app.repository import Repository, get_repository
from app.services.extract import SUPPORTED_EXTENSIONS, ExtractionError, extract_text
from app.services.storage import get_storage

router = APIRouter(prefix="/api/documents", tags=["documents"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


@router.post("", response_model=DocumentDetail, status_code=201)
async def upload_document(
    file: UploadFile, repo: Repository = Depends(get_repository)
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

    key = f"documents/{uuid4().hex}{suffix}"
    get_storage().save(key, data)

    return repo.add_document(
        Document(
            filename=filename,
            stored_path=key,
            text=text,
            word_count=len(text.split()),
        )
    )


@router.get("", response_model=list[DocumentSummary])
def list_documents(repo: Repository = Depends(get_repository)) -> list[DocumentSummary]:
    return repo.list_documents()


@router.get("/{document_id}", response_model=DocumentDetail)
def get_document(
    document_id: int, repo: Repository = Depends(get_repository)
) -> Document:
    document = repo.get_document(document_id)
    if document is None:
        raise HTTPException(404, "document not found")
    return document


@router.get("/{document_id}/overview", response_model=DocumentOverview)
def get_document_overview(
    document_id: int, repo: Repository = Depends(get_repository)
) -> DocumentOverview:
    """The document with its translations and their jobs, in one response."""
    document = repo.get_document(document_id)
    if document is None:
        raise HTTPException(404, "document not found")

    with_jobs = [
        TranslationWithJobs.model_validate(
            translation,
            update={
                "jobs": [
                    SynthesisJobOut.model_validate(job)
                    for job in repo.list_jobs(translation.id)
                ]
            },
        )
        for translation in repo.list_translations(document_id)
    ]
    return DocumentOverview.model_validate(
        document,
        update={
            "translations": with_jobs,
            "translation_count": len(with_jobs),
            "audio_count": sum(
                1 for t in with_jobs for job in t.jobs if job.status == "completed"
            ),
        },
    )


@router.delete("/{document_id}", status_code=204)
def delete_document(
    document_id: int, repo: Repository = Depends(get_repository)
) -> None:
    """Delete the document and everything hanging off it, files included."""
    document = repo.get_document(document_id)
    if document is None:
        raise HTTPException(404, "document not found")

    storage = get_storage()
    for translation in repo.list_translations(document_id):
        for job in repo.list_jobs(translation.id):
            if job.audio_path:
                storage.delete(job.audio_path)
            repo.delete_job(job.id)
        repo.delete_translation(translation.id)

    storage.delete(document.stored_path)
    repo.delete_document(document_id)
