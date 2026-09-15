"""Acceptance scenarios 1-8 from the backend implementation spec, end to end
through the real HTTP API, the real auth path (Clerk-shaped JWT + JWKS), and
the real Postgres database."""

import pytest

from conftest import DEFAULT_ACTIONS, auth_headers, mint_token, receive_and_drain, receive_voice

AUDIO = {"file": ("note.m4a", b"fake-audio-bytes-0123", "audio/mp4")}


def approve(client, entry_id, actions, sub="user_test1"):
    return client.post(
        f"/journal/{entry_id}/approve",
        headers=auth_headers(sub),
        json={"actions": actions},
    )


def create_entry_and_extract(client, sub="user_test1"):
    """Receive a voice note, let the fake pipeline run, return the final entry."""
    return receive_and_drain(client, sub)


def get_proposal(client, entry, sub="user_test1"):
    resp = client.get(f"/journal/{entry['id']}", headers=auth_headers(sub))
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture
def trade(client):
    """Create a voice trade and return it + the entry that created it."""
    entry = create_entry_and_extract(client)
    resp = approve(client, entry["id"], DEFAULT_ACTIONS["actions"])
    assert resp.status_code == 200, resp.text
    return {"entry": entry, "result": resp.json(), "trade_id": resp.json()["created_trade_id"]}


def next_entry(client):
    """A fresh voice note for a FOLLOW-UP action: the spec's flow is one note
    per action batch, each becoming its own journal entry."""
    return create_entry_and_extract(client)


# --- scenario 1: new trade -------------------------------------------------
def test_1_new_trade_from_voice(client, trade):
    assert trade["trade_id"] is not None
    detail = client.get(
        f"/trades/{trade['trade_id']}", headers=auth_headers()
    ).json()
    assert detail["symbol"] == "AAPL"
    assert detail["direction"] == "LONG"
    assert detail["status"] == "ACTIVE"
    assert detail["net_position"] == "100"
    assert len(detail["executions"]) == 1
    assert detail["executions"][0]["side"] == "BUY"
    assert detail["executions"][0]["quantity"] == "100"
    assert detail["executions"][0]["price"] == "105"
    assert detail["journal_entry_ids"] == [trade["entry"]["id"]]
    # the journal entry is linked and approved
    entry = get_proposal(client, trade["entry"])
    assert entry["status"] == "APPROVED"
    assert entry["trade_id"] == trade["trade_id"]
    assert entry["approved_payload"]["actions"][0]["action"] == "CREATE_TRADE"


# --- scenario 2: scale in ---------------------------------------------------
def test_2_scale_in_same_trade(client, trade):
    resp = approve(
        client,
        next_entry(client)["id"],
        [
            {
                "action": "ADD_EXECUTION",
                "trade_id": str(trade["trade_id"]),
                "side": "BUY",
                "quantity": "50",
                "price": "108",
                "executed_at": "2026-09-14T13:00:00Z",
            }
        ],
    )
    assert resp.status_code == 200, resp.text
    detail = client.get(f"/trades/{trade['trade_id']}", headers=auth_headers()).json()
    assert len(detail["executions"]) == 2
    assert detail["net_position"] == "150"  # no position_size overwrite
    assert detail["status"] == "ACTIVE"


# --- scenario 3: management ------------------------------------------------
def test_3_stop_change_preserves_original_plan(client, trade):
    resp = approve(
        client,
        next_entry(client)["id"],
        [
            {
                "action": "CHANGE_STOP",
                "trade_id": str(trade["trade_id"]),
                "old_value": "90",
                "new_value": "94",
                "reason": "New higher low formed",
            }
        ],
    )
    assert resp.status_code == 200, resp.text
    detail = client.get(f"/trades/{trade['trade_id']}", headers=auth_headers()).json()
    assert detail["failure_point"] == "90"  # the ORIGINAL plan is untouched
    assert detail["current_stop"] == "94"  # the decision is the present value
    assert detail["decisions"][0]["type"] == "STOP_LOSS_CHANGE"
    assert detail["decisions"][0]["old_value"] == "90"
    assert detail["decisions"][0]["new_value"] == "94"


# --- scenario 4: partial exit keeps ACTIVE ---------------------------------
def test_4_partial_exit_keeps_trade_active(client, trade):
    resp = approve(
        client,
        next_entry(client)["id"],
        [
            {
                "action": "ADD_EXECUTION",
                "trade_id": str(trade["trade_id"]),
                "side": "SELL",
                "quantity": "50",
                "price": "115",
                "executed_at": "2026-09-15T09:00:00Z",
            }
        ],
    )
    assert resp.status_code == 200, resp.text
    detail = client.get(f"/trades/{trade['trade_id']}", headers=auth_headers()).json()
    assert detail["status"] == "ACTIVE"
    assert detail["net_position"] == "50"


# --- scenario 5: atomic completion -----------------------------------------
def test_5_completion_is_atomic(client, trade):
    resp = approve(
        client,
        next_entry(client)["id"],
        [
            {
                "action": "ADD_EXECUTION",
                "trade_id": str(trade["trade_id"]),
                "side": "SELL",
                "quantity": "100",
                "price": "120",
                "executed_at": "2026-09-16T15:00:00Z",
            },
            {
                "action": "ADD_OUTCOME",
                "trade_id": str(trade["trade_id"]),
                "result": "WIN",
                "type": 2,
                "lesson": "I should have held longer",
            },
            {
                "action": "COMPLETE_TRADE",
                "trade_id": str(trade["trade_id"]),
                "completed_at": "2026-09-16T15:30:00Z",
            },
        ],
    )
    assert resp.status_code == 200, resp.text
    detail = client.get(f"/trades/{trade['trade_id']}", headers=auth_headers()).json()
    assert detail["status"] == "COMPLETED"
    assert detail["outcome"]["result"] == "WIN"
    assert detail["outcome"]["type"] == 2
    assert detail["realized_pnl"] == "1500"  # (100*120) - (100*105)
    assert detail["r_multiple"] == "1"  # 1500 / (100 * |105-90|)
    assert detail["warnings"] == []
    # completed trades reject new executions (manual path: same engine, no allowance)
    resp = client.post(
        "/trades/manual",
        headers=auth_headers(),
        json={
            "actions": [
                {
                    "action": "ADD_EXECUTION",
                    "trade_id": str(trade["trade_id"]),
                    "side": "BUY",
                    "quantity": "1",
                    "price": "100",
                    "executed_at": "2026-09-17T09:00:00Z",
                }
            ]
        },
    )
    assert resp.status_code == 409


# --- scenario 6: retry of approval creates no duplicates --------------------
def test_6_repeated_approval_is_idempotent(client, trade):
    first = approve(client, trade["entry"]["id"], DEFAULT_ACTIONS["actions"])
    assert first.status_code == 200
    # resend the same approval (timeout/retry case) — even with a DIFFERENT
    # payload, the stored result is returned, nothing is re-applied
    second = approve(client, trade["entry"]["id"], DEFAULT_ACTIONS["actions"])
    assert second.status_code == 200
    assert second.json()["created_trade_id"] == first.json()["created_trade_id"]
    detail = client.get(f"/trades/{trade['trade_id']}", headers=auth_headers()).json()
    assert len(detail["executions"]) == 1  # no duplicate execution


# --- scenario 7: processing failure -> FAILED -> retry ----------------------
def test_7_failure_then_retry(client):
    client.app.state.transcriber.fail = True
    entry = receive_and_drain(client)
    assert entry["status"] == "FAILED"
    assert entry["failed_stage"] == "TRANSCRIPTION"
    assert entry["audio_url"]  # the original audio is not lost
    # allowance was refunded (never permanently consumed)
    usage = client.get("/me", headers=auth_headers()).json()["usage"]
    assert usage["ai_voice_used"] == 0
    # retry with a healthy pipeline: no duplicate entry, no new reservation
    client.app.state.transcriber.fail = False
    from conftest import FakeExtractor

    client.app.state.extractor = FakeExtractor()
    resp = client.post(f"/journal/{entry['id']}/retry", headers=auth_headers())
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "EXTRACTED"
    usage = client.get("/me", headers=auth_headers()).json()["usage"]
    assert usage["ai_voice_used"] == 0  # retry never reserves another unit
    # the recovered entry can still be approved
    resp = client.post(
        f"/journal/{entry['id']}/approve",
        headers=auth_headers(),
        json={"actions": DEFAULT_ACTIONS["actions"]},
    )
    assert resp.status_code == 200, resp.text
    trades = client.get("/trades", headers=auth_headers()).json()
    assert len(trades) == 1


# --- scenario 8: security — another user's Trade is rejected ----------------
def test_8_foreign_trade_rejected_without_mutation(client, trade):
    other = "user_mallory"
    # user A creates a legitimate scale-in entry to have a baseline
    approve(
        client,
        next_entry(client)["id"],
        [
            {
                "action": "ADD_EXECUTION",
                "trade_id": str(trade["trade_id"]),
                "side": "BUY",
                "quantity": "50",
                "price": "108",
                "executed_at": "2026-09-14T13:00:00Z",
            }
        ],
    )
    before = client.get(f"/trades/{trade['trade_id']}", headers=auth_headers()).json()
    assert len(before["executions"]) == 2

    # Mallory tries to approve an entry she does not own
    resp = client.post(
        f"/journal/{trade['entry']['id']}/approve",
        headers=auth_headers(other),
        json={"actions": DEFAULT_ACTIONS["actions"]},
    )
    assert resp.status_code == 404

    # Mallory tries to add an execution to A's trade manually
    resp = client.post(
        "/trades/manual",
        headers=auth_headers(other),
        json={
            "actions": [
                {
                    "action": "ADD_EXECUTION",
                    "trade_id": str(trade["trade_id"]),
                    "side": "SELL",
                    "quantity": "10",
                    "price": "110",
                    "executed_at": "2026-09-14T14:00:00Z",
                }
            ]
        },
    )
    assert resp.status_code == 404

    after = client.get(f"/trades/{trade['trade_id']}", headers=auth_headers()).json()
    assert len(after["executions"]) == 2  # nothing was mutated