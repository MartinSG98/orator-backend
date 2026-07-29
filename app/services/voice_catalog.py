"""Language and voice discovery.

Polly is the source of truth for what can be voiced, Translate for what can
be translated into. The catalog is the intersection of the two, built from
live AWS data on first request. Successful results are cached and refreshed
daily. Failures are retried on a cooldown, serving the last good catalog in
the meantime, or a small static fallback if there has never been one, so the
API stays usable for local development without AWS credentials.
"""

import logging
import threading
import time
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from app.services import aws

logger = logging.getLogger(__name__)

# Polly language codes that do not line up with Translate codes by prefix.
TRANSLATE_OVERRIDES: dict[str, str | None] = {
    "arb": "ar",  # Polly calls Modern Standard Arabic "arb"
    "cmn-CN": "zh",  # Mandarin
    "yue-CN": None,  # Cantonese, not a Translate target
    "nb-NO": "no",
}

FALLBACK_LANGUAGES: list[dict[str, Any]] = [
    {
        "code": "arb",
        "label": "Arabic",
        "translate_code": "ar",
        "voices": [{"id": "Zeina", "name": "Zeina", "gender": "female", "engine": "standard"}],
    },
    {
        "code": "en-GB",
        "label": "British English",
        "translate_code": "en",
        "voices": [
            {"id": "Amy", "name": "Amy", "gender": "female", "engine": "neural"},
            {"id": "Arthur", "name": "Arthur", "gender": "male", "engine": "neural"},
        ],
    },
    {
        "code": "fr-FR",
        "label": "French",
        "translate_code": "fr",
        "voices": [
            {"id": "Lea", "name": "Léa", "gender": "female", "engine": "neural"},
            {"id": "Remi", "name": "Rémi", "gender": "male", "engine": "neural"},
        ],
    },
    {
        "code": "de-DE",
        "label": "German",
        "translate_code": "de",
        "voices": [
            {"id": "Daniel", "name": "Daniel", "gender": "male", "engine": "neural"},
            {"id": "Vicki", "name": "Vicki", "gender": "female", "engine": "neural"},
        ],
    },
    {
        "code": "it-IT",
        "label": "Italian",
        "translate_code": "it",
        "voices": [
            {"id": "Adriano", "name": "Adriano", "gender": "male", "engine": "neural"},
            {"id": "Bianca", "name": "Bianca", "gender": "female", "engine": "neural"},
        ],
    },
    {
        "code": "en-US",
        "label": "US English",
        "translate_code": "en",
        "voices": [
            {"id": "Gregory", "name": "Gregory", "gender": "male", "engine": "neural"},
            {"id": "Ruth", "name": "Ruth", "gender": "female", "engine": "neural"},
        ],
    },
]

CATALOG_TTL_SECONDS = 24 * 60 * 60
RETRY_COOLDOWN_SECONDS = 5 * 60


def _translate_code_for(polly_code: str, translate_codes: set[str]) -> str | None:
    if polly_code in TRANSLATE_OVERRIDES:
        return TRANSLATE_OVERRIDES[polly_code]
    if polly_code in translate_codes:
        return polly_code
    primary = polly_code.split("-")[0]
    return primary if primary in translate_codes else None


def _list_translate_codes(translate) -> set[str]:
    codes: set[str] = set()
    token: str | None = None
    while True:
        kwargs = {"NextToken": token} if token else {}
        page = translate.list_languages(**kwargs)
        codes.update(lang["LanguageCode"] for lang in page["Languages"])
        token = page.get("NextToken")
        if not token:
            return codes


def _list_voices(polly) -> list[dict[str, Any]]:
    voices: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        kwargs = {"NextToken": token} if token else {}
        page = polly.describe_voices(**kwargs)
        voices.extend(page["Voices"])
        token = page.get("NextToken")
        if not token:
            return voices


def _build_catalog() -> list[dict[str, Any]]:
    polly = aws.client("polly")
    translate = aws.client("translate")

    translate_codes = _list_translate_codes(translate)

    languages: dict[str, dict[str, Any]] = {}
    for voice in _list_voices(polly):
        engines = voice.get("SupportedEngines", [])
        # Prefer neural, fall back to standard, skip voices that offer
        # neither (for example generative-only voices).
        if "neural" in engines:
            engine = "neural"
        elif "standard" in engines:
            engine = "standard"
        else:
            continue
        code = voice["LanguageCode"]
        lang = languages.setdefault(
            code,
            {
                "code": code,
                "label": voice["LanguageName"],
                "translate_code": _translate_code_for(code, translate_codes),
                "voices": [],
            },
        )
        lang["voices"].append(
            {
                "id": voice["Id"],
                "name": voice["Name"],
                "gender": voice["Gender"].lower(),
                "engine": engine,
            }
        )

    for lang in languages.values():
        lang["voices"].sort(key=lambda v: v["name"])
    return sorted(languages.values(), key=lambda lang: lang["label"])


class _CatalogCache:
    """Thread-safe cache around the discovery call.

    The lock is held across the AWS calls on purpose: concurrent first
    requests would otherwise all trigger their own discovery.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._languages: list[dict[str, Any]] | None = None
        self._source = "fallback"
        self._expires_at = 0.0
        self._next_attempt_at = 0.0

    def get(self) -> tuple[list[dict[str, Any]], str]:
        with self._lock:
            now = time.monotonic()
            if self._source == "aws" and now < self._expires_at:
                return self._languages, self._source
            if now < self._next_attempt_at:
                return self._languages or FALLBACK_LANGUAGES, self._source
            try:
                self._languages = _build_catalog()
                self._source = "aws"
                self._expires_at = now + CATALOG_TTL_SECONDS
            except (BotoCoreError, ClientError) as exc:
                logger.warning(
                    "Voice discovery failed, retrying in %d seconds: %s",
                    RETRY_COOLDOWN_SECONDS,
                    exc,
                )
                self._next_attempt_at = now + RETRY_COOLDOWN_SECONDS
                if self._languages is None:
                    self._languages = FALLBACK_LANGUAGES
                    self._source = "fallback"
                # An expired AWS catalog is kept and served stale rather
                # than downgraded to the fallback.
            return self._languages, self._source


_cache = _CatalogCache()


def get_catalog() -> tuple[list[dict[str, Any]], str]:
    """Return (languages, source) where source is "aws" or "fallback"."""
    return _cache.get()
