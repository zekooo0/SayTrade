"""The scoreboard: the numbers a trader decides with must be exactly right.

Every expectation here is arithmetic a trader could do by hand from the same
change rows — that is the point. A test that only checked that a field exists
would let a wrong formula ship.

The workhorse trade: 100 shares entered at 100 with a stop at 95, so one R is
5.00 a share. Exiting at 110 pays 1000 and scores +2R.
"""

from conftest import (
    approve,
    auth_headers,
    open_a_trade,
    proposal,
    record_text,
    set_proposal,
)

AT_PAR = {"shares": "100", "price": "100", "stop": "95", "target": "120"}


def close_a_trade(client, symbol: str, exit_price: str, **opened):
    """Open a trade, then close it at `exit_price`. The numbers come from /analytics."""
    open_a_trade(client, symbol=symbol, **opened)
    set_proposal(client, proposal([{"kind": "CLOSE", "price": exit_price}], symbol=symbol))
    note = record_text(client, f"I closed {symbol}")
    resp = approve(client, note["id"])
    assert resp.status_code == 200, resp.text


def analytics(client, sub: str = "user_test1") -> dict:
    resp = client.get("/analytics", headers=auth_headers(sub))
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_the_edge_numbers_come_from_the_closed_trades(client):
    """+2R at 110, -1R at 95, -0.4R at 98 — all on the same 100/95 trade."""
    close_a_trade(client, "AAA", "110", **AT_PAR)
    close_a_trade(client, "BBB", "95", **AT_PAR)
    close_a_trade(client, "CCC", "98", **AT_PAR)

    view = analytics(client)

    # Counts and the money.
    assert view["closed_trades"] == 3
    assert (view["wins"], view["losses"], view["breakeven"]) == (1, 2, 0)
    assert view["win_rate"] == 0.3333
    assert view["total_pnl"] == "300"  # 1000 - 500 - 200
    assert view["average_pnl"] == "100"
    assert view["best_pnl"] == "1000"
    assert view["worst_pnl"] == "-500"
    assert view["gross_profit"] == "1000"
    assert view["gross_loss"] == "-700"
    assert view["profit_factor"] == "1.4286"  # 1000 / 700

    # The R statistics: (110-100)/5, (95-100)/5, (98-100)/5.
    assert view["rated_trades"] == 3
    assert view["unrated_trades"] == 0
    assert view["average_r"] == "0.2"  # (2 - 1 - 0.4) / 3
    assert view["best_r"] == "2"
    assert view["worst_r"] == "-1"
    assert view["average_win_r"] == "2"
    assert view["average_loss_r"] == "-0.7"
    assert view["win_rate_r"] == 0.3333
    assert view["expectancy_r"] == "0.2"  # 2R won, 1.4R lost

    # The stop held on every trade, so nothing sits beyond -1R.
    assert view["beyond_stop"] == 0
    assert view["beyond_stop_trades"] == []

    # The curve and the drawdown read off it: 1000 -> 500 -> 300.
    assert [p["cumulative_pnl"] for p in view["equity_curve"]] == ["1000", "500", "300"]
    assert [p["symbol"] for p in view["equity_curve"]] == ["AAA", "BBB", "CCC"]
    assert view["max_drawdown"] == "700"  # peak 1000, trough 300

    # The R distribution: two trades between -1R and 0, one at 2R.
    assert {b["label"]: b["count"] for b in view["r_distribution"]} == {
        "≤ -1R": 0,
        "-1 to 0R": 2,
        "0 to 1R": 0,
        "1 to 2R": 0,
        "2 to 3R": 1,
        "3R or more": 0,
    }
    assert [b["label"] for b in view["r_distribution"] if b["loss"]] == [
        "≤ -1R",
        "-1 to 0R",
    ]

    # One month, three trades, one winner.
    assert len(view["monthly"]) == 1
    assert view["monthly"][0]["pnl"] == "300"
    assert view["monthly"][0]["trades"] == 3
    assert view["monthly"][0]["wins"] == 1


def test_a_stop_that_was_overrun_is_called_out(client):
    """-1R is the stop holding. Below that, the trade ran through it."""
    close_a_trade(client, "AAA", "110", **AT_PAR)
    close_a_trade(client, "BBB", "95", **AT_PAR)
    close_a_trade(client, "CCC", "98", **AT_PAR)
    close_a_trade(client, "DDD", "90", **AT_PAR)  # -2R

    view = analytics(client)

    assert view["beyond_stop"] == 1
    assert view["beyond_stop_trades"][0]["symbol"] == "DDD"
    assert view["beyond_stop_trades"][0]["r_multiple"] == "-2"
    assert view["worst_r"] == "-2"
    assert view["average_loss_r"] == "-1.1333"  # (-1 - 0.4 - 2) / 3
    counts = {b["label"]: b["count"] for b in view["r_distribution"]}
    assert counts["≤ -1R"] == 1
    # 1000 -> 500 -> 300 -> -700: measured from the peak, never from the last close.
    assert view["max_drawdown"] == "1700"


def test_r_statistics_skip_a_trade_without_a_stop(client):
    """A trade with no stop has no R. It still has a P&L. Report both honestly."""
    close_a_trade(client, "AAA", "110", **AT_PAR)

    open_a_trade(client, symbol="BBB", shares="100", price="100", stop=None, target=None)
    set_proposal(client, proposal([{"kind": "CLOSE", "price": "110"}], symbol="BBB"))
    note = record_text(client, "I closed BBB")
    resp = approve(client, note["id"])
    assert resp.status_code == 200, resp.text

    view = analytics(client)

    assert view["closed_trades"] == 2
    assert view["total_pnl"] == "2000"  # both trades paid 1000
    assert view["win_rate"] == 1.0
    assert view["rated_trades"] == 1  # only AAA can be measured in R
    assert view["unrated_trades"] == 1
    assert view["average_r"] == "2"
    assert view["expectancy_r"] == "2"
    assert view["average_loss_r"] is None
    assert len(view["equity_curve"]) == 2  # the curve is money, so both appear


def test_an_unpriced_sale_is_not_a_zero(client):
    """No exit price means the profit is unknown, not 0 — and never a loss."""
    open_a_trade(client, symbol="AAA", **AT_PAR)
    set_proposal(client, proposal([{"kind": "CLOSE"}], symbol="AAA"))
    note = record_text(client, "I am out of AAA")
    resp = approve(client, note["id"])
    assert resp.status_code == 200, resp.text

    view = analytics(client)

    assert view["closed_trades"] == 1
    assert view["unpriced_trades"] == 1
    assert view["total_pnl"] is None
    assert view["win_rate"] is None
    assert (view["wins"], view["losses"]) == (0, 0)
    assert view["equity_curve"] == []
    assert view["max_drawdown"] is None
    assert view["profit_factor"] is None
    # The exit price is missing but the stop is not, so R still computes.
    assert view["beyond_stop"] == 0


def test_an_empty_journal_reports_nothing_not_zero(client):
    view = analytics(client)

    assert view["total_trades"] == 0
    assert view["total_pnl"] is None
    assert view["win_rate"] is None
    assert view["expectancy_r"] is None
    assert view["profit_factor"] is None
    assert view["max_drawdown"] is None
    assert view["average_r"] is None
    assert view["equity_curve"] == []
    assert view["monthly"] == []
    assert all(b["count"] == 0 for b in view["r_distribution"])
    assert len(view["notes_per_week"]) == 12
    assert all(w["notes"] == 0 for w in view["notes_per_week"])


def test_the_habit_counts_notes_weeks_and_the_streak(client):
    """A note that changed nothing is still a note. It is the thinking, kept."""
    open_a_trade(client, symbol="AAA", **AT_PAR)  # note 1: the entry
    set_proposal(client, proposal([{"kind": "STOP", "stop": "99"}], symbol="AAA"))
    moved = record_text(client, "I moved the stop up")  # note 2: a change
    assert approve(client, moved["id"]).status_code == 200

    set_proposal(client, proposal([], symbol=None, summary="Just thinking out loud."))
    thought = record_text(client, "The market felt heavy today, no trades")  # note 3
    assert approve(client, thought["id"]).status_code == 200

    view = analytics(client)

    assert view["notes_total"] == 3
    assert view["notes_typed"] == 3
    assert view["notes_voice"] == 0
    assert view["observation_notes"] == 1  # the one with no change rows
    assert view["note_streak_days"] == 1  # all three are today

    weeks = view["notes_per_week"]
    assert len(weeks) == 12
    assert weeks[-1]["notes"] == 3  # this week
    assert sum(w["notes"] for w in weeks[:-1]) == 0


def test_the_scoreboard_is_scoped_to_the_trader(client):
    close_a_trade(client, "AAA", "110", **AT_PAR)

    other = analytics(client, sub="user_b")
    assert other["closed_trades"] == 0
    assert other["total_pnl"] is None
    assert other["equity_curve"] == []
    assert other["notes_total"] == 0
