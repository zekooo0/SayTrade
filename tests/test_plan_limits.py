"""The plan: a small active-trade limit on Free (three in this suite, ten in
production), unlimited on Pro, plus the monthly voice guard that stops one
account burning unlimited AI spend."""

from conftest import (
    approve,
    auth_headers,
    drain,
    on_free,
    open_a_trade,
    open_proposal,
    post_text,
    post_voice,
    proposal,
    record_text,
    set_plan,
    set_proposal,
    trade,
)


def test_the_free_plan_defaults_to_ten_active_trades():
    """The production default, independent of the fixture's small limit: the
    suite pins three so it does not open ten trades to reach the boundary."""
    from app.billing.entitlements import PLAN_FREE, Entitlements
    from app.config import Settings

    assert Settings.model_fields["free_active_trades"].default == 10
    assert Entitlements().limits[PLAN_FREE].active_trades == 10


def test_a_new_account_starts_on_free(client):
    """A plan is bought now, so handing out Pro at sign-up would give the paid
    plan away. An operator can still move one account up by hand."""
    body = client.get("/me", headers=auth_headers()).json()

    assert body["plan"] == "FREE"
    assert body["usage"]["active_trades_limit"] == 3

    # The switch still works the other way, which is what makes PRO real.
    set_plan("user_test1", "PRO")
    body = client.get("/me", headers=auth_headers()).json()
    assert body["plan"] == "PRO"
    assert body["usage"]["active_trades_limit"] is None  # unlimited


def test_the_free_plan_holds_three_active_trades(client):
    on_free(client)
    open_a_trade(client, symbol="AAA")
    open_a_trade(client, symbol="BBB")
    open_a_trade(client, symbol="CCC")

    set_proposal(client, open_proposal(symbol="DDD"))
    note = record_text(client, "I opened a position in DDD")

    blocked = approve(client, note["id"])
    assert blocked.status_code == 402, blocked.text
    detail = blocked.json()["detail"]
    assert "3 active trades" in detail
    assert "Upgrade to Pro" in detail
    # No price reaches a user: the checkout page owns the number.
    assert "$" not in detail

    # The note is not lost: it stays extracted, and it approves once a slot frees.
    assert (
        client.get(f"/notes/{note['id']}", headers=auth_headers()).json()["status"]
        == "EXTRACTED"
    )

    set_proposal(client, proposal([{"kind": "CLOSE", "price": "11"}], symbol="AAA"))
    closing = record_text(client, "I sold all AAA at 11")
    assert approve(client, closing["id"]).status_code == 200

    assert approve(client, note["id"]).status_code == 200


def test_a_follow_up_is_never_blocked_by_the_trade_limit(client):
    """The limit is on opening trades. A note on a trade you hold always works."""
    on_free(client)
    aaa = open_a_trade(client, symbol="AAA")
    open_a_trade(client, symbol="BBB")
    open_a_trade(client, symbol="CCC")

    set_proposal(client, proposal([{"kind": "STOP", "stop": "8.5"}], symbol="AAA"))
    note = record_text(client, "I moved the stop to 8.5")
    assert approve(client, note["id"]).status_code == 200
    assert trade(client, aaa["id"])["current_stop"] == "8.5"


def test_pro_lifts_the_trade_limit(client):
    on_free(client)  # three opens below sit exactly on the Free limit
    for symbol in ("AAA", "BBB", "CCC"):
        open_a_trade(client, symbol=symbol)

    set_plan("user_test1", "PRO")
    body = client.get("/me", headers=auth_headers()).json()
    assert body["plan"] == "PRO"
    assert body["usage"]["active_trades_limit"] is None  # unlimited

    set_proposal(client, open_proposal(symbol="DDD"))
    note = record_text(client, "I opened a position in DDD")
    assert approve(client, note["id"]).status_code == 200


def test_the_monthly_voice_guard_caps_ai_spend(client):
    on_free(client)  # the guard is the Free plan's; Pro has no voice limit
    assert post_voice(client).status_code == 201
    assert post_voice(client).status_code == 201

    third = post_voice(client)
    assert third.status_code == 402
    assert "typed notes" in third.json()["detail"].lower()

    # A typed note still works: the guard is on AI spend, not on journaling.
    assert post_text(client, "a typed note").status_code == 201

    usage = client.get("/me", headers=auth_headers()).json()["usage"]
    assert usage["voice_notes_used"] == 2
    assert usage["voice_notes_limit"] == 2


def test_the_voice_guard_is_per_user(client):
    for sub in ("user_a", "user_b"):
        on_free(client, sub)  # provisions the account, then moves it to FREE
    assert post_voice(client, sub="user_a").status_code == 201
    assert post_voice(client, sub="user_a").status_code == 201
    assert post_voice(client, sub="user_a").status_code == 402
    assert post_voice(client, sub="user_b").status_code == 201


def test_a_failed_note_still_counts_against_the_guard(client):
    """The AI already ran, so the unit is spent and the retry is free."""
    from app.main import app

    on_free(client)
    app.state.extractor.fail = True
    resp = post_voice(client)
    note = drain(client, resp.json()["id"])
    assert note["status"] == "FAILED"

    usage = client.get("/me", headers=auth_headers()).json()["usage"]
    assert usage["voice_notes_used"] == 1

    assert post_voice(client).status_code == 201
    assert post_voice(client).status_code == 402

    app.state.extractor.fail = False
    assert (
        client.post(f"/notes/{note['id']}/retry", headers=auth_headers()).status_code
        == 200
    )


def test_the_usage_view_describes_the_plan(client):
    on_free(client)  # this asserts the Free numbers; Pro has no limits
    open_a_trade(client, symbol="AAA")

    body = client.get("/me", headers=auth_headers()).json()
    assert body["plan"] == "FREE"

    usage = body["usage"]
    assert usage["active_trades_used"] == 1
    assert usage["active_trades_limit"] == 3
    assert usage["voice_notes_used"] == 0
    assert usage["voice_notes_limit"] == 2

    entitlements = body["entitlements"]
    # No price and no link: the provider owns the price, and the checkout link is
    # made per trader by POST /billing/checkout.
    assert "pro_price_usd" not in entitlements
    assert "upgrade_url" not in entitlements
    assert entitlements["pro_active_trades"] is None
