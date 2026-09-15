"""Derived P&L and R multiple. Never persisted; always computed from executions.

The spec's formulas, applied exactly:
    gross P&L        = Σ(SELL qty × price) − Σ(BUY qty × price)   [cash flow]
    realized P&L     = gross P&L only when net position == 0
    R multiple       = realized P&L / (initial qty × |entry price − failure_point|)
    R is NULL when failure_point is missing, entry is missing, or risk is 0.
"""

from decimal import Decimal

from app.domain.position import BUY, SELL, net_position


def _ordered(executions) -> list:
    """Chronological order: by executed_at, then by row creation order."""
    return sorted(
        executions,
        key=lambda e: (e.executed_at, e.created_at if hasattr(e, "created_at") else None),
    )


def gross_pnl(executions) -> Decimal:
    """Cash-flow P&L. Direction-agnostic by construction (matches the spec)."""
    proceeds = sum(
        (e.quantity * e.price for e in executions if e.side == SELL), Decimal("0")
    )
    cost = sum(
        (e.quantity * e.price for e in executions if e.side == BUY), Decimal("0")
    )
    return proceeds - cost


def is_fully_closed(executions) -> bool:
    """A Trade's P&L is only "realized" once nothing is left open."""
    return net_position(executions) == 0


def realized_pnl(executions) -> Decimal | None:
    """Existing only for fully closed Trades; NULL otherwise (no invented value)."""
    if not is_fully_closed(executions):
        return None
    return gross_pnl(executions)


def initial_entry(executions) -> tuple[Decimal, Decimal] | None:
    """(price, quantity) of the first execution.

    This is the R denominator anchor: scale-ins later in the same Trade never
    retroactively change it (MVP convention from the spec).
    """
    ordered = _ordered(executions)
    if not ordered:
        return None
    first = ordered[0]
    return first.price, first.quantity


def r_multiple(executions, failure_point: Decimal | None) -> Decimal | None:
    """Realized R, or NULL per the spec's undefined cases."""
    if failure_point is None:
        return None
    entry = initial_entry(executions)
    if entry is None:
        return None
    entry_price, entry_quantity = entry
    initial_planned_risk = entry_quantity * abs(entry_price - failure_point)
    if initial_planned_risk == 0:
        return None
    pnl = realized_pnl(executions)
    if pnl is None:
        return None
    return pnl / initial_planned_risk