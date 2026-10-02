"""A portfolio: one market, one currency, one capital.

Trades never mix across portfolios. The switch in the app decides which market
the trader is looking at, which currency sizes the positions, and which symbols
the AI should expect when it reads a note — that last one is the point: a note
recorded in the Egypt portfolio should never resolve to a US ticker.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Portfolio(Base):
    __tablename__ = "portfolios"

    __table_args__ = (Index("ix_portfolios_user_created", "user_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False
    )

    # Named by the trader: "Egypt", "US", "Swing" — whatever they call a market.
    name: Mapped[str] = mapped_column(String(60), nullable=False)

    # The market itself: "EGX" or "US". The currency is a consequence of it
    # (EGX -> EGP, US -> USD), and the symbol lookup is scoped by it, so a note
    # recorded here can only ever resolve to a ticker that trades here.
    exchange: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # The currency this market trades in. Nullable until the trader says which:
    # an unset currency must read as unknown, never as dollars.
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # The money the trader runs in this market. Sizes every position in it.
    capital: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
