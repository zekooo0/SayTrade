"""The predictions list: the flat history behind the accuracy number.

The scoreboard says how often the trader was right. This route says which calls
those were, so a page can print "9 right · 6 wrong" above the nine rows that
earned it. Both read the same rows.
"""

from conftest import (
    approve,
    auth_headers,
    open_a_trade,
    open_proposal,
    proposal,
    record_text,
    set_proposal,
)


def _list(client, **params):
    resp = client.get("/predictions", headers=auth_headers(), params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _close_with_prediction(
    client, *, symbol: str = "NVDA", exit_price: str = "9.5", outcome: str | None = None
) -> dict:
    """Open a trade, close it expecting failure, optionally judge the call."""
    opened = open_a_trade(client, symbol=symbol)  # entry 10, stop 9, target 12
    set_proposal(
        client,
        proposal(
            [{"kind": "CLOSE", "price": exit_price}],
            prediction={"reason": "I expect it to fail"},
        ),
    )
    note = record_text(client, "I am out, I expect it to fail", trade_id=opened["id"])
    assert approve(client, note["id"]).status_code == 200

    if outcome is not None:
        set_proposal(
            client,
            proposal([], resolution={"outcome": outcome, "note": "what I saw"}),
        )
        follow_up = record_text(client, "here is what happened", trade_id=opened["id"])
        assert approve(client, follow_up["id"]).status_code == 200
    return opened


def test_a_prediction_is_listed_with_its_trade_and_its_level(client):
    """The row says which trade, what level, and what happened — nothing else."""
    opened = _close_with_prediction(client, outcome="CORRECT")

    rows = _list(client)

    assert len(rows) == 1
    row = rows[0]
    assert row["trade_id"] == opened["id"]
    assert row["symbol"] == "NVDA"
    # The level defaults to the trade's own stop: that is the failure point the
    # trader was talking about.
    assert row["level"] == "9"
    assert row["result"] == "CORRECT"
    assert row["trade_closed_at"] is not None
    assert row["resolved_at"] is not None


def test_an_unjudged_prediction_is_listed_as_pending(client):
    """It happened nowhere yet, so the row must not claim either answer."""
    _close_with_prediction(client)

    row = _list(client)[0]

    assert row["result"] == "PENDING"
    assert row["resolved_at"] is None


def test_the_list_is_newest_first(client):
    _close_with_prediction(client, symbol="NVDA")
    _close_with_prediction(client, symbol="AAPL")

    rows = _list(client)

    assert [row["symbol"] for row in rows] == ["AAPL", "NVDA"]


def test_limit_caps_the_list(client):
    for symbol in ("NVDA", "AAPL", "TSLA"):
        _close_with_prediction(client, symbol=symbol)

    assert len(_list(client, limit=2)) == 2


def test_a_market_scopes_the_list(client):
    """Two books, one prediction each: naming a book lists only its own."""
    first = _close_with_prediction(client, symbol="NVDA")

    second_market = client.post(
        "/portfolios",
        headers=auth_headers(),
        json={"name": "Second book", "exchange": "US"},
    ).json()["id"]

    set_proposal(client, open_proposal(symbol="AAPL"))
    opened = record_text(client, "I opened a position", portfolio_id=second_market)
    approved = approve(client, opened["id"])
    assert approved.status_code == 200, approved.text
    other_trade = approved.json()["trade_id"]

    set_proposal(
        client,
        proposal(
            [{"kind": "CLOSE", "price": "9"}],
            prediction={"level": "8", "reason": "it fails"},
        ),
    )
    note = record_text(client, "out, it fails", trade_id=other_trade)
    assert approve(client, note["id"]).status_code == 200

    first_market = first["portfolio_id"]
    assert [row["trade_id"] for row in _list(client, portfolio_id=first_market)] == [first["id"]]
    assert [row["trade_id"] for row in _list(client, portfolio_id=second_market)] == [other_trade]
    assert len(_list(client)) == 2


def test_a_market_that_is_not_yours_is_not_found(client):
    """Another trader's market id is a 404, never an empty list."""
    from conftest import ensure_market

    theirs = ensure_market(client, sub="user_test2")

    resp = client.get(
        "/predictions", headers=auth_headers("user_test1"), params={"portfolio_id": theirs}
    )

    assert resp.status_code == 404


def test_the_list_is_empty_before_a_trade_closes(client):
    open_a_trade(client, symbol="NVDA")

    assert _list(client) == []
