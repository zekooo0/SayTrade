"""Billing: the provider sells, the app keeps ONE switch (`users.plan`).

Polar is the merchant of record. It takes the payment, the sales tax and
the refund, and it owns the PRICE. Nothing in this module renders a number: the
checkout page shows it, and a second copy here would disagree with it.

The webhook is the only writer of `users.plan`, beside the operator's comp
script. It writes the CURRENT state of a subscription on every event, so a
replay writes the same value and no event log is needed.

The status vocabulary is Polar's: `active`, `trialing`, `past_due` keep the
paid plan; `canceled`, `revoked`, `incomplete`, and every value this app has
not seen give FREE. A `canceled` subscription runs to `current_period_end`
with status `active` and `cancel_at_period_end` set, so the trader keeps Pro
for the period he paid for, and the plan flips when Polar actually ends it.
"""

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import PLAN_FREE, PLAN_PRO
from app.models import PolarSubscription, User


class InvalidWebhookError(Exception):
    """The payload is not a subscription event this app can act on."""


class WebhookSignatureError(Exception):
    """The call does not carry the provider's valid signature."""


# The events that carry a subscription's state. Polar also sends order and
# invoice events: their payloads are not subscriptions, and the subscription
# events already carry everything the plan needs. This app does not act on them.
WEBHOOK_EVENTS = (
    "subscription.created",
    "subscription.updated",
    "subscription.active",
    "subscription.canceled",
    "subscription.uncanceled",
    "subscription.revoked",
)

# Which plan a status grants.
#
# PRO: `trialing` (a trial is access), `active`, `past_due` (the provider
# retries a failed renewal; one failed charge must not lock a paying trader out
# of his journal).
# FREE: `canceled`, `revoked`, `incomplete`, and every value this app has not
# seen — a value nobody has decided on must never hand out the paid plan.
PRO_STATUSES = frozenset({"active", "trialing", "past_due"})


def plan_for_status(status: str) -> str:
    """The plan a subscription status grants. An unknown value gives FREE."""
    return PLAN_PRO if status in PRO_STATUSES else PLAN_FREE


# How far the webhook timestamp may sit in the past or the future. A replayed
# body carries its original signature, so the window is the replay guard.
TIMESTAMP_TOLERANCE_SEC = 300


def _candidate_keys(secret: str) -> list[bytes]:
    """The signing keys a Polar secret can be.

    Standard Webhooks secrets are base64 with a `whsec_` (or Polar's newer
    `polar_whs_`) prefix; older Polar endpoints used the literal secret string.
    Both are tried, in that order, and the signature decides.
    """
    stripped = secret.removeprefix("whsec_").removeprefix("polar_whs_")
    keys = []
    try:
        decoded = base64.b64decode(stripped, validate=True)
        if decoded:
            keys.append(decoded)
    except (ValueError, base64.binascii.Error):
        pass
    raw = secret.encode()
    if raw and raw not in keys:
        keys.append(raw)
    return keys


def verify_signature(
    raw_body: bytes,
    *,
    webhook_id: str,
    webhook_timestamp: str,
    webhook_signature: str,
    secret: str,
) -> None:
    """Standard Webhooks verification: HMAC-SHA256 over `id.timestamp.body`.

    The body must be the RAW request bytes: a parsed-and-dumped payload has
    different bytes and can never match. No secret means no trust, which is
    why an unconfigured app refuses every call.
    """
    if not secret:
        raise WebhookSignatureError("No webhook secret is configured.")

    if not webhook_id or not webhook_timestamp or not webhook_signature:
        raise WebhookSignatureError("Missing Standard Webhooks headers.")

    try:
        timestamp = int(webhook_timestamp)
    except ValueError as exc:
        raise WebhookSignatureError("The webhook timestamp is not a number.") from exc

    if abs(time.time() - timestamp) > TIMESTAMP_TOLERANCE_SEC:
        raise WebhookSignatureError("The webhook timestamp is outside tolerance.")

    signed_content = f"{webhook_id}.{webhook_timestamp}.".encode() + raw_body

    supplied = []
    for entry in webhook_signature.split():
        version, _, signature = entry.partition(",")
        if version == "v1" and signature:
            supplied.append(signature)

    if not supplied:
        raise WebhookSignatureError("No v1 signature in webhook-signature.")

    for key in _candidate_keys(secret):
        expected = base64.b64encode(
            hmac.new(key, signed_content, hashlib.sha256).digest()
        ).decode()
        for candidate in supplied:
            if hmac.compare_digest(expected, candidate):
                return

    raise WebhookSignatureError("Invalid webhook signature.")


@dataclass(frozen=True)
class SubscriptionEvent:
    """One subscription event, read out of the provider's payload.

    The account arrives as the checkout's `metadata.user_id` (this app's
    account id), or — when a subscription is created outside a checkout this
    app minted — as the Polar customer's `external_id`, which is the verified
    Firebase subject. `record_event` turns whichever link is present into the
    account, and refuses a payload with neither.
    """

    user_ref: str | None
    auth_sub: str | None
    polar_subscription_id: str
    status: str
    polar_customer_id: str | None
    polar_product_id: str | None
    billing_period: str | None
    current_period_start: datetime | None
    current_period_end: datetime | None
    cancel_at_period_end: bool

    @property
    def plan(self) -> str:
        return plan_for_status(self.status)


@dataclass(frozen=True)
class BillingPolicy:
    """What the webhook is allowed to accept.

    The signing secret, and the products this deployment sells. An event for a
    product nobody configured — a stray endpoint, another app's product — is
    ignored rather than written, the same way a rehearsal must never grant a
    real plan.
    """

    webhook_secret: str = ""
    product_ids: frozenset[str] = frozenset()

    def accepts(self, event: SubscriptionEvent) -> bool:
        if not self.product_ids:
            return False
        return event.polar_product_id in self.product_ids


def _time(value: object) -> datetime | None:
    """An ISO 8601 value from the provider, or None. `Z` is accepted."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _period(data: dict) -> str | None:
    """The billing period, from the checkout metadata or the product's interval."""
    metadata = data.get("metadata") or {}
    period = str(metadata.get("billing_period") or "").strip().lower()
    if period in ("monthly", "yearly"):
        return period
    interval = str(data.get("recurring_interval") or "").strip().lower()
    if interval == "month":
        return "monthly"
    if interval == "year":
        return "yearly"
    return None


async def _resolve_user(
    session: AsyncSession, event: SubscriptionEvent
) -> User:
    """The account a subscription belongs to.

    The checkout put the account id in the metadata; Polar's customer carries
    the Firebase subject as its external id. Either one resolves the account,
    and a payload with neither — or one naming an account this app does not
    know — is refused loudly instead of silently ignored.
    """
    if event.user_ref:
        try:
            user_id = UUID(event.user_ref)
        except ValueError as exc:
            raise InvalidWebhookError(
                f"metadata.user_id is not an account id: {event.user_ref!r}"
            ) from exc
        user = await session.get(User, user_id)
        if user is None:
            raise InvalidWebhookError(
                f"no account for {user_id} — the checkout carried an id this "
                "app does not know"
            )
        return user

    if event.auth_sub:
        user = await session.scalar(
            select(User).where(User.auth_sub == event.auth_sub)
        )
        if user is not None:
            return user
        raise InvalidWebhookError(
            f"no account for subject {event.auth_sub!r} — the subscription is "
            "not tied to an account this app knows"
        )

    raise InvalidWebhookError("the event carries no account link")


def parse_event(payload: dict) -> SubscriptionEvent | None:
    """Read a subscription event out of a webhook payload.

    Returns None for an event this app does not act on — an order or an
    invoice, or a type not in `WEBHOOK_EVENTS`. Raises when the event IS one
    of ours and cannot be used: a silent return would hide a real fault
    behind a 200.
    """
    if payload.get("type") not in WEBHOOK_EVENTS:
        return None

    data = payload.get("data") or {}
    subscription_id = data.get("id")
    status = data.get("status")
    if not subscription_id or not status:
        raise InvalidWebhookError("the subscription event is unusable")

    metadata = data.get("metadata") or {}
    customer = data.get("customer") or {}

    return SubscriptionEvent(
        user_ref=(
            str(metadata["user_id"])
            if metadata.get("user_id") is not None
            else None
        ),
        auth_sub=(
            str(customer["external_id"]).strip()
            if customer.get("external_id")
            else None
        ),
        polar_subscription_id=str(subscription_id),
        status=str(status),
        polar_customer_id=(
            str(data["customer_id"]) if data.get("customer_id") else None
        ),
        polar_product_id=(
            str(data["product_id"]) if data.get("product_id") else None
        ),
        billing_period=_period(data),
        current_period_start=_time(data.get("current_period_start")),
        current_period_end=_time(data.get("current_period_end")),
        cancel_at_period_end=bool(data.get("cancel_at_period_end")),
    )


async def record_event(
    session: AsyncSession, event: SubscriptionEvent
) -> User:
    """Write the subscription, and the plan it grants.

    The row is created or updated first, and the plan is written last: a clash
    on the insert can then be settled without losing anything, and the trader's
    plan is never changed on a row this function could not store.
    """
    user = await _resolve_user(session, event)

    row = await session.scalar(
        select(PolarSubscription).where(
            PolarSubscription.polar_subscription_id == event.polar_subscription_id
        )
    )
    if row is None:
        row = PolarSubscription(
            user_id=user.id,
            polar_subscription_id=event.polar_subscription_id,
            status=event.status,
        )
        _apply(row, event)
        session.add(row)
        try:
            await session.flush()
        except IntegrityError:
            # The provider delivered the same subscription twice at once. The
            # loser reloads the winner's row and writes the same state onto it.
            await session.rollback()
            user = await _resolve_user(session, event)
            row = await session.scalar(
                select(PolarSubscription).where(
                    PolarSubscription.polar_subscription_id
                    == event.polar_subscription_id
                )
            )
            if user is None or row is None:  # pragma: no cover - defensive
                raise InvalidWebhookError(
                    "the subscription row vanished while it was being written"
                ) from None
    _apply(row, event)

    user.plan = event.plan
    return user


def _apply(row: PolarSubscription, event: SubscriptionEvent) -> None:
    row.status = event.status
    row.polar_customer_id = event.polar_customer_id or row.polar_customer_id
    row.polar_product_id = event.polar_product_id or row.polar_product_id
    row.billing_period = event.billing_period or row.billing_period
    row.current_period_start = (
        event.current_period_start or row.current_period_start
    )
    row.current_period_end = event.current_period_end or row.current_period_end
    row.cancel_at_period_end = event.cancel_at_period_end


def event_type(payload: dict) -> str | None:
    """The payload's event type, for the route's log line."""
    value = payload.get("type")
    return str(value) if value else None
