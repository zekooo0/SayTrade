"""Live pipeline probe: real extractor, real database, real applier.

Uses a throwaway probe user and deletes every row it creates, so the trader's
own data is never touched. Run from the backend root:

    PYTHONPATH=. ./.venv/bin/python scripts/live_pipeline_check.py
"""

import asyncio
import json

from sqlalchemy import delete, select, text

from app.config import settings
from app.db import SessionLocal, engine
from app.models import Change, Note, Trade, User
from app.providers.extraction import OpenAIExtractor
from app.services import journal as journal_service
from app.services import notes as note_service
from app.services import trades as trade_service

PROBE_SUB = "user_live_probe_zz"

OPEN_NOTE = "i opened a position today in nivida, i bought 100 shares at 10$, my sl is 9$ my target is 12$"
FOLLOW_UP = "I sold half at 12 and moved the stop to 10.5"


async def run_note(session, user, extractor, body: str, trade_id=None):
    view = await journal_service.receive_text_note(session, user, body, trade_id=trade_id)
    await journal_service.process_note(
        SessionLocal, view.id, None, None, extractor  # type: ignore[arg-type]
    )
    note = await journal_service.get_note(session, user, view.id)
    print(f"\nn SAID   : {body}")
    print(f"  STATUS : {note.status}  (failed_stage={note.failed_stage})")
    if note.error_message:
        print(f"  ERROR  : {note.error_message[:300]}")
    print(f"  SUMMARY: {note.summary}")
    print(f"  PROPOSAL: {json.dumps(note.proposal, ensure_ascii=False)}")
    if note.status != "EXTRACTED":
        return None
    result = await note_service.apply_note(session, user, note)
    await session.commit()
    print(f"  APPLIED: {result.changes_applied} change(s), trade {result.trade_id}")
    return result


async def main() -> None:
    extractor = OpenAIExtractor(
        api_key=settings.openai_api_key or "",
        model=settings.openai_extraction_model,
    )

    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.clerk_sub == PROBE_SUB))
        if user is None:
            user = User(clerk_sub=PROBE_SUB)
            session.add(user)
            await session.commit()
            await session.refresh(user)

        first = await run_note(session, user, extractor, OPEN_NOTE)
        if first is None or first.trade_id is None:
            print("open step failed; nothing else to check")
            return

        detail = await trade_service.get_trade(session, user, first.trade_id)
        print(
            f"  TRADE  : {detail.symbol} shares={detail.shares} avg={detail.average_price} "
            f"stop={detail.current_stop} target={detail.current_target} plannedR={detail.planned_r}"
        )

        await run_note(session, user, extractor, FOLLOW_UP, trade_id=first.trade_id)
        detail = await trade_service.get_trade(session, user, first.trade_id)
        print(
            f"  TRADE  : {detail.symbol} shares={detail.shares} avg={detail.average_price} "
            f"stop={detail.current_stop} realized={detail.realized_pnl} notes={detail.note_count}"
        )


async def cleanup() -> None:
    async with engine.begin() as conn:
        user_id = await conn.scalar(
            text("SELECT id FROM users WHERE clerk_sub = :sub"), {"sub": PROBE_SUB}
        )
        if user_id is None:
            print("\ncleanup: no probe user left")
            return
        await conn.execute(
            text("DELETE FROM changes WHERE note_id IN (SELECT id FROM notes WHERE user_id = :u)"),
            {"u": user_id},
        )
        await conn.execute(text("DELETE FROM notes WHERE user_id = :u"), {"u": user_id})
        await conn.execute(text("DELETE FROM trades WHERE user_id = :u"), {"u": user_id})
        await conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
        print("\ncleanup: probe rows deleted")


asyncio.run(main())
asyncio.run(cleanup())
