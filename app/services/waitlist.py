"""The waiting list: one public write, no account and no read.

The landing page is the only caller. It answers the same thing whether the
address is new or already on the list, because a public endpoint must not be a
way to ask us who has signed up.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.waitlist import WaitlistSignup

DEFAULT_SOURCE = "landing"


async def join_waitlist(
    session: AsyncSession, email: str, source: str | None = None
) -> None:
    """Record an address once. A repeat is not an error, and not a second row."""
    normalized = email.strip().lower()
    existing = await session.scalar(
        select(WaitlistSignup.id).where(WaitlistSignup.email == normalized)
    )
    if existing is not None:
        return

    session.add(
        WaitlistSignup(email=normalized, source=(source or DEFAULT_SOURCE).strip().lower())
    )
    try:
        await session.commit()
    except IntegrityError:
        # Two submits raced and the unique constraint picked a winner. That is
        # the constraint doing its job, not a failure to report to the visitor.
        await session.rollback()
