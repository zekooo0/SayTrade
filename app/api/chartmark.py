"""ChartMark over HTTP: the studio's three server needs.

POST /chartmark/ohlcv    the routed, cached, normalized series
GET  /chartmark/symbols  autocomplete over the journal's reference + live search
POST /chartmark/brief    the Step 5 AI commentary, metered by plan

The 4-step pipeline itself never runs here — it runs in the trader's browser,
so a parameter tweak recomputes instantly. These routes only serve data and
the one AI call the studio cannot make itself.

The keys are camelCase, matching the studio client's existing shapes.
"""

from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import AliasChoices, BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.billing import PLAN_PRO
from app.config import settings
from app.db import get_session
from app.domain.errors import AllowanceExhaustedError
from app.log import get_logger
from app.models import User
from app.providers.ohlcv import SAMPLE_PRESETS, ProviderError
from app.services import chartmark, chartmark_symbols

log = get_logger("saytrade.chartmark")

router = APIRouter(prefix="/chartmark", tags=["chartmark"])

SAMPLE_TICKERS = ("EGX_ORAS", "US_NVDA", "FX_XAUUSD_BROKEN")


class OhlcvRequest(BaseModel):
    ticker: str = Field(default="SAMPLE:EGX_ORAS", min_length=1, max_length=64)
    timeframe: Literal["4H", "1D", "1W"] = "1D"
    force_refresh: bool = Field(
        default=False,
        validation_alias=AliasChoices("forceRefresh", "force_refresh"),
    )


def _sample_catalog() -> list[dict]:
    return [
        {
            "canonicalTicker": f"SAMPLE:{key}",
            "market": "SAMPLE",
            "symbol": key,
            "name": preset["name"],
            "type": "SAMPLE",
            "exchange": preset["exchange_name"],
            "provider": "sample",
            "providerLabel": "Deterministic Sample",
            "currency": preset["currency"],
        }
        for key, preset in SAMPLE_PRESETS.items()
    ]


@router.post("/ohlcv")
async def ohlcv(
    request: OhlcvRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
    x_twelvedata_api_key: str | None = Header(default=None, alias="x-twelvedata-api-key"),
) -> dict[str, Any]:
    """The normalized series behind the studio's chart, and its meta plate."""
    try:
        return await chartmark.fetch_ohlcv(
            session,
            ticker=request.ticker,
            timeframe=request.timeframe,
            force_refresh=request.force_refresh,
            twelve_data_key=(
                (x_twelvedata_api_key or "").strip()
                or (settings.twelve_data_api_key or "").strip()
                or None
            ),
        )
    except chartmark.TickerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProviderError as exc:
        # A missing Twelve Data key is a configuration the trader can fix
        # themselves: 401 says so without drama.
        status = 401 if "Missing Twelve Data API key" in str(exc) else 502
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.get("/symbols")
async def symbols(
    market: str = "US",
    q: str = "",
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
    x_twelvedata_api_key: str | None = Header(default=None, alias="x-twelvedata-api-key"),
) -> dict[str, Any]:
    """Autocomplete: the journal's own ticker reference first, live search second."""
    market = (market or "US").strip().upper()
    if market not in ("EGX", "US", "FX", "INDEX", "SAMPLE"):
        market = "US"
    query = (q or "").strip()

    if market == "SAMPLE":
        return {"items": _sample_catalog(), "source": "catalog"}

    if not query:
        return {"items": [], "source": "catalog"}

    twelve_data_key = (
        (x_twelvedata_api_key or "").strip()
        or (settings.twelve_data_api_key or "").strip()
        or None
    )

    try:
        if market == "EGX":
            db_items = await chartmark_symbols.search_native_symbols(
                session, "EGX", query
            )
            yahoo_items = await chartmark_symbols.search_yahoo_egx(query)
            merged = chartmark_symbols.merge_symbol_results(
                db_items, yahoo_items, limit=12
            )
            if not merged:
                probed = await chartmark_symbols.probe_yahoo_egx_chart(query)
                merged = chartmark_symbols.merge_symbol_results(
                    probed, merged, limit=12
                )
            return {
                "items": merged,
                "source": "live" if merged else "catalog",
            }

        if market == "US":
            db_items = await chartmark_symbols.search_native_symbols(
                session, "US", query
            )
            td_items = await chartmark_symbols.search_twelvedata(
                "US", query, twelve_data_key
            )
            merged = chartmark_symbols.merge_symbol_results(
                db_items, td_items, limit=12
            )
            return {"items": merged, "source": "live" if merged else "catalog"}

        live = (
            await chartmark_symbols.search_yahoo_indices(query)
            if market == "INDEX"
            else await chartmark_symbols.search_twelvedata(
                market, query, twelve_data_key
            )
        )
        return {
            "items": chartmark_symbols.merge_symbol_results(live, limit=12),
            "source": "live" if live else "catalog",
        }
    except Exception:  # noqa: BLE001 - autocomplete degrades, never fails
        log.warning("symbol search degraded for %s", market, exc_info=True)
        return {"items": [], "source": "catalog"}


class BriefRequest(BaseModel):
    """The Steps 1–4 output, exactly as the studio computed it. The model is
    grounded in this and never recomputes a figure."""

    brief: dict[str, Any]


@router.post("/brief")
async def brief(
    request: Request,
    payload: BriefRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    """The Step 5 commentary, metered by plan like the voice notes.

    FREE holds a monthly allowance; PRO is unlimited. The check runs before
    any AI spend, and the row is written only after the spend happened, so a
    failed generation never eats the allowance.
    """
    writer = getattr(request.app.state, "brief_writer", None)
    if writer is None:
        raise HTTPException(
            status_code=503,
            detail="AI briefs are not open yet on this deployment.",
        )

    plan = request.app.state.entitlements.plan_for(user)
    limit = None if plan == PLAN_PRO else settings.chartmark_free_ai_briefs_per_month
    used = await chartmark.count_briefs_this_month(session, user.id)
    if limit is not None and used >= limit:
        _, end = chartmark.month_window()
        raise AllowanceExhaustedError(
            f"You have used {used} of {limit} AI briefs this month. The count "
            f"resets on {end.date().isoformat()}. SayTrade Pro lifts the limit."
        )

    from app.providers.briefs import BriefWriterError

    try:
        commentary = await writer.write(payload.brief)
    except BriefWriterError as exc:
        log.warning("brief refused: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - an AI outage is a 502, not a crash
        log.warning("brief failed: %s %s", type(exc).__name__, exc)
        raise HTTPException(
            status_code=502,
            detail="The AI brief could not be generated. Try again in a moment.",
        ) from exc

    await chartmark.record_brief(session, user.id)
    log.info(
        "chartmark brief for %s (%s plan): %s",
        user.id, plan, commentary.get("headline"),
    )

    return {
        "commentary": commentary,
        "modelUsed": getattr(writer, "model", None),
        "usage": {
            "aiBriefsUsed": used + 1,
            "aiBriefsLimit": limit,
        },
    }
