import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# The whole position vocabulary. Six words, long-only:
#   OPEN   the first buy (creates the trade)
#   ADD    buy more of the same trade
#   TRIM   sell part of the position
#   STOP   move the trade stop
#   TARGET move the trade target
#   CLOSE  sell everything left (closes the trade)
CHANGE_KINDS = ("OPEN", "ADD", "TRIM", "STOP", "TARGET", "CLOSE")

# Kinds that move shares.
POSITION_KINDS = ("OPEN", "ADD", "TRIM", "CLOSE")


class Change(Base):
    """One approved change to one position.

    A note carries zero or more of these: zero for a pure observation ("I saw
    a reverse candle, I will wait"), several when one sentence carries several
    changes. Rows are the approved truth — the applier writes them inside the
    approval transaction and nothing else ever mutates them.

    Only OPEN and STOP set the trade stop. A stop that applies to one added
    part stays in the note text, by design (one stop per trade).
    """

    __tablename__ = "changes"

    __table_args__ = (
        CheckConstraint("shares IS NULL OR shares > 0", name="ck_changes_shares_positive"),
        CheckConstraint("price IS NULL OR price > 0", name="ck_changes_price_positive"),
        CheckConstraint("stop IS NULL OR stop > 0", name="ck_changes_stop_positive"),
        CheckConstraint("target IS NULL OR target > 0", name="ck_changes_target_positive"),
        Index("ix_changes_note_id", "note_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )

    note_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("notes.id"), nullable=False
    )

    kind: Mapped[str] = mapped_column(String(10), nullable=False)

    # When the change happened in the market. Defaults to the note's time.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    shares: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    stop: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    target: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
