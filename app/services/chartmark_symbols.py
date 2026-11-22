"""Symbol autocomplete for the ChartMark studio.

Three sources, one merged answer: this database's own ticker reference (the
journal's `symbols` table — 215 EGX names and 5,300 US ones, already here),
the provider's live search, and — for EGX codes Yahoo search misses — a
direct chart probe of `<code>.CA`. Merge order is fixed: exact matches first,
then prefixes, then everything else, capped.
"""

import re
from urllib.parse import quote

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Symbol

SEARCH_TIMEOUT_SEC = 3.5
YAHOO_SEARCH_UA = "Mozilla/5.0 (ChartMark Symbol Search)"


async def search_native_symbols(
    session: AsyncSession, market: str, query: str, limit: int = 12
) -> list[dict]:
    """The journal's own reference, exact-first."""
    clean = query.strip()
    if not clean:
        return []

    exact = clean.upper()
    prefix = f"{clean}%"
    contains = f"%{clean}%"

    rows = (
        await session.scalars(
            select(Symbol)
            .where(
                Symbol.market == market,
                or_(Symbol.ticker.ilike(contains), Symbol.name.ilike(contains)),
            )
            .order_by(
                _match_rank(Symbol.ticker, Symbol.name, exact, prefix),
                func.length(Symbol.ticker).asc(),
                Symbol.ticker.asc(),
            )
            .limit(limit)
        )
    ).all()

    return [
        {
            "canonicalTicker": f"{market}:{row.ticker.upper()}",
            "market": market,
            "symbol": row.ticker.upper(),
            "name": row.name,
            "type": "STOCK",
            "exchange": row.exchange or market,
            "provider": "saytrade",
            "providerLabel": "SayTrade reference",
            "currency": "EGP" if market == "EGX" else "USD",
        }
        for row in rows
    ]


def _match_rank(ticker, name, exact: str, prefix: str):
    from sqlalchemy import case

    return case(
        (ticker == exact, 0),
        (ticker.ilike(prefix), 1),
        (name.ilike(prefix), 2),
        else_=3,
    )

async def _search_yahoo(query: str) -> list[dict]:
    url = (
        "https://query1.finance.yahoo.com/v1/finance/search"
        f"?q={quote(query)}&quotesCount=16&newsCount=0"
    )
    try:
        async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT_SEC) as client:
            res = await client.get(
                url,
                headers={"User-Agent": YAHOO_SEARCH_UA, "Accept": "application/json"},
            )
    except httpx.HTTPError:
        return []
    if res.status_code >= 400:
        return []
    return (res.json().get("quotes") or [])


async def search_yahoo_egx(query: str) -> list[dict]:
    clean = query.strip().upper().removesuffix(".CA")
    if not clean:
        return []

    queries = [clean]
    # Short codes surface reliably only with the .CA suffix.
    if re.fullmatch(r"[A-Z0-9]{2,6}", clean):
        queries.append(f"{clean}.CA")

    items: list[dict] = []
    for q in queries:
        for row in await _search_yahoo(q):
            raw_sym = (row.get("symbol") or "").upper()
            exch = (row.get("exchange") or row.get("exchDisp") or "").upper()
            is_egx = raw_sym.endswith(".CA") or exch == "CAI" or "CAIRO" in exch
            if not is_egx:
                continue
            base = raw_sym.removesuffix(".CA")
            if not base:
                continue
            quote_type = (row.get("quoteType") or "STOCK").upper()
            items.append(
                {
                    "canonicalTicker": f"EGX:{base}",
                    "market": "EGX",
                    "symbol": base,
                    "name": row.get("longname") or row.get("shortname") or base,
                    "type": "STOCK" if quote_type == "EQUITY" else quote_type,
                    "exchange": "EGX",
                    "provider": "yahoo-finance",
                    "providerLabel": "Yahoo Finance",
                    "currency": "EGP",
                }
            )
    return items


async def search_yahoo_indices(query: str) -> list[dict]:
    clean = query.strip()
    if not clean:
        return []

    items: list[dict] = []
    for row in await _search_yahoo(clean):
        raw_sym = (row.get("symbol") or "").upper()
        quote_type = (row.get("quoteType") or "").upper()
        if not raw_sym or (quote_type != "INDEX" and not raw_sym.startswith("^")):
            continue
        items.append(
            {
                "canonicalTicker": f"INDEX:{raw_sym}",
                "market": "INDEX",
                "symbol": raw_sym,
                "name": row.get("longname") or row.get("shortname") or raw_sym,
                "type": "INDEX",
                "exchange": (row.get("exchDisp") or row.get("exchange") or "INDEX").upper(),
                "provider": "yahoo-finance",
                "providerLabel": "Yahoo Finance",
            }
        )
    return items


async def probe_yahoo_egx_chart(query: str) -> list[dict]:
    """Yahoo search misses some .CA tickers its chart endpoint serves cleanly."""
    clean = query.strip().upper().removesuffix(".CA")
    if not re.fullmatch(r"[A-Z0-9]{2,8}", clean):
        return []

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(clean + '.CA')}?interval=1d&range=5d"
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            res = await client.get(
                url,
                headers={"User-Agent": YAHOO_SEARCH_UA, "Accept": "application/json"},
            )
    except httpx.HTTPError:
        return []
    if res.status_code >= 400:
        return []

    result = ((res.json().get("chart") or {}).get("result") or [None])[0]
    if not result or not result.get("timestamp"):
        return []

    meta = result.get("meta") or {}
    short_name = meta.get("shortName") or ""
    name = (
        meta.get("longName")
        or (short_name if "," not in short_name else None)
        or clean
    )
    return [
        {
            "canonicalTicker": f"EGX:{clean}",
            "market": "EGX",
            "symbol": clean,
            "name": name,
            "type": "STOCK",
            "exchange": "EGX",
            "provider": "yahoo-finance",
            "providerLabel": "Yahoo Finance",
            "currency": meta.get("currency") or "EGP",
        }
    ]


async def search_twelvedata(
    market: str, query: str, api_key: str | None
) -> list[dict]:
    clean = query.strip()
    if not clean:
        return []

    params: dict[str, str] = {"symbol": clean, "outputsize": "25"}
    if api_key:
        params["apikey"] = api_key

    try:
        async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT_SEC) as client:
            res = await client.get(
                "https://api.twelvedata.com/symbol_search",
                params=params,
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError:
        return []
    if res.status_code >= 400:
        return []

    items: list[dict] = []
    for row in res.json().get("data") or []:
        raw_sym = (row.get("symbol") or "").strip().upper()
        if not raw_sym:
            continue
        instrument_type = (row.get("instrument_type") or "").lower()
        country = (row.get("country") or "").lower()
        exchange = (row.get("exchange") or "").upper()

        if market == "US":
            is_us = country in ("united states", "us") or exchange in (
                "NASDAQ", "NYSE", "NYSE ARCA", "AMEX", "CBOE"
            )
            if not is_us:
                continue
            if "forex" in instrument_type or "crypto" in instrument_type:
                continue
            items.append(
                {
                    "canonicalTicker": f"US:{raw_sym}",
                    "market": "US",
                    "symbol": raw_sym,
                    "name": row.get("instrument_name") or raw_sym,
                    "type": "ETF" if "etf" in instrument_type else "STOCK",
                    "exchange": exchange or "US",
                    "provider": "twelve-data",
                    "providerLabel": "Twelve Data",
                    "currency": row.get("currency") or "USD",
                }
            )
        else:  # FX
            is_fx = (
                "forex" in instrument_type
                or "physical currency" in instrument_type
                or "/" in raw_sym
            )
            if not is_fx or "digital" in instrument_type:
                continue
            compact = raw_sym.replace("/", "")
            display = raw_sym if "/" in raw_sym else (
                f"{compact[:3]}/{compact[3:]}" if len(compact) == 6 else raw_sym
            )
            is_metal = display.startswith("XAU/") or display.startswith("XAG/")
            items.append(
                {
                    "canonicalTicker": f"FX:{compact}",
                    "market": "FX",
                    "symbol": display,
                    "name": row.get("instrument_name") or display,
                    "type": "METAL" if is_metal else "FOREX",
                    "exchange": exchange or "FOREX",
                    "provider": "twelve-data",
                    "providerLabel": "Twelve Data",
                    "currency": row.get("currency"),
                }
            )
    return items


def merge_symbol_results(
    *lists: list[dict], limit: int = 12
) -> list[dict]:
    """Lists in order, deduplicated by canonical ticker, capped.

    Earlier lists win: the journal's own reference outranks the live
    provider, and the provider outranks the probe.
    """
    seen: set[str] = set()
    out: list[dict] = []
    for lst in lists:
        for item in lst:
            key = str(item.get("canonicalTicker") or "").upper()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(item)
            if len(out) >= limit:
                return out
    return out
