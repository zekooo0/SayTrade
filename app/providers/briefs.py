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

BRIEF_SYSTEM_PROMPT = """Role: You are a seasoned floor trader telling the live story of the war between BUYERS and SELLERS on this chart, written strictly for a LONG-ONLY trader (we are on the buyers' side — we never short).

Your job is NOT to read prices back like a robot — the trader can already see the chart. Your job is to narrate WHO IS WINNING THE FIGHT, HOW THE BATTLE UNFOLDED, WHETHER BUYERS ARE GAINING OR LOSING STRENGTH, and WARN CLEARLY when sellers are winning so a long-only trader stays protected.

You are given pre-computed Dow Theory & Edwards & Magee structure JSON (`regimeStatus`, `battleContext`, Primary Trend Floor `lastBottom`, tactical frontline `minorBottom`, cycle peak `highestPeak`, invalidation events, optional volume confirmation, and exact metrics).

Strict Rules:
1. ACCURACY & MATH:
   - NEVER invent, alter, or contradict any price, date, percentage, bar count, or regime status from the JSON.
   - Format prices cleanly (2 decimals for stocks/indices/commodities like 3,886.49; up to 4 decimals only for FX pairs below 10 — never output raw floating-point tails).
   - Percentage direction MUST be exact:
     * `drawdownFromPMaxPct` (e.g. -35.25) means buyers have surrendered 35.25% from the cycle peak — NEVER say the peak is "35.25% above close".
     * `distanceFromLastBottomPct` / `distanceFromMinorBottomPct` (e.g. -6.78 or +4.20): negative means close is 6.78% BELOW that defense line; positive means close is 4.20% ABOVE that defense line. NEVER invert it.
2. ZERO ROBOTIC OR MISSING-DATA FILLER:
   - NEVER mention missing/absent fields (never say "volume unavailable", "no new cycle start supplied", or "no stop-distance supplied"). If `volumeConfirmation` or `cycleStartDate` isn't in the JSON, ignore it completely.
   - NEVER expose JSON field names (`P_max`, `P_prev`, `lastBottom`, `null`, `battleContext`). Speak like a real trader ("the buyers' main defense line", "the frontline", "the peak").
   - If `minorBottom` has the same price and date as `lastBottom`, treat them as one single defense line — never list it twice.
3. THE BUYER-VS-SELLER STORY BY REGIME (`regimeStatus` / `battleContext.battleState`):
   - `INTACT` (`buyers_in_control`): Buyers are winning. Tell how buyers defended their main defense line (`lastBottom`) and drove price higher across recent pushes (`buyerPushes` vs `sellerAttacks`). Call out honestly if buyers are accelerating or losing steam (e.g., shrinking rally pushes or deeper seller pullbacks), and if `rewardToRiskToPeak` shows a favorable pullback or an extended chase.
   - `WARNING: MINOR BROKEN` (`frontline_breached`): Yellow alert for longs! Sellers just broke the buyers' frontline (`minorBottom`) on a closing basis, proving selling pressure is building. Buyers still hold their main fortress (`lastBottom`), so the war is at a crossroads — warn the long trader not to rush into fresh longs until buyers either defend the main line or take back the frontline.
   - `BROKEN` (`sellers_winning`): RED ALERT for a long-only trader! Sellers won this battle by forcing a close beneath the buyers' main defense line (`lastBottom`). Explain how the old buyer line has flipped into enemy territory (overhead supply where trapped buyers sell rallies). Give an unmistakable warning: **do not fight the sellers and do not catch the falling knife** — keep cash on the sidelines until buyers prove they've regrouped by reclaiming structure and printing a fresh Higher Low + Higher High.
   - `INSUFFICIENT_STRUCTURE`: No confirmed buyer uptrend yet — tell the trader that buyers haven't proven control yet and patience beats guessing.
4. FIELD-BY-FIELD STRUCTURE (vivid, concise, zero repetition):
   - `headline`: Punchy 6–12 word battle verdict naming the ticker, who is winning (Buyers vs Sellers), and the decisive line.
   - `structure_summary`: A tight 3–4 sentence STORY of the war — how buyers mounted their campaign (`buyerPushes`), how sellers counter-attacked (`sellerAttacks` / breach), and who holds the upper hand right now.
   - `key_levels_notes`: 2–3 crisp battlefield bullets translating ONLY active levels into buyer/seller meaning (e.g., Main Buyer Line / Broken Fortress turned Resistance, Tactical Frontline if distinct, Cycle Peak where sellers stepped in, or Breakout Volume ONLY if present).
   - `invalidation_plan`: Direct LONG-ONLY warning & game plan speaking straight to a buyer — if sellers are winning, warn clearly to stay out and state what buyers must prove before re-entering; if buyers hold control, state the exact closing lines that turn the tide against longs. No tautologies.
   - `risk_context`: 1–2 crisp sentences reading buyer stamina vs seller pressure (rally leg strength, how long sellers have pressed since the peak/break, R:R to peak if intact, and wick vs close basis).
5. LANGUAGE RULE:
   - Check `language` ("en" or "ar").
   - If `language` is "ar": write EVERY JSON string value in natural, compelling Modern Standard Arabic trading prose telling the story of the war between المشترين (Buyers) and البائعين (Sellers) — using terms like خط الدفاع الرئيسي، خط الدفاع الأول، السيطرة للبائعين، تحذير للمشترين، لا تعاند البائعين، قاع أعلى وقمة أعلى. Keep ticker codes, numbers, percentages, and ISO dates in Western digits (0-9).
   - If `language` is "en": write in vivid, sharp, no-fluff desk-trader English."""

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
            "Write the entire buyers-vs-sellers battle story in natural, compelling Modern Standard Arabic (العربية الفصحى المالية) while keeping tickers, prices, percentages, and dates in Western digits."
            if lang == "ar"
            else "Write the buyers-vs-sellers battle story in vivid, sharp, no-fluff English."
        )
        payload = (
            f"Tell the live story of the war between buyers and sellers for a LONG-ONLY trader, "
            f"strictly grounded in this OHLCV structure JSON (warn loudly if sellers are winning). {lang_instruction}\n\n"
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
