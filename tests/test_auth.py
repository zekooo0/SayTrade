"""Auth boundary: Clerk token verification and user provisioning."""

from conftest import TEST_ISSUER, auth_headers, mint_token


def test_no_token_401(client):
    assert client.get("/me").status_code == 401


def test_garbage_token_401(client):
    resp = client.get("/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert resp.status_code == 401


def test_expired_token_401(client):
    token = mint_token("user_x", ttl_seconds=-3600)
    assert client.get("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_wrong_issuer_401(client):
    token = mint_token("user_x", issuer="https://evil.clerk.accounts.dev")
    assert client.get("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_valid_token_provisions_user(client):
    first = client.get("/me", headers=auth_headers("user_a"))
    assert first.status_code == 200
    user_id = first.json()["user_id"]
    second = client.get("/me", headers=auth_headers("user_a"))
    assert second.json()["user_id"] == user_id  # same Clerk sub -> same identity
    other = client.get("/me", headers=auth_headers("user_b"))
    assert other.json()["user_id"] != user_id


def test_me_reports_usage(client):
    resp = client.get("/me", headers=auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["plan"] == "FREE"
    assert body["usage"]["ai_voice_limit"] == 2  # test-configured limit
    assert body["usage"]["ai_voice_used"] == 0
    assert body["entitlements"]["voice_notes_per_month"] == 2