"""FastAPI dependency: authenticate the request and return the internal User.

The user_id is NEVER taken from the request body — only from a verified Clerk
session token's `sub` claim (the spec's hard rule).
"""

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import provision_user
from app.auth.verify import AuthError, verify_clerk_token
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

    issuer = settings.clerk_frontend_api
    jwks = getattr(request.app.state, "jwks", None)
    if not issuer or jwks is None:
        raise HTTPException(
            status_code=503,
            detail="Clerk authentication is not configured (CLERK_FRONTEND_API)",
        )

    token = authorization.removeprefix("Bearer ").strip()
    try:
        claims = await verify_clerk_token(token, jwks, issuer)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    return await provision_user(session, claims["sub"])