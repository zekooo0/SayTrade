"""The weekly review endpoint: read a week, then finish it.

Reading is free and can be done as often as the trader likes. Writing is the one
sentence, and it is what marks the week done — there is no separate "complete"
switch, because a review with nothing in it would put nothing on Today.
"""

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.db import get_session
from app.models import User
from app.schemas.api import ReviewIn, ReviewView
from app.services import reviews as review_service

router = APIRouter(tags=["reviews"])


def _refuse_unfinished_week(week_start: date) -> None:
    """A week is reviewable only once it has ENDED.

    Without this, a trader could file a review of the week they are still living
    in and freeze a sentence about four days that have not happened. The default
    is already last week; this guards the explicit date.
    """
    if week_start.weekday() != 0:
        raise HTTPException(status_code=422, detail="A review starts on a Monday")
    if week_start >= review_service.week_start_of(datetime.now(timezone.utc)):
        raise HTTPException(status_code=422, detail="That week has not ended yet")


@router.get("/reviews", response_model=ReviewView)
async def review(
    week_start: date | None = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    """One week's review: its numbers, its notes and its sentence.

    With no `week_start`, the LAST completed week. That is what "the review is
    ready" means: a week that has ended and has numbers to show.
    """
    if week_start is not None:
        _refuse_unfinished_week(week_start)
    return await review_service.current(session, user, week_start=week_start)


@router.post("/reviews", response_model=ReviewView)
async def finish_review(
    payload: ReviewIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    """Finish a week's review by writing the one thing to do next week.

    Idempotent per week: sending a second sentence for the same week edits it.
    The commitment applies to the FOLLOWING week, which is how Today knows what
    to show for the week the trader is actually in.
    """
    if not payload.commitment.strip():
        raise HTTPException(status_code=422, detail="A review needs one thing to do")
    if payload.week_start is not None:
        _refuse_unfinished_week(payload.week_start)
    return await review_service.save(
        session, user, commitment=payload.commitment, week_start=payload.week_start
    )
