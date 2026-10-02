"""Extraction boundary: the trader's words in, a proposed change list out.

JSON mode (not OpenAI "structured outputs" strict mode): the strict-schema path
rejects or starves on complex shapes, so we use plain json_object mode with one
automatic retry and keep Pydantic re-validation as the hard boundary between the
model and the domain.

The model does not name a ticker and have it believed. It proposes companies —
`security_candidates` — and app/services/symbols.py decides which of those trade
in the note's market, against the vendored reference. That is what stops a
misheard word becoming a confident, wrong ticker.

The model is told what we already know (the recording time, the trade the note
was recorded against, the market it belongs to and the tickers held there) so it
never has to invent those facts.
"""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from pydantic import ValidationError

from app.log import get_logger
from app.schemas.proposal import NoteProposal

log = get_logger("saytrade.extraction")

EXTRACTION_SYSTEM_PROMPT = """You read one spoken trade note from a trader (English or Arabic) and return the position changes it holds.

The trader is LONG ONLY. They buy shares and they sell shares. There are no short sales, ever.

Return ONE JSON object with exactly these keys:

{
  "language": "<the language the note is spoken in: en or ar>",
  "security_candidates": [
    {
      "exchange": "<the exchange code you believe it trades on>",
      "ticker": "<its ticker, or null>",
      "name": "<the company name in English>",
      "aliases": ["<the words the trader used for it>"]
    }
  ],
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
5. security_candidates: the companies this note is about. The trader may say a ticker, or a company name in Arabic or in English. For each company give the exchange you believe it trades on, its ticker, the company name in English, and the words the trader used for it. These are PROPOSALS: the backend checks each ticker against that market's own list of what trades there and discards the ones that are not on it, so list only companies you actually recognise, and never pad the list. Leave it empty when the note names no company.
6. A stop that applies to one added part only ("the stop for this part is 10.5") is NOT a change and NOT a STOP. Leave stop null on the ADD. That part stop stays in the trader's own words.
7. occurred_at only when the trader states a time ("yesterday", "this morning"): ISO 8601, using the recording date for relative words. If no recording timestamp is given, leave occurred_at null. NEVER invent a date.
8. "I sold all", "I am out", "I closed it" with no amount means CLOSE.
9. Never infer a pattern, a reason, a result, or an emotion. The trader's words are the record.
10. Keep every number exactly as spoken. 129.5 stays 129.5.
11. language is the language the note is spoken in: "en" or "ar". It is not the language of your answer — you write no prose at all.

Return only the JSON object. No markdown, no commentary."""


@dataclass
class ExtractionResult:
    proposal: dict[str, Any]
    provider: str
    model: str


@dataclass(frozen=True)
class MarketContext:
    """The market a note is recorded into.

    The extractor is handed the trader's own name for it, its currency, and the
    tickers they already hold there. That is what stops a misheard ticker from
    becoming a confident, wrong symbol — in the wrong market.

    Defined above the Protocol: the Protocol annotates a parameter with it, and
    that annotation is evaluated when this module is imported.
    """

    name: str
    currency: str | None
    symbols: tuple[str, ...]
    # The product's market code: "EGX" or "US". The model needs it to judge
    # whether a company it recognises trades here at all.
    exchange: str | None = None


class Extractor(Protocol):
    async def extract(
        self,
        transcript: str,
        recorded_at: datetime | None = None,
        active_trade: tuple[str, str] | None = None,
        market: MarketContext | None = None,
    ) -> ExtractionResult: ...


class OpenAIExtractor:
    """OpenAI chat completions in json_object mode, double-checked by Pydantic.

    The output MUST parse into NoteProposal or the stage fails. The model never
    writes to the database.
    """

    def __init__(self, api_key: str, model: str = "gpt-6-luna"):
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
        market: MarketContext | None = None,
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

        if market is not None:
            held = ", ".join(market.symbols) if market.symbols else "nothing yet"
            where = market.exchange or market.name
            currency = f", currency {market.currency}" if market.currency else ""
            context += (
                f'\n\nThe note is recorded into the trader\'s "{market.name}" market'
                f" — {where}{currency}. Tickers already held there: {held}.\n"
                "A trader recording into this market is talking about a company "
                "listed here, not one from another exchange. Offer only companies "
                "you believe trade in this market: a ticker that does not is "
                "discarded before the trader ever sees it, so offering one only "
                "costs them the symbol.\n"
                "The tickers listed as held above are a hint about what they watch, "
                "not a limit on what they may name."
            )

        def _call(user_content: str) -> str:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                # No temperature: the GPT-5 and GPT-6 families reject anything but
                # the default and answer 400 ("temperature does not support 0 with
                # this model"). Determinism comes from the schema and the retry,
                # not from sampling.
                response_format={"type": "json_object"},
            )
            return response.choices[0].message.content or ""

        last_error: Exception | None = None
        for attempt in range(2):
            log.info(
                "extract: %s reading %d characters%s",
                self.model,
                len(transcript),
                " (retry)" if attempt else "",
            )
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
            log.debug("extract: raw model output: %s", content)
            try:
                parsed = json.loads(content)
                proposal = NoteProposal.model_validate(parsed)
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = exc
                log.warning("extract: answer did not parse: %s", exc)
                continue
            log.info(
                "extract: %d change(s), %d candidate(s), language=%r",
                len(proposal.changes),
                len(proposal.security_candidates),
                proposal.language,
            )
            return ExtractionResult(
                proposal=proposal.model_dump(mode="json", exclude_none=False),
                provider="openai",
                model=self.model,
            )

        log.error("extract: failed after retry: %s", last_error)
        raise RuntimeError(f"extraction failed after retry: {last_error}") from last_error
