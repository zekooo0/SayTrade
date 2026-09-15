"""Monetization acceptance scenarios: allowance enforcement, atomic
reservation, refund on failure, idempotent retries."""

from conftest import DEFAULT_ACTIONS, auth_headers, receive_and_drain, receive_voice


def usage(client, sub="user_test1"):
    return client.get("/me", headers=auth_headers(sub)).json()["usage"]


def test_allowance_counts_and_rejects_before_processing(client):
    for _ in range(2):  # limit is 2 in the test config
        resp = receive_voice(client)
        assert resp.status_code == 201
    u = usage(client)
    assert u["ai_voice_used"] == 2
    assert u["ai_voice_remaining"] == 0
    # third recording is rejected BEFORE any AI processing begins
    resp = receive_voice(client)
    assert resp.status_code == 402
    assert "exhausted" in resp.json()["detail"]
    u = usage(client)
    assert u["ai_voice_used"] == 2  # nothing extra was consumed


def test_failed_processing_does_not_permanently_consume(client):
    client.app.state.transcriber.fail = True
    entry = receive_and_drain(client)
    assert entry["status"] == "FAILED"
    u = usage(client)
    assert u["ai_voice_used"] == 0  # refunded


def test_per_user_allowance_is_independent(client):
    receive_voice(client, sub="user_a")
    receive_voice(client, sub="user_a")
    receive_voice(client, sub="user_b")
    assert usage(client, "user_a")["ai_voice_used"] == 2
    assert usage(client, "user_b")["ai_voice_used"] == 1
    assert client.post(
        "/journal",
        headers=auth_headers("user_b"),
        files={"file": ("n.m4a", b"x", "audio/mp4")},
        data={"duration_ms": "1000"},
    ).status_code == 201


def test_manual_journaling_consumes_no_allowance(client):
    resp = client.post(
        "/trades/manual",
        headers=auth_headers(),
        json={"actions": DEFAULT_ACTIONS["actions"]},
    )
    assert resp.status_code == 200, resp.text
    assert usage(client)["ai_voice_used"] == 0  # unlimited manual journaling
    trades = client.get("/trades", headers=auth_headers()).json()
    assert len(trades) == 1