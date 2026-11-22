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
    ForeignKey,
    Index,
    String,
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
