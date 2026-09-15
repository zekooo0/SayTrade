"""Pure domain logic: position arithmetic, P&L/R, stop/target derivation."""

from decimal import Decimal
from datetime import datetime, timezone

import pytest

from app.domain import management, pnl, position
from app.domain.errors import PositionViolationError


class FakeExec:
    def __init__(self, side, quantity, price, executed_at="2026-09-14T12:00:00Z"):
        self.side = side
        self.quantity = Decimal(quantity)
        self.price = Decimal(price)
        self.executed_at = datetime.fromisoformat(executed_at.replace("Z", "+00:00"))
        self.created_at = None


class FakeTrade:
    def __init__(self, failure_point=None, target_price=None):
        self.failure_point = failure_point
        self.target_price = target_price
        self.status = "ACTIVE"
        self.id = "t"


class FakeDecision:
    def __init__(self, type_, old_value, new_value, decided_at):
        self.type = type_
        self.old_value = old_value
        self.new_value = new_value
        self.decided_at = datetime.fromisoformat(decided_at.replace("Z", "+00:00"))
        self.created_at = None


# --- position ----------------------------------------------------------------
def test_long_position_builds_and_closes():
    pos = Decimal("0")
    pos = position.apply_execution(pos, "LONG", "BUY", Decimal("100"))
    pos = position.apply_execution(pos, "LONG", "BUY", Decimal("50"))
    pos = position.apply_execution(pos, "LONG", "SELL", Decimal("75"))
    assert pos == Decimal("75")


def test_long_cannot_sell_what_it_does_not_own():
    pos = position.apply_execution(Decimal("0"), "LONG", "BUY", Decimal("10"))
    with pytest.raises(PositionViolationError):
        position.apply_execution(pos, "LONG", "SELL", Decimal("11"))


def test_long_cannot_open_with_a_sell():
    with pytest.raises(PositionViolationError):
        position.apply_execution(Decimal("0"), "LONG", "SELL", Decimal("100"))


def test_short_convention_inverted():
    pos = Decimal("0")
    pos = position.apply_execution(pos, "SHORT", "SELL", Decimal("100"))  # open short
    assert pos == Decimal("-100")
    pos = position.apply_execution(pos, "SHORT", "BUY", Decimal("40"))  # cover
    assert pos == Decimal("-60")
    with pytest.raises(PositionViolationError):
        position.apply_execution(pos, "SHORT", "BUY", Decimal("61"))  # over-cover


# --- P&L ---------------------------------------------------------------------
def test_gross_pnl_long():
    exs = [
        FakeExec("BUY", "100", "105"),
        FakeExec("SELL", "100", "120"),
    ]
    assert pnl.gross_pnl(exs) == Decimal("1500")
    assert pnl.is_fully_closed(exs) is True
    assert pnl.realized_pnl(exs) == Decimal("1500")


def test_pnl_not_realized_while_open():
    exs = [FakeExec("BUY", "100", "105"), FakeExec("SELL", "50", "115")]
    assert pnl.is_fully_closed(exs) is False
    assert pnl.realized_pnl(exs) is None  # never invent a realized number


def test_gross_pnl_short_same_formula():
    exs = [FakeExec("SELL", "100", "120"), FakeExec("BUY", "100", "105")]
    assert pnl.gross_pnl(exs) == Decimal("1500")


# --- R multiple --------------------------------------------------------------
def test_r_multiple_spec_example():
    exs = [FakeExec("BUY", "100", "105"), FakeExec("SELL", "100", "120")]
    assert pnl.r_multiple(exs, Decimal("90")) == Decimal("1")  # 1500 / (100*15)


def test_r_multiple_undefined_cases():
    exs = [FakeExec("BUY", "100", "105"), FakeExec("SELL", "100", "120")]
    assert pnl.r_multiple(exs, None) is None  # no failure point
    assert pnl.r_multiple([], Decimal("90")) is None  # no initial entry
    assert pnl.r_multiple(exs, Decimal("105")) is None  # zero planned risk
    open_exs = [FakeExec("BUY", "100", "105")]
    assert pnl.r_multiple(open_exs, Decimal("90")) is None  # not realized


def test_r_denominator_anchored_on_initial_entry():
    # scale-in after entry does NOT change the denominator
    exs = [
        FakeExec("BUY", "100", "105", "2026-09-14T12:00:00Z"),
        FakeExec("BUY", "50", "108", "2026-09-14T13:00:00Z"),
        FakeExec("SELL", "150", "120", "2026-09-16T15:00:00Z"),
    ]
    # risk = 100 * |105-90| = 1500; pnl = 150*120 - (100*105 + 50*108) = 18000-15900 = 2100
    assert pnl.r_multiple(exs, Decimal("90")) == Decimal("2100") / Decimal("1500")


# --- stop/target semantics ---------------------------------------------------
def test_current_stop_falls_back_to_original_plan():
    trade = FakeTrade(failure_point=Decimal("90"), target_price=Decimal("120"))
    assert management.current_stop(trade, []) == Decimal("90")
    assert management.current_target(trade, []) == Decimal("120")


def test_latest_decision_wins_original_plan_is_history():
    trade = FakeTrade(failure_point=Decimal("90"))
    decisions = [
        FakeDecision("STOP_LOSS_CHANGE", Decimal("90"), Decimal("94"), "2026-09-15T10:00:00Z"),
        FakeDecision("STOP_LOSS_CHANGE", Decimal("94"), Decimal("96"), "2026-09-16T10:00:00Z"),
    ]
    assert management.current_stop(trade, decisions) == Decimal("96")
    assert trade.failure_point == Decimal("90")  # never overwritten


def test_target_and_stop_decisions_are_independent():
    trade = FakeTrade(target_price=Decimal("120"))
    decisions = [FakeDecision("TARGET_CHANGE", Decimal("120"), Decimal("125"), "2026-09-15T10:00:00Z")]
    assert management.current_target(trade, decisions) == Decimal("125")
    assert management.current_stop(trade, decisions) == trade.failure_point  # None