"""Two Clerk issuers at once: the old host and a new one.

A domain move means both instances mint live session tokens for a while. The API
has to check each token against the keys of the instance that actually signed it,
and refuse one from an instance it does not know — the signature is still the
trust boundary, and a token naming an unknown issuer never gets that far.
"""

import pytest
from fastapi.testclient import TestClient

from conftest import TEST_ISSUER, FakeJwks, mint_token

from app.main import app

NEW_ISSUER = "https://saytrade.zakarialabs.space/__clerk"
UNKNOWN_ISSUER = "https://somewhere.else.example/__clerk"


class RefusesToBeAsked:
    """A key set that fails loudly if it is consulted.

    Only there to prove WHICH issuer's keys were used: if the API fell back to
    the first issuer, this would be reached and the test would fail.
    """

    async def get(self) -> dict:
        raise AssertionError("the wrong issuer's key set was used")


@pytest.fixture
def two_issuers():
    app.state.jwks_by_issuer = {TEST_ISSUER: RefusesToBeAsked(), NEW_ISSUER: FakeJwks()}
    yield
    app.state.jwks_by_issuer = {}


def _headers(sub: str, issuer: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(sub, issuer=issuer)}"}


def test_a_token_from_the_new_issuer_is_accepted(client: TestClient, two_issuers):
    resp = client.get("/trades", headers=_headers("user_test1", NEW_ISSUER))

    assert resp.status_code == 200, resp.text


def test_a_token_from_the_old_issuer_is_still_accepted(client: TestClient):
    """The old host keeps working while the new one beds in."""
    app.state.jwks_by_issuer = {TEST_ISSUER: FakeJwks(), NEW_ISSUER: RefusesToBeAsked()}
    try:
        resp = client.get("/trades", headers=_headers("user_test1", TEST_ISSUER))
        assert resp.status_code == 200, resp.text
    finally:
        app.state.jwks_by_issuer = {}


def test_a_token_from_an_unknown_issuer_is_refused(
    client: TestClient, two_issuers
):
    resp = client.get("/trades", headers=_headers("user_test1", UNKNOWN_ISSUER))

    assert resp.status_code == 401


def test_a_signed_token_whose_issuer_is_unknown_is_not_verified(
    client: TestClient, two_issuers
):
    """A token signed correctly, but claiming an instance we do not trust, is
    still refused: the issuer is part of what is checked, not just a hint."""
    token = mint_token("user_test1", issuer=UNKNOWN_ISSUER)

    resp = client.get("/trades", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
