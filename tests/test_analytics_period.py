"""The window on the scoreboard: 7d, 30d, 90d and all.

The rule these tests pin: the period filters CLOSED trades by their close date.
It never hides a position that is still open (a position is current state, not a
period event), and it never touches the journal habit.

A close date is a fact about when the trader closed, and the API deliberately has
no route to change one — so a test that needs an old trade moves it in the table.
"""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import text

from app.db import engine
from conftest import (
    approve,
    auth_headers,
    open_a_trade,
    proposal,
    record_text,
    set_proposal,
)


def _board(client, **params) -> dict:
    resp = client.get("/analytics", headers=auth_headers(), params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _age(trade_id: str, days: int) -> None:
    """Move a trade's whole life into the past.

    Both timestamps move, because `closed_at` may never precede `opened_at` —
    that is a CHECK on the table, and a test that broke it would be testing a
    state the app cannot produce.
    """

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE trades "
                    "SET opened_at = now() - (interval '1 day' * :opened), "
                    "    closed_at = now() - (interval '1 day' * :closed) "
                    "WHERE id = :id"
                ),
                {"opened": days + 5, "closed": days, "id": trade_id},
            )

    asyncio.run(_run())


def _closed_trade(client, *, symbol: str = "NVDA", exit_price: str = "11") -> dict:
    """One closed trade. Entry 10, stop 9, target 12, so the R is easy to see."""
    opened = open_a_trade(client, symbol=symbol)
    set_proposal(client, proposal([{"kind": "CLOSE", "price": exit_price}]))
    note = record_text(client, "I am out", trade_id=opened["id"])
    assert approve(client, note["id"]).status_code == 200
    return opened


def test_the_default_window_covers_everything(client):
    """No period asked for is the shipped behaviour: all of it."""
    _closed_trade(client)

    view = _board(client)

    assert view["period"] == "all"
    assert view["period_start"] is None
    assert view["closed_trades"] == 1
    assert view["total_r"] == "1"


def test_a_trade_closed_before_the_window_leaves_every_figure(client):
    """Forty days old: out of 7d and 30d, still inside 90d."""
    trade = _closed_trade(client)
    _age(trade["id"], 40)

    week = _board(client, period="7d")
    month = _board(client, period="30d")
    quarter = _board(client, period="90d")

    for view in (week, month):
        assert view["closed_trades"] == 0
        assert view["total_trades"] == 0
        assert view["total_pnl"] is None
        assert view["total_r"] is None
        assert view["equity_curve"] == []
        # The distribution keeps its six named buckets so the chart's axis never
        # changes shape; an empty window means every one of them is zero.
        assert sum(bucket["count"] for bucket in view["r_distribution"]) == 0
    assert quarter["closed_trades"] == 1
    assert quarter["total_r"] == "1"


def test_the_window_echoes_its_own_start(client):
    """A client never has to remember what it asked for, or when it began."""
    _board(client, period="30d")

    view = _board(client, period="30d")

    assert view["period"] == "30d"
    started = datetime.fromisoformat(view["period_start"])
    age = (datetime.now(timezone.utc) - started).total_seconds()
    assert 29.9 * 86400 < age < 30.1 * 86400


def test_the_window_never_hides_a_position_that_is_still_open(client):
    """An open trade is current state. A short window must not empty the board."""
    open_a_trade(client, symbol="NVDA")

    view = _board(client, period="7d")

    assert view["open_trades"] == 1
    assert view["closed_trades"] == 0
    assert view["total_trades"] == 1


def test_total_r_sums_the_window_and_stops_held_counts_what_it_says(client):
    """Two rated trades: one stopped inside its stop, one ran through it.

    +1R and -2R add up to -1R, and a stop that was honoured is not a breach. Both
    numbers are counted by the API so no client subtracts its own answer.
    """
    _closed_trade(client, symbol="NVDA", exit_price="11")  # +1R
    _closed_trade(client, symbol="AAPL", exit_price="8")  # -2R, beyond the stop

    view = _board(client)

    assert view["rated_trades"] == 2
    assert view["total_r"] == "-1"
    assert view["beyond_stop"] == 1
    assert view["stops_held"] == 1


def test_journaled_days_lists_the_utc_days_that_carry_a_note(client):
    """The dots are the API's, not the screen's idea of which day had a note."""
    open_a_trade(client, symbol="NVDA")

    view = _board(client, period="7d")

    today = datetime.now(timezone.utc).date().isoformat()
    assert view["journaled_days"] == [today]


def test_an_unknown_window_is_refused(client):
    """A typo is a 422, never a silently ignored window."""
    resp = client.get("/analytics", headers=auth_headers(), params={"period": "1y"})
    assert resp.status_code == 422
