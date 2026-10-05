"""Test fixtures.

TWO RULES, both load-bearing:

1. Environment must be set BEFORE any app module is imported (app.config reads
   it at import time), so this file sets it at the top.
2. The suite TRUNCATEs every domain table between tests. It must never point at
   a developer's database, so it forces its own: `saytrade_test`. The guard in
   reset_database() refuses to run against any other database name.

Create the test database once (it is separate from the dev database):

    docker exec saytrade-db psql -U saytrade -d postgres -c "CREATE DATABASE saytrade_test OWNER saytrade;"
    DATABASE_URL=postgresql+asyncpg://saytrade:saytrade@127.0.0.1:5432/saytrade_test ./.venv/bin/alembic upgrade head
"""

import base64
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://saytrade:saytrade@127.0.0.1:5432/saytrade_test",
)
os.environ.setdefault("CLERK_FRONTEND_API", "https://test.clerk.accounts.dev")
# Exhaust the monthly voice guard quickly in tests; keep the real trade limit.
os.environ.setdefault("FREE_VOICE_NOTES_PER_MONTH", "2")
# The suite must never post to Sentry: a test run is not a production incident.
# Set outright rather than with setdefault — an empty DSN is the switch, and a
# value inherited from the shell must not be able to turn reporting back on.
os.environ["SENTRY_DSN"] = ""

import jwt  # noqa: E402
import pytest  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.billing.entitlements import Entitlements  # noqa: E402
from app.db import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.providers.extraction import ExtractionResult  # noqa: E402
from app.providers.transcription import TranscriptionResult  # noqa: E402

TEST_ISSUER = "https://test.clerk.accounts.dev"
TEST_KID = "test-kid"

TEST_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64u_int(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def public_jwk():
    numbers = TEST_PRIVATE_KEY.public_key().public_numbers()
    return {
        "kty": "RSA",
        "kid": TEST_KID,
        "use": "sig",
        "alg": "RS256",
        "n": _b64u_int(numbers.n),
        "e": _b64u_int(numbers.e),
    }


def mint_token(sub: str, issuer: str = TEST_ISSUER, kid: str = TEST_KID, ttl_seconds: int = 3600) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": sub,
            "iss": issuer,
            "iat": now,
            "exp": now + timedelta(seconds=ttl_seconds),
        },
        TEST_PRIVATE_KEY,
        algorithm="RS256",
        headers={"kid": kid},
    )


def auth_headers(sub: str = "user_test1") -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(sub)}"}


class FakeJwks:
    async def get(self) -> dict:
        return {"keys": [public_jwk()]}


class FakeStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.fail_save = False
        self.fail_delete = False

    async def save(self, key: str, data: bytes, mime_type: str) -> None:
        if self.fail_save:
            raise RuntimeError("simulated storage outage")
        self.objects[key] = data

    async def read(self, key: str) -> bytes:
        if key not in self.objects:
            raise FileNotFoundError(f"no object at {key}")
        return self.objects[key]

    async def url(self, key: str) -> str:
        return f"https://fake-cdn.example/{key}"

    async def delivery_url(self, key: str) -> str:
        return await self.url(key)

    async def delete(self, key: str) -> None:
        if self.fail_delete:
            raise RuntimeError("simulated storage outage")
        self.deleted.append(key)
        self.objects.pop(key, None)


class FakeTranscriber:
    def __init__(self, transcript: str = "default transcript"):
        self.transcript = transcript
        self.fail = False
        self.calls: list[dict] = []

    async def transcribe(self, data, mime_type, keywords=(), languages=()):
        self.calls.append(
            {
                "mime_type": mime_type,
                "keywords": list(keywords),
                "languages": list(languages),
            }
        )
        if self.fail:
            raise RuntimeError("simulated transcription outage")
        return TranscriptionResult(
            transcript=self.transcript,
            provider="fake",
            model="fake-1",
            language="ar",
        )


def proposal(
    changes: list[dict],
    symbol: str | None = None,
    summary: str = "a note",
    language: str = "en",
    prediction: dict | None = None,
    resolution: dict | None = None,
) -> dict:
    """A proposal body.

    ``symbol`` becomes a security candidate, because that is what the extractor
    returns now: the model proposes companies and the symbols table decides
    which of them trade in the note's market. Tests that pass a ticker here are
    describing what the model thought it heard.

    ``prediction`` is what the trader expects after closing; ``resolution`` is a
    later note saying whether it happened. Both are absent unless a test is
    about them.

    ``summary`` is accepted and ignored: a summary is written by the app from
    the changes, never read out of the trader's words. It stays in the signature
    because tests pass it to describe the note they are simulating.
    """
    candidates = (
        [{"exchange": None, "ticker": symbol, "name": symbol, "aliases": []}]
        if symbol
        else []
    )
    body: dict = {
        "language": language,
        "security_candidates": candidates,
        "changes": changes,
    }
    if prediction is not None:
        body["prediction"] = prediction
    if resolution is not None:
        body["resolution"] = resolution
    return body


def open_proposal(
    symbol: str = "NVDA",
    shares: str = "100",
    price: str = "10",
    stop: str | None = "9",
    target: str | None = "12",
    occurred_at: str | None = None,
) -> dict:
    change: dict = {"kind": "OPEN", "shares": shares, "price": price}
    if stop is not None:
        change["stop"] = stop
    if target is not None:
        change["target"] = target
    if occurred_at is not None:
        change["occurred_at"] = occurred_at
    return proposal([change], symbol=symbol, summary=f"Opened {symbol} at {price}.")


DEFAULT_PROPOSAL = open_proposal()


class FakeExtractor:
    """Stands in for the LLM. Swap `.proposal` to change what the next note reads."""

    def __init__(self, proposal_body: dict | None = None):
        self.proposal = proposal_body or DEFAULT_PROPOSAL
        self.fail = False
        self.calls: list[dict] = []

    async def extract(
        self,
        transcript: str,
        recorded_at=None,
        active_trade=None,
        market=None,
        image: bytes | None = None,
        image_mime: str | None = None,
    ) -> ExtractionResult:
        self.calls.append(
            {
                "transcript": transcript,
                "recorded_at": recorded_at,
                "active_trade": active_trade,
                "market": market,
                # Recorded so a test can prove the picture reached the model.
                "image": image,
                "image_mime": image_mime,
            }
        )
        if self.fail:
            raise RuntimeError("simulated extraction outage")
        return ExtractionResult(
            proposal=self.proposal,
            provider="fake",
            model="fake-1",
        )


class FakeSummarizer:
    """Stands in for the summary model.

    Records what it was asked, so a test can prove the call was given the
    changes and not the transcript. ``fail`` makes it refuse, which must land on
    the deterministic template.
    """

    def __init__(self, line: str | None = None):
        self.line = line
        self.fail = False
        self.calls: list[dict] = []

    async def summarize(
        self, changes, language: str, symbol: str | None = None
    ) -> str | None:
        self.calls.append(
            {
                "kind": "actions",
                "changes": [dict(c) for c in changes],
                "language": language,
                "symbol": symbol,
            }
        )
        if self.fail:
            raise RuntimeError("simulated summary outage")
        if self.line is not None:
            return self.line
        return "AI: " + " and ".join(str(c.get("kind", "")) for c in changes)

    async def summarize_observation(self, text: str, language: str) -> str | None:
        """A note with no position change: the words are the only source."""
        self.calls.append({"kind": "observation", "text": text, "language": language})
        if self.fail:
            raise RuntimeError("simulated summary outage")
        if self.line is not None:
            return self.line
        return f"AI observed: {text}"


def use_summarizer(summarizer) -> None:
    """Install a summary model. The app reads it from app.state per request."""
    app.state.summarizer = summarizer


async def reset_database() -> None:
    # Safety net: this TRUNCATE deletes every journal row. It must only ever
    # run against the test database, never against a developer's own data.
    name = engine.url.database or ""
    if not name.endswith("_test"):
        raise RuntimeError(
            f"refusing to truncate database {name!r}: tests must run against a "
            "database whose name ends in _test. Set DATABASE_URL, or create "
            "saytrade_test (see the docstring at the top of conftest.py)."
        )
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE changes, notes, trades, portfolios, users "
                "RESTART IDENTITY CASCADE"
            )
        )
        await _seed_symbols(conn)
    # The markets the helpers created went with the rows.
    _MARKETS.clear()


# The symbols the suite speaks. The resolver refuses a ticker that is not in the
# note's market's own list, so the test database needs the ones the fixtures
# name — without them every symbol in a test resolves to nothing. The real table
# is 5.5k rows; this is the handful the tests use. `symbols` is not truncated
# above, so this runs once per session.
TEST_SYMBOLS = [
    ("US", "NVDA", "NVIDIA Corporation", "NASDAQ"),
    ("US", "AAPL", "Apple Inc", "NASDAQ"),
    ("US", "TSLA", "Tesla Inc", "NASDAQ"),
    ("US", "MSFT", "Microsoft Corporation", "NASDAQ"),
    ("US", "AMZN", "Amazon.com Inc", "NASDAQ"),
    # Placeholders the suite uses for "some other trade". ZZZZ and MTSS are
    # deliberately absent: tests use them as the ticker that must NOT resolve.
    ("US", "AAA", "AAA Holdings", "NYSE"),
    ("US", "BBB", "BBB Industries", "NYSE"),
    ("US", "CCC", "CCC Corporation", "NYSE"),
    ("US", "DDD", "DDD Incorporated", "NYSE"),
    ("EGX", "SWDY", "ELSWEDY ELECTRIC", "EGX"),
    ("EGX", "ABUK", "Abou Kir Fertilizers", "EGX"),
    ("EGX", "COMI", "Commercial International Bank (Egypt)", "EGX"),
    ("EGX", "EAST", "Eastern Tobacco", "EGX"),
    ("EGX", "AMOC", "Alexandria Mineral Oils Company", "EGX"),
    ("EGX", "ETEL", "Telecom Egypt", "EGX"),
]

_symbols_seeded = False


async def _seed_symbols(conn) -> None:
    """Load the test symbols.

    The test database carries only what the suite speaks: the resolver refuses a
    ticker that is not in the note's market's own list, so without these every
    symbol in a test resolves to nothing. Written to be safe to run more than
    once — a previous run's rows are cleared first. This runs against a database
    whose name ends in `_test`, which `reset_database` has already enforced.
    """
    global _symbols_seeded
    if _symbols_seeded:
        return
    await conn.execute(text("DELETE FROM symbol_aliases"))
    await conn.execute(text("DELETE FROM symbols"))
    for market, ticker, name, venue in TEST_SYMBOLS:
        await conn.execute(
            text(
                "INSERT INTO symbols (id, market, ticker, name, exchange) "
                "VALUES (gen_random_uuid(), :market, :ticker, :name, :venue)"
            ),
            {"market": market, "ticker": ticker, "name": name, "venue": venue},
        )
        await conn.execute(
            text(
                "INSERT INTO symbol_aliases (id, market, ticker, alias) "
                "VALUES (gen_random_uuid(), :market, :ticker, :alias)"
            ),
            {"market": market, "ticker": ticker, "alias": name.lower()},
        )
    _symbols_seeded = True


def set_plan(sub: str, plan: str) -> None:
    """Switch a test user's plan, the way an operator would by hand.

    Fails loudly when there is no such row. A silent no-op here means the test
    is really running against a freshly provisioned account — which is PRO — so
    every later assertion about FREE would be a lie.
    """
    import asyncio

    async def _update() -> int:
        async with engine.begin() as conn:
            result = await conn.execute(
                text("UPDATE users SET plan = :plan WHERE clerk_sub = :sub"),
                {"plan": plan, "sub": sub},
            )
            return result.rowcount

    if not asyncio.run(_update()):
        raise AssertionError(
            f"set_plan: no user row for {sub!r}. Provision the account first — "
            "a request to /me does it. A new account is PRO, not FREE."
        )


def on_free(client: TestClient, sub: str = "user_test1") -> None:
    """Put a test account on FREE.

    A new account is provisioned as PRO, so a test about the Free plan has to
    say so. The /me call creates the row that set_plan then updates.
    """
    client.get("/me", headers=auth_headers(sub))
    set_plan(sub, "FREE")


@pytest.fixture
def client():
    import asyncio

    asyncio.run(reset_database())
    app.state.jwks = FakeJwks()
    app.state.storage = FakeStorage()
    app.state.transcriber = FakeTranscriber()
    app.state.extractor = FakeExtractor()
    # No summary model by default: the deterministic template writes the line,
    # so a test that does not care about the wording still gets a real summary.
    # A test that cares installs a FakeSummarizer with use_summarizer().
    app.state.summarizer = None
    app.state.entitlements = Entitlements(
        free_active_trades=3,
        free_voice_notes_per_month=2,
        pro_active_trades=None,
        pro_voice_notes_per_month=None,
        pro_price_usd=5,
        upgrade_url="https://example.com/upgrade",
    )
    with TestClient(app) as test_client:
        yield test_client


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

TERMINAL_STATUSES = {"EXTRACTED", "FAILED"}


def post_voice(
    client: TestClient,
    sub: str = "user_test1",
    trade_id: str | None = None,
    portfolio_id: str | None = None,
):
    data = {"duration_ms": "30000"}
    if trade_id:
        data["trade_id"] = trade_id
    # A note with no trade must name a market; tests that do not care get one.
    market = portfolio_id or (None if trade_id else ensure_market(client, sub))
    if market:
        data["portfolio_id"] = market
    return client.post(
        "/notes/voice",
        headers=auth_headers(sub),
        files={"file": ("note.m4a", b"\x00\x01\x02fake-audio-bytes", "audio/mp4")},
        data=data,
    )


def post_image(
    client: TestClient,
    sub: str = "user_test1",
    trade_id: str | None = None,
    portfolio_id: str | None = None,
    body: bytes = b"\xff\xd8\xff\xe0fake-jpeg-bytes",
    content_type: str = "image/jpeg",
):
    """Post a screenshot note. The bytes are never a real picture: the model is
    faked, so what the test checks is that the bytes reach it intact."""
    data: dict = {}
    if trade_id:
        data["trade_id"] = trade_id
    # A note with no trade must name a market; tests that do not care get one.
    market = portfolio_id or (None if trade_id else ensure_market(client, sub))
    if market:
        data["portfolio_id"] = market
    return client.post(
        "/notes/image",
        headers=auth_headers(sub),
        files={"file": ("screen.jpg", body, content_type)},
        data=data,
    )


_MARKETS: dict[str, str] = {}


def ensure_market(client: TestClient, sub: str = "user_test1") -> str:
    """A market for a test user, created through the API the app uses.

    A note needs somewhere to go: either a trade to follow up on, or a market to
    record into. Tests that do not care which market they record into get this
    one, created once per user per test.
    """
    if sub not in _MARKETS:
        existing = client.get("/portfolios", headers=auth_headers(sub)).json()
        if existing:
            _MARKETS[sub] = existing[0]["id"]
        else:
            created = client.post(
                "/portfolios",
                headers=auth_headers(sub),
                json={"name": "Test market", "exchange": "US"},
            )
            _MARKETS[sub] = created.json()["id"]
    return _MARKETS[sub]


def post_text(
    client: TestClient,
    body: str,
    sub: str = "user_test1",
    trade_id: str | None = None,
    portfolio_id: str | None = None,
):
    payload: dict = {"text": body}
    if trade_id:
        payload["trade_id"] = trade_id
    # A note with no trade must name a market; tests that do not care get one.
    payload["portfolio_id"] = portfolio_id or (
        None if trade_id else ensure_market(client, sub)
    )
    return client.post("/notes/text", headers=auth_headers(sub), json=payload)


def get_note(client: TestClient, note_id: str, sub: str = "user_test1") -> dict:
    resp = client.get(f"/notes/{note_id}", headers=auth_headers(sub))
    assert resp.status_code == 200, resp.text
    return resp.json()


def drain(client: TestClient, note_id: str, sub: str = "user_test1", polls: int = 25) -> dict:
    """Poll until the background pipeline settles (EXTRACTED or FAILED)."""
    import time

    body: dict = {}
    for _ in range(polls):
        body = get_note(client, note_id, sub)
        if body["status"] in TERMINAL_STATUSES:
            return body
        time.sleep(0.01)
    raise AssertionError(f"note {note_id} did not finish processing: {body}")


def approve(client: TestClient, note_id: str, sub: str = "user_test1"):
    return client.post(f"/notes/{note_id}/approve", headers=auth_headers(sub))


def set_proposal(client: TestClient, proposal_body: dict) -> None:
    app.state.extractor.proposal = proposal_body


def extractor_calls() -> list[dict]:
    return app.state.extractor.calls


def trades(client: TestClient, sub: str = "user_test1", **params) -> list[dict]:
    resp = client.get("/trades", headers=auth_headers(sub), params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def trade(client: TestClient, trade_id: str, sub: str = "user_test1") -> dict:
    resp = client.get(f"/trades/{trade_id}", headers=auth_headers(sub))
    assert resp.status_code == 200, resp.text
    return resp.json()


def record_text(
    client: TestClient,
    body: str,
    sub: str = "user_test1",
    trade_id: str | None = None,
    portfolio_id: str | None = None,
) -> dict:
    """Post a typed note and wait for extraction. Returns the note view."""
    resp = post_text(client, body, sub, trade_id, portfolio_id)
    assert resp.status_code == 201, resp.text
    return drain(client, resp.json()["id"], sub)


def open_a_trade(client: TestClient, sub: str = "user_test1", **kwargs) -> dict:
    """Approve an OPEN note and return the created trade summary."""
    set_proposal(client, open_proposal(**kwargs))
    note = record_text(client, "I opened a position", sub)
    result = approve(client, note["id"], sub)
    assert result.status_code == 200, result.text
    return trade(client, result.json()["trade_id"], sub)
