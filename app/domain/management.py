"""Stop/target semantics: the original plan is history, decisions are the present.

trades.failure_point / trades.target_price are the ORIGINAL plan and are never
overwritten. The current value is the latest management decision of the
matching type, falling back to the original when no decision exists yet.
"""

from decimal import Decimal

STOP_LOSS_CHANGE = "STOP_LOSS_CHANGE"
TARGET_CHANGE = "TARGET_CHANGE"


def _latest_value(decisions, decision_type: str, fallback: Decimal | None) -> Decimal | None:
    value = fallback
    ordered = sorted(
        decisions,
        key=lambda d: (d.decided_at, d.created_at if hasattr(d, "created_at") else None),
    )
    for d in ordered:
        if d.type == decision_type:
            value = d.new_value
    return value


def current_stop(trade, decisions) -> Decimal | None:
    """Authoritative current stop: latest STOP_LOSS_CHANGE, else the original failure point."""
    return _latest_value(decisions, STOP_LOSS_CHANGE, trade.failure_point)


def current_target(trade, decisions) -> Decimal | None:
    """Authoritative current target: latest TARGET_CHANGE, else the original target."""
    return _latest_value(decisions, TARGET_CHANGE, trade.target_price)