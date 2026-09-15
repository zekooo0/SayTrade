"""Extraction boundary: transcript -> structured actions.

The extractor returns ACTIONS (never database objects) and its output must
parse into the ActionsPayload schema or the stage fails. The schema is
single-sourced from app.schemas.actions, so the LLM's JSON contract can never
drift from the approval contract.
"""

import asyncio
import json
from dataclasses import dataclass
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
12. "I'm completely out" / "closed it" / "finished" with no additions => COMPLETE_TRADE (completed_at = the time the trader said it, else recording time)."""


@dataclass
class ExtractionResult:
    actions: list[dict]
    provider: str
    model: str


class Extractor(Protocol):
    async def extract(self, transcript: str) -> ExtractionResult: ...


class OpenAIExtractor:
    """Structured-output extraction with gpt-4o-mini.

    The JSON schema is generated from the Pydantic contract
    (ActionsPayload.model_json_schema), so the model output is shape-checked
    twice: once by OpenAI's structured outputs, once by Pydantic.
    """

    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        self.api_key = api_key
        self.model = model

    def _client(self):
        from openai import OpenAI

        if not self.api_key:
            raise RuntimeError("OpenAI is not configured (OPENAI_API_KEY)")
        return OpenAI(api_key=self.api_key)

    async def extract(self, transcript: str) -> ExtractionResult:
        client = self._client()
        json_schema = ActionsPayload.model_json_schema()

        def _call():
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": transcript},
                ],
                temperature=0,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "approved_actions",
                        "strict": True,
                        "schema": json_schema,
                    },
                },
            )
            return response.choices[0].message.content

        content = await asyncio.to_thread(_call)
        if not content:
            raise RuntimeError("extraction returned empty content")

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError("extraction returned invalid JSON") from exc

        # Pydantic re-validation: the trust boundary between the LLM and the
        # domain (the AI never writes directly; malformed output fails here).
        payload = ActionsPayload.model_validate(parsed)
        return ExtractionResult(
            actions=payload.model_dump(mode="json")["actions"],
            provider="openai",
            model=self.model,
        )