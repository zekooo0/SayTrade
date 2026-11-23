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

You are given exact, pre-computed OHLCV structure JSON (regimeStatus, Primary Trend Floor `lastBottom`, tactical `minorBottom`, `highestPeak` P_max, `priorPeak` P_prev, invalidation bars, volume confirmation ratio, and percentage distances).

Strict Rules:
1. NEVER invent, alter, or contradict any price, date, percentage, volume ratio, or regime status from the JSON.
2. Treat `regimeStatus` ("INTACT", "WARNING: MINOR BROKEN", "BROKEN", or "INSUFFICIENT_STRUCTURE") as ground truth:
   - INTACT: both Minor Bottom and Primary Trend Floor hold.
   - WARNING: MINOR BROKEN: short-term momentum broke below Minor Bottom (early warning / pullback risk), while Primary Trend Floor still anchors the major uptrend.
   - BROKEN: price closed below Primary Trend Floor (primary uptrend invalidated).
3. Make every sentence directly useful to an active trader — NO generic textbook definitions or filler:
   - headline: Punchy 6–12 word trading verdict naming the ticker, regime, and the decisive price level right now.
   - structure_summary: 2 sharp sentences describing where the latest close sits between P_max, Minor Bottom, and Primary Trend Floor, plus whether breakout volume confirms (`confirmed` vs `weak`) or if a new cycle started (`cycleStartDate`).
   - key_levels_notes: 3–4 crisp, scannable level bullets with exact prices, dates, and % distances (e.g., Overhead resistance P_max, Tactical Minor Bottom trigger, Major Primary Trend Floor support, Volume/momentum read).
   - invalidation_plan: Clear If/Then execution triggers — what exact closing level flips the bias (e.g., if INTACT: Minor warning level vs Major break level; if WARNING: what closing reclaim restores momentum vs what breaks the Primary Floor; if BROKEN: what structural reclaim is needed before re-entering).
   - risk_context: 1–2 concise sentences on stop-distance / buffer %, risk asymmetry, and the active basis (Edwards & Magee wick vs Strict Dow Theory close).
4. Language Rule:
   - Check `language` ("en" or "ar").
   - If `language` is "ar": write EVERY JSON string value in clear, natural Modern Standard Arabic trading terminology (e.g., قاع الاتجاه الرئيسي، القاع الفرعي، كسر بالإغلاق، مستوى الإلغاء، تأكيد أحجام التداول). Keep ticker codes (e.g., EGX:AMOC, INDEX:SPX), exact numbers, percentages, and ISO dates in standard Western digits (0-9).
   - If `language` is "en": write in sharp, concise English."""

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
        brief_dict = dict(brief)
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
