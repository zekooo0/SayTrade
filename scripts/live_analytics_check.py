"""Live scoreboard probe: the real applier and the real database, no AI spend.

Builds a known set of trades for a throwaway probe user, then prints the
scoreboard and checks it against arithmetic done by hand here. Every row it
creates is deleted afterwards, so the trader's own data is never touched.

    PYTHONPATH=. ./.venv/bin/python scripts/live_analytics_check.py
"""

import asyncio
import json
from decimal import Decimal

from sqlalchemy import select, text

from app.db import SessionLocal, engine
from app.models import User
from app.providers.extraction import ExtractionResult
from app.services import analytics as analytics_service
from app.services import journal as journal_service
from app.services import notes as note_service

PROBE_SUB = "user_live_analytics_zz"
SHARES = 100
ENTRY = 100
STOP = 95  # one R is 5.00 a share, so 100 shares is 500.00 of risk

# (symbol, exit price, expected pnl, expected R)
CLOSES = [
    ("AAA", "110", Decimal("1000"), Decimal("2")),
    ("BBB", "95", Decimal("-500"), Decimal("-1")),
    ("CCC", "98", Decimal("-200"), Decimal("-0.4")),
    ("DDD", "90", Decimal("-1000"), Decimal("-2")),  # ran through the stop
    ("EEE", "125", Decimal("2500"), Decimal("5")),
    ("FFF", "100", Decimal("0"), Decimal("0")),
    ("GGG", "112", Decimal("1200"), Decimal("2.4")),
    ("HHH", "93", Decimal("-700"), Decimal("-1.4")),  # ran through the stop
]


class ScriptedExtractor:
    """Stands in for the LLM so the arithmetic is exact and costs nothing."""

    def __init__(self) -> None:
        self.proposal: dict = {}

    async def extract(self, transcript, recorded_at=None, active_trade=None):
        return ExtractionResult(
            proposal=self.proposal,
            summary=self.proposal.get("summary"),
            provider="script",
            model="script-1",
        )


def open_proposal(symbol: str, occurred_at: str | None = None) -> dict:
    change: dict = {
        "kind": "OPEN",
        "shares": str(SHARES),
        "price": str(ENTRY),
        "stop": str(STOP),
        "target": "120",
    }
    if occurred_at:
        change["occurred_at"] = occurred_at
    return {"summary": f"Opened {symbol}.", "symbol": symbol, "changes": [change]}


def close_proposal(symbol: str, price: str, occurred_at: str | None = None) -> dict:
    change: dict = {"kind": "CLOSE", "price": price}
    if occurred_at:
        change["occurred_at"] = occurred_at
    return {"summary": f"Closed {symbol}.", "symbol": symbol, "changes": [change]}


async def record(session, user, extractor, body: str, proposal: dict, *, approve=True):
    """One note, end to end, with a fresh session per step.

    The read and the apply run in their OWN session, the way an HTTP request
    does. A long-lived session would keep the stale copy of the note it loaded
    before the writer session ran: the app's sessionmaker uses
    expire_on_commit=False, so an already-loaded row is never refreshed.
    """
    extractor.proposal = proposal
    view = await journal_service.receive_text_note(session, user, body)
    await journal_service.process_note(
        SessionLocal, view.id, None, None, extractor  # type: ignore[arg-type]
    )
    async with SessionLocal() as worker:
        worker_user = await worker.get(User, user.id)
        assert worker_user is not None
        note = await journal_service.get_note(worker, worker_user, view.id)
        if note.status != "EXTRACTED":
            raise RuntimeError(
                f"note {view.id} did not extract: {note.status} {note.error_message}"
            )
        if approve:
            await note_service.apply_note(worker, worker_user, note)
            await worker.commit()
    return note


async def main() -> list[str]:
    extractor = ScriptedExtractor()

    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.clerk_sub == PROBE_SUB))
        if user is None:
            user = User(clerk_sub=PROBE_SUB)
            session.add(user)
            await session.commit()
            await session.refresh(user)

        # Two months of history, so the monthly chart has more than one bar.
        first_month = "2026-08-05T10:00:00Z"
        second_month = "2026-09-10T10:00:00Z"

        for index, (symbol, exit_price, _pnl, _r) in enumerate(CLOSES):
            when = first_month if index < 4 else second_month
            await record(
                session,
                user,
                extractor,
                f"I opened {symbol}",
                open_proposal(symbol, when),
            )
            await record(
                session,
                user,
                extractor,
                f"I closed {symbol}",
                close_proposal(symbol, exit_price, when),
            )

        # One trade left open, and one note that changes nothing.
        await record(session, user, extractor, "I opened III", open_proposal("III"))
        await record(
            session,
            user,
            extractor,
            "The market felt heavy today, no trades",
            {"summary": "No trades today.", "symbol": None, "changes": []},
        )

        view = await analytics_service.overview(session, user)

    payload = json.loads(view.model_dump_json())
    print(json.dumps(payload, indent=2))

    # ---------------------------------------------------------------------
    # The arithmetic, done by hand above, asserted against what the app says.
    # ---------------------------------------------------------------------
    checks = {
        "closed_trades": (payload["closed_trades"], 8),
        "open_trades": (payload["open_trades"], 1),
        "wins": (payload["wins"], 3),
        "losses": (payload["losses"], 4),
        "breakeven": (payload["breakeven"], 1),
        "win_rate": (payload["win_rate"], 0.375),
        "total_pnl": (payload["total_pnl"], "2300"),
        "average_pnl": (payload["average_pnl"], "287.5"),
        "gross_profit": (payload["gross_profit"], "4700"),
        "gross_loss": (payload["gross_loss"], "-2400"),
        "profit_factor": (payload["profit_factor"], "1.9583"),
        "best_pnl": (payload["best_pnl"], "2500"),
        "worst_pnl": (payload["worst_pnl"], "-1000"),
        "rated_trades": (payload["rated_trades"], 8),
        "average_r": (payload["average_r"], "0.575"),
        "expectancy_r": (payload["expectancy_r"], "0.575"),
        "best_r": (payload["best_r"], "5"),
        "worst_r": (payload["worst_r"], "-2"),
        "average_win_r": (payload["average_win_r"], "3.1333"),
        "average_loss_r": (payload["average_loss_r"], "-1.2"),
        "win_rate_r": (payload["win_rate_r"], 0.375),
        "beyond_stop": (payload["beyond_stop"], 2),
        "max_drawdown": (payload["max_drawdown"], "1700"),
        "observation_notes": (payload["observation_notes"], 1),
        "notes_total": (payload["notes_total"], 18),
    }
    failures = [name for name, (got, want) in checks.items() if got != want]
    print("\n--- checks ---")
    for name, (got, want) in checks.items():
        print(f"{'ok  ' if got == want else 'FAIL'} {name}: got {got!r}, want {want!r}")

    print(f"\ncurve  : {[p['cumulative_pnl'] for p in payload['equity_curve']]}")
    print(f"months : {payload['monthly']}")
    print(f"buckets: {[(b['label'], b['count']) for b in payload['r_distribution']]}")
    print(f"weeks  : {[(w['week_start'], w['notes']) for w in payload['notes_per_week']][-3:]}")
    print(f"breach : {payload['beyond_stop_trades']}")

    return failures


async def cleanup() -> None:
    async with engine.begin() as conn:
        user_id = await conn.scalar(
            text("SELECT id FROM users WHERE clerk_sub = :sub"), {"sub": PROBE_SUB}
        )
        if user_id is None:
            print("cleanup: no probe user left")
            return
        await conn.execute(
            text("DELETE FROM changes WHERE note_id IN (SELECT id FROM notes WHERE user_id = :u)"),
            {"u": user_id},
        )
        await conn.execute(text("DELETE FROM notes WHERE user_id = :u"), {"u": user_id})
        await conn.execute(text("DELETE FROM trades WHERE user_id = :u"), {"u": user_id})
        await conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
        print("cleanup: probe rows deleted")


async def run() -> None:
    # Start clean: a previous run may have died before its own cleanup, and a
    # leftover row would silently change every number below.
    await cleanup()
    failures: list[str] | None = None
    try:
        failures = await main()
    finally:
        await cleanup()
    if failures:
        raise SystemExit(f"\nFAILED: {failures}")
    print("\nall checks passed")


asyncio.run(run())
