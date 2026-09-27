"""The free allowance: two voice notes per month in tests, text notes free."""

from conftest import auth_headers, drain, post_text, post_voice


def test_voice_notes_are_counted_and_capped(client):
    assert post_voice(client).status_code == 201
    assert post_voice(client).status_code == 201

    third = post_voice(client)
    assert third.status_code == 402
    assert "allowance" in third.json()["detail"]

    usage = client.get("/me", headers=auth_headers()).json()["usage"]
    assert usage["ai_voice_used"] == 2
    assert usage["ai_voice_remaining"] == 0


def test_text_notes_do_not_use_the_voice_allowance(client):
    for _ in range(5):
        assert post_text(client, "a typed note").status_code == 201

    usage = client.get("/me", headers=auth_headers()).json()["usage"]
    assert usage["ai_voice_used"] == 0
    assert post_voice(client).status_code == 201


def test_a_failed_note_still_counts(client):
    """The AI already ran, so the unit is spent and the retry is free."""
    from app.main import app

    app.state.extractor.fail = True
    resp = post_voice(client)
    note = drain(client, resp.json()["id"])
    assert note["status"] == "FAILED"

    usage = client.get("/me", headers=auth_headers()).json()["usage"]
    assert usage["ai_voice_used"] == 1

    # record a second one to hit the cap, then prove the retry is still allowed
    assert post_voice(client).status_code == 201
    assert post_voice(client).status_code == 402

    app.state.extractor.fail = False
    assert client.post(f"/notes/{note['id']}/retry", headers=auth_headers()).status_code == 200


def test_allowance_is_per_user(client):
    assert post_voice(client, sub="user_a").status_code == 201
    assert post_voice(client, sub="user_a").status_code == 201
    assert post_voice(client, sub="user_a").status_code == 402
    assert post_voice(client, sub="user_b").status_code == 201
