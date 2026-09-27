"""Position arithmetic: pure functions over change-like objects.

Real Change rows and ChangeProposal objects both work, so a small stub is
enough to pin the math down.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from app.domain import position as pos

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


@dataclass
class StubChange:
    """Duck-typed stand-in for a Change row or a ChangeProposal."""

    kind: str
    shares: Decimal | None = None
    price: Decimal | None = None
    stop: Decimal | None = None
    target: Decimal | None = None
    occurred_at: datetime = T0
    id: UUID = field(default_factory=uuid4)


def dec(value):
    return Decimal(value) if value is not None else None


def change(kind, shares=None, price=None, stop=None, target=None, minutes=0, **kwargs):
    return StubChange(
        kind=kind,
        shares=dec(shares),
        price=dec(price),
        stop=dec(stop),
        target=dec(target),
        occurred_at=T0 + timedelta(minutes=minutes),
        id=kwargs.get("id", uuid4()),
    )


def nvda_round_trip():
    return [
        change("OPEN", shares="100", price="10", stop="9", target="12"),
        change("TRIM", shares="50", price="12", minutes=60),
        change("ADD", shares="50", price="11", stop="10.5", minutes=120),
        change("STOP", stop="10.2", minutes=180),
        change("CLOSE", shares="100", price="11.95", minutes=240),
    ]


def test_shares_held_is_buys_minus_sells():
    assert pos.shares_held(nvda_round_trip()) == Decimal("0")
    assert pos.shares_held([change("OPEN", shares="100", price="10")]) == Decimal("100")
    assert pos.shares_held(
        [change("OPEN", shares="100", price="10"), change("TRIM", shares="40", price="11")]
    ) == Decimal("60")


def test_average_price_weights_each_buy():
    changes = [change("OPEN", shares="100", price="10"), change("ADD", shares="50", price="11")]
    assert pos.average_price(changes) == Decimal("1550") / Decimal("150")
    assert pos.q8(pos.average_price(changes)) == Decimal("10.33333333")


def test_average_price_needs_a_price():
    assert pos.average_price([change("OPEN", shares="100")]) is None


def test_only_open_and_stop_move_the_trade_stop():
    """The added-part stop never becomes the trade stop (one stop per trade)."""
    changes = [change("OPEN", shares="100", price="10", stop="9")]
    assert pos.current_stop(changes) == Decimal("9")
    changes.append(change("ADD", shares="50", price="11", stop="10.5", minutes=30))
    assert pos.current_stop(changes) == Decimal("9")      # unchanged by the ADD
    changes.append(change("STOP", stop="9.5", minutes=60))
    assert pos.current_stop(changes) == Decimal("9.5")
    assert pos.initial_stop(changes) == Decimal("9")


def test_stop_and_target_moves_are_market_time_ordered():
    """A backdated note must not rewind the stop."""
    changes = [
        change("OPEN", shares="100", price="10", stop="9", minutes=60),
        change("STOP", stop="8", minutes=0),          # earlier in market time
    ]
    assert pos.current_stop(changes) == Decimal("9")


def test_realized_pnl_uses_the_average_buy_price():
    changes = [change("OPEN", shares="100", price="10")]
    assert pos.realized_pnl(changes) is None
    changes.append(change("TRIM", shares="50", price="12", minutes=30))
    assert pos.realized_pnl(changes) == Decimal("100")


def test_realized_pnl_is_none_without_a_sale_price():
    changes = [
        change("OPEN", shares="100", price="10"),
        change("TRIM", shares="50", minutes=30),
    ]
    assert pos.realized_pnl(changes) is None


def test_risk_planned_r_and_r_multiple():
    changes = [change("OPEN", shares="100", price="10", stop="9", target="12")]
    assert pos.risk_per_share(changes) == Decimal("1")
    assert pos.planned_r(changes) == Decimal("2")
    assert pos.r_multiple(changes) is None            # nothing sold yet

    changes.append(change("CLOSE", shares="100", price="13", minutes=60))
    assert pos.r_multiple(changes) == Decimal("3")


def test_r_multiple_is_none_when_the_stop_is_not_below_the_entry():
    changes = [change("OPEN", shares="100", price="10", stop="10.5", target="12")]
    assert pos.risk_per_share(changes) is None
    assert pos.planned_r(changes) is None


def test_warnings_name_the_gaps():
    changes = [change("OPEN", shares="100")]        # no price at all
    out = pos.warnings(changes, closed=False)
    assert any("no price" in w for w in out)
    assert any("no position change" not in w for w in out)

    assert any("holds no position change" in w for w in pos.warnings([], closed=False))
    closed_no_pnl = [change("OPEN", shares="100", price="10"), change("CLOSE", shares="100")]
    assert any("cannot be computed" in w for w in pos.warnings(closed_no_pnl, closed=True))
