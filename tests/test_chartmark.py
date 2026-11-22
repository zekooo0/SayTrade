"""ChartMark's server needs: routed OHLCV data, symbol autocomplete, the
metered AI brief, and the /me usage plate.

The provider APIs are faked at the service seam (`fetch=`), so these tests
prove the routing, the cache semantics, and the metering without reaching
Yahoo or Twelve Data. The sample generator is REAL: it is deterministic
Python, and one test pins it to the structural prices the TypeScript adapter
produced, so the demo datasets did not change in the port.
"""

import asyncio

import pytest
from conftest import auth_headers, on_free, reset_database

from app.db import SessionLocal
from app.main import app
from app.models import ChartmarkOhlcvCache
from app.providers.ohlcv import Candle, ProviderError
from app.providers import ohlcv
from app.services import chartmark, chartmark_symbols
from sqlalchemy import select


def _candle(ts: int, close: float = 100.0) -> Candle:
    return Candle(ts, close, close * 1.01, close * 0.99, close, 1000)


@pytest.fixture(autouse=True)
def _fresh_database():
    asyncio.run(reset_database())


# --- the ticker router ------------------------------------------------------


def test_a_bare_symbol_defaults_to_us():
    assert chartmark.parse_market_ticker("nvda").market == "US"


def test_a_caret_routes_to_the_index_market():
    parsed = chartmark.parse_market_ticker("^GSPC")
    assert parsed.market == "INDEX"
    assert parsed.canonical_ticker == "INDEX:^GSPC"


def test_a_prefixed_ticker_keeps_its_market():
    parsed = chartmark.parse_market_ticker(" egx:oras ")
    assert parsed.canonical_ticker == "EGX:ORAS"
    assert parsed.market == "EGX"
    assert parsed.symbol == "ORAS"


@pytest.mark.parametrize("bad", ["", "   ", "EGX:", "MARS:ORAS"])
def test_an_unroutable_ticker_is_refused(bad):
    with pytest.raises(chartmark.TickerError):
        chartmark.parse_market_ticker(bad)


# --- the deterministic sample generator -------------------------------------


def test_the_sample_generator_is_deterministic():
    waypoints = [{"bar": 0, "price": 100.0}, {"bar": 10, "price": 150.0}]
    kwargs = dict(
        start_iso="2026-04-01T00:00:00Z",
        timeframe="1D",
        volatility_pct=0.01,
        base_volume=1000,
        seed=1234,
    )
    a = ohlcv._synthesize_series(waypoints, **kwargs)
    b = ohlcv._synthesize_series(waypoints, **kwargs)
    assert a == b


def test_the_oras_sample_pins_its_structural_prices():
    """The demo dataset survived the port unchanged. These are the exact bars
    the TypeScript generator produced (verified against it at port time):
    same seed, same mulberry32 draws, same decimals."""
    candles = asyncio.run(
        ohlcv.sample_fetch_candles(symbol="EGX_ORAS", timeframe="1D")
    )["candles"]

    assert len(candles) == 139
    # P_prev — a waypoint, so the close is the structure's own price.
    assert candles[58].timestamp == 1780012800000
    assert candles[58].close == 342.0
    assert candles[58].open == 343.497
    # The Last Bottom — close exact, and the distinct low wick the
    # wick-basis pipeline reads, exact to the decimal.
    assert candles[76].timestamp == 1781568000000
    assert candles[76].close == 304.25
    assert candles[76].low == 294.4559
    assert candles[76].volume == 604353


# --- the cache flow (faked provider) ----------------------------------------


class FakeProvider:
    """The edge adapter, faked. Counts its calls; understands start_ms."""

    def __init__(self, bars: list[Candle]):
        self.bars = bars
        self.calls = 0

    async def __call__(self, parsed, timeframe, start_ms=None, twelve_data_key=None):
        self.calls += 1
        if start_ms is not None:
            newer = [b for b in self.bars if b.timestamp > start_ms]
            return {
                "candles": newer or self.bars[-1:],
                "provider_symbol": "FAKE",
                "currency": "USD",
                "exchange_name": "FAKE",
            }
        return {
            "candles": self.bars,
            "provider_symbol": "FAKE",
            "currency": "USD",
            "exchange_name": "FAKE",
        }


def _age_entry(ticker: str) -> None:
    """Make a cached series due for an incremental check: only the session
    window is aged — the last candle stays what it was."""

    async def scenario():
        from datetime import datetime, timedelta, timezone

        async with SessionLocal() as session:
            entry = await session.scalar(
                select(ChartmarkOhlcvCache).where(
                    ChartmarkOhlcvCache.canonical_ticker == ticker
                )
            )
            entry.last_checked_at = datetime.now(timezone.utc) - timedelta(hours=6)
            await session.commit()

    asyncio.run(scenario())


def test_a_cache_miss_fetches_and_stores():
    provider = FakeProvider([_candle(1_000_000), _candle(2_000_000)])

    async def scenario():
        async with SessionLocal() as session:
            res = await chartmark.fetch_ohlcv(
                session, ticker="US:NVDA", timeframe="1D", fetch=provider
            )
            await session.commit()
            return res

    res = asyncio.run(scenario())
    assert res["meta"]["cacheStatus"] == "miss"
    assert res["meta"]["providerLabel"] == "Twelve Data"
    assert res["meta"]["currency"] == "USD"
    assert [c["timestamp"] for c in res["candles"]] == [1_000_000, 2_000_000]


def test_a_second_call_in_the_session_window_is_a_hit():
    provider = FakeProvider([_candle(1_000_000)])

    async def scenario():
        async with SessionLocal() as session:
            first = await chartmark.fetch_ohlcv(
                session, ticker="US:NVDA", timeframe="1D", fetch=provider
            )
            await session.commit()
            second = await chartmark.fetch_ohlcv(
                session, ticker="US:NVDA", timeframe="1D", fetch=provider
            )
            await session.commit()
            return first, second, provider.calls

    first, second, calls = asyncio.run(scenario())
    assert first["meta"]["cacheStatus"] == "miss"
    assert second["meta"]["cacheStatus"] == "hit"
    assert calls == 1  # the second call never reached the provider


def test_a_new_closed_bar_is_appended_incrementally():
    """The provider is asked only for bars newer than the last one served."""
    provider = FakeProvider([_candle(1_000_000)])

    async def warm():
        async with SessionLocal() as session:
            await chartmark.fetch_ohlcv(
                session, ticker="US:NVDA", timeframe="1D", fetch=provider
            )
            await session.commit()

    asyncio.run(warm())
    provider.bars.append(_candle(2_000_000))  # a bar closed upstream
    _age_entry("US:NVDA")

    async def scenario():
        async with SessionLocal() as session:
            res = await chartmark.fetch_ohlcv(
                session, ticker="US:NVDA", timeframe="1D", fetch=provider
            )
            await session.commit()
            return res

    res = asyncio.run(scenario())
    assert res["meta"]["cacheStatus"] == "appended"
    assert res["meta"]["appendedCount"] == 1
    assert [c["timestamp"] for c in res["candles"]] == [1_000_000, 2_000_000]


def test_an_unreachable_provider_serves_the_cache_with_a_warning():
    provider = FakeProvider([_candle(1_000_000)])

    async def warm():
        async with SessionLocal() as session:
            await chartmark.fetch_ohlcv(
                session, ticker="US:NVDA", timeframe="1D", fetch=provider
            )
            await session.commit()

    asyncio.run(warm())
    _age_entry("US:NVDA")

    async def failing(parsed, timeframe, start_ms=None, twelve_data_key=None):
        raise ProviderError("upstream unavailable")

    async def scenario():
        async with SessionLocal() as session:
            res = await chartmark.fetch_ohlcv(
                session, ticker="US:NVDA", timeframe="1D", fetch=failing
            )
            await session.commit()
            return res

    res = asyncio.run(scenario())
    assert res["meta"]["cacheStatus"] == "stale-fallback"
    assert res["meta"]["warning"]
    assert res["candles"]


def test_a_sample_bypasses_the_cache_entirely():
    async def scenario():
        async with SessionLocal() as session:
            res = await chartmark.fetch_ohlcv(
                session, ticker="SAMPLE:US_NVDA", timeframe="1W"
            )
            await session.commit()
            return res

    res = asyncio.run(scenario())
    assert res["meta"]["cacheStatus"] == "bypass"
    assert res["meta"]["providerLabel"] == "Deterministic Sample"
    assert len(res["candles"]) == 137


# --- the routes --------------------------------------------------------------


def test_the_series_route_needs_a_session(client):
    assert client.post("/chartmark/ohlcv", json={}).status_code == 401


def test_the_series_route_serves_the_default_sample(client):
    on_free(client, "user_test1")

    res = client.post("/chartmark/ohlcv", json={}, headers=auth_headers())

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["meta"]["canonicalTicker"] == "SAMPLE:EGX_ORAS"
    assert body["meta"]["cacheStatus"] == "bypass"
    assert len(body["candles"]) == 139
    # Oldest to newest, strictly.
    stamps = [c["timestamp"] for c in body["candles"]]
    assert stamps == sorted(stamps)


def test_the_symbols_route_lists_the_sample_catalog(client):
    on_free(client, "user_test1")

    res = client.get(
        "/chartmark/symbols",
        params={"market": "SAMPLE", "q": ""},
        headers=auth_headers(),
    )

    assert res.status_code == 200
    assert res.json()["source"] == "catalog"
    assert len(res.json()["items"]) == 3


def test_the_symbols_route_finds_the_journals_own_reference(client, monkeypatch):
    on_free(client, "user_test1")

    async def no_live(query):
        return []

    monkeypatch.setattr(chartmark_symbols, "search_yahoo_egx", no_live)
    monkeypatch.setattr(chartmark_symbols, "probe_yahoo_egx_chart", no_live)

    res = client.get(
        "/chartmark/symbols",
        params={"market": "EGX", "q": "SWDY"},
        headers=auth_headers(),
    )

    assert res.status_code == 200
    items = res.json()["items"]
    assert items and items[0]["canonicalTicker"] == "EGX:SWDY"
    assert items[0]["providerLabel"] == "SayTrade reference"


# --- the brief: grounded, metered -------------------------------------------


def _brief_body():
    return {"brief": {"ticker": "SAMPLE:EGX_ORAS", "trendStatus": "intact"}}


def test_the_brief_route_needs_a_session(client):
    assert client.post("/chartmark/brief", json=_brief_body()).status_code == 401


def test_a_brief_counts_against_the_free_allowance(client):
    on_free(client, "user_test1")

    first = client.post("/chartmark/brief", json=_brief_body(), headers=auth_headers())
    assert first.status_code == 200, first.text
    assert first.json()["commentary"]["headline"]
    assert first.json()["usage"]["aiBriefsUsed"] == 1

    second = client.post("/chartmark/brief", json=_brief_body(), headers=auth_headers())
    assert second.status_code == 200
    assert second.json()["usage"]["aiBriefsUsed"] == 2


def test_the_free_allowance_refuses_the_third_brief(client):
    """The check runs BEFORE the AI spend: the third call never reaches the model."""
    on_free(client, "user_test1")
    for _ in range(2):
        client.post("/chartmark/brief", json=_brief_body(), headers=auth_headers())

    before = len(app.state.brief_writer.calls)
    third = client.post("/chartmark/brief", json=_brief_body(), headers=auth_headers())

    assert third.status_code == 402
    assert "AI briefs this month" in third.json()["detail"]
    assert len(app.state.brief_writer.calls) == before


def test_a_failed_generation_does_not_eat_the_allowance(client):
    on_free(client, "user_test1")
    app.state.brief_writer.fail = True

    res = client.post("/chartmark/brief", json=_brief_body(), headers=auth_headers())

    assert res.status_code == 502
    plate = client.get("/me", headers=auth_headers()).json()["chartmark"]
    assert plate["ai_briefs_used"] == 0


def test_a_pro_account_briefs_without_a_cap(client):
    from test_billing import me, post_webhook, subscription_payload

    on_free(client, "user_test1")
    account = me(client)["user_id"]
    post_webhook(client, subscription_payload(account))

    for _ in range(3):  # beyond the free allowance of two
        res = client.post("/chartmark/brief", json=_brief_body(), headers=auth_headers())
        assert res.status_code == 200, res.text

    plate = client.get("/me", headers=auth_headers()).json()["chartmark"]
    assert plate["ai_briefs_limit"] is None
    assert plate["ai_briefs_used"] == 3


def test_me_carries_the_chartmark_plate(client):
    on_free(client, "user_test1")

    plate = client.get("/me", headers=auth_headers()).json()["chartmark"]

    assert plate["ai_briefs_used"] == 0
    assert plate["ai_briefs_limit"] == 2  # pinned small in conftest
