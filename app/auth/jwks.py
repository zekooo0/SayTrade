"""Async JWKS fetching with caching.

The fetcher is an explicit seam: production reads the provider's published key
set over httpx; tests inject a static JWKS and mint tokens signed with a matching
test key.

For Firebase this is Google's `securetoken` key set, which serves EVERY Firebase
project — the project is distinguished by the token's issuer and audience, never
by the key. That is why the URL is a constant and not derived from the project.
"""

import time

from typing import Protocol

import httpx

CACHE_TTL_SECONDS = 6 * 3600

# Every Firebase project's ID tokens are signed by this key set.
FIREBASE_JWKS_URL = (
    "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com"
)


class JwksFetcher(Protocol):
    async def get(self, force: bool = False) -> dict:
        """Return the JWKS document: {"keys": [...]}. `force` skips the cache."""
        ...


class HttpJwksFetcher:
    """Fetches a published public key set over HTTP, cached for six hours."""

    def __init__(self, url: str):
        self._url = url
        self._cache: dict | None = None
        self._fetched_at: float = 0.0

    async def get(self, force: bool = False) -> dict:
        now = time.monotonic()
        if not force and self._cache is not None and now - self._fetched_at < CACHE_TTL_SECONDS:
            return self._cache
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(self._url)
            response.raise_for_status()
            fetched: dict = response.json()
            self._cache = fetched
            self._fetched_at = time.monotonic()
            return fetched