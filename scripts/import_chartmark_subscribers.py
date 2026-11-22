"""One-time import: ChartMark's paid subscribers into the suite's one database.

Run AFTER the API deploy with the chartmark schema (alembic c7e5a91f3b82), so
a trader who bought Pro on ChartMark and never opened the journal still lands
as PRO: without this, he has no public.users row and no webhook event coming
until his next renewal, and would read FREE on day one.

    DATABASE_URL="<saytrade neon>" \
    CHARTMARK_DATABASE_URL="<old chartmark neon>" \
    uv run python scripts/import_chartmark_subscribers.py [--dry-run]

Idempotent: users upsert by auth_sub, subscriptions by polar_subscription_id.
A live subscription sets plan=PRO; a dead one never downgrades an account that
is PRO by some other path.
"""

import argparse
import asyncio
import os

import asyncpg

# The statuses that grant PRO — the same vocabulary the webhook applier uses.
LIVE_STATUSES = ("active", "trialing", "past_due")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="print what would happen, change nothing"
    )
    args = parser.parse_args()

    target_url = os.environ.get("DATABASE_URL", "").strip()
    source_url = os.environ.get("CHARTMARK_DATABASE_URL", "").strip()
    if not target_url or not source_url:
        raise SystemExit("Set DATABASE_URL (SayTrade Neon) and CHARTMARK_DATABASE_URL.")

    source = await asyncpg.connect(source_url)
    target = await asyncpg.connect(target_url)
    try:
        rows = await source.fetch(
            """
            SELECT u.firebase_uid,
                   u.email,
                   u.display_name,
                   s.provider_subscription_id,
                   s.provider_customer_id,
                   s.provider_product_id,
                   s.status,
                   s.billing_period,
                   s.current_period_start,
                   s.current_period_end,
                   s.cancel_at_period_end,
                   s.canceled_at
            FROM chartmark.subscriptions s
            JOIN chartmark.users u ON u.id = s.user_id
            WHERE s.provider_subscription_id IS NOT NULL
            ORDER BY s.created_at
            """
        )
        print(f"chartmark subscriptions found: {len(rows)}")

        imported = live = skipped = 0
        for row in rows:
            user_id = await target.fetchval(
                """
                INSERT INTO users (auth_sub, plan)
                VALUES ($1, 'FREE')
                ON CONFLICT (auth_sub) DO UPDATE
                  SET updated_at = NOW()
                RETURNING id
                """,
                row["firebase_uid"],
            )

            exists = await target.fetchval(
                "SELECT 1 FROM polar_subscriptions WHERE polar_subscription_id = $1",
                row["provider_subscription_id"],
            )
            if exists:
                skipped += 1
                continue

            plan = "PRO" if row["status"] in LIVE_STATUSES else "FREE"
            if not args.dry_run:
                async with target.transaction():
                    await target.execute(
                        """
                        INSERT INTO polar_subscriptions (
                            user_id, polar_subscription_id, polar_customer_id,
                            polar_product_id, billing_period, status,
                            current_period_start, current_period_end,
                            cancel_at_period_end
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                        ON CONFLICT (polar_subscription_id) DO NOTHING
                        """,
                        user_id,
                        row["provider_subscription_id"],
                        row["provider_customer_id"],
                        row["provider_product_id"],
                        row["billing_period"],
                        row["status"],
                        row["current_period_start"],
                        row["current_period_end"],
                        row["cancel_at_period_end"],
                    )
                    if plan == "PRO":
                        # Never downgrade: an account already PRO stays PRO.
                        await target.execute(
                            "UPDATE users SET plan = 'PRO', updated_at = NOW() "
                            "WHERE id = $1 AND plan = 'FREE'",
                            user_id,
                        )
            imported += 1
            if plan == "PRO":
                live += 1
            print(
                f"  {row['status']:>10}  {row['email'] or row['firebase_uid']}"
                f"  ->  plan={plan}"
            )

        verb = "would import" if args.dry_run else "imported"
        print(
            f"{verb} {imported} subscription(s), {live} live (PRO); "
            f"{skipped} already present"
        )
        if args.dry_run:
            print("dry run: nothing was written")
    finally:
        await source.close()
        await target.close()


if __name__ == "__main__":
    asyncio.run(main())
