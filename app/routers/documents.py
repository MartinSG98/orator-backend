from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlmodel import Session, select

from app.config import get_settings
from app.db import get_session
from app.models import Document, DocumentDetail, DocumentSummary
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
