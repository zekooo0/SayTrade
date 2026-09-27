"""Response DTOs for the trading API.

Decimals are always serialized as strings so clients never incur float drift
on prices or share counts.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


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
    price: str | None = None
    stop: str | None = None
    target: str | None = None


class NoteView(BaseModel):
    """One note: the trader's words, the AI summary, and the applied changes."""

    id: UUID
    status: str
    source: str
    trade_id: UUID | None = None
    text: str | None = None
    summary: str | None = None
    proposal: dict[str, Any] | None = None
    changes: list[ChangeView] = Field(default_factory=list)
    audio_url: str | None = None
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


class TradeSummaryView(BaseModel):
    """A trade with every number derived from its changes."""

    id: UUID
    symbol: str
    currency: str | None = None
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


class TradeDetailView(TradeSummaryView):
    notes: list[NoteView] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ApproveResultView(BaseModel):
    note_id: UUID
    status: str = "APPROVED"
    trade_id: UUID | None = None
    created_trade: bool = False
    changes_applied: int = 0
    warnings: list[str] = Field(default_factory=list)


class UsageView(BaseModel):
    plan: str = "FREE"
    ai_voice_limit: int
    ai_voice_used: int
    ai_voice_remaining: int
    period_start: datetime
    period_end: datetime


class MeView(BaseModel):
    user_id: UUID
    plan: str = "FREE"
    usage: UsageView
    entitlements: dict[str, Any] = Field(default_factory=dict)


class AnalyticsView(BaseModel):
    total_trades: int = 0
    open_trades: int = 0
    closed_trades: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    win_rate: float | None = None
    total_pnl: str | None = None
    average_pnl: str | None = None
    average_r: str | None = None
    best_r: str | None = None
    worst_r: str | None = None
    average_holding_days: float | None = None
