"""Document translation via AWS Translate.

A single TranslateText call caps input at 5000 characters, so text is
chunked at 4500 to leave headroom. Chunks break on paragraph boundaries
first, on sentence boundaries inside oversized paragraphs, and only hard
split when a single sentence exceeds the limit. Translated chunks are
reassembled with paragraph breaks.

The source language is auto-detected. When it turns out to equal the
target, Translate refuses the pair, and that refusal is treated as
"nothing to translate", the chunk passes through untouched. This is what
makes translating an English document "to English" a cheap no-op instead
of an error.
"""

import re
import time
from dataclasses import dataclass

from botocore.exceptions import ClientError

from app.services import aws

CHUNK_LIMIT = 4500
_MAX_ATTEMPTS = 3
_RETRYABLE = {"ThrottlingException", "TooManyRequestsException"}
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class TranslationResult:
    text: str
    detected_source: str | None


def chunk_text(text: str, limit: int = CHUNK_LIMIT) -> list[str]:
    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = ""
        if len(paragraph) <= limit:
            current = paragraph
            continue
        chunks.extend(_chunk_sentences(paragraph, limit))
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk.strip()]


def _chunk_sentences(paragraph: str, limit: int) -> list[str]:
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(paragraph):
        candidate = f"{current} {sentence}" if current else sentence
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            pieces.append(current)
        while len(sentence) > limit:
            pieces.append(sentence[:limit])
            sentence = sentence[limit:]
        current = sentence
    if current:
        pieces.append(current)
    return pieces


def _translate_chunk(client, chunk: str, target_code: str) -> tuple[str, str | None]:
    delay = 0.5
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            response = client.translate_text(
                Text=chunk,
                SourceLanguageCode="auto",
                TargetLanguageCode=target_code,
            )
            return response["TranslatedText"], response.get("SourceLanguageCode")
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "UnsupportedLanguagePairException":
                # The target always comes from the catalog, so the only
                # refusable pair is source equals target. Pass through.
                return chunk, target_code
            if code in _RETRYABLE and attempt < _MAX_ATTEMPTS:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise RuntimeError("unreachable")


def translate_document(text: str, target_code: str) -> TranslationResult:
    client = aws.client("translate")
    translated: list[str] = []
    detected: str | None = None
    for chunk in chunk_text(text):
        chunk_translated, chunk_source = _translate_chunk(client, chunk, target_code)
        translated.append(chunk_translated)
        detected = detected or chunk_source
    return TranslationResult(text="\n\n".join(translated), detected_source=detected)
