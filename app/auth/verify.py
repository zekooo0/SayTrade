"""Clerk session-token verification (RS256 over the instance JWKS).

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


def verify_token(token: str, issuer: str, jwks: dict) -> dict:
    """Verify signature, issuer, and validity window; return the claims.

    Clerk does not promise a stable `aud` across token generations, so the
    audience is not checked — issuer + signature + expiry + sub presence are
    the trust boundary.
    """
    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if not isinstance(kid, str):
            raise AuthError("token header has no 'kid'")
        key = key_for_kid(jwks, kid)
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=issuer,
            options={"require": ["sub", "exp", "iss"]},
        )
    except AuthError:
        raise
    except jwt.PyJWTError as exc:
        raise AuthError(f"token verification failed: {exc}") from exc
    return claims


async def verify_clerk_token(token: str, fetcher, issuer: str) -> dict:
    try:
        jwks = await fetcher.get()
    except httpx.HTTPError as exc:
        # Clerk unreachable is an outage, not a bad token: say so, and leave the
        # trader signed in. A 401 here would throw away a good session.
        logger.warning("Clerk JWKS unreachable at %s: %s", issuer, exc)
        raise AuthUnavailableError(
            "Authentication is temporarily unavailable; try again in a moment"
        ) from exc
    return verify_token(token, issuer, jwks)