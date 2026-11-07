"""Response DTOs for the trading API.

Decimals are always serialized as strings so clients never incur float drift
on prices or share counts.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


def d(dec: Decimal | None) -> str | None:
    """Canonical decimal -> string: no exponent, no trailing zeros.

    NUMERIC(20,8) columns round-trip as 100.00000000; the API contract is the
    clean numeric string (100) so clients never see format surprises.
    """
    if dec is None:
        return None
    try:
        return format(dec.normalize(), "f")
    except Exception:  # normalize can raise on NaN; never ship a broken view
        return str(dec)


class ChangeView(BaseModel):
    """One approved position change, as shown on the timeline."""

    id: UUID
    kind: str
    occurred_at: datetime
    shares: str | None = None
    amount: str | None = None
    price: str | None = None
    stop: str | None = None
    target: str | None = None


class NoteView(BaseModel):
    """One note: the trader's words, the AI summary, and the applied changes."""

    id: UUID
    status: str
    source: str
    trade_id: UUID | None = None
    # The market this note was recorded into. Null for a follow-up, which takes
    # its market from the trade it belongs to.
    portfolio_id: UUID | None = None
    # The symbol this note belongs to: its trade's symbol, else the symbol the
    # model read in the trader's words (an opening note has no trade yet).
    symbol: str | None = None
    # The tickers this note's market already holds. The review screen warns when
    # the model read one that is not here.
    market_symbols: list[str] = Field(default_factory=list)
    # The open trades to choose from, when a note changes a position and does not
    # say which trade it belongs to. The review screen offers them; without a
    # choice the note cannot be approved.
    symbol_candidates: list[str] | None = None
    text: str | None = None
    summary: str | None = None
    proposal: dict[str, Any] | None = None
    changes: list[ChangeView] = Field(default_factory=list)
    audio_url: str | None = None
    # The screenshot the note was read from, when it came from an image.
    image_url: str | None = None
    duration_ms: int | None = None
    transcription_provider: str | None = None
    transcription_model: str | None = None
    extraction_provider: str | None = None
    extraction_model: str | None = None
    failed_stage: str | None = None
    error_message: str | None = None
    approved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class PredictionAccuracyView(BaseModel):
    """How often the trader's predictions about what happens next were right.

    PENDING predictions are counted, so the page can say how many are waiting,
    but excluded from the percentage: an unjudged prediction is not evidence
    either way.
    """

    accuracy: float | None = None
    correct: int = 0
    incorrect: int = 0
    pending: int = 0
    judged: int = 0
    # Under five judged predictions the number says nothing.
    thin: bool = True


class PredictionView(BaseModel):
    """What the trader expected after closing, and whether it happened.

    Deliberately separate from the trade's own result. A losing trade can carry a
    correct prediction, and that combination is the whole reason this is measured.
    """

    level: str | None = None
    direction: str | None = None
    reason: str | None = None
    result: str = "PENDING"
    resolved_at: datetime | None = None


class TradeSummaryView(BaseModel):
    """A trade with every number derived from its changes."""

    id: UUID
    # The trader's readable handle: "#12". Display only — a note carries the id.
    number: int | None = None
    symbol: str
    currency: str | None = None
    # The market this trade belongs to. Null only for a trade recorded before
    # portfolios existed, or one recorded without a portfolio selected.
    portfolio_id: UUID | None = None
    opened_at: datetime
    closed_at: datetime | None = None
    is_open: bool = True

    shares: str = "0"
    average_price: str | None = None
    current_stop: str | None = None
    initial_stop: str | None = None
    current_target: str | None = None
    planned_r: str | None = None

    realized_pnl: str | None = None
    r_multiple: str | None = None
    holding_days: float | None = None

    note_count: int = 0
    last_note_at: datetime | None = None

    # Cost of the position as a percent of the account's capital. Derived on
    # read from shares and average price; None when the trader has not set a
    # capital, or the position has no size yet.
    position_size_percent: str | None = None


class TradeDetailView(TradeSummaryView):
    notes: list[NoteView] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    # What the trader expected after closing, and whether it happened. Null for
    # a trade that is still open, or one closed without an expectation.
    prediction: PredictionView | None = None


class ApproveResultView(BaseModel):
    note_id: UUID
    status: str = "APPROVED"
    trade_id: UUID | None = None
    created_trade: bool = False
    changes_applied: int = 0
    warnings: list[str] = Field(default_factory=list)


class UsageView(BaseModel):
    """What the plan allows and what is used. `None` limits are unlimited."""

    plan: str = "FREE"
    active_trades_used: int = 0
    active_trades_limit: int | None = None
    voice_notes_used: int = 0
    voice_notes_limit: int | None = None
    period_start: datetime
    period_end: datetime


class MeView(BaseModel):
    user_id: UUID
    plan: str = "FREE"
    usage: UsageView
    entitlements: dict[str, Any] = Field(default_factory=dict)


class PortfolioView(BaseModel):
    """A market: its own exchange, its own currency, its own trades."""

    id: UUID
    name: str
    # "EGX" or "US". This is the market the trader picked; the currency follows
    # from it, and the symbol lookup is scoped by it.
    exchange: str | None = None
    currency: str | None = None
    capital: str | None = None
    trade_count: int = 0
    created_at: datetime


class PortfolioIn(BaseModel):
    """Create or change a portfolio. Absent fields are left alone; an explicit
    null capital clears it."""

    name: str | None = Field(default=None, min_length=1, max_length=60)
    # The market. Sending this also sets the currency (EGX -> EGP, US -> USD).
    exchange: str | None = Field(default=None, max_length=8)
    currency: str | None = Field(default=None, max_length=8)
    capital: Decimal | None = Field(default=None, ge=0)
    clear_capital: bool = False


class EquityPoint(BaseModel):
    """One closed trade on the cumulative curve, in close-date order."""

    trade_id: UUID
    symbol: str
    closed_at: datetime
    pnl: str
    cumulative_pnl: str
    r_multiple: str | None = None


class RBucket(BaseModel):
    """One bar of the R distribution."""

    label: str
    count: int
    loss: bool = False


class MonthBucket(BaseModel):
    month: str  # YYYY-MM
    pnl: str
    trades: int
    wins: int


class WeekBucket(BaseModel):
    week_start: str  # YYYY-MM-DD, a Monday
    notes: int


class StopBreach(BaseModel):
    """A closed trade that lost more than the 1R the stop should have capped."""

    trade_id: UUID
    symbol: str
    r_multiple: str
    closed_at: datetime | None = None


class MoneyScopeView(BaseModel):
    """The market the money figures are scoped to.

    Money is only meaningful inside one currency, so the client asks for one
    market and the figures below cover its trades alone. ``None`` on the view
    means no market was named: every market was added together, which the API
    allows for a caller that has only one.
    """

    portfolio_id: UUID | None = None
    name: str | None = None
    currency: str | None = None


class AnalyticsView(BaseModel):
    """The scoreboard. Every number is derived from change rows on read.

    `rated_trades` is the sample the R statistics use: closed trades that have
    both an initial stop and an exit price. `unrated_trades` are the closed
    trades those statistics had to leave out, so a small sample is visible
    instead of hidden.
    """

    # --- counts ---
    total_trades: int = 0
    open_trades: int = 0
    closed_trades: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    win_rate: float | None = None

    # How often the trader's predictions about what happens next were right.
    # Beside the win rate, never merged with it: a losing trade can carry a
    # correct prediction, and that is the point of measuring it separately.
    prediction_accuracy: PredictionAccuracyView | None = None

    # The market the money figures below cover. None means every market was
    # added together, which is only honest when they share one currency.
    money_scope: MoneyScopeView | None = None

    # --- money ---
    total_pnl: str | None = None
    average_pnl: str | None = None
    best_pnl: str | None = None
    worst_pnl: str | None = None
    gross_profit: str | None = None
    gross_loss: str | None = None
    profit_factor: str | None = None  # None when there is no loss yet to divide by
    max_drawdown: str | None = None
    average_holding_days: float | None = None

    # --- R statistics (over rated_trades only) ---
    rated_trades: int = 0
    unrated_trades: int = 0
    unpriced_trades: int = 0  # closed with a sale that carries no price
    # The two other reasons a closed trade is left out of the R statistics.
    # The three add up to unrated_trades, and no trade is counted twice: a
    # priced exit is what makes the other two reasons possible at all.
    unrated_no_stop: int = 0  # priced exit, but no initial stop was recorded
    unrated_no_risk: int = 0  # a stop and an exit, but no measurable R
    average_r: str | None = None
    best_r: str | None = None
    worst_r: str | None = None
    average_win_r: str | None = None
    average_loss_r: str | None = None
    win_rate_r: float | None = None
    expectancy_r: str | None = None

    # --- stop discipline ---
    beyond_stop: int = 0
    beyond_stop_trades: list[StopBreach] = Field(default_factory=list)

    # --- the journal habit ---
    notes_total: int = 0
    notes_voice: int = 0
    notes_typed: int = 0
    notes_image: int = 0
    observation_notes: int = 0
    note_streak_days: int = 0
    notes_per_week: list[WeekBucket] = Field(default_factory=list)

    # --- series ---
    equity_curve: list[EquityPoint] = Field(default_factory=list)
    r_distribution: list[RBucket] = Field(default_factory=list)
    monthly: list[MonthBucket] = Field(default_factory=list)


class BillingUrlView(BaseModel):
    """A payments URL for the client to open: a checkout, or the portal."""

    url: str


class WaitlistIn(BaseModel):
    """A public signup: an address, and optionally the page that earned it."""

    email: EmailStr
    source: str | None = Field(default=None, max_length=32)
