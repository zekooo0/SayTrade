"""FastAPI dependency: authenticate the request and return the internal User.

The user_id is NEVER taken from the request body — only from a verified Clerk
session token's `sub` claim (the spec's hard rule).
"""

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import provision_user
from app.auth.verify import AuthError, unverified_issuer, verify_clerk_token
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

    # Which Clerk instance minted this token? With one issuer there is nothing to
    # ask. With two — a domain move, where the old host and the new one are both
    # live — the token's own `iss` selects the key set, because only the token
    # knows which instance signed it. The claim is read without verifying and
    # used only as a lookup key: the signature is still checked afterwards
    # against the keys that claim pointed at, and against that issuer's name.
    token = authorization.removeprefix("Bearer ").strip()

    by_issuer = getattr(request.app.state, "jwks_by_issuer", None) or {}
    if by_issuer:
        claimed = unverified_issuer(token)
        jwks = by_issuer.get(claimed or "")
        if claimed is None or jwks is None:
            raise HTTPException(
                status_code=401,
                detail="This session token is not from a Clerk instance this API trusts.",
            )
        issuer = claimed
    else:
        issuer = settings.clerk_frontend_api
        jwks = getattr(request.app.state, "jwks", None)

    if not issuer or jwks is None:
        raise HTTPException(
            status_code=503,
            detail="Clerk authentication is not configured (CLERK_FRONTEND_API)",
        )

    try:
        claims = await verify_clerk_token(token, jwks, issuer)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    return await provision_user(session, claims["sub"])