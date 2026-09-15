"""Response DTOs for the trading API.

Decimals are always serialized as strings so clients never incur float
drift on prices/quantities.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


def d(dec: Decimal | None) -> str | None:
    """Canonical decimal -> string: no exponent, no trailing zeros.

    NUMERIC(20,8) columns round-trip as 100.00000000; the API contract is the
    clean numeric string (100) so clients never format surprises.
    """
    if dec is None:
        return None
    try:
        return format(dec.normalize(), "f")
    except Exception:  # normalize can raise on NaN; never ship a broken view
        return str(dec)


def ds(dec: Decimal) -> str:
    """Non-optional variant for columns the schema guarantees NOT NULL."""
    return d(dec) or ""


class ExecutionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    side: str
    quantity: str
    price: str
    executed_at: datetime
    reason: str | None = None


class DecisionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    old_value: str | None = None
    new_value: str | None = None
    reason: str
    decided_at: datetime


class OutcomeView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: int | None = None
    result: str
    lesson: str | None = None
    notes: str | None = None
    completed_at: datetime


class TradeSummaryView(BaseModel):
    id: UUID
    symbol: str
    direction: str
    pattern: str
    status: str
    opened_at: datetime
    completed_at: datetime | None = None
    net_position: str = "0"
    current_stop: str | None = None
    current_target: str | None = None


class TradeDetailView(TradeSummaryView):
    rationale: str | None = None
    failure_point: str | None = None
    target_price: str | None = None
    executions: list[ExecutionView] = Field(default_factory=list)
    decisions: list[DecisionView] = Field(default_factory=list)
    outcome: OutcomeView | None = None
    journal_entry_ids: list[UUID] = Field(default_factory=list)
    realized_pnl: str | None = None  # NULL unless fully closed (modeled like the spec)
    r_multiple: str | None = None
    warnings: list[str] = Field(default_factory=list)


class JournalEntryView(BaseModel):
    id: UUID
    status: str
    type: str
    trade_id: UUID | None = None
    transcript: str | None = None
    proposal: dict[str, Any] | None = None
    approved_payload: dict[str, Any] | None = None
    audio_url: str | None = None
    transcription_provider: str | None = None
    transcription_model: str | None = None
    extraction_provider: str | None = None
    extraction_model: str | None = None
    failed_stage: str | None = None
    error_message: str | None = None
    approved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ApprovalResultView(BaseModel):
    journal_entry_id: UUID | None = None  # None on the manual path
    status: str = "APPROVED"
    trade_ids: list[UUID] = Field(default_factory=list)
    created_trade_id: UUID | None = None
    warning: str | None = None


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
    trade_count: int = 0
    wins: int = 0
    losses: int = 0
    breakevens: int = 0
    win_rate: float | None = None
    gross_pnl: str | None = None
    average_pnl: str | None = None
    total_r: str | None = None
    average_r: str | None = None
    average_holding_days: float | None = None
    pnl_result_disagreements: int = 0
    by_pattern: dict[str, dict[str, Any]] = Field(default_factory=dict)
    by_type: dict[str, dict[str, Any]] = Field(default_factory=dict)
    over_time: list[dict[str, Any]] = Field(default_factory=list)