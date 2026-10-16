"""Payments: the checkout link, the provider's webhook, and the plan it writes.

The provider's API is faked. The SIGNATURE is not: these tests sign their own
payloads with the same secret the app checks, so a green run proves the raw-body
HMAC, and a payload signed with another secret is refused.

Every event here writes the plan from the subscription's CURRENT state, which is
what makes a replay harmless — one test says so out loud.
"""

import hashlib
import hmac
import json

from conftest import WEBHOOK_SECRET, auth_headers, on_free
from app.main import app
from app.services.billing import BillingPolicy


def sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def subscription_payload(
    user_id,
    status: str = "active",
    ls_id: str = "4242",
    event: str = "subscription_created",
    test_mode: bool = False,
    custom: bool = True,
    data_type: str = "subscriptions",
) -> dict:
    """A payload shaped like the provider's subscription webhook."""
    return {
        "meta": {
            "event_name": event,
            "custom_data": {"user_id": str(user_id)} if custom else {},
        },
        "data": {
            "type": data_type,
            "id": ls_id,
            "attributes": {
                "status": status,
                "variant_id": 111,
                "customer_id": 7,
                "order_id": 9,
                "renews_at": "2026-11-16T00:00:00.000000Z",
                "ends_at": None,
                "trial_ends_at": None,
                "test_mode": test_mode,
            },
        },
    }


def post_webhook(client, payload: dict, secret: str = WEBHOOK_SECRET):
    body = json.dumps(payload).encode()
    return client.post(
        "/billing/webhook",
        content=body,
        headers={
            "X-Signature": sign(body, secret),
            "Content-Type": "application/json",
        },
    )


def me(client, sub: str = "user_test1") -> dict:
    return client.get("/me", headers=auth_headers(sub)).json()


def provision(client, sub: str = "user_test1") -> str:
    """A signed-in account on FREE, and its id."""
    on_free(client, sub)
    return me(client, sub)["user_id"]


# --- the webhook is the only door in -----------------------------------------


def test_the_webhook_refuses_an_unsigned_call(client):
    body = json.dumps(subscription_payload("00000000-0000-0000-0000-000000000000"))

    assert client.post("/billing/webhook", content=body.encode()).status_code == 401


def test_the_webhook_refuses_another_secret(client):
    account = provision(client)

    response = post_webhook(
        client, subscription_payload(account), secret="not-the-store-secret"
    )

    assert response.status_code == 401
    assert me(client)["plan"] == "FREE"  # and nothing was written


def test_a_signed_subscription_grants_pro(client):
    account = provision(client)

    response = post_webhook(client, subscription_payload(account))

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"
    assert me(client)["plan"] == "PRO"


def test_pro_costs_nothing_until_a_slot_is_taken(client):
    """The plan the payment grants is the app's own: PRO lifts the trade limit."""
    from conftest import open_a_trade, open_proposal, record_text, approve, set_proposal

    account = provision(client)
    post_webhook(client, subscription_payload(account))

    for symbol in ("AAA", "BBB", "CCC", "DDD"):
        open_a_trade(client, symbol=symbol)

    set_proposal(client, open_proposal(symbol="MSFT"))
    note = record_text(client, "I opened a position in MSFT")
    assert approve(client, note["id"]).status_code == 200


def test_a_replay_writes_the_same_state(client):
    account = provision(client)
    payload = subscription_payload(account)

    assert post_webhook(client, payload).status_code == 200
    assert post_webhook(client, payload).status_code == 200

    assert me(client)["plan"] == "PRO"


def test_the_later_state_wins(client):
    """Two events for one subscription, the newer one last: no event log needed."""
    account = provision(client)

    post_webhook(client, subscription_payload(account, status="active"))
    assert me(client)["plan"] == "PRO"

    post_webhook(
        client,
        subscription_payload(account, status="expired", event="subscription_expired"),
    )
    assert me(client)["plan"] == "FREE"


def test_a_cancelled_subscription_keeps_pro_for_the_paid_period(client):
    account = provision(client)

    post_webhook(
        client,
        subscription_payload(account, status="cancelled", event="subscription_cancelled"),
    )

    assert me(client)["plan"] == "PRO"


def test_a_failed_renewal_does_not_lock_a_paying_trader_out(client):
    """The provider retries for two weeks; the journal stays open meanwhile."""
    account = provision(client)

    post_webhook(client, subscription_payload(account, status="past_due"))

    assert me(client)["plan"] == "PRO"


def test_an_unpaid_subscription_takes_pro_back(client):
    account = provision(client)

    post_webhook(client, subscription_payload(account, status="unpaid"))

    assert me(client)["plan"] == "FREE"


def test_a_status_this_app_has_never_seen_gives_free(client):
    account = provision(client)

    post_webhook(client, subscription_payload(account, status="something_new"))

    assert me(client)["plan"] == "FREE"


def test_a_test_mode_purchase_is_ignored_in_production(client):
    account = provision(client)

    response = post_webhook(
        client, subscription_payload(account, test_mode=True)
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert me(client)["plan"] == "FREE"


def test_a_test_mode_purchase_lands_while_rehearsing(client):
    account = provision(client)
    app.state.billing = BillingPolicy(webhook_secret=WEBHOOK_SECRET, test_mode=True)

    post_webhook(client, subscription_payload(account, test_mode=True))

    assert me(client)["plan"] == "PRO"


def test_an_order_event_is_ignored(client):
    """The order arrives beside the subscription, and it is not this app's business."""
    account = provision(client)

    response = post_webhook(
        client, subscription_payload(account, data_type="orders")
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert me(client)["plan"] == "FREE"


def test_an_event_without_an_account_id_is_refused(client):
    response = post_webhook(client, subscription_payload("none", custom=False))

    assert response.status_code == 400


def test_an_event_for_an_account_this_app_does_not_know_is_refused(client):
    response = post_webhook(
        client, subscription_payload("11111111-1111-1111-1111-111111111111")
    )

    assert response.status_code == 400


def test_a_statusless_event_is_refused(client):
    account = provision(client)
    payload = subscription_payload(account)
    payload["data"]["attributes"].pop("status")

    assert post_webhook(client, payload).status_code == 400


# --- the checkout ------------------------------------------------------------


def test_the_checkout_is_unavailable_without_a_provider(client):
    app.state.lemonsqueezy = None

    response = client.post(
        "/billing/checkout", headers=auth_headers(), json={"period": "monthly"}
    )

    assert response.status_code == 503


def test_the_checkout_carries_the_account_and_the_period(client):
    account = provision(client)

    response = client.post(
        "/billing/checkout", headers=auth_headers(), json={"period": "yearly"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["url"].startswith("https://")

    call = app.state.lemonsqueezy.checkouts[-1]
    assert call["user_id"] == account
    assert call["period"] == "yearly"
    assert call["redirect_url"] == "https://saytrade.app/trades"
    assert call["test_mode"] is False


def test_the_checkout_defaults_to_monthly(client):
    provision(client)

    client.post("/billing/checkout", headers=auth_headers(), json={})

    assert app.state.lemonsqueezy.checkouts[-1]["period"] == "monthly"


def test_the_checkout_carries_the_traders_email(client):
    """The receipt reaches him, and the provider hides its own email field."""
    provision(client)

    client.post(
        "/billing/checkout",
        headers=auth_headers(),
        json={"period": "monthly", "email": "  trader@example.com  "},
    )

    assert app.state.lemonsqueezy.checkouts[-1]["email"] == "trader@example.com"


def test_the_checkout_works_without_an_email(client):
    """A client that sends none is not refused: the provider then asks for it."""
    provision(client)

    response = client.post("/billing/checkout", headers=auth_headers(), json={})

    assert response.status_code == 200
    assert app.state.lemonsqueezy.checkouts[-1]["email"] is None


def test_the_checkout_leaves_a_non_subscriber_alone(client):
    """A checkout is not a subscription: it writes no plan and no row."""
    provision(client)

    client.post("/billing/checkout", headers=auth_headers(), json={})

    assert me(client)["plan"] == "FREE"
    assert client.get("/billing/portal", headers=auth_headers()).status_code == 404


def test_the_checkout_needs_a_session(client):
    assert client.post("/billing/checkout", json={}).status_code == 401


def test_a_provider_outage_is_a_503_not_a_silent_success(client):
    provision(client)
    app.state.lemonsqueezy.fail = True

    response = client.post("/billing/checkout", headers=auth_headers(), json={})

    assert response.status_code == 503


# --- the portal --------------------------------------------------------------


def test_the_portal_answers_the_provider_link(client):
    account = provision(client)
    post_webhook(client, subscription_payload(account, ls_id="777"))
    app.state.lemonsqueezy.subscriptions["777"] = {
        "urls": {"customer_portal": "https://fake-store.lemonsqueezy.com/billing/777"}
    }

    response = client.get("/billing/portal", headers=auth_headers())

    assert response.status_code == 200, response.text
    assert response.json()["url"].endswith("/billing/777")


def test_the_portal_is_404_without_a_subscription(client):
    provision(client)

    assert client.get("/billing/portal", headers=auth_headers()).status_code == 404


def test_the_portal_still_answers_after_expiry(client):
    """The row stays — it is the history — and the plan is off."""
    account = provision(client)
    post_webhook(
        client, subscription_payload(account, status="expired", event="subscription_expired")
    )
    app.state.lemonsqueezy.subscriptions["4242"] = {
        "urls": {"customer_portal": "https://fake-store.lemonsqueezy.com/billing/4242"}
    }

    response = client.get("/billing/portal", headers=auth_headers())

    # The trader can still read his own billing history — that is what the
    # provider's page is for. What matters is that access is off.
    assert response.status_code == 200
    assert me(client)["plan"] == "FREE"


def test_the_portal_reports_a_provider_outage(client):
    account = provision(client)
    post_webhook(client, subscription_payload(account))
    app.state.lemonsqueezy.fail = True

    assert client.get("/billing/portal", headers=auth_headers()).status_code == 503
