"""Community Setups over HTTP: shared across ChartMark and SayTrade Web.

GET    /community/setups                      public feed (optional auth for vote state)
POST   /community/setups                      publish a ChartMark setup (signed-in)
DELETE /community/setups/{setup_id}           delete own setup (owner-only)
POST   /community/setups/{setup_id}/vote      toggle upvote (signed-in)
GET    /community/setups/{setup_id}/comments  list replies (public)
POST   /community/setups/{setup_id}/comments  post a reply (signed-in)
DELETE /community/setups/{setup_id}/comments/{comment_id}  delete own reply
"""

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user, optional_user
from app.db import get_session
from app.log import get_logger
from app.models import (
    CommunitySetup,
    CommunitySetupComment,
    CommunitySetupVote,
    User,
)

log = get_logger("saytrade.community")

router = APIRouter(prefix="/community", tags=["community"])

ALLOWED_MARKETS = ("EGX", "US", "FX", "INDEX", "SAMPLE")
ALLOWED_REGIMES = (
    "INTACT",
    "WARNING: MINOR BROKEN",
    "BROKEN",
    "INSUFFICIENT_STRUCTURE",
)


def _clean_author(raw: str | None) -> str:
    name = " ".join((raw or "").strip().split())
    if not name:
        return "Trader"
    return name[:48]


def _round_price(value: float | None) -> float | None:
    if value is None:
        return None
    decimals = 2 if abs(value) >= 10 else 4
    return round(float(value), decimals)


class CreateSetupRequest(BaseModel):
    canonicalTicker: str = Field(min_length=1, max_length=48)
    market: Literal["EGX", "US", "FX", "INDEX", "SAMPLE"]
    timeframe: Literal["4H", "1D", "1W"]
    regimeStatus: Literal[
        "INTACT",
        "WARNING: MINOR BROKEN",
        "BROKEN",
        "INSUFFICIENT_STRUCTURE",
    ]
    priceBasis: Literal["wick", "close"] = "wick"
    latestClose: float = Field(gt=0)
    primaryFloorPrice: float | None = Field(default=None, gt=0)
    primaryFloorDate: str | None = Field(default=None, max_length=16)
    minorBottomPrice: float | None = Field(default=None, gt=0)
    minorBottomDate: str | None = Field(default=None, max_length=16)
    highestPeakPrice: float | None = Field(default=None, gt=0)
    currency: str | None = Field(default=None, max_length=16)
    thesis: str = Field(min_length=4, max_length=600)
    authorName: str | None = Field(default=None, max_length=64)


class CreateCommentRequest(BaseModel):
    body: str = Field(min_length=1, max_length=500)
    authorName: str | None = Field(default=None, max_length=64)


def _serialize_setup(
    row: CommunitySetup,
    *,
    votes_count: int,
    comments_count: int,
    viewer_has_voted: bool,
    viewer_id: uuid.UUID | None,
) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "authorName": row.author_name,
        "canonicalTicker": row.canonical_ticker,
        "market": row.market,
        "timeframe": row.timeframe,
        "regimeStatus": row.regime_status,
        "priceBasis": row.price_basis,
        "latestClose": row.latest_close,
        "primaryFloorPrice": row.primary_floor_price,
        "primaryFloorDate": row.primary_floor_date,
        "minorBottomPrice": row.minor_bottom_price,
        "minorBottomDate": row.minor_bottom_date,
        "highestPeakPrice": row.highest_peak_price,
        "currency": row.currency,
        "thesis": row.thesis,
        "votesCount": votes_count,
        "commentsCount": comments_count,
        "viewerHasVoted": viewer_has_voted,
        "isOwner": viewer_id is not None and row.user_id == viewer_id,
        "createdAt": row.created_at.isoformat(),
    }


def _serialize_comment(
    row: CommunitySetupComment, *, viewer_id: uuid.UUID | None
) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "setupId": str(row.setup_id),
        "authorName": row.author_name,
        "body": row.body,
        "isOwner": viewer_id is not None and row.user_id == viewer_id,
        "createdAt": row.created_at.isoformat(),
    }


@router.get("/setups")
async def list_setups(
    market: str | None = Query(default=None),
    regime: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    viewer: User | None = Depends(optional_user),
) -> dict[str, Any]:
    stmt = select(CommunitySetup)

    if market:
        m = market.strip().upper()
        if m in ALLOWED_MARKETS:
            stmt = stmt.where(CommunitySetup.market == m)

    if regime:
        r = regime.strip().upper()
        if r == "WARNING":
            r = "WARNING: MINOR BROKEN"
        if r in ALLOWED_REGIMES:
            stmt = stmt.where(CommunitySetup.regime_status == r)

    if q and q.strip():
        needle = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                CommunitySetup.canonical_ticker.ilike(needle),
                CommunitySetup.thesis.ilike(needle),
                CommunitySetup.author_name.ilike(needle),
            )
        )

    stmt = stmt.order_by(CommunitySetup.created_at.desc()).limit(limit)
    rows = list((await session.scalars(stmt)).all())
    if not rows:
        return {"items": []}

    setup_ids = [r.id for r in rows]

    vote_rows = (
        await session.execute(
            select(
                CommunitySetupVote.setup_id,
                func.count().label("cnt"),
            )
            .where(CommunitySetupVote.setup_id.in_(setup_ids))
            .group_by(CommunitySetupVote.setup_id)
        )
    ).all()
    vote_counts = {sid: int(cnt) for sid, cnt in vote_rows}

    comment_rows = (
        await session.execute(
            select(
                CommunitySetupComment.setup_id,
                func.count().label("cnt"),
            )
            .where(CommunitySetupComment.setup_id.in_(setup_ids))
            .group_by(CommunitySetupComment.setup_id)
        )
    ).all()
    comment_counts = {sid: int(cnt) for sid, cnt in comment_rows}

    voted_set: set[uuid.UUID] = set()
    if viewer is not None:
        voted_ids = (
            await session.scalars(
                select(CommunitySetupVote.setup_id).where(
                    CommunitySetupVote.setup_id.in_(setup_ids),
                    CommunitySetupVote.user_id == viewer.id,
                )
            )
        ).all()
        voted_set = set(voted_ids)

    viewer_id = viewer.id if viewer else None
    return {
        "items": [
            _serialize_setup(
                row,
                votes_count=vote_counts.get(row.id, 0),
                comments_count=comment_counts.get(row.id, 0),
                viewer_has_voted=row.id in voted_set,
                viewer_id=viewer_id,
            )
            for row in rows
        ]
    }


@router.post("/setups", status_code=201)
async def create_setup(
    payload: CreateSetupRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    thesis = payload.thesis.strip()
    if len(thesis) < 4:
        raise HTTPException(
            status_code=422, detail="Share a short note (at least 4 characters)."
        )

    row = CommunitySetup(
        user_id=user.id,
        author_name=_clean_author(payload.authorName),
        canonical_ticker=payload.canonicalTicker.strip().upper(),
        market=payload.market,
        timeframe=payload.timeframe,
        regime_status=payload.regimeStatus,
        price_basis=payload.priceBasis,
        latest_close=_round_price(payload.latestClose) or payload.latestClose,
        primary_floor_price=_round_price(payload.primaryFloorPrice),
        primary_floor_date=(payload.primaryFloorDate or "").strip() or None,
        minor_bottom_price=_round_price(payload.minorBottomPrice),
        minor_bottom_date=(payload.minorBottomDate or "").strip() or None,
        highest_peak_price=_round_price(payload.highestPeakPrice),
        currency=(payload.currency or "").strip().upper() or None,
        thesis=thesis,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)

    log.info(
        "community setup %s by %s: %s %s (%s)",
        row.id,
        user.id,
        row.canonical_ticker,
        row.timeframe,
        row.regime_status,
    )
    return {
        "setup": _serialize_setup(
            row,
            votes_count=0,
            comments_count=0,
            viewer_has_voted=False,
            viewer_id=user.id,
        )
    }


@router.delete("/setups/{setup_id}", status_code=204)
async def delete_setup(
    setup_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> None:
    row = await session.get(CommunitySetup, setup_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Setup not found.")
    if row.user_id != user.id:
        raise HTTPException(status_code=403, detail="You can only delete your own setup.")
    await session.delete(row)


@router.post("/setups/{setup_id}/vote")
async def toggle_vote(
    setup_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    setup = await session.get(CommunitySetup, setup_id)
    if setup is None:
        raise HTTPException(status_code=404, detail="Setup not found.")

    existing = await session.get(CommunitySetupVote, (setup_id, user.id))
    if existing is not None:
        await session.delete(existing)
        voted = False
    else:
        session.add(CommunitySetupVote(setup_id=setup_id, user_id=user.id))
        voted = True

    await session.flush()
    count = int(
        await session.scalar(
            select(func.count())
            .select_from(CommunitySetupVote)
            .where(CommunitySetupVote.setup_id == setup_id)
        )
        or 0
    )
    return {"voted": voted, "votesCount": count}


@router.get("/setups/{setup_id}/comments")
async def list_comments(
    setup_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    viewer: User | None = Depends(optional_user),
) -> dict[str, Any]:
    setup = await session.get(CommunitySetup, setup_id)
    if setup is None:
        raise HTTPException(status_code=404, detail="Setup not found.")

    rows = list(
        (
            await session.scalars(
                select(CommunitySetupComment)
                .where(CommunitySetupComment.setup_id == setup_id)
                .order_by(CommunitySetupComment.created_at.asc())
                .limit(100)
            )
        ).all()
    )
    viewer_id = viewer.id if viewer else None
    return {
        "items": [_serialize_comment(row, viewer_id=viewer_id) for row in rows]
    }


@router.post("/setups/{setup_id}/comments", status_code=201)
async def create_comment(
    setup_id: uuid.UUID,
    payload: CreateCommentRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    setup = await session.get(CommunitySetup, setup_id)
    if setup is None:
        raise HTTPException(status_code=404, detail="Setup not found.")

    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=422, detail="Comment cannot be empty.")

    row = CommunitySetupComment(
        setup_id=setup_id,
        user_id=user.id,
        author_name=_clean_author(payload.authorName),
        body=body,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)

    return {"comment": _serialize_comment(row, viewer_id=user.id)}


@router.delete("/setups/{setup_id}/comments/{comment_id}", status_code=204)
async def delete_comment(
    setup_id: uuid.UUID,
    comment_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> None:
    row = await session.get(CommunitySetupComment, comment_id)
    if row is None or row.setup_id != setup_id:
        raise HTTPException(status_code=404, detail="Comment not found.")
    if row.user_id != user.id:
        raise HTTPException(
            status_code=403, detail="You can only delete your own comment."
        )
    await session.delete(row)
