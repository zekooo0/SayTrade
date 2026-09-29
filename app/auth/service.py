"""User provisioning: Clerk subject -> internal user row."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import PLAN_PRO
from app.models import User


async def provision_user(session: AsyncSession, clerk_sub: str) -> User:
    """Return the internal User for a verified Clerk subject, creating it on
    first sight (just-in-time provisioning).

    A new account starts on PRO: every feature is open while Pro has no price
    and no payment provider exists, and an operator can still move an account
    down to FREE by hand (`scripts/set_plan.py`).

    The clerk_sub unique constraint resolves the first-sight race: the loser
    of a concurrent insert reloads the winner's row.
    """
    user = await session.scalar(select(User).where(User.clerk_sub == clerk_sub))
    if user is not None:
        return user

    user = User(clerk_sub=clerk_sub, plan=PLAN_PRO)
    session.add(user)
    try:
        await session.flush()
    except IntegrityError:
        # The dependency session is otherwise untouched at this point, so a
        # rollback only discards the failed insert.
        await session.rollback()
        user = await session.scalar(select(User).where(User.clerk_sub == clerk_sub))
        if user is None:  # pragma: no cover - impossible after IntegrityError
            raise
    return user