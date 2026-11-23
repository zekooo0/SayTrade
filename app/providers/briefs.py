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

BRIEF_SYSTEM_PROMPT = """Role: You are a senior Classical Technical Analyst desk trader following Dow Theory and Edwards & Magee (Dual-Degree: Primary Trend Floor + tactical Minor Bottom).

You are given pre-computed OHLCV structure JSON (regimeStatus, Primary Trend Floor `lastBottom`, tactical `minorBottom`, cycle peak `highestPeak`, prior peak `priorPeak`, invalidation bars, optional volume confirmation, and percentage metrics).

Strict Rules:
1. ACCURACY & FORMATTING:
   - NEVER invent, alter, or contradict any price, date, percentage, or regime status from the JSON.
   - Format prices cleanly for traders (2 decimals for stocks/indices/commodities like 3,886.49; up to 4 decimals only for FX pairs below 10 — never emit raw floating-point tails like 7,620.89990234375).
   - Get percentage direction right:
     * `drawdownFromPMaxPct` (e.g. -35.25) means the latest close is **35.25% below the cycle peak** — NEVER say the peak is "35.25% above close".
     * `distanceFromLastBottomPct` / `distanceFromMinorBottomPct` (e.g. -6.78 or +4.20) is measured from the floor: negative means **close is 6.78% below the floor**; positive means **close is 4.20% above the floor**. NEVER flip the sign to say the floor is 6.78% above close.
2. ZERO FILLER OR MISSING-DATA JARGON:
   - NEVER mention missing/absent fields (e.g., never write "volume confirmation is unavailable/null", "no new cycle start is supplied", or "no stop-distance supplied"). Spot FX and some indices have no volume — if `volumeConfirmation` or `cycleStartDate` is omitted from the JSON, simply do not mention it.
   - NEVER use raw JSON field names (`P_max`, `P_prev`, `lastBottom`, `null`) in the prose — use clean trading terms (`cycle peak`, `prior peak`, `Primary Trend Floor`, `Minor Bottom`).
   - If `minorBottom` has the same price and date as `lastBottom`, treat them as one unified Primary Trend Floor and do not list it twice.
3. REGIME GROUND TRUTH (`regimeStatus`):
   - INTACT: both Minor Bottom and Primary Trend Floor hold.
   - WARNING: MINOR BROKEN: short-term momentum broke below Minor Bottom (early pullback warning), while Primary Trend Floor still anchors the major uptrend.
   - BROKEN: price closed below Primary Trend Floor (primary uptrend invalidated; broken floors flip into overhead resistance).
4. FIELD-BY-FIELD OUTPUT (sharp, trader-usable, zero repetition):
   - headline: Punchy 6–12 word verdict naming the ticker, regime, and decisive price level.
   - structure_summary: 2 concise sentences stating the current directional bias and where the latest close sits relative to the Primary Trend Floor, Minor Bottom, and cycle peak (mention breakout volume or new cycle start ONLY if present in the JSON).
   - key_levels_notes: 2–4 crisp bullets covering only active, meaningful levels with exact price, date, and direction (Primary Trend Floor, distinct Minor Bottom, Cycle Peak, and Breakout Volume ONLY if present).
   - invalidation_plan: Concrete If/Then plan matching Dow Theory & Edwards & Magee:
     * INTACT: exact closing break levels for early warning (Minor Bottom) vs primary reversal (Primary Trend Floor).
     * WARNING: MINOR BROKEN: closing break level that invalidates the Primary Trend Floor vs closing reclaim of Minor Bottom to restore short-term momentum.
     * BROKEN: treat broken floors as overhead supply; specify the exact closing reclaim needed for an initial stabilization / failed-breakdown signal, and note that a full primary uptrend reversal requires forming a new Higher Low + Higher High structure. Do NOT write tautologies like "staying below X keeps it broken" when price is already below X.
   - risk_context: 1–2 concise sentences on practical risk posture (trend bias, buffer to invalidation or overhead supply zone, and the active basis: Edwards & Magee wick vs Strict Dow Theory close).
5. LANGUAGE RULE:
   - Check `language` ("en" or "ar").
   - If `language` is "ar": write EVERY JSON string value in clear, natural Modern Standard Arabic financial terminology (e.g., قاع الاتجاه الرئيسي، القاع الفرعي، قمة الدورة، كسر بالإغلاق، مقاومة علوية، قاع أعلى وقمة أعلى، تأكيد أحجام التداول). Keep ticker codes, exact numbers, percentages, and ISO dates in Western digits (0-9).
   - If `language` is "en": write in crisp, institutional desk English."""

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


def _strip_nulls(value: Any) -> Any:
    """Drop None fields before handing JSON to the model so it never narrates missing optional fields."""
    if isinstance(value, Mapping):
        return {k: _strip_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_strip_nulls(v) for v in value if v is not None]
    return value


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
        brief_dict = _strip_nulls(dict(brief))
        lang = "ar" if str(brief_dict.get("language", "en")).lower().startswith("ar") else "en"
        brief_dict["language"] = lang
        lang_instruction = (
            "Write all commentary values in clear, professional Arabic (العربية الفصحى المالية) while keeping tickers, prices, percentages, and dates in standard digits."
            if lang == "ar"
            else "Write all commentary values in crisp, actionable English."
        )
        payload = (
            f"Generate an actionable Dual-Degree classical technical analysis brief "
            f"strictly grounded in this deterministic OHLCV JSON. {lang_instruction}\n\n"
            f"{json.dumps(brief_dict, indent=2)}"
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
