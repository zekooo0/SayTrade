"""POST /waitlist — the landing page's one public write.

No authentication: the caller has no account yet, and asking for one would be
the sign-in this site no longer has. Nothing here can read, list or confirm who
is on the list.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.schemas.api import WaitlistIn
from app.services.waitlist import join_waitlist

router = APIRouter(prefix="/waitlist", tags=["waitlist"])


@router.post("", status_code=201)
async def join(
    payload: WaitlistIn,
    session: AsyncSession = Depends(get_session),
):
    """Always the same answer, new address or old.

    A different status for "already joined" would turn this into a lookup service
    for anyone who wants to know whether a given person signed up.
    """
    await join_waitlist(session, payload.email, payload.source)
    return {"status": "ok"}
