"""Deleting a trade: a HARD delete of the trade and everything recorded on it.

This is the one place the app destroys history. The trader asks for it from the
trade page, behind a confirmation, and nothing is restored afterwards — so the
tests pin what "the trade" means: its notes, the changes those notes applied,
its prediction, and the media in storage.
"""

import asyncio

from sqlalchemy import text

from app.db import engine
from app.main import app
from conftest import (
    approve,
    auth_headers,
    drain,
    on_free,
    open_a_trade,
    open_proposal,
    post_voice,
    proposal,
    record_text,
    set_proposal,
    trades,
)


def delete(client, trade_id: str, sub: str = "user_test1"):
    return client.delete(f"/trades/{trade_id}", headers=auth_headers(sub))


def rows(sql: str, **params) -> int:
    """A count straight out of the database.

    The API cannot show an orphaned change row: nothing reads one. Only the
    table can say whether the applied history really went with the trade.
    """

    async def _run() -> int:
        async with engine.begin() as conn:
            return (await conn.execute(text(sql), params)).scalar()

    return asyncio.run(_run())


def a_trade_with_history(client) -> dict:
    """One open trade plus an applied follow-up: two notes and two changes."""
    opened = open_a_trade(
        client, symbol="AAA", shares="10", price="100", stop="95", target="120"
    )
    set_proposal(client, proposal([{"kind": "TRIM", "shares": "4", "price": "110"}], symbol="AAA"))
    follow_up = record_text(client, "I sold 4 at 110", trade_id=opened["id"])
    assert approve(client, follow_up["id"]).status_code == 200
    return opened


def test_deleting_a_trade_removes_it_and_everything_recorded_on_it(client):
    """The applied history goes too: no change row outlives its trade."""
    opened = a_trade_with_history(client)
    assert rows("select count(*) from changes") == 2  # OPEN + TRIM
    assert rows("select count(*) from notes") == 2

    resp = delete(client, opened["id"])

    assert resp.status_code == 204, resp.text
    assert resp.content == b""
    assert (
        client.get(f"/trades/{opened['id']}", headers=auth_headers()).status_code == 404
    )
    assert all(t["id"] != opened["id"] for t in trades(client))
    assert rows("select count(*) from changes") == 0
    assert rows("select count(*) from notes") == 0


def test_deleting_a_trade_removes_the_recording_stored_for_it(client):
    """The recording must not outlive the trade in a bucket."""
    opened = open_a_trade(client, symbol="AAA")
    note = drain(client, post_voice(client, trade_id=opened["id"]).json()["id"])
    keys = list(app.state.storage.objects)
    assert len(keys) == 1  # the follow-up recording is stored

    assert delete(client, opened["id"]).status_code == 204

    assert app.state.storage.deleted == keys
    assert app.state.storage.objects == {}
    assert client.get(f"/notes/{note['id']}", headers=auth_headers()).status_code == 404


def test_a_storage_failure_does_not_keep_the_trade(client):
    """The rows are the copy that matters: an outage cannot block the delete."""
    opened = open_a_trade(client, symbol="AAA")
    drain(client, post_voice(client, trade_id=opened["id"]).json()["id"])
    app.state.storage.fail_delete = True

    assert delete(client, opened["id"]).status_code == 204
    assert (
        client.get(f"/trades/{opened['id']}", headers=auth_headers()).status_code == 404
    )


def test_a_note_that_was_not_recorded_on_the_trade_stays(client):
    """A market note is not part of a trade, so deleting the trade spares it."""
    opened = open_a_trade(client, symbol="AAA")
    loose = record_text(client, "I saw a reverse candle, I will wait")
    assert loose["trade_id"] is None

    assert delete(client, opened["id"]).status_code == 204

    assert client.get(f"/notes/{loose['id']}", headers=auth_headers()).status_code == 200


def test_a_deleted_trade_leaves_the_scoreboard(client):
    """The prediction goes first: it points at the trade and at its notes."""
    opened = open_a_trade(client, symbol="AAA", stop="95")
    set_proposal(
        client,
        proposal(
            [{"kind": "CLOSE", "price": "110"}],
            symbol="AAA",
            prediction={"reason": "I expect it to fail"},
        ),
    )
    closing = record_text(client, "I sold all AAA at 110", trade_id=opened["id"])
    assert approve(client, closing["id"]).status_code == 200

    # A later note judges it, so the prediction row points at two of the trade's
    # own notes — both of which the delete has to take first.
    set_proposal(
        client,
        proposal([], resolution={"outcome": "INCORRECT", "note": "it went up instead"}),
    )
    judged = record_text(client, "it went up instead", trade_id=opened["id"])
    assert approve(client, judged["id"]).status_code == 200

    before = client.get("/analytics", headers=auth_headers()).json()
    assert before["closed_trades"] == 1
    assert before["notes_total"] == 3
    assert before["prediction_accuracy"]["judged"] == 1
    assert rows("select count(*) from predictions") == 1
    assert (
        rows("select count(*) from predictions where resolved_by_note_id is not null") == 1
    )

    assert delete(client, opened["id"]).status_code == 204

    after = client.get("/analytics", headers=auth_headers()).json()
    assert after["closed_trades"] == 0
    assert after["notes_total"] == 0
    assert after["prediction_accuracy"]["judged"] == 0
    assert after["prediction_accuracy"]["pending"] == 0
    assert rows("select count(*) from predictions") == 0
    assert rows("select count(*) from changes") == 0


def test_deleting_a_trade_frees_a_slot_on_the_free_plan(client):
    """Three active trades on Free: a delete gives one back."""
    on_free(client)
    aaa = open_a_trade(client, symbol="AAA")
    open_a_trade(client, symbol="BBB")
    open_a_trade(client, symbol="CCC")

    set_proposal(client, open_proposal(symbol="DDD"))
    blocked = record_text(client, "I opened a position in DDD")
    assert approve(client, blocked["id"]).status_code == 402

    assert delete(client, aaa["id"]).status_code == 204

    assert approve(client, blocked["id"]).status_code == 200
    usage = client.get("/me", headers=auth_headers()).json()["usage"]
    assert usage["active_trades_used"] == 3


def test_another_traders_trade_cannot_be_deleted(client):
    """A foreign id is a 404, never a hint that it exists."""
    theirs = open_a_trade(client, symbol="AAA", sub="user_a")

    assert delete(client, theirs["id"], sub="user_b").status_code == 404
    assert (
        client.get(f"/trades/{theirs['id']}", headers=auth_headers("user_a")).status_code
        == 200
    )


def test_deleting_twice_is_a_404(client):
    opened = open_a_trade(client, symbol="AAA")

    assert delete(client, opened["id"]).status_code == 204
    assert delete(client, opened["id"]).status_code == 404


def test_a_trade_that_never_existed_is_a_404(client):
    resp = delete(client, "00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Trade not found"
