"""The AI action model — the contract between extraction, the review UI, and
the approval endpoint. Both `proposal` and `approved_payload` on a journal
entry are serialized ActionsPayload documents ("actions": [...]).

The extraction layer returns actions, not replacement database objects; the
backend validates and applies them. The LLM output must parse into
ActionsPayload or the extraction stage fails — the AI never writes directly.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

# Application-supported pattern vocabulary (API layer, not a DB CHECK).
# The extractor normalizes natural language to exactly these codes; the API
# rejects anything outside the set.
PATTERNS = frozenset(
    {
        "CUP_AND_HANDLE",
        "DOUBLE_BOTTOM",
        "DOUBLE_TOP",
        "ASCENDING_TRIANGLE",
        "DESCENDING_TRIANGLE",
        "SYMMETRICAL_TRIANGLE",
        "HEAD_AND_SHOULDERS",
        "INVERSE_HEAD_AND_SHOULDERS",
        "FLAG",
        "PENNANT",
        "WEDGE",
        "BREAKOUT",
        "PULLBACK",
        "RANGE",
    }
)


class ExecutionSpec(BaseModel):
    # Nullable at PROPOSAL for ambiguous notes ("I bought more" has no
    # quantity); the approval service requires real values.
    side: Literal["BUY", "SELL"] | None = None
    quantity: Decimal | None = Field(default=None, gt=0)
    price: Decimal | None = Field(default=None, gt=0)
    executed_at: datetime | None = None
    reason: str | None = None


class CreateTradeAction(BaseModel):
    action: Literal["CREATE_TRADE"] = "CREATE_TRADE"
    # Nullable at PROPOSAL: the extractor must never invent facts, and
    # ambiguous notes stay editable (spec rule 7). The approval service
    # requires these to be filled in before a Trade can be created.
    symbol: str | None = Field(default=None, min_length=1, max_length=20)
    direction: Literal["LONG", "SHORT"] | None = None
    pattern: str | None = None
    opened_at: datetime
    # The initial fill(s): creating a Trade without an actual execution is
    # impossible (the MVP only journals trades that were actually entered).
    executions: list[ExecutionSpec] = Field(min_length=1)
    rationale: str | None = None
    failure_point: Decimal | None = Field(default=None, gt=0)
    target_price: Decimal | None = Field(default=None, gt=0)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().upper()
        if not v:
            raise ValueError("symbol must not be empty")
        return v

    @field_validator("pattern")
    @classmethod
    def pattern_must_be_supported(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().upper()
        if v not in PATTERNS:
            raise ValueError(
                f"pattern '{v}' is not a supported value; supported: {sorted(PATTERNS)}"
            )
        return v


class AddExecutionAction(BaseModel):
    action: Literal["ADD_EXECUTION"] = "ADD_EXECUTION"
    trade_id: UUID
    # Nullable at PROPOSAL like the entry fill above; enforced at approval.
    side: Literal["BUY", "SELL"] | None = None
    quantity: Decimal | None = Field(default=None, gt=0)
    price: Decimal | None = Field(default=None, gt=0)
    executed_at: datetime | None = None
    reason: str | None = None


class _ChangeManagementAction(BaseModel):
    trade_id: UUID
    new_value: Decimal = Field(gt=0)
    # Sent by the client for conflict detection; if absent, the backend stores
    # the derived pre-change value as history.
    old_value: Decimal | None = Field(default=None, gt=0)
    reason: str = Field(min_length=1)


class ChangeStopAction(_ChangeManagementAction):
    action: Literal["CHANGE_STOP"] = "CHANGE_STOP"


class ChangeTargetAction(_ChangeManagementAction):
    action: Literal["CHANGE_TARGET"] = "CHANGE_TARGET"


class AddOutcomeAction(BaseModel):
    action: Literal["ADD_OUTCOME"] = "ADD_OUTCOME"
    trade_id: UUID
    result: Literal["WIN", "LOSS", "BREAKEVEN"]
    # Trader-declared Type 1-4; never inferred from executions.
    type: int | None = Field(default=None, ge=1, le=4)
    lesson: str | None = None
    notes: str | None = None


class CompleteTradeAction(BaseModel):
    action: Literal["COMPLETE_TRADE"] = "COMPLETE_TRADE"
    trade_id: UUID
    completed_at: datetime


ApprovedAction = Annotated[
    Union[
        CreateTradeAction,
        AddExecutionAction,
        ChangeStopAction,
        ChangeTargetAction,
        AddOutcomeAction,
        CompleteTradeAction,
    ],
    Field(discriminator="action"),
]


class ActionsPayload(BaseModel):
    """The approved (user-edited) payload. Order matters: the backend applies
    actions in the order given, in one transaction, all-or-nothing."""

    actions: list[ApprovedAction] = Field(min_length=1)