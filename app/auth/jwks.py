"""Async JWKS fetching with caching.

The fetcher is an explicit seam: production uses the Clerk instance's JWKS
endpoint over httpx; tests inject a static test JWKS and mint Clerk-shaped
tokens signed with a matching test key.
"""

import time

from typing import Protocol

import httpx

CACHE_TTL_SECONDS = 6 * 3600


class JwksFetcher(Protocol):
    async def get(self) -> dict:
        """Return the JWKS document: {"keys": [...]}."""
        ...


class HttpJwksFetcher:
    """Fetches the Clerk instance's public keys from its well-known JWKS URL."""

    def __init__(self, issuer: str):
        self.issuer = issuer
        self._url = f"{issuer.rstrip('/')}/.well-known/jwks.json"
        self._cache: dict | None = None
        self._fetched_at: float = 0.0

    async def get(self) -> dict:
        import time

        now = time.monotonic()
        if self._cache is not None and now - self._fetched_at < CACHE_TTL_SECONDS:
            return self._cache
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(self._url)
            response.raise_for_status()
            fetched: dict = response.json()
            self._cache = fetched
            self._fetched_at = now
            return fetched