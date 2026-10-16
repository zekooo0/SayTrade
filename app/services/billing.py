"""Billing: the provider sells, the app keeps ONE switch (`users.plan`).

LemonSqueezy is the merchant of record. It takes the payment, the sales tax and
the refund, and it owns the PRICE. Nothing in this module renders a number: the
checkout page shows it, and a second copy here would disagree with it.

The webhook is the only writer of `users.plan`, beside the operator's comp
script. It writes the CURRENT state of a subscription on every event, so a
replay writes the same value and no event log is needed.

The status vocabulary is the provider's (its subscription object): on_trial,
active, paused, past_due, unpaid, cancelled, expired. `cancelled` still works
until `ends_at` — the trader paid for the period — so it keeps Pro.
"""

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import PLAN_FREE, PLAN_PRO
from app.models import Subscription, User


class InvalidWebhookError(Exception):
    """The payload is not a subscription event this app can act on."""


# The events that carry a subscription's state. The provider also sends
# subscription_payment_* events, whose payload is an invoice and not a
# subscription: they add nothing the plan needs, because subscription_updated
# follows every one of them. This app does not subscribe to them.
WEBHOOK_EVENTS = (
    "subscription_created",
    "subscription_updated",
    "subscription_cancelled",
    "subscription_resumed",
    "subscription_expired",
    "subscription_paused",
    "subscription_unpaused",
)

# Which plan a status grants.
#
# PRO: `on_trial` (a trial is access), `active`, `cancelled` (the trader paid for
# the period, and it runs to `ends_at`), `past_due` (the provider retries a
# failed renewal for two weeks; one failed charge must not lock a paying trader
# out of his journal).
# FREE: `paused`, `unpaid`, `expired`, and every value this app has not seen.
PRO_STATUSES = frozenset({"on_trial", "active", "cancelled", "past_due"})


def plan_for_status(status: str) -> str:
    """The plan a subscription status grants. An unknown value gives FREE.

    The same rule as `Entitlements.plan_for`: a value nobody has decided on must
    never hand out the paid plan.
    """
    return PLAN_PRO if status in PRO_STATUSES else PLAN_FREE


@dataclass(frozen=True)
class SubscriptionEvent:
    """One subscription event, read out of the provider's payload."""

    user_id: UUID
    ls_subscription_id: str
    status: str
    variant_id: str | None
    ls_customer_id: str | None
    ls_order_id: str | None
    renews_at: datetime | None
    ends_at: datetime | None
    trial_ends_at: datetime | None
    test_mode: bool

    @property
    def plan(self) -> str:
        return plan_for_status(self.status)


@dataclass(frozen=True)
class BillingPolicy:
    """What the webhook is allowed to accept."""

    webhook_secret: str = ""
    # Accept a test-mode purchase. True while rehearsing, false in production —
    # a rehearsal must never grant a real plan. The event says which it is.
    test_mode: bool = False

    def accepts(self, event: SubscriptionEvent) -> bool:
        return self.test_mode or not event.test_mode


def verify_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """Does this request carry the provider's signature?

    HMAC-SHA256 over the RAW request body, hex, in the `X-Signature` header. The
    body must not be re-encoded first: a parsed-and-dumped payload has different
    bytes and can never match. No secret means no trust, which is why an
    unconfigured app refuses every call.
    """
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _time(value: object) -> datetime | None:
    """An ISO 8601 value from the provider, or None. `Z` is accepted."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _int_or_none(value: object) -> str | None:
    return None if value is None else str(value)


def parse_event(payload: dict) -> SubscriptionEvent | None:
    """Read a subscription event out of a webhook payload.

    Returns None for an event this app does not act on — an order or an invoice,
    or a type not in `WEBHOOK_EVENTS`. Raises when the event IS one of ours and
    cannot be used: a silent return would hide a real fault behind a 200.
    """
    data = payload.get("data") or {}
    meta = payload.get("meta") or {}

    if data.get("type") != "subscriptions":
        return None
    if (meta.get("event_name") or "") not in WEBHOOK_EVENTS:
        return None

    raw_user = (meta.get("custom_data") or {}).get("user_id")
    if not raw_user:
        raise InvalidWebhookError("the event carries no user_id")
    try:
        user_id = UUID(str(raw_user))
    except ValueError as exc:
        raise InvalidWebhookError(
            f"custom_data.user_id is not an account id: {raw_user!r}"
        ) from exc

    attributes = data.get("attributes") or {}
    status = attributes.get("status")
    if not status:
        raise InvalidWebhookError("the subscription event carries no status")

    return SubscriptionEvent(
        user_id=user_id,
        ls_subscription_id=str(data.get("id") or ""),
        status=str(status),
        variant_id=_int_or_none(attributes.get("variant_id")),
        ls_customer_id=_int_or_none(attributes.get("customer_id")),
        ls_order_id=_int_or_none(attributes.get("order_id")),
        renews_at=_time(attributes.get("renews_at")),
        ends_at=_time(attributes.get("ends_at")),
        trial_ends_at=_time(attributes.get("trial_ends_at")),
        test_mode=bool(attributes.get("test_mode")),
    )


def _apply(row: Subscription, event: SubscriptionEvent) -> None:
    row.status = event.status
    row.variant_id = event.variant_id or row.variant_id or ""
    row.ls_customer_id = event.ls_customer_id or row.ls_customer_id
    row.ls_order_id = event.ls_order_id or row.ls_order_id
    row.renews_at = event.renews_at
    row.ends_at = event.ends_at
    row.trial_ends_at = event.trial_ends_at
    row.test_mode = event.test_mode


async def record_event(session: AsyncSession, event: SubscriptionEvent) -> User:
    """Write the subscription, and the plan it grants.

    The row is created or updated first, and the plan is written last: a clash
    on the insert can then be settled without losing anything, and the trader's
    plan is never changed on a row this function could not store.
    """
    user = await session.get(User, event.user_id)
    if user is None:
        raise InvalidWebhookError(
            f"no account for {event.user_id} — the checkout carried an id this app "
            "does not know"
        )

    row = await session.scalar(
        select(Subscription).where(
            Subscription.ls_subscription_id == event.ls_subscription_id
        )
    )
    if row is None:
        row = Subscription(
            user_id=event.user_id,
            ls_subscription_id=event.ls_subscription_id,
            status=event.status,
            variant_id=event.variant_id or "",
        )
        _apply(row, event)
        session.add(row)
        try:
            await session.flush()
        except IntegrityError:
            # The provider delivered the same subscription twice at once. The
            # loser reloads the winner's row and writes the same state onto it.
            await session.rollback()
            user = await session.get(User, event.user_id)
            row = await session.scalar(
                select(Subscription).where(
                    Subscription.ls_subscription_id == event.ls_subscription_id
                )
            )
            if user is None or row is None:  # pragma: no cover - defensive
                raise InvalidWebhookError(
                    "the subscription row vanished while it was being written"
                ) from None
    _apply(row, event)

    user.plan = event.plan
    return user
