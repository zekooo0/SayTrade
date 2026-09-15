"""Audio storage boundary. The journal row stores a STABLE storage key, never
a URL — URLs change when storage/CDN infrastructure changes, keys do not.
"""

import asyncio
import urllib.request
from typing import Protocol

MIME_EXTENSIONS = {
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/m4a": "m4a",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/aac": "aac",
}


def extension_for(mime_type: str) -> str:
    return MIME_EXTENSIONS.get(mime_type, "m4a")


class AudioStorage(Protocol):
    async def save(self, key: str, data: bytes, mime_type: str) -> None: ...

    async def read(self, key: str) -> bytes: ...

    async def url(self, key: str) -> str: ...


class CloudinaryStorage:
    """Cloudinary-backed audio storage; the key IS the Cloudinary public_id.

    The public_id carries the spec's namespace shape:
        voice-journal/{user_id}/{random-uuid}
    The journal entry id is deliberately not part of the key: the key must be
    known before the row is inserted, and it must never change afterwards.
    """

    def __init__(self, cloud_name: str, api_key: str, api_secret: str):
        self.cloud_name = cloud_name
        self.api_key = api_key
        self.api_secret = api_secret

    def _require_config(self) -> None:
        if not (self.cloud_name and self.api_key and self.api_secret):
            raise RuntimeError(
                "Cloudinary is not configured (CLOUDINARY_CLOUD_NAME, "
                "CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET)"
            )

    async def save(self, key: str, data: bytes, mime_type: str) -> None:
        self._require_config()
        import cloudinary
        from cloudinary.uploader import upload

        cloudinary.config(
            cloud_name=self.cloud_name,
            api_key=self.api_key,
            api_secret=self.api_secret,
            secure=True,
        )
        ext = extension_for(mime_type)

        def _upload() -> None:
            result = upload(
                data,
                public_id=key,
                resource_type="video",  # audio is served under Cloudinary's video type
                format=ext,
                overwrite=False,  # never clobber an existing recording
            )
            if result.get("error"):
                raise RuntimeError(f"cloudinary upload failed: {result['error']}")

        await asyncio.to_thread(_upload)

    async def read(self, key: str) -> bytes:
        url = await self.url(key)
        return await asyncio.to_thread(
            lambda: urllib.request.urlopen(url, timeout=30).read()  # noqa: S310 - storage URL is our own
        )

    async def url(self, key: str) -> str:
        self._require_config()
        from cloudinary.utils import cloudinary_url

        url, _ = await asyncio.to_thread(
            lambda: cloudinary_url(key, resource_type="video", secure=True)
        )
        return url