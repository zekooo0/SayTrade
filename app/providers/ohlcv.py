"""ChartMark's OHLCV data providers, ported from the Next.js API routes.

Three adapters behind one shape: Yahoo Finance (EGX / INDEX), Twelve Data
(US / FX), and the deterministic sample generator (SAMPLE:). Each returns
completed, normalized bars — oldest to newest, deduplicated by timestamp —
and never decides anything about the trend: the 4-step pipeline that reads
these candles runs in the trader's browser, where a parameter tweak recomputes
instantly.

No cache lives here. Caching completed bars is a service concern
(app/services/chartmark.py), because it is state the database owns.
"""

import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

import httpx

HOUR_MS = 60 * 60 * 1000
DAY_MS = 24 * HOUR_MS

FETCH_TIMEOUT_SEC = 20.0
YAHOO_USER_AGENT = "Mozilla/5.0 (ChartMark OHLCV Pipeline)"


class ProviderError(Exception):
    """The edge data provider refused, returned nothing, or was unreachable."""


# ---------------------------------------------------------------------------
# Normalization — the shape every provider must return
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Candle:
    timestamp: int  # epoch ms, UTC
    open: float
    high: float
    low: float
    close: float
    volume: float


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def bar_duration_ms(timeframe: str) -> int:
    if timeframe == "4H":
        return 4 * HOUR_MS
    if timeframe == "1D":
        return DAY_MS
    if timeframe == "1W":
        return 7 * DAY_MS
    raise ValueError(f"Unknown timeframe: {timeframe}")


def normalize_candles(raw: Sequence[Candle]) -> list[Candle]:
    """Valid, deduplicated, sorted candles — whatever the provider handed back."""
    by_timestamp: dict[int, Candle] = {}
    for c in raw:
        if not all(_finite(v) for v in (c.timestamp, c.open, c.high, c.low, c.close)):
            continue
        high = max(c.high, c.open, c.close, c.low)
        low = min(c.low, c.open, c.close, c.high)
        volume = c.volume if _finite(c.volume) and c.volume >= 0 else 0
        by_timestamp[int(c.timestamp)] = Candle(
            timestamp=int(c.timestamp),
            open=c.open,
            high=high,
            low=low,
            close=c.close,
            volume=volume,
        )
    return [by_timestamp[ts] for ts in sorted(by_timestamp)]


def filter_completed_candles(
    candles: Sequence[Candle], timeframe: str, now_ms: int | None = None
) -> list[Candle]:
    """Only closed bars: Dow Theory reads finished candles, not mid-session ticks."""
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    duration = bar_duration_ms(timeframe)
    return [c for c in candles if c.timestamp + duration <= now_ms]


def aggregate_to_4h(hourly: Sequence[Candle]) -> list[Candle]:
    """Deterministic 4H UTC buckets — Yahoo serves 1H bars, not 4H."""
    sorted_candles = normalize_candles(hourly)
    buckets: dict[int, Candle] = {}
    for c in sorted_candles:
        bucket_ts = (c.timestamp // (4 * HOUR_MS)) * (4 * HOUR_MS)
        existing = buckets.get(bucket_ts)
        if existing is None:
            buckets[bucket_ts] = Candle(
                timestamp=bucket_ts,
                open=c.open,
                high=c.high,
                low=c.low,
                close=c.close,
                volume=c.volume,
            )
        else:
            buckets[bucket_ts] = Candle(
                timestamp=bucket_ts,
                open=existing.open,
                high=max(existing.high, c.high),
                low=min(existing.low, c.low),
                close=c.close,
                volume=existing.volume + c.volume,
            )
    return [buckets[ts] for ts in sorted(buckets)]


# ---------------------------------------------------------------------------
# Yahoo Finance — EGX (:symbol.CA) and INDEX (^GSPC-style)
# ---------------------------------------------------------------------------

INDEX_SYMBOL_ALIASES = {
    "SPX": "^GSPC", "SP500": "^GSPC", "GSPC": "^GSPC",
    "NDX": "^NDX", "NAS100": "^NDX", "IXIC": "^IXIC", "COMP": "^IXIC",
    "DJI": "^DJI", "US30": "^DJI", "RUT": "^RUT", "VIX": "^VIX",
    "EGX30": "^CASE30", "CASE30": "^CASE30",
    "FTSE": "^FTSE", "UKX": "^FTSE", "DAX": "^GDAXI", "GDAXI": "^GDAXI",
    "N225": "^N225", "NI225": "^N225", "HSI": "^HSI",
    "GOLD": "GC=F", "SILVER": "SI=F", "XAGUSD": "SI=F",
    "BTC": "BTC-USD", "BTCUSD": "BTC-USD",
}


def to_yahoo_symbol(market: str, symbol: str) -> str:
    clean = symbol.strip().upper()
    if market == "EGX":
        return clean if clean.endswith(".CA") else f"{clean}.CA"
    if market == "INDEX":
        if clean.startswith("^"):
            return clean
        return INDEX_SYMBOL_ALIASES.get(clean, f"^{clean}")
    return clean


def _yahoo_interval_and_range(timeframe: str) -> tuple[str, str]:
    if timeframe == "4H":
        # Yahoo has no native 4H: fetch 1H and aggregate deterministically.
        return "1h", "6mo"
    if timeframe == "1D":
        return "1d", "2y"
    return "1wk", "5y"


async def yahoo_fetch_candles(
    *, market: str, symbol: str, timeframe: str, start_ms: int | None = None
) -> dict[str, Any]:
    yahoo_symbol = to_yahoo_symbol(market, symbol)
    interval, range_ = _yahoo_interval_and_range(timeframe)

    params: dict[str, str] = {"interval": interval, "includePrePost": "false"}
    if start_ms is not None and math.isfinite(start_ms):
        params["period1"] = str(max(0, int(start_ms // 1000) - 86_400))
        params["period2"] = str(int(time.time()))
    else:
        params["range"] = range_

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{httpx_quote(yahoo_symbol)}"
    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SEC) as client:
            res = await client.get(
                url,
                params=params,
                headers={"User-Agent": YAHOO_USER_AGENT, "Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        raise ProviderError(f"Yahoo Finance could not be reached for {yahoo_symbol}.") from exc

    if res.status_code >= 400:
        raise ProviderError(f"Yahoo Finance returned HTTP {res.status_code} for {yahoo_symbol}.")

    payload = res.json()
    chart = payload.get("chart") or {}
    api_error = chart.get("error")
    if api_error:
        detail = api_error.get("description") or api_error.get("code") or "Unknown error"
        raise ProviderError(f"Yahoo Finance error for {yahoo_symbol}: {detail}")

    result = (chart.get("result") or [None])[0] or {}
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [None])[0] or {}

    if not result or not quote or not timestamps:
        raise ProviderError(f"Yahoo Finance returned no OHLCV candles for {yahoo_symbol}.")

    raw: list[Candle] = []
    for i, ts_sec in enumerate(timestamps):
        o = _at(quote.get("open"), i)
        h = _at(quote.get("high"), i)
        low = _at(quote.get("low"), i)
        c = _at(quote.get("close"), i)
        v = _at(quote.get("volume"), i)
        if not isinstance(ts_sec, (int, float)) or None in (o, h, low, c):
            continue
        raw.append(
            Candle(
                timestamp=int(ts_sec * 1000),
                open=o,
                high=h,
                low=low,
                close=c,
                volume=v if isinstance(v, (int, float)) else 0,
            )
        )

    normalized = aggregate_to_4h(raw) if timeframe == "4H" else normalize_candles(raw)
    completed = filter_completed_candles(normalized, timeframe)

    meta = result.get("meta") or {}
    return {
        "candles": completed if completed else normalized,
        "provider_symbol": meta.get("symbol") or yahoo_symbol,
        "currency": meta.get("currency"),
        "exchange_name": meta.get("exchangeName"),
    }


# ---------------------------------------------------------------------------
# Twelve Data — US and FX, key required
# ---------------------------------------------------------------------------


def to_twelvedata_symbol(market: str, symbol: str) -> str:
    clean = symbol.strip().upper()
    if market == "FX":
        if "/" in clean:
            return clean
        if len(clean) == 6:
            return f"{clean[:3]}/{clean[3:]}"
    return clean


def _twelvedata_interval(timeframe: str) -> tuple[str, int]:
    if timeframe == "4H":
        return "4h", 500
    if timeframe == "1D":
        return "1day", 500
    return "1week", 260


def _parse_twelvedata_utc(datetime_str: str) -> int:
    """'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS', interpreted in UTC."""
    trimmed = datetime_str.strip()
    if " " in trimmed:
        iso = trimmed.replace(" ", "T") + "Z"
    else:
        iso = f"{trimmed}T00:00:00Z"
    parsed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return int(parsed.timestamp() * 1000)


async def twelvedata_fetch_candles(
    *,
    market: str,
    symbol: str,
    timeframe: str,
    api_key: str,
    canonical_ticker: str,
    start_ms: int | None = None,
) -> dict[str, Any]:
    key = (api_key or "").strip()
    if not key:
        raise ProviderError(
            f"Missing Twelve Data API key for {canonical_ticker}. Enter it in the "
            "studio settings, or use EGX: / INDEX: (Yahoo Finance) or SAMPLE: presets."
        )

    td_symbol = to_twelvedata_symbol(market, symbol)
    interval, outputsize = _twelvedata_interval(timeframe)

    params: dict[str, str] = {
        "symbol": td_symbol,
        "interval": interval,
        "outputsize": str(outputsize),
        "timezone": "UTC",
        "order": "ASC",
        "apikey": key,
    }
    if start_ms is not None and math.isfinite(start_ms):
        start_iso = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        params["start_date"] = start_iso

    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SEC) as client:
            res = await client.get(
                "https://api.twelvedata.com/time_series",
                params=params,
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        raise ProviderError(f"Twelve Data could not be reached for {td_symbol}.") from exc

    if res.status_code >= 400:
        raise ProviderError(f"Twelve Data returned HTTP {res.status_code} for {td_symbol}.")

    payload = res.json()
    if payload.get("status") == "error":
        raise ProviderError(
            f"Twelve Data error for {td_symbol}: {payload.get('message') or 'Unknown API error'}"
        )

    values = payload.get("values") or []
    if not values:
        raise ProviderError(f"Twelve Data returned no OHLCV bars for {td_symbol}.")

    raw: list[Candle] = []
    for row in values:
        try:
            raw.append(
                Candle(
                    timestamp=_parse_twelvedata_utc(row["datetime"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]) if row.get("volume") else 0,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    normalized = normalize_candles(raw)
    completed = filter_completed_candles(normalized, timeframe)

    meta = payload.get("meta") or {}
    return {
        "candles": completed if completed else normalized,
        "provider_symbol": meta.get("symbol") or td_symbol,
        "currency": meta.get("currency") or meta.get("currency_quote"),
        "exchange_name": meta.get("exchange") or ("FOREX" if market == "FX" else "US"),
    }


# ---------------------------------------------------------------------------
# The deterministic sample generator — reproducible, offline, honest
# ---------------------------------------------------------------------------


def _mulberry32(seed: int):
    """The exact generator the TypeScript sample adapter used, so the demo
    datasets keep their prices: 32-bit state, one draw per call."""
    state = seed & 0xFFFFFFFF

    def rand() -> float:
        nonlocal state
        state = (state + 0x6D2B79F5) & 0xFFFFFFFF
        t = (state ^ (state >> 15)) & 0xFFFFFFFF
        t = (t * (1 | state)) & 0xFFFFFFFF  # Math.imul: multiply, keep low 32
        u = (t ^ (t >> 7)) & 0xFFFFFFFF
        t = (((t + ((u * (61 | t)) & 0xFFFFFFFF)) & 0xFFFFFFFF) ^ t) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296

    return rand


def _synthesize_series(
    waypoints: Sequence[dict],
    *,
    start_iso: str,
    timeframe: str,
    volatility_pct: float,
    base_volume: float,
    seed: int,
    special_wicks: dict[int, dict[str, float]] | None = None,
) -> list[Candle]:
    step_ms = bar_duration_ms(timeframe)
    start_ms = int(
        datetime.fromisoformat(start_iso.replace("Z", "+00:00")).timestamp() * 1000
    )
    total_bars = waypoints[-1]["bar"] + 1
    rand = _mulberry32(seed)
    special_wicks = special_wicks or {}

    # Interpolate the structural path with smoothstep easing.
    targets = [0.0] * total_bars
    for w in range(len(waypoints) - 1):
        a, b = waypoints[w], waypoints[w + 1]
        span = max(1, b["bar"] - a["bar"])
        for i in range(a["bar"], b["bar"] + 1):
            t = (i - a["bar"]) / span
            eased = t * t * (3 - 2 * t)
            targets[i] = a["price"] + (b["price"] - a["price"]) * eased

    waypoint_bars = {wp["bar"] for wp in waypoints}
    candles: list[Candle] = []
    prev_close = waypoints[0]["price"]
    for i in range(total_bars):
        is_waypoint = i in waypoint_bars
        noise = 0.0 if is_waypoint else (rand() - 0.5) * volatility_pct * 0.9
        close = round(targets[i] * (1 + noise), 4)
        open_ = round(close * 0.996, 4) if i == 0 else prev_close

        wick_up = max(0.002, rand() * volatility_pct * 0.85)
        wick_down = max(0.002, rand() * volatility_pct * 0.85)
        high = round(max(open_, close) * (1 + wick_up), 4)
        low = round(min(open_, close) * (1 - wick_down), 4)

        special = special_wicks.get(i) or {}
        if special.get("highDelta"):
            high = round(high + special["highDelta"], 4)
        if special.get("lowDelta"):
            low = round(low - special["lowDelta"], 4)

        move_factor = abs(close - open_) / max(1e-6, open_)
        volume = round(base_volume * (0.65 + rand() * 0.75 + move_factor * 18))

        candles.append(
            Candle(
                timestamp=start_ms + i * step_ms,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
            )
        )
        prev_close = close

    return candles


SAMPLE_PRESETS = {
    "EGX_ORAS": {
        "name": "ORAS.CA (Sample · Intact Uptrend)",
        "currency": "EGP",
        "exchange_name": "EGX (Simulated)",
        "waypoints": [
            {"bar": 0, "price": 245.0},
            {"bar": 18, "price": 298.5},
            {"bar": 34, "price": 266.0},
            {"bar": 58, "price": 342.0},  # P_prev
            {"bar": 76, "price": 304.25},  # the Last Bottom
            {"bar": 92, "price": 356.0},
            {"bar": 96, "price": 347.5},  # minor dip, filtered by minBars
            {"bar": 114, "price": 412.8},  # P_max
            {"bar": 126, "price": 378.4},
            {"bar": 138, "price": 394.6},
        ],
        "start_iso": "2026-04-01T00:00:00Z",
        "volatility_pct": 0.014,
        "base_volume": 480_000,
        "seed": 4201,
        "special_wicks": {76: {"lowDelta": 7.45}},
    },
    "US_NVDA": {
        "name": "NVDA (Sample · Minor-Dip Trap)",
        "currency": "USD",
        "exchange_name": "NASDAQ (Simulated)",
        "waypoints": [
            {"bar": 0, "price": 108.0},
            {"bar": 20, "price": 136.5},
            {"bar": 35, "price": 118.2},
            {"bar": 60, "price": 154.8},  # P_prev
            {"bar": 78, "price": 132.4},  # the Last Bottom
            {"bar": 95, "price": 162.0},
            {"bar": 99, "price": 156.5},
            {"bar": 109, "price": 174.0},
            {"bar": 113, "price": 168.4},
            {"bar": 124, "price": 189.75},  # P_max
            {"bar": 136, "price": 171.2},  # intact above 132.40
        ],
        "start_iso": "2026-04-01T00:00:00Z",
        "volatility_pct": 0.016,
        "base_volume": 38_000_000,
        "seed": 9007,
        "special_wicks": {78: {"lowDelta": 3.15}},
    },
    "FX_XAUUSD_BROKEN": {
        "name": "XAU/USD (Sample · Broken Trend)",
        "currency": "USD",
        "exchange_name": "FOREX (Simulated)",
        "waypoints": [
            {"bar": 0, "price": 2420.0},
            {"bar": 18, "price": 2595.0},
            {"bar": 32, "price": 2490.0},
            {"bar": 54, "price": 2745.0},  # P_prev
            {"bar": 70, "price": 2612.5},  # the Last Bottom
            {"bar": 98, "price": 2918.4},  # P_max
            {"bar": 112, "price": 2720.0},
            {"bar": 122, "price": 2588.2},  # breaks below -> TREND BROKEN
            {"bar": 134, "price": 2515.6},
        ],
        "start_iso": "2026-04-01T00:00:00Z",
        "volatility_pct": 0.008,
        "base_volume": 125_000,
        "seed": 7713,
        "special_wicks": {70: {"lowDelta": 19.5}},
    },
}


async def sample_fetch_candles(*, symbol: str, timeframe: str) -> dict[str, Any]:
    key = symbol.strip().upper()
    preset = SAMPLE_PRESETS.get(key) or SAMPLE_PRESETS["EGX_ORAS"]
    return {
        "candles": _synthesize_series(
            preset["waypoints"],
            start_iso=preset["start_iso"],
            timeframe=timeframe,
            volatility_pct=preset["volatility_pct"],
            base_volume=preset["base_volume"],
            seed=preset["seed"],
            special_wicks=preset["special_wicks"],
        ),
        "provider_symbol": preset["name"],
        "currency": preset["currency"],
        "exchange_name": preset["exchange_name"],
    }


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _at(values: Sequence[float | None] | None, i: int) -> float | None:
    if not values or i >= len(values):
        return None
    return values[i]


def httpx_quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")
