"""The readable trade number: #1, #2, #3 — per trader, never reused.

Display only. A note finds its trade by trade_id, so nothing here can affect
which trade a change lands on; the number exists so a person can say "#12".
"""

from conftest import open_a_trade


def test_trades_are_numbered_from_one_per_trader(client):
    """His own sequence, starting at 1 — not a global counter."""
    first = open_a_trade(client, symbol="NVDA")
    second = open_a_trade(client, symbol="AAPL")

    assert first["number"] == 1
    assert second["number"] == 2


def test_another_trader_starts_at_one(client):
    """A global sequence would make a new account's first trade #8,412."""
    open_a_trade(client, symbol="NVDA", sub="user_test1")
    open_a_trade(client, symbol="AAPL", sub="user_test1")

    theirs = open_a_trade(client, symbol="NVDA", sub="user_test2")

    assert theirs["number"] == 1


def test_a_closed_trade_keeps_its_number(client):
    """A number is never reused, and closing does not change it."""
    from conftest import approve, proposal, record_text, set_proposal, trades

    opened = open_a_trade(client, symbol="NVDA")
    set_proposal(client, proposal([{"kind": "CLOSE", "price": "12"}]))
    note = record_text(client, "I sold everything", trade_id=opened["id"])
    assert approve(client, note["id"]).status_code == 200

    closed = [t for t in trades(client) if t["id"] == opened["id"]][0]
    assert closed["number"] == 1
    assert closed["is_open"] is False
