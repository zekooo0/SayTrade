"""Discarding a note: gone for good, and never over applied history.

A note the trader has not approved can be thrown away — the row, the AI reading
and the recording. An approved note is the trade's history and stays.
"""

from uuid import UUID

from conftest import (
    approve,
    auth_headers,
    drain,
    get_note,
    open_a_trade,
    post_text,
    post_voice,
    proposal,
    record_text,
    set_proposal,
    trade,
)
from app.main import app
from app.models import Note


def discard(client, note_id: str, sub: str = "user_test1"):
    return client.delete(f"/notes/{note_id}", headers=auth_headers(sub))


def recorded(client, body: str = "I closed all the position at 110") -> dict:
    set_proposal(client, proposal([{"kind": "CLOSE", "price": "110"}], symbol=None))
    return record_text(client, body)


def test_an_extracted_note_can_be_discarded(client):
    """The note the AI read but the trader does not want is thrown away."""
    note = recorded(client, "a note I do not want to keep")

    resp = discard(client, note["id"])
    assert resp.status_code == 204, resp.text
    assert resp.content == b""

    assert client.get(f"/notes/{note['id']}", headers=auth_headers()).status_code == 404
    assert all(n["id"] != note["id"] for n in client.get("/notes", headers=auth_headers()).json())


def test_discarding_removes_the_recording(client):
    """A discarded note must not leave audio behind in storage."""
    resp = post_voice(client)
    note = drain(client, resp.json()["id"])
    keys = list(app.state.storage.objects)
    assert len(keys) == 1  # the recording is stored

    assert discard(client, note["id"]).status_code == 204

    assert app.state.storage.deleted == keys
    assert app.state.storage.objects == {}


def test_a_storage_failure_does_not_keep_the_note(client):
    """The row is the copy that matters: a storage outage cannot block a discard."""
    resp = post_voice(client)
    note = drain(client, resp.json()["id"])
    app.state.storage.fail_delete = True

    assert discard(client, note["id"]).status_code == 204
    assert client.get(f"/notes/{note['id']}", headers=auth_headers()).status_code == 404


def test_discarding_an_unapproved_follow_up_leaves_the_trade_alone(client):
    """Nothing was applied, so nothing changes: the trade keeps its numbers."""
    aaa = open_a_trade(client, symbol="AAA", shares="10", price="100", stop="95", target="120")
    before = trade(client, aaa["id"])
    note = recorded(client, "I closed all the position at 110")

    assert discard(client, note["id"]).status_code == 204

    after = trade(client, aaa["id"])
    assert after["shares"] == before["shares"] == "10"
    assert after["is_open"] is True
    assert after["current_stop"] == "95"
    assert after["realized_pnl"] is None
    assert after["note_count"] == before["note_count"] == 1


def test_an_approved_note_cannot_be_discarded(client):
    """Once a note moved a position it is the trade's history, not a draft."""
    aaa = open_a_trade(client, symbol="AAA", shares="10", price="100", stop="95", target="120")
    set_proposal(client, proposal([{"kind": "CLOSE", "price": "110"}], symbol="AAA"))
    closing = record_text(client, "I closed AAA at 110", trade_id=aaa["id"])
    assert approve(client, closing["id"]).status_code == 200

    blocked = discard(client, closing["id"])
    assert blocked.status_code == 409
    assert "part of the trade's history" in blocked.json()["detail"]

    # The note and its applied change both survive.
    assert get_note(client, closing["id"])["status"] == "APPROVED"
    closed = trade(client, aaa["id"])
    assert closed["is_open"] is False
    assert closed["realized_pnl"] == "100"


def test_a_failed_note_can_be_discarded(client):
    """A failed attempt is a draft too."""
    app.state.extractor.fail = True
    try:
        resp = post_text(client, "this will fail")
        note = drain(client, resp.json()["id"])
        assert note["status"] == "FAILED"
    finally:
        app.state.extractor.fail = False

    assert discard(client, note["id"]).status_code == 204


def test_another_traders_note_cannot_be_discarded(client):
    """A foreign id is a 404, never a hint that it exists."""
    resp = post_text(client, "mine", sub="user_a")
    note = drain(client, resp.json()["id"], sub="user_a")

    assert discard(client, note["id"], sub="user_b").status_code == 404
    assert client.get(f"/notes/{note['id']}", headers=auth_headers("user_a")).status_code == 200


def test_discarding_twice_is_a_404(client):
    note = recorded(client)
    assert discard(client, note["id"]).status_code == 204
    assert discard(client, note["id"]).status_code == 404


def test_a_discarded_note_stops_counting(client):
    """The journal counts what the trader keeps."""
    recorded(client, "keep me")
    throwaway = recorded(client, "throw me away")

    before = client.get("/analytics", headers=auth_headers()).json()
    assert before["notes_total"] == 2

    assert discard(client, throwaway["id"]).status_code == 204

    after = client.get("/analytics", headers=auth_headers()).json()
    assert after["notes_total"] == 1


class DiscardingExtractor:
    """Deletes the note while the AI is still reading it.

    This is what a trader tapping Discard mid-flight does to a running pipeline:
    the row is gone by the time the stage result is written. With `boom` the
    reading also breaks, which exercises the failure write on a vanished row.
    """

    def __init__(self, note_id: str, boom: bool = False):
        self.note_id = UUID(note_id)
        self.inner = app.state.extractor
        self.boom = boom

    async def extract(self, text: str, **kwargs):
        result = None if self.boom else await self.inner.extract(text, **kwargs)
        async with app.state.session_factory() as session:
            note = await session.get(Note, self.note_id)
            if note is not None:
                await session.delete(note)
                await session.commit()
        if self.boom:
            raise RuntimeError("the model is down")
        return result


def a_failed_note(client) -> dict:
    """A note whose reading broke, so it is ready for a retry."""
    app.state.extractor.fail = True
    try:
        note = drain(client, post_text(client, "I moved the stop to 88").json()["id"])
    finally:
        app.state.extractor.fail = False
    assert note["status"] == "FAILED"
    return note


def test_a_note_discarded_while_it_is_read_stays_discarded(client):
    """Nothing writes to a deleted row, and the retry says the note is gone."""
    note = a_failed_note(client)
    app.state.extractor = DiscardingExtractor(note["id"])

    resp = client.post(f"/notes/{note['id']}/retry", headers=auth_headers())

    assert resp.status_code == 404, resp.text
    assert "discarded" in resp.json()["detail"]
    assert client.get(f"/notes/{note['id']}", headers=auth_headers()).status_code == 404


def test_a_discard_and_a_broken_reading_together_stay_quiet(client):
    """The failure write is guarded too: a discarded note cannot be failed."""
    note = a_failed_note(client)
    app.state.extractor = DiscardingExtractor(note["id"], boom=True)

    resp = client.post(f"/notes/{note['id']}/retry", headers=auth_headers())

    assert resp.status_code == 404, resp.text
    assert client.get(f"/notes/{note['id']}", headers=auth_headers()).status_code == 404


def test_discarding_a_voice_note_frees_the_monthly_ai_unit(client):
    """The guard counts the notes the trader keeps, so a discarded voice note
    gives its unit back.

    This is a product decision and it is worth knowing: the AI spend already
    happened, so if the cap must count money spent rather than notes kept, it
    needs a counter of its own that survives the delete.
    """
    resp = post_voice(client)
    note = drain(client, resp.json()["id"])
    usage = client.get("/me", headers=auth_headers()).json()["usage"]
    assert usage["voice_notes_used"] == 1

    assert discard(client, note["id"]).status_code == 204

    usage = client.get("/me", headers=auth_headers()).json()["usage"]
    assert usage["voice_notes_used"] == 0
