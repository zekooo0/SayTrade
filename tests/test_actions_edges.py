"""Action-model edge cases through the API: multi-trade payloads, stale
old_value conflict detection, oversell rejection, outcome uniqueness."""

from conftest import DEFAULT_ACTIONS, auth_headers, receive_and_drain


def approve(client, entry_id, actions):
    return client.post(
        f"/journal/{entry_id}/approve", headers=auth_headers(), json={"actions": actions}
    )


def test_invalid_pattern_rejected(client):
    actions = {
        "actions": [
            {
                "action": "CREATE_TRADE",
                "symbol": "AAPL",
                "direction": "LONG",
                "pattern": "NOT_A_REAL_PATTERN",
                "opened_at": "2026-09-14T12:00:00Z",
                "executions": [
                    {"side": "BUY", "quantity": "100", "price": "105",
                     "executed_at": "2026-09-14T12:00:00Z"}
                ],
            }
        ]
    }
    resp = client.post(
        "/trades/manual", headers=auth_headers(), json=actions
    )
    assert resp.status_code == 422


def test_create_trade_without_execution_rejected(client):
    actions = {
        "actions": [
            {
                "action": "CREATE_TRADE",
                "symbol": "AAPL",
                "direction": "LONG",
                "pattern": "BREAKOUT",
                "opened_at": "2026-09-14T12:00:00Z",
                "executions": [],  # the MVP only journals entered trades
            }
        ]
    }
    assert client.post("/trades/manual", headers=auth_headers(), json=actions).status_code == 422


def test_multi_trade_payload_rejected(client):
    # create one trade first
    first = client.post(
        "/trades/manual", headers=auth_headers(), json=DEFAULT_ACTIONS
    ).json()
    trade_id = first["created_trade_id"]
    actions = {
        "actions": [
            # a hypothetical second trade cannot be created in the same note
            {"action": "CREATE_TRADE", "symbol": "MSFT", "direction": "LONG",
             "pattern": "BREAKOUT", "opened_at": "2026-09-14T12:00:00Z",
             "executions": [{"side": "BUY", "quantity": "10", "price": "400",
                             "executed_at": "2026-09-14T12:00:00Z"}]},
            {"action": "ADD_EXECUTION", "trade_id": trade_id, "side": "BUY",
             "quantity": "10", "price": "105", "executed_at": "2026-09-14T13:00:00Z"},
        ]
    }
    resp = client.post("/trades/manual", headers=auth_headers(), json=actions)
    assert resp.status_code == 422


def test_stale_stop_conflict_rejected(client):
    first = client.post(
        "/trades/manual", headers=auth_headers(), json=DEFAULT_ACTIONS
    ).json()
    trade_id = first["created_trade_id"]
    # move the stop to 94
    ok = client.post(
        "/trades/manual",
        headers=auth_headers(),
        json={
            "actions": [
                {"action": "CHANGE_STOP", "trade_id": trade_id, "new_value": "94",
                 "old_value": "90", "reason": "higher low"}
            ]
        },
    )
    assert ok.status_code == 200, ok.text
    # a second note still built from stop=90 must be rejected, not silently win
    resp = client.post(
        "/trades/manual",
        headers=auth_headers(),
        json={
            "actions": [
                {"action": "CHANGE_STOP", "trade_id": trade_id, "new_value": "92",
                 "old_value": "90", "reason": "outdated note"}
            ]
        },
    )
    assert resp.status_code == 409
    assert "stale" in resp.json()["detail"]


def test_oversell_rejected(client):
    first = client.post(
        "/trades/manual", headers=auth_headers(), json=DEFAULT_ACTIONS
    ).json()
    trade_id = first["created_trade_id"]
    resp = client.post(
        "/trades/manual",
        headers=auth_headers(),
        json={
            "actions": [
                {"action": "ADD_EXECUTION", "trade_id": trade_id, "side": "SELL",
                 "quantity": "101", "price": "110",
                 "executed_at": "2026-09-14T13:00:00Z"}
            ]
        },
    )
    assert resp.status_code == 422
    # nothing was applied
    detail = client.get(f"/trades/{trade_id}", headers=auth_headers()).json()
    assert len(detail["executions"]) == 1
    assert detail["net_position"] == "100"


def test_one_outcome_per_trade(client):
    first = client.post(
        "/trades/manual", headers=auth_headers(), json=DEFAULT_ACTIONS
    ).json()
    trade_id = first["created_trade_id"]
    payload = lambda result: {
        "actions": [
            {"action": "ADD_OUTCOME", "trade_id": trade_id, "result": result,
             "type": 2}
        ]
    }
    assert client.post(
        "/trades/manual", headers=auth_headers(), json=payload("WIN")
    ).status_code == 200

    from conftest import DEFAULT_ACTIONS as _  # noqa: F401

    assert client.post(
        "/trades/manual", headers=auth_headers(), json=payload("LOSS")
    ).status_code == 409


def test_current_active_trade_sentinel_resolves(client):
    """The extractor never invents ids: 'CURRENT_ACTIVE_TRADE' must resolve to
    the user's most recent ACTIVE trade, backend-verified (spec: resolving
    later notes)."""
    first = client.post(
        "/trades/manual", headers=auth_headers(), json=DEFAULT_ACTIONS
    ).json()
    trade_id = first["created_trade_id"]
    resp = client.post(
        "/trades/manual",
        headers=auth_headers(),
        json={
            "actions": [
                {"action": "ADD_EXECUTION", "trade_id": "CURRENT_ACTIVE_TRADE",
                 "side": "SELL", "quantity": "30", "price": "129.5",
                 "executed_at": "2026-09-15T11:00:00Z"}
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    detail = client.get(f"/trades/{trade_id}", headers=auth_headers()).json()
    assert len(detail["executions"]) == 2
    assert detail["executions"][1]["quantity"] == "30"


def test_sentinel_without_active_trade_rejected(client):
    resp = client.post(
        "/trades/manual",
        headers=auth_headers(),
        json={
            "actions": [
                {"action": "CHANGE_STOP", "trade_id": "CURRENT_ACTIVE_TRADE",
                 "new_value": "110", "reason": "tighter stop"}
            ]
        },
    )
    assert resp.status_code == 422
    assert "no open Trade" in resp.json()["detail"]


def test_approve_only_from_extracted(client):
    # a FAILED entry (broken extractor) must not be approvable
    entry = receive_failed_entry(client)
    assert entry["status"] == "FAILED"
    resp = client.post(
        f"/journal/{entry['id']}/approve",
        headers=auth_headers(),
        json=DEFAULT_ACTIONS,
    )
    assert resp.status_code == 409


def receive_failed_entry(client):
    client.app.state.extractor.fail = True
    return receive_and_drain(client)