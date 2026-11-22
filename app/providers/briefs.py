"""The Step 5 brief: an institutional classical read of a computed structure.

The brief is GROUNDED. Steps 1–4 already ran — in the trader's browser — and
their exact output arrives as JSON: pivots, P_prev, P_max, the Last Bottom,
the invalidation state. This call narrates those figures; it never invents,
alters, or recomputes one of them. The same boundary as the summary: the
model is handed the facts and nothing else.
"""

import asyncio
import json
from typing import Any, Mapping, Protocol

BRIEF_SYSTEM_PROMPT = """Role: You are an institutional Classical Technical Analyst strictly following Dow Theory and Edwards & Magee.

You are Step 5 (Narrative Commentary) of a deterministic OHLCV pipeline. Steps 1–4 have ALREADY computed the exact swing pivots, P_prev, P_max, Last Bottom, and trend invalidation state mathematically from raw OHLCV data.

Strict Grounding Rules:
1. NEVER invent, alter, round contradictorily, or override any price, date, percentage, or trend status provided in the JSON input.
2. Treat the provided trendStatus ("intact", "broken", or "insufficient_structure") and lastBottom price/date as ground truth.
3. Explain the market structure concisely in clear trader language:
   - headline: Crisp 6–12 word verdict stating the instrument, trend state, and Last Bottom floor.
   - structure_summary: 2–3 sentences synthesizing the swing progression from P_prev through the Secondary Reaction trough (Last Bottom) to P_max, and where the latest close sits relative to that structure.
   - key_levels_notes: 3 concise bullet points referencing exact computed prices, dates, and distances (e.g. buffer above/below Last Bottom, rally gain from Last Bottom to P_max, current pullback depth from P_max).
   - invalidation_plan: Clear classical rule (if intact: what closing breach below Last Bottom breaks the primary uptrend; if broken: reference the exact invalidation bar and what structural reclaim is required).
   - risk_context: 1–2 sentences noting the chosen basis (Edwards & Magee wick vs Strict Dow Theory close), reaction span (minBars), and swing filter threshold.
4. Keep prose sharp, objective, and free of filler or financial-advice boilerplate."""

# The strict JSON contract the model must answer with — the same one the
# Next.js route enforced.
BRIEF_JSON_SCHEMA = {
    "name": "deterministic_ohlcv_step5_commentary",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "headline",
            "structure_summary",
            "key_levels_notes",
            "invalidation_plan",
            "risk_context",
        ],
        "properties": {
            "headline": {"type": "string"},
            "structure_summary": {"type": "string"},
            "key_levels_notes": {"type": "array", "items": {"type": "string"}},
            "invalidation_plan": {"type": "string"},
            "risk_context": {"type": "string"},
        },
    },
}

MIN_KEY_LEVELS = 1
MAX_KEY_LEVELS = 5


class BriefWriterError(Exception):
    """The model answered with something this app cannot hand to the studio."""


class BriefWriter(Protocol):
    async def write(self, brief: Mapping[str, Any]) -> dict[str, Any]: ...


class OpenAIBriefWriter:
    """One chat completion over the deterministic Steps 1–4 output."""

    def __init__(self, api_key: str, model: str = "gpt-6-luna"):
        self.api_key = api_key
        self.model = model

    def _client(self):
        from openai import OpenAI

        if not self.api_key:
            raise BriefWriterError(
                "AI briefs are not configured on this deployment (OPENAI_API_KEY)."
            )
        return OpenAI(api_key=self.api_key)

    async def write(self, brief: Mapping[str, Any]) -> dict[str, Any]:
        client = self._client()
        payload = json.dumps(
            f"Generate Step 5 classical technical analysis commentary strictly "
            f"grounded in this deterministic Steps 1–4 OHLCV output:\n\n"
            f"{json.dumps(dict(brief), indent=2)}",
        )

        def _call() -> str:
            response = client.chat.completions.create(
                model=self.model,
                response_format={
                    "type": "json_schema",
                    "json_schema": BRIEF_JSON_SCHEMA,
                },
                messages=[
                    {"role": "system", "content": BRIEF_SYSTEM_PROMPT},
                    {"role": "user", "content": payload},
                ],
            )
            return response.choices[0].message.content or ""

        content = await asyncio.to_thread(_call)
        if not content:
            raise BriefWriterError("The AI returned an empty commentary.")

        try:
            parsed = json.loads(content)
        except ValueError as exc:
            raise BriefWriterError("The AI's commentary was not valid JSON.") from exc

        notes = parsed.get("key_levels_notes")
        if (
            not isinstance(parsed.get("headline"), str)
            or not isinstance(parsed.get("structure_summary"), str)
            or not isinstance(parsed.get("invalidation_plan"), str)
            or not isinstance(parsed.get("risk_context"), str)
            or not isinstance(notes, list)
            or not MIN_KEY_LEVELS <= len(notes) <= MAX_KEY_LEVELS
            or not all(isinstance(n, str) for n in notes)
        ):
            raise BriefWriterError(
                "The AI's commentary did not match the Step 5 contract."
            )

        return {
            "headline": parsed["headline"],
            "structure_summary": parsed["structure_summary"],
            "key_levels_notes": notes,
            "invalidation_plan": parsed["invalidation_plan"],
            "risk_context": parsed["risk_context"],
        }
