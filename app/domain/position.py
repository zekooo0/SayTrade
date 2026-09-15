"""Position arithmetic. Pure functions over execution-like objects.

An execution-like object needs `.side` ("BUY"/"SELL") and `.quantity` (Decimal).
Both SQLAlchemy Execution rows and the Pydantic execution specs satisfy that.

Convention (from the schema spec): position is signed shares, LONG positive,
SHORT negative. BUY always adds to the signed position, SELL always subtracts;
the Trade direction decides which side of zero the position may live on.
"""

from decimal import Decimal

from app.domain.errors import PositionViolationError

BUY = "BUY"
SELL = "SELL"


def signed_delta(side: str, quantity: Decimal) -> Decimal:
    """Position delta of one execution, in signed shares."""
    return quantity if side == BUY else -quantity


def net_position(executions) -> Decimal:
    """Signed net position from a sequence of executions."""
    return sum((signed_delta(e.side, e.quantity) for e in executions), Decimal("0"))


def apply_execution(
    position: Decimal, direction: str, side: str, quantity: Decimal
) -> Decimal:
    """Validate and apply one execution to the running position.

    LONG may never go short (SELL more than held); SHORT may never go long
    (BUY more than the short exposure). The check is per-execution on the
    running state, which is why the database cannot express it as a row CHECK.
    """
    new_position = position + signed_delta(side, quantity)
    if direction == "LONG" and new_position < 0:
        raise PositionViolationError(
            f"SELL of {quantity} exceeds the held LONG position ({position}); "
            "a LONG Trade cannot sell what it does not own"
        )
    if direction == "SHORT" and new_position > 0:
        raise PositionViolationError(
            f"BUY of {quantity} exceeds the held SHORT exposure ({position}); "
            "a SHORT Trade cannot buy back more than it sold"
        )
    return new_position