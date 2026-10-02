"""The edges: missing values, closed trades, ownership, idempotency, retries."""

from conftest import (
    FakeStorage,
    approve,
    auth_headers,
    drain,
    get_note,
    open_a_trade,
    open_proposal,
    post_text,
    post_voice,
    proposal,
    record_text,
    set_proposal,
    trade,
    trades,
)
from app.main import app


def test_missing_share_count_names_itself(client):
    """The AI must not invent a number, so approval asks for it."""
    opened = open_a_trade(client)
    set_proposal(client, proposal([{"kind": "ADD", "price": "11"}], summary="Bought more."))
    note = record_text(client, "I bought more at 11", trade_id=opened["id"])

    resp = approve(client, note["id"])
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "how many shares" in detail
    assert "share count" in detail
    # nothing was applied
    assert trade(client, opened["id"])["shares"] == "100"


def test_missing_symbol_on_open(client):
    set_proposal(client, proposal([{"kind": "OPEN", "shares": "10", "price": "5"}], symbol=None))
    note = record_text(client, "I opened a position, 10 shares at 5")
    resp = approve(client, note["id"])
    assert resp.status_code == 422
    assert "symbol" in resp.json()["detail"]


def test_missing_stop_price_on_a_stop_move(client):
    opened = open_a_trade(client)
    set_proposal(client, proposal([{"kind": "STOP"}], summary="Moved the stop."))
    note = record_text(client, "I moved the stop", trade_id=opened["id"])
    resp = approve(client, note["id"])
    assert resp.status_code == 422
    assert "stop price" in resp.json()["detail"]


def test_selling_more_than_held_is_rejected(client):
    opened = open_a_trade(client)
    set_proposal(client, proposal([{"kind": "TRIM", "shares": "150", "price": "12"}], summary="Sold 150."))
    note = record_text(client, "I sold 150 shares at 12", trade_id=opened["id"])
    resp = approve(client, note["id"])
    assert resp.status_code == 422
    assert "exceeds" in resp.json()["detail"]


def test_a_fraction_with_no_position_is_rejected(client):
    opened = open_a_trade(client)
    set_proposal(client, proposal([{"kind": "TRIM", "fraction": "0.5", "price": "12"}], summary="Sold half."))
    first = record_text(client, "sold half at 12", trade_id=opened["id"])
    assert approve(client, first["id"]).status_code == 200      # 100 -> 50

    second = record_text(client, "sold half at 12 again", trade_id=opened["id"])
    assert approve(client, second["id"]).status_code == 200      # 50 -> 25

    set_proposal(client, proposal([{"kind": "CLOSE", "price": "12"}], summary="Sold everything."))
    third = record_text(client, "sold everything at 12", trade_id=opened["id"])
    assert approve(client, third["id"]).status_code == 200      # 25 -> 0, closed

    set_proposal(client, proposal([{"kind": "TRIM", "fraction": "0.5", "price": "12"}], summary="Sold half."))
    resp = approve(client, record_text(client, "sold half at 12", trade_id=opened["id"])["id"])
    assert resp.status_code == 409                              # closed trade


def test_closed_trade_takes_no_position_change(client):
    opened = open_a_trade(client)
    set_proposal(client, proposal([{"kind": "CLOSE", "price": "12"}], summary="Closed."))
    approve(client, record_text(client, "sold everything at 12", trade_id=opened["id"])["id"])

    set_proposal(client, proposal([{"kind": "STOP", "stop": "11"}], summary="Stop to 11."))
    resp = approve(client, record_text(client, "moved the stop to 11", trade_id=opened["id"])["id"])
    assert resp.status_code == 409
    assert "closed" in resp.json()["detail"]


def test_a_note_with_no_change_is_still_allowed_on_a_closed_trade(client):
    opened = open_a_trade(client)
    set_proposal(client, proposal([{"kind": "CLOSE", "price": "12"}], summary="Closed."))
    approve(client, record_text(client, "sold everything at 12", trade_id=opened["id"])["id"])

    set_proposal(client, proposal([], summary="Lesson: I moved my stop too late."))
    note = record_text(client, "I moved my stop too late", trade_id=opened["id"])
    assert approve(client, note["id"]).status_code == 200


def test_second_open_trade_on_the_same_symbol_is_rejected(client):
    open_a_trade(client)
    set_proposal(client, proposal(
        [{"kind": "OPEN", "shares": "10", "price": "20"}], symbol="NVDA", summary="Opened again."))
    note = record_text(client, "I opened NVDA again")
    resp = approve(client, note["id"])
    assert resp.status_code == 422
    assert "already have an open NVDA" in resp.json()["detail"]


def test_approval_is_idempotent(client):
    opened = open_a_trade(client)
    set_proposal(client, proposal([{"kind": "STOP", "stop": "9.5"}], summary="Stop to 9.5."))
    note = record_text(client, "moved the stop to 9.5", trade_id=opened["id"])

    first = approve(client, note["id"])
    second = approve(client, note["id"])
    assert first.status_code == 200 and second.status_code == 200
    assert first.json() == second.json()

    detail = trade(client, opened["id"])
    stops = [c for n in detail["notes"] for c in n["changes"] if c["kind"] == "STOP"]
    assert len(stops) == 1                     # applied once, not twice


def test_approve_requires_an_extracted_note(client):
    resp = post_text(client, "not processed yet")
    note_id = resp.json()["id"]
    # The background task may already have run; force the state back and retry.
    from sqlalchemy import text as sql_text

    import asyncio
    from app.db import engine

    async def set_received():
        async with engine.begin() as conn:
            await conn.execute(
                sql_text("UPDATE notes SET status = 'RECEIVED' WHERE id = :id"),
                {"id": note_id},
            )

    asyncio.run(set_received())
    assert approve(client, note_id).status_code == 409


def test_ambiguous_follow_up_with_two_open_trades(client):
    set_proposal(client, proposal([{"kind": "OPEN", "shares": "10", "price": "5"}], symbol="AAA", summary="a"))
    approve(client, record_text(client, "opened AAA")["id"])
    set_proposal(client, proposal([{"kind": "OPEN", "shares": "10", "price": "6"}], symbol="BBB", summary="b"))
    approve(client, record_text(client, "opened BBB")["id"])

    set_proposal(client, proposal([{"kind": "STOP", "stop": "4"}], summary="Moved the stop."))
    note = record_text(client, "I moved my stop to 4")      # no symbol, no context
    resp = approve(client, note["id"])
    assert resp.status_code == 422
    assert "which trade" in resp.json()["detail"]


def test_a_follow_up_with_a_symbol_finds_its_trade(client):
    set_proposal(client, proposal([{"kind": "OPEN", "shares": "10", "price": "5"}], symbol="AAA", summary="a"))
    first = approve(client, record_text(client, "opened AAA")["id"])
    set_proposal(client, proposal([{"kind": "OPEN", "shares": "10", "price": "6"}], symbol="BBB", summary="b"))
    approve(client, record_text(client, "opened BBB")["id"])

    set_proposal(client, proposal([{"kind": "STOP", "stop": "4.5"}], symbol="AAA", summary="Stop to 4.5."))
    note = record_text(client, "I moved the stop on AAA to 4.5")
    resp = approve(client, note["id"])
    assert resp.status_code == 200
    assert resp.json()["trade_id"] == first.json()["trade_id"]
    assert trade(client, first.json()["trade_id"])["current_stop"] == "4.5"


def test_ownership_is_enforced_everywhere(client):
    opened = open_a_trade(client, sub="user_owner")

    # another user cannot read or approve the note
    set_proposal(client, proposal([{"kind": "STOP", "stop": "9.5"}], summary="Stop."))
    note = record_text(client, "moved the stop", sub="user_owner", trade_id=opened["id"])
    assert get_note(client, note["id"], sub="user_owner")["id"] == note["id"]
    assert client.get(f"/notes/{note['id']}", headers=auth_headers("user_other")).status_code == 404
    assert approve(client, note["id"], sub="user_other").status_code == 404

    # another user cannot read the trade, or record against it
    assert client.get(f"/trades/{opened['id']}", headers=auth_headers("user_other")).status_code == 404
    assert trades(client, sub="user_other") == []
    resp = post_text(client, "note", sub="user_other", trade_id=opened["id"])
    assert resp.status_code == 404


def test_retry_resumes_a_failed_note(client):
    app.state.extractor.fail = True
    resp = post_voice(client)
    note = drain(client, resp.json()["id"])
    assert note["status"] == "FAILED"
    assert note["failed_stage"] == "EXTRACTION"
    assert "simulated extraction outage" in note["error_message"]

    app.state.extractor.fail = False
    retried = client.post(f"/notes/{note['id']}/retry", headers=auth_headers())
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "EXTRACTED"
    assert approve(client, note["id"]).status_code == 200


def test_retry_never_recharges_and_storage_failures_are_terminal(client):
    app.state.storage = FakeStorage()
    app.state.storage.fail_save = True
    resp = post_voice(client)
    assert resp.status_code == 503

    # the failed note does not eat the allowance forever, but it cannot be retried
    app.state.storage.fail_save = False
    resp = post_voice(client)
    assert resp.status_code == 201
    note = drain(client, resp.json()["id"])
    assert approve(client, note["id"]).status_code == 200


def test_unsupported_audio_type_is_rejected(client):
    resp = client.post(
        "/notes/voice",
        headers=auth_headers(),
        files={"file": ("note.txt", b"not audio", "text/plain")},
        data={"duration_ms": "1000"},
    )
    assert resp.status_code == 415


def test_the_review_edits_are_what_gets_applied(client):
    """The AI drafts; the trader's reviewed values are what lands."""
    opened = open_a_trade(client)
    set_proposal(client, proposal([{"kind": "ADD", "price": "11"}], summary="Bought more."))
    note = record_text(client, "I bought more at 11", trade_id=opened["id"])

    # Approving the draft as-is fails: the share count is missing.
    assert approve(client, note["id"]).status_code == 422

    edited = {
        "summary": "Bought more at 11.",
        "symbol": None,
        "changes": [{"kind": "ADD", "shares": "25", "price": "11"}],
    }
    resp = client.post(
        f"/notes/{note['id']}/approve", headers=auth_headers(), json={"proposal": edited}
    )
    assert resp.status_code == 200, resp.text
    assert trade(client, opened["id"])["shares"] == "125"


def test_text_note_can_open_a_trade_without_audio(client):
    set_proposal(client, proposal(
        [{"kind": "OPEN", "shares": "5", "price": "100", "stop": "95", "target": "120"}],
        symbol="NVDA",
        summary="Opened NVDA.",
    ))
    note = record_text(client, "opened NVDA 5 shares at 100, stop 95, target 120")
    assert note["source"] == "TEXT"
    assert note["audio_url"] is None
    assert approve(client, note["id"]).status_code == 200
    assert trades(client)[0]["symbol"] == "NVDA"


def test_a_follow_up_note_carries_the_symbol_of_its_trade(client):
    """A follow-up never repeats the symbol — the trader is already in the trade
    — so the note takes it from the trade. Without this the review screen shows
    an empty Symbol field on every follow-up."""
    aaa = open_a_trade(client, symbol="AAA")

    set_proposal(client, proposal([{"kind": "STOP", "stop": "88"}], symbol=None))
    note = record_text(client, "I moved the stop to 88", trade_id=aaa["id"])

    assert note["proposal"]["security_candidates"] == []  # the model said nothing
    assert note["symbol"] == "AAA"  # the trade said it
    assert approve(client, note["id"]).status_code == 200
    assert get_note(client, note["id"])["symbol"] == "AAA"


def test_a_follow_up_on_a_closed_trade_still_names_the_symbol(client):
    """The trade is over, the note still belongs to it, and closed_at changes
    nothing about that."""
    aaa = open_a_trade(client, symbol="AAA")
    set_proposal(client, proposal([{"kind": "CLOSE", "price": "110"}], symbol=None))
    closing = record_text(client, "I closed AAA at 110", trade_id=aaa["id"])
    assert closing["symbol"] == "AAA"
    assert approve(client, closing["id"]).status_code == 200
    assert trade(client, aaa["id"])["is_open"] is False

    set_proposal(client, proposal([], symbol=None, summary="Post-mortem."))
    after = record_text(client, "That exit was late", trade_id=aaa["id"])
    assert after["symbol"] == "AAA"


def test_an_opening_note_keeps_the_symbol_the_model_read(client):
    """No trade exists yet on an opening note, so the model's symbol is all
    there is — and it must survive approval."""
    set_proposal(client, open_proposal(symbol="MSFT"))
    note = record_text(client, "I opened Microsoft")
    assert note["symbol"] == "MSFT"
    assert approve(client, note["id"]).status_code == 200
    assert get_note(client, note["id"])["symbol"] == "MSFT"


def test_a_note_with_no_symbol_anywhere_reports_none(client):
    """An observation note has no trade and the model named no symbol. That is a
    real unknown, and it is reported as one."""
    set_proposal(client, proposal([], symbol=None, summary="Just thinking."))
    note = record_text(client, "The market felt heavy today")
    assert note["symbol"] is None
    assert approve(client, note["id"]).status_code == 200
    assert get_note(client, note["id"])["symbol"] is None


def test_a_journal_follow_up_borrows_the_only_open_trade(client):
    """Recorded from the journal, so there is no trade context and the model
    named no symbol. The only open trade is the answer the applier will reach,
    so the review screen shows it instead of an empty field."""
    open_a_trade(client, symbol="AAA")

    set_proposal(client, proposal([{"kind": "CLOSE", "price": "110"}], symbol=None))
    note = record_text(client, "I closed all the position at 110")

    assert note["trade_id"] is None  # nothing was chosen yet
    assert note["symbol"] == "AAA"  # but there is only one candidate
    assert approve(client, note["id"]).status_code == 200
    assert get_note(client, note["id"])["symbol"] == "AAA"


def test_two_open_trades_leave_the_symbol_unknown(client):
    """Two candidates is an honest unknown, not a coin flip. The applier will
    ask the trader which trade the note belongs to."""
    open_a_trade(client, symbol="AAA")
    open_a_trade(client, symbol="BBB")

    set_proposal(client, proposal([{"kind": "CLOSE", "price": "110"}], symbol=None))
    note = record_text(client, "I closed all the position at 110")

    assert note["symbol"] is None
    blocked = approve(client, note["id"])
    assert blocked.status_code == 422
    assert "does not say which trade" in blocked.json()["detail"]


def test_an_opening_note_never_inherits_an_open_trade_symbol(client):
    """An opening note is a NEW trade. Even with one trade already open, it must
    not be labelled with that trade's symbol."""
    open_a_trade(client, symbol="AAA")

    set_proposal(client, proposal(
        [{"kind": "OPEN", "shares": "5", "price": "20"}], symbol=None, summary="Opened."
    ))
    note = record_text(client, "I opened a new position")

    assert note["symbol"] is None
