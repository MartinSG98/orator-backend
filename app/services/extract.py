"""Plain text extraction from uploaded documents.

Everything downstream, translation and synthesis alike, works with plain
UTF-8 text, so format handling is isolated here. Paragraph breaks are kept
as blank lines, which the translation chunker later uses as natural
boundaries.
"""

import io
from pathlib import Path

from docx import Document as DocxDocument
from pypdf import PdfReader

SUPPORTED_EXTENSIONS = {".docx", ".pdf", ".txt", ".md"}


class ExtractionError(ValueError):
    """Raised when a supported file cannot be turned into usable text."""


def extract_text(filename: str, data: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".docx":
        return _from_docx(data)
    if suffix == ".pdf":
        return _from_pdf(data)
    if suffix in {".txt", ".md"}:
        return _from_plain(data)
    raise ExtractionError(f"unsupported file type '{suffix or 'none'}'")


def _from_docx(data: bytes) -> str:
    try:
        document = DocxDocument(io.BytesIO(data))
    except Exception as exc:
        raise ExtractionError("file is not a readable .docx") from exc
    paragraphs = [p.text.strip() for p in document.paragraphs]
    return "\n\n".join(p for p in paragraphs if p)


def _from_pdf(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:
        raise ExtractionError("file is not a readable PDF") from exc
    return "\n\n".join(p.strip() for p in pages if p.strip())


def _from_plain(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ExtractionError("file is not valid UTF-8 text") from exc
