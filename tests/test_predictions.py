"""Prediction accuracy: whether the trader's expectation about what happens next
was right — measured apart from the trade's own result.

The bug this pins: a trade closed early at a loss, because the trader expected it
to fail, was scored only as a loss. If the price then reached the failure point he
predicted, his prediction was correct, and that has to be visible.
"""

from conftest import (
    approve,
    open_a_trade,
    proposal,
    record_text,
    set_proposal,
    trade,
)


def _close_with_a_prediction(client, *, reason: str = "I expect it to fail"):
    """Open a trade, close it expecting failure. Returns the trade detail."""
    opened = open_a_trade(client, symbol="NVDA")  # entry 10, stop 9, target 12
    set_proposal(
        client,
        proposal(
            [{"kind": "CLOSE", "price": "9.5"}],
            prediction={"reason": reason},
        ),
    )
    note = record_text(
        client, "I am out, I expect it to fail", trade_id=opened["id"]
    )
    assert approve(client, note["id"]).status_code == 200
    return trade(client, opened["id"])


def test_closing_a_trade_records_the_prediction(client):
    """The level is the trade's own stop — the failure point he is talking about.

    He did not name a price, and he does not have to: the trade already holds the
    stop, and that is what "my failure point" means.
    """
    detail = _close_with_a_prediction(client)

    assert detail["prediction"] is not None
    assert detail["prediction"]["result"] == "PENDING"
    assert detail["prediction"]["level"] == "9"
    assert detail["prediction"]["direction"] == "BELOW"
    assert "fail" in detail["prediction"]["reason"]


def test_a_later_note_resolves_the_prediction(client):
    """The trade is already closed, so a follow-up note can only be about this."""
    detail = _close_with_a_prediction(client)

    set_proposal(
        client,
        proposal(
            [],
            resolution={"outcome": "CORRECT", "note": "it reached my failure point"},
        ),
    )
    note = record_text(client, "it hit the failure point", trade_id=detail["id"])
    assert approve(client, note["id"]).status_code == 200

    after = trade(client, detail["id"])
    assert after["prediction"]["result"] == "CORRECT"
    assert after["prediction"]["resolved_at"] is not None


def test_the_trade_result_and_the_prediction_are_separate(client):
    """The case the whole feature exists for: a LOSS with a CORRECT prediction.

    The trade result does not change when the prediction is judged. The loss
    stays a loss; the prediction is counted separately.
    """
    detail = _close_with_a_prediction(client)

    # Sold at 9.5 against an entry of 10: a losing trade, by its own number.
    assert detail["realized_pnl"].startswith("-")

    set_proposal(
        client,
        proposal([], resolution={"outcome": "CORRECT", "note": "it reached 9"}),
    )
    note = record_text(client, "it reached 9", trade_id=detail["id"])
    approve(client, note["id"])

    after = trade(client, detail["id"])
    assert after["realized_pnl"] == detail["realized_pnl"]  # still the same loss
    assert after["prediction"]["result"] == "CORRECT"  # but the reading was right


def test_an_incorrect_prediction_is_recorded_too(client):
    """He expected a failure and the trade went on to the target."""
    detail = _close_with_a_prediction(client)

    set_proposal(
        client,
        proposal([], resolution={"outcome": "INCORRECT", "note": "it recovered"}),
    )
    note = record_text(client, "it recovered and kept going", trade_id=detail["id"])
    approve(client, note["id"])

    after = trade(client, detail["id"])
    assert after["prediction"]["result"] == "INCORRECT"


def test_a_closed_trade_with_no_prediction_has_none(client):
    """A trade closed without an expectation records no prediction, and that is
    not the same as predicting wrongly."""
    opened = open_a_trade(client, symbol="NVDA")
    set_proposal(client, proposal([{"kind": "CLOSE", "price": "12"}]))
    note = record_text(client, "sold everything", trade_id=opened["id"])
    approve(client, note["id"])

    assert trade(client, opened["id"])["prediction"] is None


def test_an_open_does_not_record_a_prediction(client):
    """The stop of a new position is not a prediction about the future.

    Rule 12 tells the extractor that a prediction belongs to a closing note, but
    the stop of a brand new position reads almost exactly like the "failure
    point" a prediction is about, so one can arrive on an OPEN. The applier
    decides by the note's own changes instead of trusting the proposal: a note
    that does not close the trade records no prediction, whatever it offered.
    """
    set_proposal(
        client,
        proposal(
            [
                {
                    "kind": "OPEN",
                    "shares": "100",
                    "price": "10",
                    "stop": "9",
                    "target": "12",
                }
            ],
            symbol="NVDA",
            prediction={"reason": "I expect it to fail and reach my stop"},
        ),
    )
    note = record_text(client, "I opened NVDA at 10, stop 9, target 12")
    result = approve(client, note["id"])
    assert result.status_code == 200, result.text

    detail = trade(client, result.json()["trade_id"])

    assert detail["is_open"] is True
    assert detail["prediction"] is None
    # The stop is still on the trade: it is a level, not a forecast.
    assert detail["current_stop"] is not None
