"""The weekly review: one week's numbers, and the one sentence it produces.

Three rules these tests pin:

- a review is about a week that has ENDED, and it defaults to the last one
- the numbers are derived from the change rows, never stored in the review
- the sentence is the only thing stored, and it governs the week after the one
  being reviewed — which is what Today shows

Moving a trade or a note into the past is done in the table: the API has no route
for it and should not have one.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone

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


def _monday(weeks_ago: int = 0) -> date:
    today = datetime.now(timezone.utc).date()
    return today - timedelta(days=today.weekday()) - timedelta(weeks=weeks_ago)


async def _run(sql: str, params: dict) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(sql), params)


def _move_trade(trade_id: str, day: date) -> None:
    """Land a closed trade in the past, both timestamps together.

    `closed_at >= opened_at` is a CHECK, so a test that moved only one would be
    testing a state the app cannot produce.
    """
    moment = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
    asyncio.run(
        _run(
            "UPDATE trades "
            "SET opened_at = CAST(:at AS timestamptz) - interval '5 days', "
            "    closed_at = CAST(:at AS timestamptz) "
            "WHERE id = :id",
            {"at": moment, "id": trade_id},
        )
    )


def _move_note(note_id: str, day: date) -> None:
    moment = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
    asyncio.run(
        _run(
            "UPDATE notes SET created_at = CAST(:at AS timestamptz) WHERE id = :id",
            {"at": moment, "id": note_id},
        )
    )


def _review(client, **params) -> dict:
    resp = client.get("/reviews", headers=auth_headers(), params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _finish(client, commitment: str, week_start: str | None = None):
    body: dict = {"commitment": commitment}
    if week_start:
        body["week_start"] = week_start
    return client.post("/reviews", headers=auth_headers(), json=body)


def _closed_trade(client, *, symbol: str = "NVDA", exit_price: str = "11") -> dict:
    """Open a trade and close it. Entry 10, stop 9, so the R is easy to read."""
    opened = open_a_trade(client, symbol=symbol)
    set_proposal(client, proposal([{"kind": "CLOSE", "price": exit_price}]))
    note = record_text(client, "I am out", trade_id=opened["id"])
    assert approve(client, note["id"]).status_code == 200
    return opened


# --- which week ------------------------------------------------------------


def test_a_review_is_about_the_last_week_that_ended(client):
    """Never the live week: a review of four unfinished days freezes a guess."""
    view = _review(client)

    assert date.fromisoformat(view["week_start"]) == _monday(1)
    assert date.fromisoformat(view["week_start"]).weekday() == 0
    assert date.fromisoformat(view["week_end"]) - date.fromisoformat(view["week_start"]) == timedelta(days=6)
    assert view["completed"] is False
    assert view["commitment"] is None


def test_a_review_of_the_live_week_is_refused(client):
    resp = _finish(client, "Never lower a stop.", week_start=_monday(0).isoformat())

    assert resp.status_code == 422
    assert "not ended" in resp.json()["detail"]


def test_a_week_that_does_not_start_on_monday_is_refused(client):
    resp = client.get(
        "/reviews",
        headers=auth_headers(),
        params={"week_start": (_monday(2) + timedelta(days=2)).isoformat()},
    )

    assert resp.status_code == 422
    assert "Monday" in resp.json()["detail"]


def test_a_past_week_can_be_reviewed_by_name(client):
    """A skipped week is fillable: the row is keyed by the week, not by 'now'."""
    resp = _finish(client, "Size every entry at 1R.", week_start=_monday(3).isoformat())
    assert resp.status_code == 200, resp.text

    view = _review(client, week_start=_monday(3).isoformat())
    assert view["completed"] is True
    assert view["commitment"] == "Size every entry at 1R."


# --- the numbers -----------------------------------------------------------


def test_the_numbers_come_from_the_trades_closed_inside_the_week(client):
    """+1R and -2R, both closed last week: net -1R, two rated, one stop held."""
    good = _closed_trade(client, symbol="NVDA", exit_price="11")
    bad = _closed_trade(client, symbol="AAPL", exit_price="8")
    middle = _monday(1) + timedelta(days=2)
    _move_trade(good["id"], middle)
    _move_trade(bad["id"], middle)

    view = _review(client)

    assert view["trades_closed"] == 2
    assert view["rated_trades"] == 2
    assert view["net_r"] == "-1"
    assert view["stops_held"] == 1


def test_a_trade_closed_this_week_is_not_in_last_weeks_review(client):
    _closed_trade(client, symbol="NVDA", exit_price="11")

    view = _review(client)

    assert view["trades_closed"] == 0
    assert view["net_r"] is None
    assert view["rated_trades"] == 0


def test_the_notes_of_the_week_are_listed_with_their_own_summaries(client):
    opened = open_a_trade(client, symbol="NVDA")
    noted = record_text(client, "I am holding through the pullback", trade_id=opened["id"])
    _move_note(noted["id"], _monday(1) + timedelta(days=1))

    view = _review(client)

    assert [note["id"] for note in view["notes"]] == [noted["id"]]
    assert view["notes"][0]["summary"]


# --- the sentence ----------------------------------------------------------


def test_finishing_a_review_stores_the_sentence_and_the_row_is_the_completion(client):
    resp = _finish(client, "Never lower a stop once it is set.")
    assert resp.status_code == 200, resp.text
    assert resp.json()["completed"] is True

    view = _review(client)
    assert view["completed"] is True
    assert view["commitment"] == "Never lower a stop once it is set."


def test_the_commitment_is_the_one_today_shows(client):
    """It governs the FOLLOWING week, so the latest sentence is the active one."""
    _finish(client, "Only trade the first hour.")

    view = _review(client)

    assert view["active_commitment"] == "Only trade the first hour."


def test_a_second_sentence_for_the_same_week_edits_it(client):
    _finish(client, "Only trade the first hour.")
    _finish(client, "Only trade the first hour, and never before 10.")

    view = _review(client)
    assert view["commitment"] == "Only trade the first hour, and never before 10."
    assert view["streak_weeks"] == 1


def test_an_empty_commitment_is_refused(client):
    resp = _finish(client, "   ")

    assert resp.status_code == 422


def test_the_streak_counts_consecutive_finished_weeks(client):
    _finish(client, "Week three.", week_start=_monday(3).isoformat())
    _finish(client, "Week two.", week_start=_monday(2).isoformat())
    _finish(client, "Week one.", week_start=_monday(1).isoformat())

    assert _review(client)["streak_weeks"] == 3


def test_the_streak_stops_at_a_week_that_was_skipped(client):
    """Two rows two weeks apart is a run of ONE: a gap ends the count."""
    _finish(client, "Week three.", week_start=_monday(3).isoformat())
    _finish(client, "Week one.", week_start=_monday(1).isoformat())

    assert _review(client)["streak_weeks"] == 1


def test_the_streak_starts_at_zero_before_any_review(client):
    assert _review(client)["streak_weeks"] == 0
