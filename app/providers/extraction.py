"""Extraction boundary: the trader's words in, a proposed change list out.

JSON mode (not OpenAI "structured outputs" strict mode): gpt-4o-mini's
strict-schema path rejects or starves on complex shapes, so we use plain
json_object mode with one automatic retry and keep Pydantic re-validation as
the hard boundary between the model and the domain.

The model is told what we already know (the recording time, the trade the note
was recorded against) so it never has to invent those facts.
"""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from pydantic import ValidationError

from app.schemas.proposal import NoteProposal

EXTRACTION_SYSTEM_PROMPT = """You read one spoken trade note from a trader (English or Arabic) and return the position changes it holds.

The trader is LONG ONLY. They buy shares and they sell shares. There are no short sales, ever.

Return ONE JSON object with exactly these keys:

{
  "summary": "<one short line saying what happened. Write it in the SAME language as the note: an English note gets an English summary, an Arabic note gets an Arabic summary.>",
  "symbol": "<ticker, e.g. NVDA, or null>",
  "changes": [
    {
      "kind": "OPEN | ADD | TRIM | STOP | TARGET | CLOSE",
      "occurred_at": null,
      "shares": null,
      "price": null,
      "stop": null,
      "target": null,
      "fraction": null
    }
  ]
}

The six kinds:
- OPEN   the first buy that opens a new position. Needs shares and price. Add stop and target when the trader states them.
- ADD    buy more of the same position. Needs shares and price.
- TRIM   sell part of the position. Needs shares, OR fraction when the trader gives a share of the position instead of a number (0.5 for "half", 0.3 for "30%"). Add price when stated.
- STOP   move the stop of the trade. Needs stop.
- TARGET move the target of the trade. Needs target.
- CLOSE  sell everything still held and close the trade. Add price when stated.

Rules. A broken rule is a failed extraction:
1. Never invent a value. If the trader did not give it, leave it null. The review screen asks for it later.
2. Never do arithmetic. For "half", "a third", "30% of my position" use fraction (0.5, 0.3333, 0.3). The backend computes the shares from the real position.
3. One note can hold several changes ("I sold half and moved the stop to 9.5"). Keep them in spoken order.
4. A note with no change is valid: use an empty changes list. Example: "I saw a reverse candle, I will wait for tomorrow."
5. symbol only when the trader names the instrument. Use the market ticker: "Nvidia" -> NVDA, "اموك" -> AMOC. Use null when the trader does not name it.
6. A stop that applies to one added part only ("the stop for this part is 10.5") is NOT a change and NOT a STOP. Leave stop null on the ADD. That part stop stays in the trader's own words.
7. occurred_at only when the trader states a time ("yesterday", "this morning"): ISO 8601, using the recording date for relative words. If no recording timestamp is given, leave occurred_at null. NEVER invent a date.
8. "I sold all", "I am out", "I closed it" with no amount means CLOSE.
9. Never infer a pattern, a reason, a result, or an emotion. The trader's words are the record.
10. Keep every number exactly as spoken. 129.5 stays 129.5.
11. The summary language MUST match the note language. English note -> English summary. Arabic note -> Arabic summary. Never answer an English note in Arabic.

Return only the JSON object. No markdown, no commentary."""


@dataclass
class ExtractionResult:
    proposal: dict[str, Any]
    summary: str | None
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
    """OpenAI chat completions in json_object mode, double-checked by Pydantic.

    The output MUST parse into NoteProposal or the stage fails. The model never
    writes to the database.
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

        context = transcript
        if recorded_at is not None:
            context += (
                f"\n\nRecording timestamp (UTC): {recorded_at.isoformat()}."
                " Use it for any time the trader did not state."
            )
        if active_trade is not None:
            trade_id, symbol = active_trade
            context += (
                f"\n\nThe trader is recording this note from their open {symbol} "
                f"trade (id {trade_id}). The note is about that trade."
            )

        def _call(user_content: str) -> str:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                temperature=0,
                response_format={"type": "json_object"},
            )
            return response.choices[0].message.content or ""

        last_error: Exception | None = None
        for attempt in range(2):
            content = await asyncio.to_thread(
                _call,
                context
                + (
                    "\n\nYour previous answer was not valid JSON. "
                    "Return ONLY the JSON object."
                    if attempt > 0
                    else ""
                ),
            )
            try:
                parsed = json.loads(content)
                proposal = NoteProposal.model_validate(parsed)
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = exc
                continue
            return ExtractionResult(
                proposal=proposal.model_dump(mode="json", exclude_none=False),
                summary=proposal.summary,
                provider="openai",
                model=self.model,
            )

        raise RuntimeError(f"extraction failed after retry: {last_error}") from last_error
