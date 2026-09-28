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

    async def delivery_url(self, key: str) -> str: ...

    async def delete(self, key: str) -> None:
        """Remove the recording for good. Used when a note is discarded."""
        ...


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

        def _upload() -> None:
            # No `format` argument and no extension in the key: Cloudinary
            # infers the format from the bytes and the public_id stays exactly
            # `key`. Passing both produced `<key>.webm.webm` on delivery — a URL
            # mismatch that broke reads.
            result = upload(
                data,
                public_id=key,
                resource_type="video",  # audio is served under Cloudinary's video type
                overwrite=False,  # never clobber an existing recording
            )
            if result.get("error"):
                raise RuntimeError(f"cloudinary upload failed: {result['error']}")

        await asyncio.to_thread(_upload)

    async def read(self, key: str) -> bytes:
        url = await self.delivery_url(key)
        return await asyncio.to_thread(
            lambda: urllib.request.urlopen(url, timeout=30).read()  # noqa: S310 - storage URL is our own
        )

    async def delivery_url(self, key: str) -> str:
        """Resolve the real delivery URL via the Cloudinary API.

        Deriving the URL from the key alone is guesswork (format/version
        suffixes); asking the API is authoritative and also repairs assets
        uploaded under the older key scheme. Falls back to the derived URL.
        """
        self._require_config()
        import cloudinary
        from cloudinary import api

        cloudinary.config(
            cloud_name=self.cloud_name,
            api_key=self.api_key,
            api_secret=self.api_secret,
            secure=True,
        )

        def _lookup() -> str | None:
            try:
                resource = api.resource(key, resource_type="video")
                return resource.get("secure_url") or resource.get("url")
            except Exception:
                return None

        resolved = await asyncio.to_thread(_lookup)
        return resolved or await self.url(key)

    async def delete(self, key: str) -> None:
        """Remove the recording from Cloudinary, CDN copy included.

        Called when a note is discarded: the trader asked for the note to be
        gone, so the audio must not outlive it in a bucket.
        """
        self._require_config()
        import cloudinary
        from cloudinary.uploader import destroy

        cloudinary.config(
            cloud_name=self.cloud_name,
            api_key=self.api_key,
            api_secret=self.api_secret,
            secure=True,
        )

        def _destroy() -> None:
            result = destroy(key, resource_type="video", invalidate=True)
            # `not found` is not a failure: the goal (no recording) is met.
            if isinstance(result, dict) and result.get("error"):
                raise RuntimeError(f"cloudinary destroy failed: {result['error']}")

        await asyncio.to_thread(_destroy)

    async def url(self, key: str) -> str:
        self._require_config()
        from cloudinary.utils import cloudinary_url

        url, _ = await asyncio.to_thread(
            lambda: cloudinary_url(key, resource_type="video", secure=True)
        )
        return url