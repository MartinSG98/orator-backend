from typing import Any

from fastapi import APIRouter

from app.services.voice_catalog import get_catalog

router = APIRouter(prefix="/api/languages", tags=["languages"])


@router.get("")
def list_languages() -> dict[str, Any]:
    languages, source = get_catalog()
    return {"source": source, "languages": languages}
