"""Firebase ID-token verification (RS256 over Google's securetoken key set).

A Firebase ID token is a plain RS256 JWT:
`iss = https://securetoken.google.com/<project>`, `aud = <project>`, `sub` is the
Firebase uid. Google publishes every project's signing keys at one URL, so the
project is proven by issuer AND audience — never by the key.

The signing key is reconstructed from the JWK (n, e) with cryptography —
PyJWT 2.14 removed RSAAlgorithm.from_jwk, and its PyJWKSet is Fetch-oriented,
so the five-line construction is the stable path here.
"""

import base64
import logging

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from app.domain.errors import AuthUnavailableError

logger = logging.getLogger(__name__)


class AuthError(Exception):
    """Invalid/expired/forged token. Maps to HTTP 401."""


def _b64_uint(value: str) -> int:
    return int.from_bytes(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)), "big")


def rsa_key_from_jwk(jwk: dict):
    try:
        return rsa.RSAPublicNumbers(
            e=_b64_uint(jwk["e"]), n=_b64_uint(jwk["n"])
        ).public_key()
    except (KeyError, ValueError) as exc:
        raise AuthError("JWK is not a usable RSA key") from exc


def key_for_kid(jwks: dict, kid: str):
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return rsa_key_from_jwk(key)
    raise AuthError(f"no signing key found for kid '{kid}'")


def verify_token(token: str, issuer: str, jwks: dict, audience: str | None = None) -> dict:
    """Verify signature, issuer, audience and validity window; return the claims.

    `aud` is REQUIRED for Firebase: Google's key set signs every project's
    tokens, so the audience is what proves the token was minted for THIS project.
    """
    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if not isinstance(kid, str):
            raise AuthError("token header has no 'kid'")
        key = key_for_kid(jwks, kid)
        required = ["sub", "exp", "iss"] + (["aud"] if audience else [])
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=issuer,
            audience=audience if audience else None,
            options={"require": required},
        )
    except AuthError:
        raise
    except jwt.PyJWTError as exc:
        raise AuthError(f"token verification failed: {exc}") from exc
    if not claims.get("sub"):
        # Google's rule, and the app's: a token with no subject identifies nobody.
        raise AuthError("token has an empty subject")
    return claims


async def _fetch_jwks(fetcher, issuer: str, force: bool = False) -> dict:
    try:
        return await fetcher.get(force=True) if force else await fetcher.get()
    except httpx.HTTPError as exc:
        # Google unreachable is an outage, not a bad token: say so, and leave the
        # trader signed in. A 401 here would throw away a good session.
        logger.warning("Firebase JWKS unreachable for %s: %s", issuer, exc)
        raise AuthUnavailableError(
            "Authentication is temporarily unavailable; try again in a moment"
        ) from exc


async def verify_firebase_token(token: str, fetcher, issuer: str, audience: str) -> dict:
    """Verify a Firebase ID token, refetching the keys ONCE on an unknown kid.

    Google rotates the securetoken keys, and the cached set can be hours old. An
    unknown `kid` is either a forgery or a rotation; one forced refetch tells the
    two apart without making every request pay a network round trip.
    """
    jwks = await _fetch_jwks(fetcher, issuer)
    try:
        return verify_token(token, issuer, jwks, audience)
    except AuthError as exc:
        if "no signing key" not in str(exc):
            raise
    jwks = await _fetch_jwks(fetcher, issuer, force=True)
    return verify_token(token, issuer, jwks, audience)