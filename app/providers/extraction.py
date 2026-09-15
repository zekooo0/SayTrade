"""Extraction boundary: transcript -> structured actions.

The extractor returns ACTIONS (never database objects) and its output must
parse into the ActionsPayload schema or the stage fails. The schema is
single-sourced from app.schemas.actions, so the extraction contract can never
drift from the approval contract.

JSON mode (not OpenAI "structured outputs" strict mode): gpt-4o-mini's
strict-schema path rejects/starves on complex discriminated unions
(finish_reason=length with 0 tokens), so we use plain json_object mode with
one automatic retry and keep Pydantic re-validation as the hard boundary
between the LLM and the domain.
"""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.schemas.actions import ActionsPayload, PATTERNS

EXTRACTION_SYSTEM_PROMPT = f"""You extract structured trading actions from a trader's voice-journal transcript.

Return ONLY a JSON object of the form {{"actions": [...]}}, one entry per action, in the order the trader said them. Each action object has an "action" field with exactly one of these values:

- CREATE_TRADE: symbol (ticker, uppercase), direction ("LONG"/"SHORT"), pattern (code from the supported list below), opened_at (ISO-8601 UTC; if the trader gives no time, use the recording time), executions: at least one {{side, quantity, price, executed_at, reason?}}, optional rationale, failure_point, target_price (all prices > 0).
- ADD_EXECUTION: trade_id (see rule 3 below), side, quantity, price, executed_at, optional reason.
- CHANGE_STOP: trade_id, new_value, optional old_value, reason (required — why the trader moved it).
- CHANGE_TARGET: trade_id, new_value, optional old_value, reason (required).
- ADD_OUTCOME: trade_id, result ("WIN"/"LOSS"/"BREAKEVEN"), optional type (integer 1-4), lesson, notes.
- COMPLETE_TRADE: trade_id, completed_at.

Supported pattern codes (use exactly these): {", ".join(sorted(PATTERNS))}.

STRICT RULES (from the product spec — violations are failures):
1. Never invent facts. If the trader did not say it, leave the field null/omitted.
2. Distinguish actual executions from intentions. "I'm thinking of adding" is not an execution.
3. Never invent trade_id values. Only include trade_id when the trader references an existing trade by context (e.g. "the same trade"), and mark it "CURRENT_ACTIVE_TRADE" when the backend context tells you which trade is active. Never guess an id string.
4. Distinguish the original plan from later management: the first mention is CREATE_TRADE; later stop/target moves are CHANGE_STOP/CHANGE_TARGET.
5. Distinguish pattern (what the trader saw, e.g. cup and handle -> CUP_AND_HANDLE) from Type 1-4 (what happened after the breakout, an integer) — they are different fields on different actions.
6. Preserve spoken quantities, prices, dates and times exactly; compute nothing.
7. Mark ambiguity instead of guessing: if "I bought more" has no quantity, emit the action with quantity null/omitted — never invent a number.
8. Keep actions in spoken order; do not reorder or dedupe.
9. Never infer WIN/LOSS/BREAKEVEN from P&L. Only ADD_OUTCOME when the trader explicitly declares how it finished.
10. Never infer Type 1-4 — only emit `type` when the trader states it.
11. Never create a Trade for an opportunity the trader observed but did not enter. "I'm watching it" is not CREATE_TRADE.
12. "I'm completely out" / "closed it" / "finished" with no additions => COMPLETE_TRADE (completed_at = the time the trader said it, else recording time).
13. Partial position reductions — "I sold/took off/cut X% of the position", "still holding the rest" — are ADD_EXECUTION with side SELL (quantity = the reduced amount). They are NOT CHANGE_STOP or CHANGE_TARGET and never COMPLETE the Trade.

The output must be valid JSON that parses without errors."""


@dataclass
class ExtractionResult:
    actions: list[dict]
    provider: str
    model: str


class Extractor(Protocol):
    async def extract(
        self,
        transcript: str,
        recorded_at: datetime | None = None,
        active_trade: tuple[str, str] | None = None,
    ) -> ExtractionResult: ...


class OpenAIExtractor:
    """gpt-4o-mini extraction in json_object mode, double-checked by Pydantic.

    The output MUST parse into ActionsPayload or the stage fails (the AI never
    writes directly). One retry absorbs the occasional malformed emission.
    """

    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        self.api_key = api_key
        self.model = model

    def _client(self):
        from openai import OpenAI

        if not self.api_key:
            raise RuntimeError("OpenAI is not configured (OPENAI_API_KEY)")
        return OpenAI(api_key=self.api_key)

    async def extract(
        self,
        transcript: str,
        recorded_at: datetime | None = None,
        active_trade: tuple[str, str] | None = None,
    ) -> ExtractionResult:
        client = self._client()
        # Facts we KNOW — seed them so the model never invents:
        #  - the recording time (opened_at/executed_at when unstated)
        #  - the Trade the note was recorded against (the id goes into the
        #    payload verbatim; the sentinel would be a dead end)
        context = transcript
        if recorded_at is not None:
            context += (
                f"\n\nRecording timestamp (UTC): {recorded_at.isoformat()}."
                " Use it for any times the trader did not state."
            )
        if active_trade is not None:
            trade_id, symbol = active_trade
            context += (
                f"\n\nActive Trade context: id={trade_id}, symbol={symbol} (the "
                "Trade the user is recording this note from). When the trader "
                f"refers to 'the trade' or 'the position', use trade_id "
                f"'{trade_id}' VERBATIM in every action — never invent another "
                "id and never use the CURRENT_ACTIVE_TRADE placeholder."
            )

        def _call(user_content: str):
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                temperature=0,
                response_format={"type": "json_object"},
            )
            return response.choices[0].message.content

        last_error: Exception | None = None
        for attempt in range(2):
            try:
                content = await asyncio.to_thread(
                    _call,
                    context
                    + (
                        "\n\nYour previous output could not be parsed. "
                        "Return ONLY a valid JSON object; nothing else."
                        if attempt > 0
                        else ""
                    ),
                )
                if not content:
                    raise RuntimeError("extraction returned empty content")
                parsed = json.loads(content)
                payload = ActionsPayload.model_validate(parsed)
                return ExtractionResult(
                    actions=payload.model_dump(mode="json")["actions"],
                    provider="openai",
                    model=self.model,
                )
            except (json.JSONDecodeError, RuntimeError, ValueError) as exc:
                last_error = exc

        raise RuntimeError(
            f"extraction failed after retry: {last_error}"
        ) from last_error