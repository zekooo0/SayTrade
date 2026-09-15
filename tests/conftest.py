"""Test fixtures. IMPORTANT: environment must be set before any app module is
imported (app.config reads it at import time), so this file sets it at the
very top."""

import base64
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("CLERK_FRONTEND_API", "https://test.clerk.accounts.dev")
os.environ.setdefault("FREE_AI_VOICE_LIMIT", "2")  # exhaust quickly in tests

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

# A single test signing key for the whole session (minted per test user).
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


def mint_token(
    sub: str,
    issuer: str = TEST_ISSUER,
    kid: str = TEST_KID,
    ttl_seconds: int = 3600,
) -> str:
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
        self.fail_save = False

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


class FakeTranscriber:
    def __init__(self, transcript: str = "default transcript"):
        self.transcript = transcript
        self.fail = False

    async def transcribe(self, data: bytes, mime_type: str) -> TranscriptionResult:
        if self.fail:
            raise RuntimeError("simulated transcription outage")
        return TranscriptionResult(transcript=self.transcript, provider="fake", model="fake-1")


DEFAULT_ACTIONS = {
    "actions": [
        {
            "action": "CREATE_TRADE",
            "symbol": "AAPL",
            "direction": "LONG",
            "pattern": "CUP_AND_HANDLE",
            "opened_at": "2026-09-14T12:00:00Z",
            "executions": [
                {
                    "side": "BUY",
                    "quantity": "100",
                    "price": "105",
                    "executed_at": "2026-09-14T12:00:00Z",
                }
            ],
            "failure_point": "90",
            "target_price": "120",
        }
    ]
}


class FakeExtractor:
    def __init__(self, actions: dict | None = None):
        self.actions = actions or DEFAULT_ACTIONS
        self.fail = False

    async def extract(self, transcript: str) -> ExtractionResult:
        if self.fail:
            raise RuntimeError("simulated extraction outage")
        return ExtractionResult(
            actions=self.actions["actions"], provider="fake", model="fake-1"
        )


async def reset_database() -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE usage_periods, journal_entries, trade_outcomes, "
                "management_decisions, executions, trades, users RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture
def client():
    import asyncio

    asyncio.run(reset_database())
    app.state.jwks = FakeJwks()
    app.state.storage = FakeStorage()
    app.state.transcriber = FakeTranscriber()
    app.state.extractor = FakeExtractor()
    app.state.entitlements = Entitlements(free_voice_limit=2)
    with TestClient(app) as test_client:
        yield test_client


def receive_voice(client: TestClient, sub: str = "user_test1", audio_file_name: str = "note.m4a"):
    """POST a fake voice recording; returns the entry as of the POST response
    (status RECEIVED — processing runs in the background after the response)."""
    return client.post(
        "/journal",
        headers=auth_headers(sub),
        files={"file": (audio_file_name, b"\x00\x01\x02fake-audio-bytes", "audio/mp4")},
        data={"duration_ms": "30000"},
    )


TERMINAL_STATUSES = {"EXTRACTED", "FAILED"}


def drain_entry(client: TestClient, entry_id: str, sub: str = "user_test1", polls: int = 25):
    """Poll GET /journal/{id} until the background pipeline reaches a stable
    state (EXTRACTED, or FAILED on a broken provider).

    The background task completes between TestClient requests; the small poll
    loop makes the tests independent of that timing.
    """
    import time

    body: dict = {}
    for _ in range(polls):
        body = client.get(f"/journal/{entry_id}", headers=auth_headers(sub)).json()
        if body["status"] in TERMINAL_STATUSES:
            return body
        time.sleep(0.01)
    raise AssertionError(f"entry {entry_id} did not finish processing: {body}")


def receive_and_drain(client: TestClient, sub: str = "user_test1"):
    resp = receive_voice(client, sub)
    assert resp.status_code == 201, resp.text
    return drain_entry(client, resp.json()["id"], sub)