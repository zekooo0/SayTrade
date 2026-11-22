"""Billing over HTTP: where a trader buys, and where the provider reports.

POST /billing/checkout  the trader's own checkout link (a session is required)
POST /billing/webhook   the provider's report (no session: the SIGNATURE is the gate)
GET  /billing/portal    the provider's own page for managing this subscription
"""

import json

from typing import ClassVar, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.db import get_session
from app.domain.errors import BillingUnavailableError
from app.log import get_logger
from app.models import PolarSubscription, User
from app.providers.polar import PolarError
from app.schemas.api import BillingUrlView
from app.services import billing as billing_service

log = get_logger("saytrade.billing")

router = APIRouter(prefix="/billing", tags=["billing"])


class CheckoutRequest(BaseModel):
    """Which price to buy, and the address the receipt should reach.

    The product carries the price, so this picks one. The email is a courtesy
    and not an identity: the account link is the subject the API adds from the
    verified session, so a wrong address misroutes a receipt and nothing else.
    """

    period: Literal["monthly", "yearly"] = "monthly"
    email: str | None = None

    ALLOWED_PERIODS: ClassVar[tuple[str, ...]] = ("monthly", "yearly")


def _client(request: Request):
    """The provider client, or a clear 503 when payments are not configured."""
    client = getattr(request.app.state, "polar", None)
    if client is None:
        raise BillingUnavailableError(
            "Payments are not open yet on this deployment."
        )
    return client


@router.post("/checkout", response_model=BillingUrlView)
async def create_checkout(
    request: Request,
    payload: CheckoutRequest,
    user: User = Depends(current_user),
) -> BillingUrlView:
    """Make a checkout for THIS trader and return the link to open it.

    The account travels with the checkout — as the metadata Polar echoes into
    the webhook, and as the customer's external id — so the payment that
    follows is tied to this account and to no other.
    """
    if payload.period not in CheckoutRequest.ALLOWED_PERIODS:
        raise HTTPException(status_code=400, detail="Unknown billing period.")

    client = _client(request)
    settings = request.app.state.settings
    # The trader's own address, so the receipt reaches him and the provider
    # hides the field. Trimmed, and absent when the client sends nothing.
    email = (payload.email or "").strip() or None
    try:
        url = await client.create_checkout(
            user_id=str(user.id),
            auth_sub=user.auth_sub,
            period=payload.period,
            email=email,
            success_url=settings.polar_success_url,
        )
    except PolarError as exc:
        log.warning("checkout for %s failed: %s", user.id, exc)
        raise BillingUnavailableError(
            "The payment provider could not be reached. Try again in a moment."
        ) from exc
    return BillingUrlView(url=url)


@router.post("/webhook")
async def webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Take the provider's report and write the plan it describes.

    No session is required and none is read: the trader is not here. The
    Standard Webhooks signature over the raw body is what proves the call came
    from the provider.
    """
    raw_body = await request.body()
    policy = request.app.state.billing

    try:
        billing_service.verify_signature(
            raw_body,
            webhook_id=request.headers.get("webhook-id", ""),
            webhook_timestamp=request.headers.get("webhook-timestamp", ""),
            webhook_signature=request.headers.get("webhook-signature", ""),
            secret=policy.webhook_secret,
        )
    except billing_service.WebhookSignatureError as exc:
        log.warning("refused: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid signature") from exc

    try:
        payload = json.loads(raw_body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Body is not JSON") from exc

    try:
        event = billing_service.parse_event(payload)
    except billing_service.InvalidWebhookError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if event is None:
        # An order or an invoice, or a type this app does not act on. Answer
        # 200 so the provider stops retrying a call there is nothing to do with.
        return {"status": "ignored"}

    if not policy.accepts(event):
        log.info(
            "ignored: a subscription for a product this app does not sell (%s)",
            event.polar_product_id,
        )
        return {"status": "ignored"}

    try:
        user = await billing_service.record_event(session, event)
    except billing_service.InvalidWebhookError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    log.info(
        "billing: subscription %s is %s -> %s",
        event.polar_subscription_id,
        event.status,
        user.plan,
    )
    return {"status": "ok"}


@router.get("/portal", response_model=BillingUrlView)
async def billing_portal(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> BillingUrlView:
    """The provider's own page for this trader: cancel, card, invoices.

    The link is generated per read and lives 24 hours, so it is asked for when
    the trader asks for it and never stored.
    """
    client = _client(request)
    row = await session.scalar(
        select(PolarSubscription)
        .where(PolarSubscription.user_id == user.id)
        .order_by(PolarSubscription.created_at.desc())
        .limit(1)
    )
    if row is None:
        raise HTTPException(
            status_code=404, detail="This account has no subscription to manage."
        )

    try:
        url = await client.create_customer_portal(
            customer_id=row.polar_customer_id,
            external_customer_id=user.auth_sub,
        )
    except PolarError as exc:
        log.warning("portal for %s failed: %s", user.id, exc)
        raise BillingUnavailableError(
            "The payment provider could not be reached. Try again in a moment."
        ) from exc
    return BillingUrlView(url=url)
