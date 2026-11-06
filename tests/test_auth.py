"""Auth boundary: Firebase ID-token verification and user provisioning."""

from conftest import TEST_ISSUER, auth_headers, mint_token, on_free


def test_no_token_401(client):
    assert client.get("/me").status_code == 401


def test_garbage_token_401(client):
    resp = client.get("/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert resp.status_code == 401


def test_expired_token_401(client):
    token = mint_token("user_x", ttl_seconds=-3600)
    assert client.get("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_wrong_issuer_401(client):
    token = mint_token("user_x", issuer="https://evil.example.test")
    assert client.get("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_wrong_audience_401(client):
    """`aud` is load-bearing for Firebase.

    Google publishes ONE key set for every Firebase project, so a valid signature
    proves nothing about WHICH project minted the token. The audience is that
    proof, and a token for another project must never reach a trader's journal.
    """
    token = mint_token("user_x", audience="some-other-project")
    assert client.get("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_token_with_no_audience_401(client):
    token = mint_token("user_x", audience="")
    assert client.get("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_valid_token_provisions_user(client):
    first = client.get("/me", headers=auth_headers("user_a"))
    assert first.status_code == 200
    user_id = first.json()["user_id"]
    second = client.get("/me", headers=auth_headers("user_a"))
    assert second.json()["user_id"] == user_id  # same subject -> same identity
    other = client.get("/me", headers=auth_headers("user_b"))
    assert other.json()["user_id"] != user_id


def test_firebase_keys_unreachable_is_503_not_401(client, monkeypatch):
    """A key-endpoint outage must not look like a bad session.

    Returning 401 would make the client discard a perfectly good session, so an
    unreachable JWKS endpoint is a 503.
    """
    import httpx

    class DeadFetcher:
        async def get(self):
            raise httpx.ConnectError("google is unreachable")

    monkeypatch.setattr(client.app.state, "firebase_jwks", DeadFetcher(), raising=False)

    resp = client.get("/me", headers=auth_headers())

    assert resp.status_code == 503
    assert "unavailable" in resp.json()["detail"].lower()


def test_me_reports_usage(client):
    on_free(client)  # a new account is PRO; these are the Free numbers
    resp = client.get("/me", headers=auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["plan"] == "FREE"
    assert body["usage"]["voice_notes_limit"] == 2  # test-configured guard
    assert body["usage"]["voice_notes_used"] == 0
    assert body["usage"]["active_trades_limit"] == 3
    # No price is exposed while Pro has no price on the page.
    assert "pro_price_usd" not in body["entitlements"]