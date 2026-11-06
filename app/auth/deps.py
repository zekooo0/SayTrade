"""FastAPI dependency: authenticate the request and return the internal User.

The user_id is NEVER taken from the request body — only from a verified Firebase
ID token's `sub` claim (the spec's hard rule).
"""

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import provision_user
from app.auth.verify import AuthError, verify_firebase_token
from app.db import get_session
from app.models import User


async def current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User:
    settings = request.app.state.settings
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = authorization.removeprefix("Bearer ").strip()

    issuer = settings.firebase_issuer
    audience = settings.firebase_audience
    fetcher = getattr(request.app.state, "firebase_jwks", None)

    if not issuer or not audience or fetcher is None:
        raise HTTPException(
            status_code=503,
            detail="Firebase authentication is not configured (FIREBASE_PROJECT_ID)",
        )

    try:
        claims = await verify_firebase_token(token, fetcher, issuer, audience)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    return await provision_user(session, claims["sub"])