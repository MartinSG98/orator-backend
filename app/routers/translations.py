from datetime import datetime, timezone

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException

from app.models import (
    Translation,
    TranslationCreate,
    TranslationDetail,
    TranslationSummary,
    TranslationUpdate,
)
from app.repository import Repository, get_repository
from app.services.storage import get_storage
from app.services.translate import translate_document
from app.services.voice_catalog import get_language

router = APIRouter(prefix="/api", tags=["translations"])


@router.post(
    "/documents/{document_id}/translations",
    response_model=TranslationDetail,
    status_code=201,
)
def create_translation(
    document_id: int, body: TranslationCreate, repo: Repository = Depends(get_repository)
) -> Translation:
    document = repo.get_document(document_id)
    if document is None:
        raise HTTPException(404, "document not found")

    language = get_language(body.language_code)
    if language is None:
        raise HTTPException(400, f"unknown language code '{body.language_code}'")
    if language["translate_code"] is None:
        raise HTTPException(400, f"'{body.language_code}' cannot be translated into")

    if repo.find_translation(document_id, body.language_code) is not None:
        raise HTTPException(
            409,
            f"translation to '{body.language_code}' already exists, "
            "edit it with PATCH or delete it first",
        )

    try:
        result = translate_document(document.text, language["translate_code"])
    except (BotoCoreError, ClientError) as exc:
        raise HTTPException(502, f"translation failed: {exc}") from exc

    return repo.add_translation(
        Translation(
            document_id=document_id,
            language_code=body.language_code,
            translate_code=language["translate_code"],
            detected_source=result.detected_source,
            text=result.text,
        )
    )


@router.get(
    "/documents/{document_id}/translations",
    response_model=list[TranslationSummary],
)
def list_translations(
    document_id: int, repo: Repository = Depends(get_repository)
) -> list[Translation]:
    if repo.get_document(document_id) is None:
        raise HTTPException(404, "document not found")
    return repo.list_translations(document_id)


@router.get("/translations/{translation_id}", response_model=TranslationDetail)
def get_translation(
    translation_id: int, repo: Repository = Depends(get_repository)
) -> Translation:
    translation = repo.get_translation(translation_id)
    if translation is None:
        raise HTTPException(404, "translation not found")
    return translation


@router.patch("/translations/{translation_id}", response_model=TranslationDetail)
def update_translation(
    translation_id: int,
    body: TranslationUpdate,
    repo: Repository = Depends(get_repository),
) -> Translation:
    translation = repo.get_translation(translation_id)
    if translation is None:
        raise HTTPException(404, "translation not found")
    if not body.text.strip():
        raise HTTPException(400, "text cannot be empty")

    translation.text = body.text
    translation.edited = True
    translation.updated_at = datetime.now(timezone.utc)
    return repo.save_translation(translation)


@router.delete("/translations/{translation_id}", status_code=204)
def delete_translation(
    translation_id: int, repo: Repository = Depends(get_repository)
) -> None:
    translation = repo.get_translation(translation_id)
    if translation is None:
        raise HTTPException(404, "translation not found")

    storage = get_storage()
    for job in repo.list_jobs(translation_id):
        if job.audio_path:
            storage.delete(job.audio_path)
        repo.delete_job(job.id)

    repo.delete_translation(translation_id)
