"""A screenshot as a note: what it reads, what it skips, what it spends.

The model is not called here. What is pinned is the machinery around it: an image
note has no transcript, it reaches the extractor with the picture itself, it
skips the transcriber entirely, and it spends the same AI allowance as a
recording. A picture with nowhere to go is refused before anything is spent.
"""

from conftest import (
    approve,
    auth_headers,
    drain,
    extractor_calls,
    on_free,
    open_proposal,
    post_image,
    proposal,
    set_proposal,
)

from app.main import app


def test_the_model_reads_the_picture_not_a_transcript(client):
    """An image note has no transcript at all: the picture is the note."""
    posted = post_image(client)
    assert posted.status_code == 201, posted.text
    assert posted.json()["source"] == "IMAGE"

    note = drain(client, posted.json()["id"])

    assert note["status"] == "EXTRACTED"
    assert note["text"] is None
    assert note["image_url"].startswith("https://fake-cdn.example/note-images/")

    call = extractor_calls()[-1]
    assert call["image"] == b"\xff\xd8\xff\xe0fake-jpeg-bytes"
    assert call["image_mime"] == "image/jpeg"
    assert call["transcript"] == ""


def test_an_image_note_is_never_sent_to_the_transcriber(client):
    """Transcription belongs to the voice path. A JPEG sent there would spend a
    second AI call and come back as noise."""
    posted = post_image(client)
    drain(client, posted.json()["id"])

    assert app.state.transcriber.calls == []


def test_an_image_note_spends_the_same_guard_as_a_recording(client):
    """Free allows 2 AI notes a month, and an image spends one of them. The
    guard exists to bound AI spend, and an image note spends."""
    on_free(client)

    drain(client, post_image(client).json()["id"])
    usage = client.get("/me", headers=auth_headers()).json()["usage"]
    assert usage["voice_notes_used"] == 1
    assert usage["voice_notes_limit"] == 2

    drain(client, post_image(client).json()["id"])

    refused = post_image(client)
    assert refused.status_code == 402, refused.text
    assert "AI notes" in refused.json()["detail"]

    # Nothing was stored for the refused note, and nothing was read.
    assert len(app.state.storage.objects) == 2
    assert len(extractor_calls()) == 2


def test_a_screenshot_can_also_be_a_follow_up(client):
    """A broker screen after a sell is a follow-up. The trade arrives as a form
    field, exactly as it does for a recording."""
    set_proposal(client, open_proposal(symbol="AAA", price="10", shares="100"))
    opened = approve(client, drain(client, post_image(client).json()["id"])["id"])
    assert opened.status_code == 200, opened.text
    trade_id = opened.json()["trade_id"]

    set_proposal(
        client,
        proposal([{"kind": "TRIM", "shares": "50", "price": "12"}], symbol="AAA"),
    )
    followed = post_image(client, trade_id=trade_id)

    assert followed.status_code == 201, followed.text
    assert followed.json()["trade_id"] == trade_id


def test_a_picture_with_nowhere_to_go_is_refused_before_anything_is_spent(client):
    """The home rule runs before the allowance. A picture with no trade and no
    market must not cost the trader a unit."""
    on_free(client)

    refused = client.post(
        "/notes/image",
        headers=auth_headers(),
        files={"file": ("screen.jpg", b"\xff\xd8\xff\xe0x", "image/jpeg")},
    )

    assert refused.status_code == 422, refused.text
    usage = client.get("/me", headers=auth_headers()).json()["usage"]
    assert usage["voice_notes_used"] == 0


def test_a_pdf_is_not_a_screenshot(client):
    refused = post_image(client, content_type="application/pdf")

    assert refused.status_code == 415, refused.text


def test_an_empty_image_is_refused(client):
    refused = post_image(client, body=b"")

    assert refused.status_code == 422, refused.text


def test_an_oversized_screenshot_is_refused(client):
    """The client shrinks the picture. The server still caps what it accepts,
    because a client is not a promise."""
    refused = post_image(
        client, body=b"x" * (app.state.settings.max_image_bytes + 1)
    )

    assert refused.status_code == 413, refused.text
