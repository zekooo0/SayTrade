"""ChartMark's data service: parse a ticker, route it, cache the closed bars.

The edge routing is the same one the Next.js API routes used:
    EGX: / INDEX:  -> Yahoo Finance
    US:  / FX:     -> Twelve Data (a key is required)
    SAMPLE:        -> the deterministic generator

Caching moved from local JSON files to the database: the old `.cache/ohlcv`
directory was per-lambda and evaporated on every cold start, while a row in
the suite's one Postgres survives everything. The semantics did not change —
completed bars only, a session window before re-checking the provider, and an
incremental append that asks the provider only for bars newer than the last
one served.
"""

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.log import get_logger
from app.models import ChartmarkAIBrief, ChartmarkOhlcvCache
from app.providers import ohlcv

log = get_logger("saytrade.chartmark")

MARKET_PREFIXES = ("EGX", "US", "FX", "INDEX", "SAMPLE")


class TickerError(Exception):
    """The ticker is not something this router can route."""


@dataclass(frozen=True)
class ParsedTicker:
    canonical_ticker: str
    market: str
    symbol: str


def parse_market_ticker(raw: str) -> ParsedTicker:
    """`EGX:ORAS`, `US:NVDA`, `FX:XAUUSD`, `INDEX:SPX`, `SAMPLE:EGX_ORAS`.

    A leading `^` routes to INDEX:, a bare symbol defaults to US:.
    """
    trimmed = (raw or "").strip().upper()
    if not trimmed:
        raise TickerError(
            "Please enter a ticker such as INDEX:SPX, EGX:ORAS, US:NVDA, or FX:XAUUSD."
        )

    if ":" not in trimmed:
        if trimmed.startswith("^"):
            return ParsedTicker(f"INDEX:{trimmed}", "INDEX", trimmed)
        return ParsedTicker(f"US:{trimmed}", "US", trimmed)

    prefix, _, symbol = trimmed.partition(":")
    prefix, symbol = prefix.strip(), symbol.strip()
    if not symbol:
        raise TickerError(f'Ticker "{raw}" is missing a symbol after "{prefix}:".')

    if prefix in MARKET_PREFIXES:
        return ParsedTicker(f"{prefix}:{symbol}", prefix, symbol)

    raise TickerError(
        f'Unsupported market prefix "{prefix}:". Use EGX: / INDEX: (Yahoo '
        "Finance), US: / FX: (Twelve Data), or SAMPLE:."
    )


# How long a cached series stays fresh before we incrementally check the
# provider for a new bar or an updated latest candle (intraday -> close).
_SESSION_CHECK_INTERVAL_MS = {
    "4H": 5 * 60 * 1000,   # 5 minutes
    "1D": 15 * 60 * 1000,  # 15 minutes
    "1W": 30 * 60 * 1000,  # 30 minutes
}


def _should_check_for_new_bar(
    entry: ChartmarkOhlcvCache, now_ms: int
) -> bool:
    return (now_ms - _entry_checked_ms(entry)) >= _SESSION_CHECK_INTERVAL_MS[
        entry.timeframe
    ]


def _entry_checked_ms(entry: ChartmarkOhlcvCache) -> int:
    checked = entry.last_checked_at
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    return int(checked.timestamp() * 1000)


def _limited_history_warning(
    provider_label: str, provider_symbol: str, candle_count: int
) -> str | None:
    if candle_count >= 15:
        return None
    plural = "" if candle_count == 1 else "s"
    return (
        f"{provider_label} only returned {candle_count} bar{plural} for "
        f"{provider_symbol}. Historical OHLCV series isn't provided for this "
        f"symbol/index by {provider_label}, so swing structure and Last Bottom "
        "cannot be computed."
    )


def _candles_to_dicts(candles: list[ohlcv.Candle]) -> list[dict[str, Any]]:
    return [
        {
            "timestamp": c.timestamp,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
        }
        for c in candles
    ]


def _dicts_to_candles(rows: list[dict[str, Any]]) -> list[ohlcv.Candle]:
    return [
        ohlcv.Candle(
            timestamp=int(r["timestamp"]),
            open=float(r["open"]),
            high=float(r["high"]),
            low=float(r["low"]),
            close=float(r["close"]),
            volume=float(r.get("volume") or 0),
        )
        for r in rows
    ]


def merge_incremental_candles(
    existing: list[ohlcv.Candle], incoming: list[ohlcv.Candle]
) -> tuple[list[ohlcv.Candle], int]:
    """Append newer bars and refresh the latest bar in place (e.g., intraday -> close)."""
    base = ohlcv.normalize_candles(existing)
    last_ts = base[-1].timestamp if base else -(2**63)
    updates = [c for c in ohlcv.normalize_candles(incoming) if c.timestamp >= last_ts]
    if not updates:
        return base, 0
    appended = sum(1 for c in updates if c.timestamp > last_ts)
    return ohlcv.normalize_candles([*base, *updates]), appended


async def _fetch_from_provider(
    parsed: ParsedTicker,
    timeframe: str,
    start_ms: int | None = None,
    twelve_data_key: str | None = None,
) -> dict[str, Any]:
    if parsed.market == "SAMPLE":
        return await ohlcv.sample_fetch_candles(
            symbol=parsed.symbol, timeframe=timeframe
        )
    if parsed.market in ("EGX", "INDEX"):
        return await ohlcv.yahoo_fetch_candles(
            market=parsed.market,
            symbol=parsed.symbol,
            timeframe=timeframe,
            start_ms=start_ms,
        )
    return await ohlcv.twelvedata_fetch_candles(
        market=parsed.market,
        symbol=parsed.symbol,
        timeframe=timeframe,
        api_key=twelve_data_key or "",
        canonical_ticker=parsed.canonical_ticker,
        start_ms=start_ms,
    )


def _provider_label(market: str) -> str:
    if market == "SAMPLE":
        return "Deterministic Sample"
    if market in ("EGX", "INDEX"):
        return "Yahoo Finance"
    return "Twelve Data"


def _provider_id(market: str) -> str:
    if market == "SAMPLE":
        return "sample"
    if market in ("EGX", "INDEX"):
        return "yahoo-finance"
    return "twelve-data"


async def fetch_ohlcv(
    session: AsyncSession,
    *,
    ticker: str,
    timeframe: str,
    force_refresh: bool = False,
    twelve_data_key: str | None = None,
    fetch: Any = None,
) -> dict[str, Any]:
    """The routed, cached, normalized series, plus the meta the studio shows.

    `fetch` is a seam for tests: the provider call, with the same signature as
    `_fetch_from_provider`.
    """
    parsed = parse_market_ticker(ticker)
    fetch = fetch or _fetch_from_provider
    now_ms = int(time.time() * 1000)

    def meta(
        cache_status: str,
        appended: int,
        last_checked_at: int,
        provider: str,
        provider_symbol: str,
        currency: str | None,
        exchange_name: str | None,
        warning: str | None,
        candles_count: int,
    ) -> dict[str, Any]:
        label = _provider_label(parsed.market)
        return {
            "canonicalTicker": parsed.canonical_ticker,
            "market": parsed.market,
            "symbol": parsed.symbol,
            "provider": provider,
            "providerLabel": label,
            "providerSymbol": provider_symbol,
            "timeframe": timeframe,
            "currency": currency,
            "exchangeName": exchange_name,
            "cacheStatus": cache_status,
            "appendedCount": appended,
            "lastCheckedAt": last_checked_at,
            "warning": warning
            or _limited_history_warning(label, provider_symbol, candles_count),
        }

    # Sample datasets bypass the cache entirely.
    if parsed.market == "SAMPLE":
        res = await fetch(parsed, timeframe)
        candles = ohlcv.normalize_candles(res["candles"])
        return {
            "candles": _candles_to_dicts(candles),
            "meta": meta(
                "bypass", 0, now_ms, "sample",
                res["provider_symbol"], res["currency"], res["exchange_name"],
                None, len(candles),
            ),
        }

    entry = await session.scalar(
        select(ChartmarkOhlcvCache).where(
            ChartmarkOhlcvCache.canonical_ticker == parsed.canonical_ticker,
            ChartmarkOhlcvCache.timeframe == timeframe,
        )
    )

    # Fast path: served this session, and no new bar could have closed.
    if (
        entry is not None
        and entry.candles
        and not force_refresh
        and not _should_check_for_new_bar(entry, now_ms)
    ):
        candles = ohlcv.normalize_candles(_dicts_to_candles(entry.candles))
        return {
            "candles": _candles_to_dicts(candles),
            "meta": meta(
                "hit", 0, _entry_checked_ms(entry), entry.provider,
                entry.provider_symbol, entry.currency, entry.exchange_name,
                None, len(candles),
            ),
        }

    # Incremental path: ask only for bars newer than the last one served.
    if entry is not None and entry.candles and not force_refresh:
        try:
            incremental = await fetch(
                parsed, timeframe,
                start_ms=entry.last_candle_timestamp,
                twelve_data_key=twelve_data_key,
            )
        except ohlcv.ProviderError as exc:
            # Upstream briefly unavailable: serve the cached history, say so.
            candles = ohlcv.normalize_candles(_dicts_to_candles(entry.candles))
            log.warning("stale fallback for %s: %s", parsed.canonical_ticker, exc)
            return {
                "candles": _candles_to_dicts(candles),
                "meta": meta(
                    "stale-fallback", 0, _entry_checked_ms(entry), entry.provider,
                    entry.provider_symbol, entry.currency, entry.exchange_name,
                    str(exc), len(candles),
                ),
            }

        merged, appended = merge_incremental_candles(
            _dicts_to_candles(entry.candles), incremental["candles"]
        )
        last_ts = merged[-1].timestamp if merged else entry.last_candle_timestamp
        provider_symbol = incremental["provider_symbol"] or entry.provider_symbol
        currency = incremental["currency"] or entry.currency
        exchange_name = incremental["exchange_name"] or entry.exchange_name

        entry.candles = _candles_to_dicts(merged)
        entry.last_candle_timestamp = last_ts
        entry.provider_symbol = provider_symbol
        entry.currency = currency
        entry.exchange_name = exchange_name
        entry.last_checked_at = datetime.now(timezone.utc)
        await session.flush()

        return {
            "candles": _candles_to_dicts(merged),
            "meta": meta(
                "appended" if appended else "hit", appended, now_ms,
                _provider_id(parsed.market), provider_symbol, currency,
                exchange_name, None, len(merged),
            ),
        }

    # Full fetch: cache miss, or a forced refresh.
    fresh = await fetch(parsed, timeframe, twelve_data_key=twelve_data_key)
    candles = ohlcv.normalize_candles(fresh["candles"])

    if candles:
        if entry is None:
            entry = ChartmarkOhlcvCache(
                canonical_ticker=parsed.canonical_ticker,
                timeframe=timeframe,
                provider=_provider_id(parsed.market),
                provider_symbol=fresh["provider_symbol"] or "",
                currency=fresh["currency"],
                exchange_name=fresh["exchange_name"],
                last_candle_timestamp=candles[-1].timestamp,
                candles=_candles_to_dicts(candles),
            )
            session.add(entry)
        else:
            entry.provider = _provider_id(parsed.market)
            entry.provider_symbol = fresh["provider_symbol"] or ""
            entry.currency = fresh["currency"]
            entry.exchange_name = fresh["exchange_name"]
            entry.candles = _candles_to_dicts(candles)
            entry.last_candle_timestamp = candles[-1].timestamp
        entry.last_checked_at = datetime.now(timezone.utc)
        await session.flush()

    return {
        "candles": _candles_to_dicts(candles),
        "meta": meta(
            "miss", 0, now_ms, _provider_id(parsed.market),
            fresh["provider_symbol"], fresh["currency"], fresh["exchange_name"],
            None, len(candles),
        ),
    }


# ---------------------------------------------------------------------------
# The AI brief allowance — a count, never a counter
# ---------------------------------------------------------------------------


def month_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now(timezone.utc)
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    next_year = now.year + 1 if now.month == 12 else now.year
    next_month = 1 if now.month == 12 else now.month + 1
    end = datetime(next_year, next_month, 1, tzinfo=timezone.utc)
    return start, end


async def count_briefs_this_month(
    session: AsyncSession, user_id: UUID, now: datetime | None = None
) -> int:
    start, end = month_window(now)
    from sqlalchemy import func as sa_func

    return int(
        await session.scalar(
            select(sa_func.count())
            .select_from(ChartmarkAIBrief)
            .where(
                ChartmarkAIBrief.user_id == user_id,
                ChartmarkAIBrief.created_at >= start,
                ChartmarkAIBrief.created_at < end,
            )
        )
        or 0
    )


async def record_brief(
    session: AsyncSession, user_id: UUID
) -> ChartmarkAIBrief:
    row = ChartmarkAIBrief(user_id=user_id)
    session.add(row)
    await session.flush()
    return row
