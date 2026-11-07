"""The public waiting list: one write, no read, no way to ask who is on it."""

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from conftest import engine


def _query(sql: str, **params) -> list:
    """Read the table on its own connection and its own event loop.

    The app's engine is bound to the loop the test client runs in, so reaching
    for it from the test body would borrow a pool that belongs to another loop.
    A throwaway NullPool engine has no pool to borrow.
    """

    url = engine.url.render_as_string(hide_password=False)

    async def _run() -> list:
        local = create_async_engine(url, poolclass=NullPool)
        try:
            async with local.begin() as conn:
                result = await conn.execute(text(sql), params)
                return [row[0] for row in result]
        finally:
            await local.dispose()

    return asyncio.run(_run())


def _count(email: str) -> int:
    rows = _query("SELECT count(*) FROM waitlist WHERE email = :email", email=email)
    return int(rows[0])


def test_an_address_joins_the_list(client):
    resp = client.post("/waitlist", json={"email": "trader@example.com"})

    assert resp.status_code == 201
    assert resp.json() == {"status": "ok"}
    assert _count("trader@example.com") == 1


def test_the_same_address_twice_is_one_row_and_no_error(client):
    first = client.post("/waitlist", json={"email": "twice@example.com"})
    second = client.post("/waitlist", json={"email": "twice@example.com"})

    # The same answer both times: a different one would tell a stranger whether
    # an address is on our list.
    assert (first.status_code, second.status_code) == (201, 201)
    assert first.json() == second.json()
    assert _count("twice@example.com") == 1


def test_the_address_is_stored_lowercase(client):
    client.post("/waitlist", json={"email": "MiXeD@Example.COM"})

    assert _count("mixed@example.com") == 1
    assert _count("MiXeD@Example.COM") == 0


def test_a_value_that_is_not_an_address_is_refused(client):
    for bad in ("not-an-address", "a@", "@example.com", ""):
        resp = client.post("/waitlist", json={"email": bad})
        assert resp.status_code == 422, (bad, resp.status_code)


def test_the_source_defaults_to_the_landing_page(client):
    client.post("/waitlist", json={"email": "source@example.com"})

    assert _query("SELECT source FROM waitlist WHERE email = :e", e="source@example.com") == [
        "landing"
    ]


def test_no_account_is_created_by_a_signup(client):
    """A signup is a mailing list, not a user: nothing may land in `users`."""
    client.post("/waitlist", json={"email": "nobody@example.com"})

    before = _query("SELECT count(*) FROM users")[0]
    client.post("/waitlist", json={"email": "another@example.com"})

    assert _query("SELECT count(*) FROM users")[0] == before
