"""Which trade a note belongs to — one answer, for the screen and the applier.

The bug this file pins: the review screen trusted the ticker the model read out
of the words, so a mishearing showed up as a confident wrong symbol, and a
follow-up that named no trade showed up as an empty field. The screen and the
applier now ask the same resolver, so they cannot disagree about a trade.
"""

from conftest import (
    approve,
    auth_headers,
    drain,
    get_note,
    open_a_trade,
    open_proposal,
    post_voice,
    proposal,
    record_text,
    set_proposal,
    trade,
)


def _two_open_trades(client) -> tuple[dict, dict]:
    first = open_a_trade(client, symbol="NVDA")
    second = open_a_trade(client, symbol="AAPL")
    return first, second


def test_a_follow_up_that_names_no_trade_offers_the_open_trades(client):
    """The empty Symbol field was the bug: the answer is unknown, so ask."""
    _two_open_trades(client)
    set_proposal(client, proposal([{"kind": "TRIM", "shares": "10", "price": "12"}]))

    note = record_text(client, "sold ten shares")

    assert note["symbol"] is None
    assert sorted(note["symbol_candidates"]) == ["AAPL", "NVDA"]


def test_a_misread_ticker_never_invents_a_trade(client):
    """A symbol the trader does not hold is not a trade reference."""
    _two_open_trades(client)
    set_proposal(
        client,
        proposal([{"kind": "TRIM", "shares": "10", "price": "12"}], symbol="ZZZZ"),
    )

    note = record_text(client, "sold ten shares")

    assert note["symbol"] is None  # not "ZZZZ"
    assert sorted(note["symbol_candidates"]) == ["AAPL", "NVDA"]

    result = approve(client, note["id"])
    assert result.status_code == 422
    detail = result.json()["detail"]
    assert "NVDA" in detail and "AAPL" in detail


def test_a_note_recorded_from_the_trade_page_wears_that_trade_symbol(client):
    """The trade wins over the model's reading, always."""
    first, _second = _two_open_trades(client)
    set_proposal(
        client,
        proposal([{"kind": "TRIM", "shares": "10", "price": "12"}], symbol="ZZZZ"),
    )

    note = record_text(client, "sold ten shares", trade_id=first["id"])

    assert note["symbol"] == "NVDA"
    assert note["symbol_candidates"] is None

    assert approve(client, note["id"]).status_code == 200
    assert trade(client, first["id"])["shares"] == "90"


def test_a_follow_up_in_one_of_two_trades_resolves_by_its_symbol(client):
    """A symbol that matches a trade the trader holds is a fact, not a guess."""
    first, second = _two_open_trades(client)
    set_proposal(
        client,
        proposal([{"kind": "TRIM", "shares": "10", "price": "12"}], symbol="AAPL"),
    )

    note = record_text(client, "sold ten shares of apple")

    assert note["symbol"] == "AAPL"
    assert note["symbol_candidates"] is None

    assert approve(client, note["id"]).status_code == 200
    assert trade(client, second["id"])["shares"] == "90"
    assert trade(client, first["id"])["shares"] == "100"


def test_the_screen_and_the_applier_agree_when_one_trade_is_open(client):
    """With a single open trade the answer is known, and the screen shows it."""
    only = open_a_trade(client, symbol="NVDA")
    set_proposal(client, proposal([{"kind": "STOP", "stop": "9.5"}]))

    note = record_text(client, "moved the stop to 9.5")

    assert note["symbol"] == "NVDA"
    assert note["symbol_candidates"] is None
    assert approve(client, note["id"]).status_code == 200
    assert trade(client, only["id"])["current_stop"] == "9.5"


def test_an_opening_note_shows_the_symbol_the_model_read(client):
    """An opening note has no trade yet, so the reading is all there is — and
    the trader confirms it on the review screen."""
    set_proposal(client, open_proposal(symbol="TSLA"))
    note = record_text(client, "I opened a position in Tesla")

    assert note["symbol"] == "TSLA"
    assert note["symbol_candidates"] is None


def test_a_note_with_no_change_needs_no_trade(client):
    """An observation is not about a trade, and it is never blocked."""
    _two_open_trades(client)
    set_proposal(client, proposal([], symbol="NVDA"))

    note = record_text(client, "I am watching Nvidia")

    assert note["symbol"] == "NVDA"
    assert note["symbol_candidates"] is None
    assert approve(client, note["id"]).status_code == 200


def test_an_observation_offers_the_open_trades_to_file_it_under(client):
    """A note that changes nothing and names no ticker used to come back empty
    with NOTHING to pick from: the trader could see the blank Symbol and not fix
    it. It is still not filed on its own — but the trades are offered."""
    _two_open_trades(client)
    set_proposal(client, proposal([]))

    note = record_text(client, "I noticed a head and shoulders top forming")

    assert note["symbol"] is None
    assert sorted(note["symbol_candidates"]) == ["AAPL", "NVDA"]


def test_an_observation_can_be_filed_under_a_trade(client):
    """Picking a trade at review attaches the note to it, and applies nothing."""
    nvda, _aapl = _two_open_trades(client)
    set_proposal(client, proposal([]))

    note = record_text(client, "I noticed a head and shoulders top forming")
    body = proposal([])
    body["security_candidates"] = [
        {"exchange": "US", "ticker": "NVDA", "name": "NVIDIA Corporation", "aliases": []}
    ]

    result = client.post(
        f"/notes/{note['id']}/approve",
        headers=auth_headers(),
        json={"proposal": body},
    )
    assert result.status_code == 200, result.text
    assert result.json()["changes_applied"] == 0

    after = get_note(client, note["id"])
    assert after["trade_id"] == nvda["id"]
    assert after["symbol"] == "NVDA"


def test_a_voice_follow_up_on_a_trade_wears_that_trade_symbol(client):
    """The path the trader actually uses, and the one no test covered: a VOICE
    note recorded from a trade page carries the trade in the form data, so the
    symbol is a fact. The extractor is made to read a different ticker here, to
    prove the trade beats the transcript."""
    nvda, _aapl = _two_open_trades(client)
    set_proposal(
        client,
        proposal([{"kind": "TRIM", "shares": "10", "price": "12"}], symbol="AAPL"),
    )

    resp = post_voice(client, trade_id=nvda["id"])
    assert resp.status_code == 201, resp.text
    note = drain(client, resp.json()["id"])

    assert note["status"] == "EXTRACTED"
    assert note["trade_id"] == nvda["id"]
    assert note["symbol"] == "NVDA"  # not "AAPL"
    assert not note["symbol_candidates"]
    # Same defect, same field type: the duration was read from the query string
    # too, so every recording was stored as 0 ms.
    assert note["duration_ms"] == 30000


def test_the_literal_string_null_is_not_a_ticker():
    """gpt-4o-mini answers with the string "null" when the note names no
    instrument. Left alone it becomes a trade whose ticker is NULL, which the
    trader then has to find and delete."""
    from app.schemas.proposal import NoteProposal

    blank = NoteProposal.model_validate(
        {
            "security_candidates": [
                {"exchange": "null", "ticker": "null", "name": " NULL ", "aliases": ["", "  "]}
            ]
        }
    )
    candidate = blank.security_candidates[0]
    assert candidate.ticker is None
    assert candidate.name is None
    assert candidate.exchange is None
    assert candidate.aliases == []

    named = NoteProposal.model_validate(
        {"security_candidates": [{"exchange": "EGX", "ticker": "ABUK", "name": "Abou Kir"}]}
    )
    assert named.security_candidates[0].ticker == "ABUK"
