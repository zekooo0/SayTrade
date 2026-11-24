"""ChartMark's own tables, in the suite's one database.

They live in a dedicated `chartmark` schema so the journal's public schema
stays the journal's. The FK rule is the same one the rest of this schema
follows: NO ACTION — a ChartMark row is never cascaded away by a user row.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

CHARTMARK_SCHEMA = "chartmark"


class ChartmarkOhlcvCache(Base):
    """One completed-bar series per (canonical ticker, timeframe).

    The provider's bookkeeping, nothing more: the bars it served last, and
    when. The studio's trend math runs in the browser; this row exists only
    so a reload does not hammer Yahoo or Twelve Data for identical history.
    """

    __tablename__ = "ohlcv_cache"
    __table_args__ = (
        UniqueConstraint(
            "canonical_ticker",
            "timeframe",
            name="uq_ohlcv_cache_ticker_timeframe",
        ),
        {"schema": CHARTMARK_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )

    canonical_ticker: Mapped[str] = mapped_column(String(48), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    provider_symbol: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    exchange_name: Mapped[str | None] = mapped_column(String(48), nullable=True)

    last_checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_candle_timestamp: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )

    # The served bars, as [{timestamp, open, high, low, close, volume}].
    candles: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ChartmarkAIBrief(Base):
    """One row per AI brief the trader generated. Nothing is stored as a
    counter: the month's usage is a count of this table's rows, so no counter
    can drift out of sync with reality — the same rule as the voice notes."""

    __tablename__ = "ai_briefs"
    __table_args__ = (
        Index(
            "ix_ai_briefs_user_id_created_at",
            "user_id",
            text("created_at DESC"),
        ),
        {"schema": CHARTMARK_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )

    # NO ACTION: a brief is never cascaded away by a user row.
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CommunitySetup(Base):
    """A deterministic ChartMark setup published by a trader to the shared
    SayTrade + ChartMark community feed."""

    __tablename__ = "community_setups"
    __table_args__ = (
        Index(
            "ix_community_setups_created_at",
            text("created_at DESC"),
        ),
        Index(
            "ix_community_setups_market_created_at",
            "market",
            text("created_at DESC"),
        ),
        {"schema": CHARTMARK_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    author_name: Mapped[str] = mapped_column(String(64), nullable=False)

    canonical_ticker: Mapped[str] = mapped_column(String(48), nullable=False)
    market: Mapped[str] = mapped_column(String(16), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    regime_status: Mapped[str] = mapped_column(String(32), nullable=False)
    price_basis: Mapped[str] = mapped_column(String(12), nullable=False, default="wick")

    latest_close: Mapped[float] = mapped_column(Float, nullable=False)
    primary_floor_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    primary_floor_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    minor_bottom_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    minor_bottom_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    highest_peak_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(16), nullable=True)

    thesis: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CommunitySetupVote(Base):
    """One upvote per (setup, trader). Toggling deletes or inserts the row."""

    __tablename__ = "community_setup_votes"
    __table_args__ = ({"schema": CHARTMARK_SCHEMA},)

    setup_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{CHARTMARK_SCHEMA}.community_setups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CommunitySetupComment(Base):
    """A reply under a published community setup."""

    __tablename__ = "community_setup_comments"
    __table_args__ = (
        Index(
            "ix_community_setup_comments_setup_created_at",
            "setup_id",
            "created_at",
        ),
        {"schema": CHARTMARK_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )
    setup_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{CHARTMARK_SCHEMA}.community_setups.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    author_name: Mapped[str] = mapped_column(String(64), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
