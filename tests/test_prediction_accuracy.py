"""The scoreboard's second dimension: how often the trader's reading was right.

Kept apart from the win rate on purpose. The interesting cell in the two-by-two
is the losing trade with a correct prediction — the trader read the setup right
and still took a loss, which the win rate alone reports as a bad decision.
"""

from conftest import (
    approve,
    auth_headers,
    open_a_trade,
    proposal,
    record_text,
    set_proposal,
)


def _close_and_judge(client, *, symbol: str, exit_price: str, outcome: str | None):
    """Open a trade, close it expecting failure, optionally judge the prediction."""
    opened = open_a_trade(client, symbol=symbol)  # entry 10, stop 9, target 12
    set_proposal(
        client,
        proposal(
            [{"kind": "CLOSE", "price": exit_price}],
            prediction={"reason": "I expect it to fail"},
        ),
    )
    note = record_text(client, "I am out, I expect it to fail", trade_id=opened["id"])
    assert approve(client, note["id"]).status_code == 200

    if outcome is not None:
        set_proposal(
            client,
            proposal([], resolution={"outcome": outcome, "note": "what I saw"}),
        )
        follow_up = record_text(client, "here is what happened", trade_id=opened["id"])
        assert approve(client, follow_up["id"]).status_code == 200
    return opened


def _board(client) -> dict:
    resp = client.get("/analytics", headers=auth_headers())
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_the_scoreboard_reports_prediction_accuracy_beside_the_win_rate(client):
    """One of each: the trader was right once and wrong once."""
    _close_and_judge(client, symbol="NVDA", exit_price="9.5", outcome="CORRECT")
    _close_and_judge(client, symbol="AAPL", exit_price="9.5", outcome="INCORRECT")

    view = _board(client)["prediction_accuracy"]

    assert view["correct"] == 1
    assert view["incorrect"] == 1
    assert view["judged"] == 2
    assert view["accuracy"] == 0.5
    # Two judged predictions say nothing yet, and the page has to say so.
    assert view["thin"] is True


def test_a_pending_prediction_is_counted_but_not_judged(client):
    """An unjudged prediction is not evidence either way, so it stays out of the
    percentage — but the page can still say how many are waiting."""
    _close_and_judge(client, symbol="NVDA", exit_price="9.5", outcome="CORRECT")
    _close_and_judge(client, symbol="AAPL", exit_price="9.5", outcome=None)

    view = _board(client)["prediction_accuracy"]

    assert view["pending"] == 1
    assert view["judged"] == 1
    assert view["accuracy"] == 1.0  # one of one, not one of two


def test_accuracy_is_none_when_nothing_has_been_judged(client):
    """No number rather than a fake zero. Nothing has been decided yet."""
    _close_and_judge(client, symbol="NVDA", exit_price="9.5", outcome=None)

    view = _board(client)["prediction_accuracy"]

    assert view["accuracy"] is None
    assert view["judged"] == 0
    assert view["pending"] == 1


def test_a_losing_trade_can_still_carry_a_correct_prediction(client):
    """The case the feature exists for, seen from the scoreboard.

    The trade is a loss — it was sold below its entry — and the win rate counts
    it as one. The prediction is correct. Both appear, and neither is bent to
    look like the other.
    """
    _close_and_judge(client, symbol="NVDA", exit_price="9.5", outcome="CORRECT")

    board = _board(client)

    assert board["losses"] == 1
    assert board["wins"] == 0
    assert board["prediction_accuracy"]["correct"] == 1
    assert board["prediction_accuracy"]["accuracy"] == 1.0
