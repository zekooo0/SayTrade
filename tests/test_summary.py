"""The one-line summary: the actions, never the words.

The bug this file pins: the summary was a field of the same extraction call that
read the transcript, so it repeated the transcription's errors and described the
words instead of the actions. It is now written from the changes — and rewritten
at approval, from the rows that are actually applied, so it can never describe a
proposal the trader edited away.
"""

from conftest import (
    FakeSummarizer,
    approve,
    auth_headers,
    get_note,
    open_proposal,
    proposal,
    record_text,
    set_proposal,
    trade,
    use_summarizer,
)

from app.services import summary as summary_service

OPENED = "Opened NVDA: bought 100 shares at 10, stop 9, target 12"


# --------------------------------------------------------------------------
# the template: what a note falls back to, and it is always true
# --------------------------------------------------------------------------


def test_the_template_names_the_action_in_english():
    line = summary_service.template_summary(
        [{"kind": "OPEN", "shares": "100", "price": "10", "stop": "9", "target": "12"}],
        symbol="NVDA",
        language="en",
    )
    assert line == OPENED


def test_the_template_names_the_action_in_arabic():
    line = summary_service.template_summary(
        [{"kind": "TRIM", "shares": "75", "price": "12"}], symbol="NVDA", language="ar"
    )
    assert line == "بيع 75 من NVDA بسعر 12"


def test_the_template_keeps_several_actions_in_order():
    line = summary_service.template_summary(
        [
            {"kind": "TRIM", "shares": "75", "price": "12"},
            {"kind": "STOP", "stop": "10.5"},
        ],
        symbol="NVDA",
        language="en",
    )
    assert line == "Sold 75 of NVDA at 12; Moved the NVDA stop to 10.5"


def test_a_note_with_no_action_has_no_summary():
    assert summary_service.template_summary([]) is None
    assert summary_service.template_summary([{"kind": ""}]) is None


def test_a_written_line_must_use_only_the_numbers_it_was_given():
    rows = summary_service.facts([{"kind": "TRIM", "shares": "75", "price": "12"}])

    assert summary_service.grounded("Sold 75 of NVDA at 12", rows)
    assert summary_service.grounded("Sold 75 of NVDA at 12.0", rows)
    # A figure the trader never gave is not allowed, however plausible.
    assert not summary_service.grounded("Sold 75 of NVDA at 12 for a 40% gain", rows)
    assert not summary_service.grounded("Sold 750 of NVDA at 12", rows)


def test_the_language_is_detected_from_the_words_when_it_is_not_reported():
    assert summary_service.detect_language("بعت 75 سهم بسعر 12") == "ar"
    assert summary_service.detect_language("sold 75 shares at 12") == "en"
    assert summary_service.language_of(None, "بعت 75 سهم") == "ar"
    assert summary_service.language_of("ar", "anything") == "ar"


# --------------------------------------------------------------------------
# the written line
# --------------------------------------------------------------------------


def test_the_summary_is_written_from_the_changes_not_the_words(client):
    """The call is handed the actions. The transcript is never part of it."""
    summarizer = FakeSummarizer()
    use_summarizer(summarizer)
    set_proposal(client, open_proposal(symbol="NVDA"))

    note = record_text(client, "I opened a position in Nvidia, 100 shares at 10")

    assert note["summary"] == "AI: OPEN"
    call = summarizer.calls[0]
    assert call["language"] == "en"
    assert call["symbol"] == "NVDA"
    assert call["changes"] == [
        {"kind": "OPEN", "shares": "100", "price": "10", "stop": "9", "target": "12"}
    ]
    # The words the model may have misheard are not in the call at all.
    assert "Nvidia" not in str(call)


def test_a_refused_summary_call_falls_back_to_the_template(client):
    summarizer = FakeSummarizer()
    summarizer.fail = True
    use_summarizer(summarizer)
    set_proposal(client, open_proposal(symbol="NVDA"))

    note = record_text(client, "I opened a position")

    assert note["summary"] == OPENED


def test_a_line_that_invents_a_number_is_thrown_away(client):
    """The model may phrase freely; it may not introduce a figure."""
    use_summarizer(FakeSummarizer(line="Bought 100 shares at 10 and made 300 dollars"))
    set_proposal(client, open_proposal(symbol="NVDA"))

    note = record_text(client, "I opened a position")

    assert note["summary"] == OPENED
    assert "300" not in note["summary"]


def test_an_arabic_note_gets_an_arabic_summary(client):
    set_proposal(
        client,
        proposal(
            [{"kind": "OPEN", "shares": "100", "price": "10", "stop": "9", "target": "12"}],
            symbol="NVDA",
            language="ar",
        ),
    )

    note = record_text(client, "فتحت مركز في إنفيديا واشتريت 100 سهم بسعر 10")

    assert note["summary"] == "فتح NVDA: شراء 100 سهم بسعر 10، وقف 9، هدف 12"


def test_a_note_whose_language_is_not_reported_is_read_from_its_words(client):
    body = proposal([{"kind": "TRIM", "shares": "75", "price": "12"}], symbol="NVDA")
    body.pop("language")
    set_proposal(client, body)

    note = record_text(client, "بعت 75 سهم بسعر 12")

    assert note["summary"] == "بيع 75 من NVDA بسعر 12"


# --------------------------------------------------------------------------
# the summary follows the trader's edits
# --------------------------------------------------------------------------


def test_editing_the_proposal_at_review_rewrites_the_summary(client):
    """The line describes what was applied, not the draft that was read."""
    set_proposal(client, open_proposal(symbol="NVDA", shares="100", price="10"))
    note = record_text(client, "I opened a position")

    assert note["summary"] == OPENED

    # The trader corrects the price on the review screen and approves that.
    result = client.post(
        f"/notes/{note['id']}/approve",
        headers=auth_headers(),
        json={
            "proposal": proposal(
                [
                    {
                        "kind": "OPEN",
                        "shares": "100",
                        "price": "11",
                        "stop": "9",
                        "target": "12",
                    }
                ],
                symbol="NVDA",
            )
        },
    )
    assert result.status_code == 200, result.text

    after = get_note(client, note["id"])
    assert after["summary"] == "Opened NVDA: bought 100 shares at 11, stop 9, target 12"
    assert trade(client, result.json()["trade_id"])["average_price"] == "11"


def test_an_observation_gets_a_line_about_what_the_trader_saw(client):
    """A note with no change still gets a line. There is no action to describe,
    so the line comes from the trader's words, and it may not invent a move."""
    summarizer = FakeSummarizer(line="Saw a reverse candle; waiting for tomorrow.")
    use_summarizer(summarizer)
    set_proposal(client, proposal([]))

    note = record_text(client, "I saw a reverse candle, I will wait for tomorrow")

    assert note["summary"] == "Saw a reverse candle; waiting for tomorrow."
    call = summarizer.calls[0]
    assert call["kind"] == "observation"
    assert call["language"] == "en"
    assert call["text"] == "I saw a reverse candle, I will wait for tomorrow"
    # The call is given the words, and no change list at all: there is none.
    assert "changes" not in call


def test_an_observation_with_no_model_keeps_the_traders_own_sentence(client):
    """The floor under an observation is the trader's own words."""
    set_proposal(client, proposal([]))

    note = record_text(client, "I saw a reverse candle. I will wait for tomorrow.")

    assert note["summary"] == "I saw a reverse candle."

    assert approve(client, note["id"]).status_code == 200
    assert get_note(client, note["id"])["summary"] == "I saw a reverse candle."


def test_an_observation_line_may_not_invent_a_number(client):
    use_summarizer(FakeSummarizer(line="Saw a reverse candle and expects a 40% move"))
    set_proposal(client, proposal([]))

    note = record_text(client, "I saw a reverse candle, I will wait")

    assert note["summary"] == "I saw a reverse candle, I will wait"


def test_an_observation_keeps_the_traders_own_language(client):
    set_proposal(client, proposal([]))

    note = record_text(client, "شفت شمعة انعكاس وهستنى بكرة")

    assert note["summary"] == "شفت شمعة انعكاس وهستنى بكرة"


def test_an_arabic_observation_is_written_in_arabic(client):
    summarizer = FakeSummarizer(line="شاف شمعة انعكاس وهينتظر بكرة.")
    use_summarizer(summarizer)
    body = proposal([])
    body["language"] = "ar"
    set_proposal(client, body)

    note = record_text(client, "شفت شمعة انعكاس وهستنى بكرة")

    assert note["summary"] == "شاف شمعة انعكاس وهينتظر بكرة."
    assert summarizer.calls[0]["language"] == "ar"


def test_removing_every_change_at_review_leaves_an_observation_line(client):
    """The trader deletes the drafted change: the line follows the approval, not
    the draft the model read."""
    set_proposal(client, open_proposal(symbol="NVDA"))
    note = record_text(client, "I opened a position")
    assert note["summary"] == OPENED

    result = client.post(
        f"/notes/{note['id']}/approve",
        headers=auth_headers(),
        json={"proposal": proposal([])},
    )
    assert result.status_code == 200, result.text

    after = get_note(client, note["id"])
    assert after["changes"] == []
    assert after["summary"] == "I opened a position"
