"""The plan switch: give an account PRO, or take it back.

A plan is bought now, and the payment provider's webhook is what writes it when a
trader pays. This script is the operator's tool beside that: a comp, a fix, a
manual downgrade. Nothing else in the app writes `users.plan`.

    # what accounts exist, and who is on what
    PYTHONPATH=. ./.venv/bin/python scripts/set_plan.py --list

    # grant Pro to one account (the handle is Clerk's user id, "user_...")
    PYTHONPATH=. ./.venv/bin/python scripts/set_plan.py --sub user_2abcXYZ --plan PRO

    # take it back
    PYTHONPATH=. ./.venv/bin/python scripts/set_plan.py --sub user_2abcXYZ --plan FREE

It reads DATABASE_URL, so the same command runs against the local database or
against production:

    DATABASE_URL="$(grep -h '^DATABASE_URL' .env.neon | cut -d= -f2-)" \
      PYTHONPATH=. ./.venv/bin/python scripts/set_plan.py --sub user_2abcXYZ --plan PRO

Prints the plan before and after, with the active trades held, so you can see the
change landed. Idempotent: setting the plan an account already has changes
nothing. The account must have signed in once — rows are created on first
verified request.

Find a Clerk user id in the Clerk dashboard: Users, pick the person, copy the ID.
"""

import argparse
import asyncio
import sys
from uuid import UUID

from sqlalchemy import func, select

from app.billing.entitlements import PLAN_FREE, PLAN_PRO
from app.db import SessionLocal, engine
from app.models import Trade, User

PLANS = (PLAN_FREE, PLAN_PRO)


async def list_accounts() -> None:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    User.auth_sub,
                    User.plan,
                    User.created_at,
                    select(func.count())
                    .select_from(Trade)
                    .where(Trade.user_id == User.id, Trade.closed_at.is_(None))
                    .scalar_subquery()
                    .label("active_trades"),
                ).order_by(User.created_at.desc())
            )
        ).all()

    if not rows:
        print("No accounts yet. A row is created on a user's first verified request.")
        return

    print(f"{len(rows)} account(s):")
    for auth_sub, plan, created_at, active in rows:
        print(f"  {plan:<5} {active:>3} active  {auth_sub}  (since {created_at:%Y-%m-%d})")


async def find_user(session, sub: str | None, user_id: str | None) -> User | None:
    if sub:
        return await session.scalar(select(User).where(User.auth_sub == sub))
    try:
        uid = UUID(user_id)
    except (TypeError, ValueError):
        print(f"error: --id must be a UUID, got {user_id!r}")
        return None
    return await session.get(User, uid)


async def set_plan(sub: str | None, user_id: str | None, plan: str) -> int:
    async with SessionLocal() as session:
        user = await find_user(session, sub, user_id)
        if user is None:
            who = sub or user_id
            print(
                f"error: no account found for {who!r}.\n"
                "       Run --list to see the accounts. The handle is Clerk's user id\n"
                "       (Clerk dashboard: Users, pick the person, copy the ID)."
            )
            return 1

        active = (
            await session.scalar(
                select(func.count())
                .select_from(Trade)
                .where(Trade.user_id == user.id, Trade.closed_at.is_(None))
            )
            or 0
        )
        before = user.plan
        if before == plan:
            print(f"{user.auth_sub}: already {plan} ({active} active trades). Nothing to do.")
            return 0

        user.plan = plan
        await session.commit()
        print(f"{user.auth_sub}: {before} -> {plan} ({active} active trades).")
        print("The change is live on the user's next request; no redeploy needed.")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Give an account PRO, or take it back. The only writer of users.plan."
    )
    parser.add_argument("--sub", help="Clerk user id, e.g. user_2abcXYZ")
    parser.add_argument("--id", dest="user_id", help="internal user UUID, if you have it")
    parser.add_argument("--plan", choices=PLANS, help=f"target plan: {' or '.join(PLANS)}")
    parser.add_argument("--list", action="store_true", help="list accounts and their plans")
    args = parser.parse_args()

    try:
        if args.list:
            asyncio.run(list_accounts())
            return 0
        if not (args.sub or args.user_id) or not args.plan:
            parser.error("give --list, or --sub/--id together with --plan")
        return asyncio.run(set_plan(args.sub, args.user_id, args.plan))
    finally:
        # The engine holds a connection; dispose it so the process exits cleanly.
        asyncio.run(engine.dispose())


if __name__ == "__main__":
    sys.exit(main())
