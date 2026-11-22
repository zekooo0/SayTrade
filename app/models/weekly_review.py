"""One week's review: the commitment the trader writes, and nothing else.

The numbers a review shows — how many trades closed that week, the net R, the
stops that held — are NOT stored here. They are derived from the change rows on
read, like every other figure in this app, so a review can never disagree with
the journal it is about. What cannot be derived is the one sentence only the
trader can write, and that is the whole table.

`week_start` is the Monday the review is OF. The commitment it holds governs the
FOLLOWING week — that is what the design means by "your commitment for next
week", and it is why Today reads the most recent review rather than this week's
week number.

A row exists only when the trader finished that week's review. There is no
`completed` flag: the row is the completion, and `created_at` says when.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class WeeklyReview(Base):
    __tablename__ = "weekly_reviews"

    __table_args__ = (
        # One review per week. Finishing the same week twice edits it: the
        # trader may want a better sentence, and there is nothing else to keep.
        UniqueConstraint("user_id", "week_start", name="uq_weekly_reviews_user_week"),
        # Reading "the latest review for this trader" is the only hot path:
        # the Review tab and Today's commitment line both do it.
        Index("ix_weekly_reviews_user_week", "user_id", "week_start"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False
    )

    # The Monday of the week being reviewed, in UTC. A DATE, not a timestamp:
    # a week is a calendar fact, and a timezone would make two devices disagree
    # about which week a Sunday-night review belongs to.
    week_start: Mapped[date] = mapped_column(Date, nullable=False)

    # The trader's own words, one sentence. NOT NULL on purpose: the design asks
    # for exactly one thing to do next week, and a review finished with an empty
    # box would put nothing on Today for seven days.
    commitment: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
