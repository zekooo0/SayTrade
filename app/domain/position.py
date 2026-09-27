"""Long-only position arithmetic. Pure functions over change-like objects.

A change-like object has: kind, shares, price, stop, target, occurred_at.
Both SQLAlchemy Change rows and ChangeProposal objects satisfy that.

Everything here is derived on read. Nothing is cached in a column, so the
timeline (the change rows) stays the single source of truth.
"""

from datetime import datetime
from decimal import Decimal

from app.models.change import CHANGE_KINDS, POSITION_KINDS

OPEN = "OPEN"
ADD = "ADD"
TRIM = "TRIM"
STOP = "STOP"
TARGET = "TARGET"
CLOSE = "CLOSE"

ZERO = Decimal("0")
PRICE_QUANT = Decimal("0.00000001")  # matches NUMERIC(20,8) columns
RATIO_QUANT = Decimal("0.0001")


def q8(value: Decimal | None) -> Decimal | None:
    """Round a derived money value for display. Math stays exact internally."""
    return None if value is None else value.quantize(PRICE_QUANT)


def q4(value: Decimal | None) -> Decimal | None:
    """Round a derived ratio (R multiple) for display."""
    return None if value is None else value.quantize(RATIO_QUANT)


def _ordered(changes) -> list:
    """Market-time order. The spoken order of a note does not reorder history."""
    return sorted(changes, key=lambda c: (c.occurred_at or datetime.min, str(c.id)))


def _shares(change) -> Decimal:
    return change.shares or ZERO


def shares_held(changes) -> Decimal:
    """Shares still open: buys minus sells."""
    total = ZERO
    for c in changes:
        if c.kind in (OPEN, ADD):
            total += _shares(c)
        elif c.kind in (TRIM, CLOSE):
            total -= _shares(c)
    return total


def bought_shares(changes) -> Decimal:
    return sum((_shares(c) for c in changes if c.kind in (OPEN, ADD)), ZERO)


def sold_shares(changes) -> Decimal:
    return sum((_shares(c) for c in changes if c.kind in (TRIM, CLOSE)), ZERO)


def average_price(changes) -> Decimal | None:
    """Average buy price over every bought share. None when no buy has a price."""
    shares = ZERO
    cost = ZERO
    for c in changes:
        if c.kind in (OPEN, ADD) and c.price is not None:
            shares += _shares(c)
            cost += _shares(c) * c.price
    if shares == ZERO:
        return None
    return cost / shares


def bought_cost(changes) -> Decimal:
    """Total cash in, over buys that carry a price."""
    return sum(
        (_shares(c) * c.price for c in changes if c.kind in (OPEN, ADD) and c.price is not None),
        ZERO,
    )


def sold_proceeds(changes) -> Decimal:
    """Total cash out, over sells that carry a price."""
    return sum(
        (_shares(c) * c.price for c in changes if c.kind in (TRIM, CLOSE) and c.price is not None),
        ZERO,
    )


def current_stop(changes) -> Decimal | None:
    """The trade stop: the last stop stated on an OPEN or STOP change.

    A stop on an ADD change is informational only — one stop per trade is the
    agreed model, so an added-part stop never moves the trade stop.
    """
    stop = None
    for c in _ordered(changes):
        if c.kind in (OPEN, STOP) and c.stop is not None:
            stop = c.stop
    return stop


def initial_stop(changes) -> Decimal | None:
    for c in _ordered(changes):
        if c.kind in (OPEN, STOP) and c.stop is not None:
            return c.stop
    return None


def current_target(changes) -> Decimal | None:
    target = None
    for c in _ordered(changes):
        if c.kind in (OPEN, TARGET) and c.target is not None:
            target = c.target
    return target


def realized_pnl(changes) -> Decimal | None:
    """Cash out minus the cost of the shares sold, on the average buy price.

    None while no sell carries a price — an unknown is reported as an unknown,
    never as zero.
    """
    avg = average_price(changes)
    if avg is None:
        return None
    sold = ZERO
    priced_sold = ZERO
    for c in changes:
        if c.kind in (TRIM, CLOSE):
            sold += _shares(c)
            if c.price is not None:
                priced_sold += _shares(c)
    if priced_sold == ZERO:
        return None
    proceeds = sold_proceeds(changes)
    cost_of_sold = avg * priced_sold
    pnl = proceeds - cost_of_sold
    if priced_sold < sold:
        # Some sold shares have no price: the number is a partial view.
        return pnl
    return pnl


def risk_per_share(changes) -> Decimal | None:
    """Entry minus stop on the first stated stop."""
    avg = average_price(changes)
    stop = initial_stop(changes)
    if avg is None or stop is None:
        return None
    risk = avg - stop
    return risk if risk > ZERO else None


def planned_r(changes) -> Decimal | None:
    """Reward/risk of the plan: (target - entry) / (entry - stop)."""
    avg = average_price(changes)
    stop = initial_stop(changes)
    target = current_target(changes)
    if avg is None or stop is None or target is None:
        return None
    risk = avg - stop
    if risk <= ZERO:
        return None
    return (target - avg) / risk


def r_multiple(changes) -> Decimal | None:
    """Realized R: (average exit - entry) / (entry - stop)."""
    avg = average_price(changes)
    risk = risk_per_share(changes)
    if avg is None or risk is None:
        return None
    exit_shares = ZERO
    exit_value = ZERO
    for c in changes:
        if c.kind in (TRIM, CLOSE) and c.price is not None:
            exit_shares += _shares(c)
            exit_value += _shares(c) * c.price
    if exit_shares == ZERO:
        return None
    avg_exit = exit_value / exit_shares
    return (avg_exit - avg) / risk


def warnings(changes, *, closed: bool) -> list[str]:
    """Plain-language gaps the trader can fix later."""
    out: list[str] = []
    buys = [c for c in changes if c.kind in (OPEN, ADD)]
    sells = [c for c in changes if c.kind in (TRIM, CLOSE)]
    if any(c.price is None for c in buys):
        out.append("A buy has no price, so the average price is incomplete.")
    if any(c.price is None for c in sells):
        out.append("A sale has no price, so the profit for it is unknown.")
    if any(c.shares is None for c in changes if c.kind in POSITION_KINDS):
        out.append("A position change has no share count yet.")
    if closed and realized_pnl(changes) is None:
        out.append("The trade is closed but the profit cannot be computed yet.")
    if not changes:
        out.append("This note holds no position change.")
    return out


def validate_kind(kind: str) -> str:
    if kind not in CHANGE_KINDS:
        raise ValueError(f"unknown change kind {kind!r}")
    return kind
