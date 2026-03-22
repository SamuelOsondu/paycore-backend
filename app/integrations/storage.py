import asyncio
import logging
from functools import partial

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings
from app.core.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)

# Allowed MIME types detected from file magic bytes.
_MAGIC: list[tuple[bytes, str]] = [
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG", "image/png"),
    (b"%PDF", "application/pdf"),
]

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


def detect_mime_type(data: bytes) -> str | None:
    """
    Identify MIME type by inspecting the file's leading magic bytes.
    Returns None if the bytes do not match any allowed format.
    """
    for magic, mime in _MAGIC:
        if data[: len(magic)] == magic:
            return mime
    return None


class StorageService:
    """
    Thin async wrapper around boto3 S3.

    All boto3 calls are CPU/IO-bound and synchronous; they are dispatched via
    asyncio.run_in_executor to avoid blocking the event loop.
    """

    def __init__(self) -> None:
        self._client = boto3.client(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
            region_name=settings.AWS_REGION,
        )
        self._bucket = settings.S3_BUCKET_NAME

    async def upload_file(self, file_bytes: bytes, key: str) -> str:
        """
        Upload raw bytes to S3 at the given key.
        Returns the key on success.
        Raises ExternalServiceError on any S3/network failure.
        """
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None,
                partial(
                    self._client.put_object,
                    Bucket=self._bucket,
                    Key=key,
                    Body=file_bytes,
                ),
            )
        except (BotoCoreError, ClientError) as exc:
            logger.error("S3 upload failed for key %s: %s", key, exc)
            raise ExternalServiceError("Document storage")
        return key

    async def get_presigned_url(self, key: str, expiry: int = 3600) -> str:
        """
        Generate a presigned GET URL for a private S3 object.
        URL expires after `expiry` seconds (default 1 hour).
        Raises ExternalServiceError on failure.
        """
        loop = asyncio.get_event_loop()
        try:
            url: str = await loop.run_in_executor(
                None,
                partial(
                    self._client.generate_presigned_url,
                    "get_object",
                    Params={"Bucket": self._bucket, "Key": key},
                    ExpiresIn=expiry,
                ),
            )
        except (BotoCoreError, ClientError) as exc:
            logger.error("Presigned URL generation failed for key %s: %s", key, exc)
            raise ExternalServiceError("Document storage")
        return url
