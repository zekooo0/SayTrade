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
  "prediction": {
    "level": "<the price he expects to be reached, or null>",
    "direction": "BELOW | ABOVE | null",
    "reason": "<why he closed, in his own words, or null>"
  },
  "resolution": {
    "outcome": "CORRECT | INCORRECT | null",
    "note": "<what he saw, in his own words, or null>"
  },
  "changes": [
    {
      "kind": "OPEN | ADD | TRIM | STOP | TARGET | CLOSE",
      "occurred_at": null,
      "shares": null,
      "amount": "<the money he said he put in, or null>",
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
2. Never do arithmetic. For "half", "a third", "30% of my position" use fraction (0.5, 0.3333, 0.3). The backend computes the shares from the real position. The ONE calculation you do is a currency conversion, and only as rule 14 describes it.
3. One note can hold several changes ("I sold half and moved the stop to 9.5"). Keep them in spoken order.
4. A note with no change is valid: use an empty changes list. Example: "I saw a reverse candle, I will wait for tomorrow."
5. security_candidates: the companies this note is about. The trader may say a ticker, or a company name in Arabic or in English. For each company give the exchange you believe it trades on, its ticker, the company name in English, and the words the trader used for it. These are PROPOSALS: the backend checks each ticker against that market's own list of what trades there and discards the ones that are not on it, so list only companies you actually recognise, and never pad the list. Leave it empty when the note names no company.
6. A stop that applies to one added part only ("the stop for this part is 10.5") is NOT a change and NOT a STOP. Leave stop null on the ADD. That part stop stays in the trader's own words.
7. occurred_at only when the trader states a time ("yesterday", "this morning"): ISO 8601, using the recording date for relative words. If no recording timestamp is given, leave occurred_at null. NEVER invent a date.
8. "I sold all", "I am out", "I closed it" with no amount means CLOSE.
9. Never infer a pattern, a reason, a result, or an emotion. The trader's words are the record.
10. Keep every number at the precision it was spoken with: do not round it and do not rescale it. The unit is the one exception, and rule 14 explains it.
11. language is the language the note is spoken in: "en" or "ar". It is not the language of your answer — you write no prose at all.
12. prediction: ONLY when the note CLOSES a trade. The trader usually says why ("I am out, I expect it to fail and reach my failure point"). Record what he expects to happen next: the price level he expects to be reached if he names one, which way price has to move to get there, and his reason in his own words. The failure point is usually the trade's own stop: leave level null when he means the stop, and set it only when he names a different price. A note that OPENS a position, ADDS to one, or sets its stop is NOT a close, no matter how the trader describes that stop — its changes carry the stop, and its prediction MUST be null. There is nothing to predict about a trade that has not begun.
13. resolution: ONLY when the note is about a trade that is already CLOSED. There is no position to change, so the only question is whether the prediction he made at the close came true: CORRECT if the price did what he expected, INCORRECT if it did not. Leave resolution null when the note says nothing about the outcome, and never invent one.
14. MONEY: every price, stop, target and level is recorded in the market's own currency (the currency given above). A trader may state an amount in that currency's MINOR unit instead — a trader in Egypt says "قرش" or "إرش" where the currency is the pound, a trader in the United States says cents where it is the dollar. One hundred minor units make one major unit, so convert it and give the amount in the market's currency. Convert ONLY a number that actually carries a minor unit word; a bare number is already in the market's currency and must be left alone. When one amount names both units together, they are a single amount and belong added together. This is the only calculation you ever do.
15. SIZING BY MONEY: instead of a share count, a trader may give the money he put in — "I opened a position with 3000 egp at a price of 10". Put that money in amount and the price in price, and leave shares NULL: the backend divides the money by the price and rounds down to a whole share. Do NOT divide it yourself and do NOT guess a share count from the money — an amount you divide is a share count nobody can check. Use amount only when he names money for size; a share count he states himself goes in shares, and a change that has neither is left with both null.

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


@dataclass(frozen=True)
class TradeContext:
    """The trade a note was recorded against, and what is already known of it.

    A note on a CLOSED trade cannot be about a position — the applier refuses
    that change — so the only thing it can be is a resolution of the prediction
    made at the close. Telling the model the trade is closed, and what that
    prediction was, is what makes that reading possible.
    """

    id: str
    symbol: str
    closed: bool = False
    # "price 95, below" while the prediction is still pending.
    pending_prediction: str | None = None


class Extractor(Protocol):
    async def extract(
        self,
        transcript: str,
        recorded_at: datetime | None = None,
        active_trade: TradeContext | None = None,
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
        active_trade: TradeContext | None = None,
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
            state = "CLOSED" if active_trade.closed else "open"
            context += (
                f"\n\nThe trader is recording this note from their {state} "
                f"{active_trade.symbol} trade (id {active_trade.id}). The note is "
                "about that trade."
            )
            if active_trade.closed:
                context += (
                    " That trade is CLOSED, so this note cannot change a position:"
                    " all it can report is whether the prediction made when it was"
                    " closed came true."
                )
            if active_trade.pending_prediction:
                context += (
                    " A prediction on it is still pending:"
                    f" {active_trade.pending_prediction}."
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
