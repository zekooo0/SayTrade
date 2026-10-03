"""The trader's own stories, end to end.

Story 1  "I opened a position today in Nvidia, I bought 100 shares at 10$, my
          sl is 9$ my target is 12$"
Story 2  "today I saw reverse candle, will wait for the next day candle"
Story 3  "yesterday I sold half of my position to secure some gains"
Story 4  "today I moved the sl to 9.5$"
Story 5  "today I bought more shares at 11$ and the sl for this portion is 10.5$"
Story 6  "yesterday I sold all the shares at 11.95$"
"""

from conftest import (
    approve,
    auth_headers,
    drain,
    extractor_calls,
    open_a_trade,
    post_voice,
    proposal,
    record_text,
    set_proposal,
    trade,
    trades,
)


def test_story_1_open_a_position_by_voice(client):
    """A voice note opens the trade; the numbers come from the extractor."""
    resp = post_voice(client)
    assert resp.status_code == 201, resp.text
    note = drain(client, resp.json()["id"])
    assert note["status"] == "EXTRACTED"
    assert note["source"] == "VOICE"
    assert note["text"] is not None      # the transcript is kept
    assert note["audio_url"]             # the audio is reachable

    result = approve(client, note["id"])
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["created_trade"] is True
    assert body["changes_applied"] == 1

    summary = trade(client, body["trade_id"])
    assert summary["symbol"] == "NVDA"
    assert summary["shares"] == "100"
    assert summary["average_price"] == "10"
    assert summary["current_stop"] == "9"
    assert summary["current_target"] == "12"
    assert summary["planned_r"] == "2"
    assert summary["is_open"] is True
    assert summary["realized_pnl"] is None


def test_story_2_observation_note_changes_nothing(client):
    """'I saw a reverse candle, I will wait' — a note with no change."""
    opened = open_a_trade(client)
    set_proposal(client, proposal([], summary="Reverse candle; waiting for confirmation."))
    note = record_text(client, "today I saw reverse candle, will wait", trade_id=opened["id"])
    assert note["status"] == "EXTRACTED"

    result = approve(client, note["id"])
    assert result.status_code == 200, result.text
    assert result.json()["changes_applied"] == 0

    after = trade(client, opened["id"])
    assert after["shares"] == "100"
    assert after["current_stop"] == "9"
    assert after["note_count"] == 2          # the open note plus the observation
    assert after["notes"][-1]["changes"] == []


def test_story_3_sold_half_uses_the_position(client):
    """'I sold half' — a fraction the backend turns into shares."""
    opened = open_a_trade(client)
    set_proposal(
        client,
        proposal([{"kind": "TRIM", "fraction": "0.5", "price": "12"}], summary="Sold half at 12."),
    )
    note = record_text(client, "yesterday I sold half at 12", trade_id=opened["id"])
    result = approve(client, note["id"])
    assert result.status_code == 200, result.text

    after = trade(client, opened["id"])
    assert after["shares"] == "50"           # half of 100, computed by the backend
    assert after["realized_pnl"] == "100"    # (12 - 10) * 50
    change = after["notes"][-1]["changes"][0]
    assert change["kind"] == "TRIM"
    assert change["shares"] == "50"
    assert change["price"] == "12"


def test_story_4_moved_the_stop(client):
    opened = open_a_trade(client)
    set_proposal(client, proposal([{"kind": "STOP", "stop": "9.5"}], summary="Stop to 9.5."))
    note = record_text(client, "today I moved the sl to 9.5", trade_id=opened["id"])
    assert approve(client, note["id"]).status_code == 200

    after = trade(client, opened["id"])
    assert after["current_stop"] == "9.5"
    assert after["initial_stop"] == "9"      # the original plan is kept
    assert after["shares"] == "100"          # a stop move changes no shares
    # planned R stays on the ORIGINAL risk: a trailed stop must not inflate it
    assert after["planned_r"] == "2"         # (12 - 10) / (10 - 9)


def test_story_5_bought_more_with_a_part_stop(client):
    """The part stop stays in the words; the trade keeps one stop."""
    opened = open_a_trade(client)
    set_proposal(
        client,
        proposal(
            [{"kind": "ADD", "shares": "50", "price": "11"}],  # stop stays null, by design
            summary="Bought 50 more at 11; stop for this part 10.5.",
        ),
    )
    note = record_text(
        client,
        "today I bought more shares at 11 and the sl for this portion is 10.5",
        trade_id=opened["id"],
    )
    assert approve(client, note["id"]).status_code == 200

    after = trade(client, opened["id"])
    assert after["shares"] == "150"
    assert after["average_price"] == "10.33333333"   # (100*10 + 50*11) / 150
    assert after["current_stop"] == "9"              # unchanged: one stop per trade
    assert "10.5" in after["notes"][-1]["text"]


def test_story_6_sold_everything_closes_the_trade(client):
    opened = open_a_trade(client)
    set_proposal(
        client,
        proposal([{"kind": "ADD", "shares": "50", "price": "11"}], summary="Added 50 at 11."),
    )
    approve(client, record_text(client, "bought 50 more at 11", trade_id=opened["id"])["id"])

    set_proposal(
        client,
        proposal([{"kind": "CLOSE", "price": "11.95"}], summary="Sold everything at 11.95."),
    )
    note = record_text(client, "yesterday I sold all the shares at 11.95", trade_id=opened["id"])
    assert approve(client, note["id"]).status_code == 200

    after = trade(client, opened["id"])
    assert after["is_open"] is False
    assert after["closed_at"] is not None
    assert after["shares"] == "0"
    assert after["realized_pnl"] == "242.5"    # 150 * 11.95 - (100*10 + 50*11)
    assert after["r_multiple"] == "1.2125"     # (11.95 - 10.3333) / (10.3333 - 9)

    assert trades(client, open_only=True) == []


def test_full_story_timeline(client):
    """All six stories on one trade, in order, with a clean timeline."""
    opened = open_a_trade(client)
    tid = opened["id"]

    set_proposal(client, proposal([], summary="Reverse candle; waiting."))
    approve(client, record_text(client, "saw a reverse candle", trade_id=tid)["id"])
    set_proposal(client, proposal([{"kind": "TRIM", "fraction": "0.5", "price": "12"}], summary="Sold half at 12."))
    approve(client, record_text(client, "sold half at 12", trade_id=tid)["id"])
    set_proposal(client, proposal([{"kind": "STOP", "stop": "9.5"}], summary="Stop to 9.5."))
    approve(client, record_text(client, "moved the sl to 9.5", trade_id=tid)["id"])
    set_proposal(client, proposal([{"kind": "ADD", "shares": "50", "price": "11"}], summary="Added 50 at 11."))
    approve(client, record_text(client, "bought 50 more at 11", trade_id=tid)["id"])
    set_proposal(client, proposal([{"kind": "CLOSE", "price": "11.95"}], summary="Sold all at 11.95."))
    approve(client, record_text(client, "sold all at 11.95", trade_id=tid)["id"])

    detail = trade(client, tid)
    assert detail["note_count"] == 6
    kinds = [c["kind"] for n in detail["notes"] for c in n["changes"]]
    assert kinds == ["OPEN", "TRIM", "STOP", "ADD", "CLOSE"]
    assert detail["is_open"] is False
    assert detail["shares"] == "0"

    view = client.get("/analytics", headers=auth_headers()).json()
    assert view["total_trades"] == 1
    assert view["closed_trades"] == 1
    assert view["wins"] == 1


def test_follow_up_from_the_trade_page_gets_context(client):
    """Recording from the trade page hands the real trade id to the extractor."""
    opened = open_a_trade(client)
    set_proposal(client, proposal([{"kind": "STOP", "stop": "9.5"}], summary="Stop to 9.5."))
    record_text(client, "moved the sl to 9.5", trade_id=opened["id"])

    call = extractor_calls()[-1]
    context = call["active_trade"]
    assert context.id == opened["id"]
    assert context.symbol == "NVDA"
    assert context.closed is False
    assert call["recorded_at"] is not None


def test_multiple_changes_in_one_note(client):
    """One sentence, two changes: they both land."""
    opened = open_a_trade(client)
    set_proposal(
        client,
        proposal(
            [
                {"kind": "TRIM", "fraction": "0.5", "price": "12"},
                {"kind": "STOP", "stop": "10.5"},
            ],
            summary="Sold half at 12 and moved the stop to 10.5.",
        ),
    )
    note = record_text(client, "I sold half at 12 and moved the stop to 10.5", trade_id=opened["id"])
    result = approve(client, note["id"])
    assert result.status_code == 200, result.text
    assert result.json()["changes_applied"] == 2

    after = trade(client, opened["id"])
    assert after["shares"] == "50"
    assert after["current_stop"] == "10.5"
