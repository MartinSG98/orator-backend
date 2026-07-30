from datetime import datetime, timezone
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.db import get_session
from app.models import (
    Document,
    SynthesisJob,
    Translation,
    TranslationCreate,
    TranslationDetail,
    TranslationSummary,
    TranslationUpdate,
)
from app.services.translate import translate_document
from app.services.voice_catalog import get_language

router = APIRouter(prefix="/api", tags=["translations"])


@router.post(
    "/documents/{document_id}/translations",
    response_model=TranslationDetail,
    status_code=201,
)
def create_translation(
    document_id: int, body: TranslationCreate, session: Session = Depends(get_session)
) -> Translation:
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(404, "document not found")

    language = get_language(body.language_code)
    if language is None:
        raise HTTPException(400, f"unknown language code '{body.language_code}'")
    if language["translate_code"] is None:
        raise HTTPException(400, f"'{body.language_code}' cannot be translated into")

    existing = session.exec(
        select(Translation)
        .where(Translation.document_id == document_id)
        .where(Translation.language_code == body.language_code)
    ).first()
    if existing is not None:
        raise HTTPException(
            409,
            f"translation to '{body.language_code}' already exists, "
            "edit it with PATCH or delete it first",
        )

    try:
        result = translate_document(document.text, language["translate_code"])
    except (BotoCoreError, ClientError) as exc:
        raise HTTPException(502, f"translation failed: {exc}") from exc

    translation = Translation(
        document_id=document_id,
        language_code=body.language_code,
        translate_code=language["translate_code"],
        detected_source=result.detected_source,
        text=result.text,
    )
    session.add(translation)
    session.commit()
    session.refresh(translation)
    return translation


@router.get(
    "/documents/{document_id}/translations",
    response_model=list[TranslationSummary],
)
def list_translations(
    document_id: int, session: Session = Depends(get_session)
) -> list[Translation]:
    if session.get(Document, document_id) is None:
        raise HTTPException(404, "document not found")
    return list(
        session.exec(
            select(Translation)
            .where(Translation.document_id == document_id)
            .order_by(Translation.id.desc())
        ).all()
    )


@router.get("/translations/{translation_id}", response_model=TranslationDetail)
def get_translation(
    translation_id: int, session: Session = Depends(get_session)
) -> Translation:
    translation = session.get(Translation, translation_id)
    if translation is None:
        raise HTTPException(404, "translation not found")
    return translation


@router.patch("/translations/{translation_id}", response_model=TranslationDetail)
def update_translation(
    translation_id: int, body: TranslationUpdate, session: Session = Depends(get_session)
) -> Translation:
    translation = session.get(Translation, translation_id)
    if translation is None:
        raise HTTPException(404, "translation not found")
    if not body.text.strip():
        raise HTTPException(400, "text cannot be empty")

    translation.text = body.text
    translation.edited = True
    translation.updated_at = datetime.now(timezone.utc)
    session.add(translation)
    session.commit()
    session.refresh(translation)
    return translation


@router.delete("/translations/{translation_id}", status_code=204)
def delete_translation(
    translation_id: int, session: Session = Depends(get_session)
) -> None:
    translation = session.get(Translation, translation_id)
    if translation is None:
        raise HTTPException(404, "translation not found")

    jobs = session.exec(
        select(SynthesisJob).where(SynthesisJob.translation_id == translation_id)
    ).all()
    for job in jobs:
        if job.audio_path:
            Path(job.audio_path).unlink(missing_ok=True)
        session.delete(job)

    session.delete(translation)
    session.commit()
