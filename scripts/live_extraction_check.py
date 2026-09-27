"""Live extraction check: the trader's own sentences through the real model."""

import asyncio
import json
from datetime import datetime, timezone

from app.config import settings
from app.providers.extraction import OpenAIExtractor

RECORDED = datetime(2026, 9, 27, 10, 0, tzinfo=timezone.utc)

SENTENCES = [
    "i opened a position today in nivida, i bought 100 shares at 10$, my sl is 9$ my target is 12$",
    "today I saw reverse candle, will wait for the next day candle for confirmation",
    "yesterday I sold half of my position to secure some gains and i did follow the rule that say sell the half and pray you're wrong",
    "today I moved the sl to 9.5$ to lower the R:R",
    "today I bought more shares at 11$ and the sl for this portion is 10.5$",
    "yesterday I sold all the shares at 11.95$",
    "I sold half at 12 and moved the stop to 10.5",
    "بالنسبة للسهم عموك فأنا بقيت 30% من المركز عند 129.5 ولسة معي الباقي",
]


async def main() -> None:
    extractor = OpenAIExtractor(
        api_key=settings.openai_api_key or "",
        model=settings.openai_extraction_model,
    )
    for text in SENTENCES:
        print("=" * 78)
        print("SAID:", text)
        print("recorded_at:", RECORDED.isoformat())
        try:
            result = await extractor.extract(text, recorded_at=RECORDED)
            print(json.dumps(result.proposal, ensure_ascii=False, indent=2))
        except Exception as exc:  # noqa: BLE001 - this is a probe
            print("FAILED:", type(exc).__name__, exc)

    # No recording timestamp: a relative word must NOT become an invented date.
    print("=" * 78)
    print("SAID (no recording time):", SENTENCES[5])
    result = await extractor.extract(SENTENCES[5])
    print(json.dumps(result.proposal, ensure_ascii=False, indent=2))


asyncio.run(main())
