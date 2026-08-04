"""Media storage behind one interface, per ADR 0007.

Rows in the database hold opaque storage keys like "documents/abc.docx" or
"audio/xyz.mp3". What a key physically means belongs here: a path under the
media directory in the local runtime, an S3 object key in the aws runtime.

Serving audio differs per runtime, so the interface exposes both shapes and
the router picks whichever is not None: local storage yields a filesystem
path to stream, S3 storage yields a presigned URL to redirect to.
"""

import shutil
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path, PurePosixPath

from app.config import get_settings
from app.services import aws

PRESIGNED_URL_TTL_SECONDS = 3600


class Storage(ABC):
    @abstractmethod
    def save(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    def save_file(self, key: str, source: Path) -> None: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def local_path(self, key: str) -> Path | None:
        """Filesystem path for the key, None when the runtime has no disk."""

    @abstractmethod
    def presigned_url(self, key: str, filename: str) -> str | None:
        """Time-limited download URL, None when the runtime serves bytes itself."""


class LocalStorage(Storage):
    def __init__(self, base_dir: Path) -> None:
        self._base = base_dir

    def _resolve(self, key: str) -> Path:
        # Rows written before this layer existed hold absolute paths,
        # resolve those as-is so old data keeps working.
        path = Path(key)
        return path if path.is_absolute() else self._base / PurePosixPath(key)

    def save(self, key: str, data: bytes) -> None:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def save_file(self, key: str, source: Path) -> None:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, path)

    def delete(self, key: str) -> None:
        self._resolve(key).unlink(missing_ok=True)

    def local_path(self, key: str) -> Path | None:
        return self._resolve(key)

    def presigned_url(self, key: str, filename: str) -> str | None:
        return None


class S3Storage(Storage):
    def __init__(self, bucket: str) -> None:
        if not bucket:
            raise RuntimeError("aws runtime requires ORATOR_MEDIA_BUCKET")
        self._bucket = bucket
        self._s3 = aws.client("s3")

    def save(self, key: str, data: bytes) -> None:
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=data)

    def save_file(self, key: str, source: Path) -> None:
        self._s3.upload_file(str(source), self._bucket, key)

    def delete(self, key: str) -> None:
        self._s3.delete_object(Bucket=self._bucket, Key=key)

    def local_path(self, key: str) -> Path | None:
        return None

    def presigned_url(self, key: str, filename: str) -> str | None:
        return self._s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
                "ResponseContentType": "audio/mpeg",
            },
            ExpiresIn=PRESIGNED_URL_TTL_SECONDS,
        )


@lru_cache
def get_storage() -> Storage:
    settings = get_settings()
    if settings.runtime == "aws":
        return S3Storage(settings.media_bucket)
    return LocalStorage(settings.media_dir)
