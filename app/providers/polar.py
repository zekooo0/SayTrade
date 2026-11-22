"""The Polar.sh provider: checkout sessions and customer-portal sessions.

Polar is the merchant of record. It takes the payment, the sales tax and the
refund, and it owns the PRICE — no price lives in this code. One product is one
price; both products grant the same plan, and the billing period is the only
difference.

The checkout is linked to the trader by `external_customer_id`, which is the
verified Firebase subject (`users.auth_sub`): the payment that follows is tied
to this account and to no other, and the portal can always find the customer
again without storing anything beyond Polar's own ids.
"""

import logging

import httpx

logger = logging.getLogger(__name__)

PRODUCTION_BASE_URL = "https://api.polar.sh"
SANDBOX_BASE_URL = "https://sandbox-api.polar.sh"


class PolarError(Exception):
    """The provider refused, or could not be reached. Maps to a 503."""


class PolarClient:
    """A minimal Polar v1 client: enough to sell, nothing more."""

    def __init__(
        self,
        *,
        access_token: str,
        server: str = "production",
        products: dict[str, str],
    ):
        self._token = access_token
        self._base_url = (
            SANDBOX_BASE_URL if server == "sandbox" else PRODUCTION_BASE_URL
        )
        self._products = {k: v for k, v in products.items() if v}

    @property
    def configured(self) -> bool:
        return bool(self._token and self._products)

    async def _post(self, path: str, body: dict) -> dict:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    f"{self._base_url}{path}",
                    json=body,
                    headers={
                        "Authorization": f"Bearer {self._token}",
                        "Accept": "application/json",
                    },
                )
        except httpx.HTTPError as exc:
            raise PolarError("Polar could not be reached.") from exc

        if response.status_code >= 400:
            try:
                detail = response.json().get("detail") or response.text
            except ValueError:
                detail = response.text
            logger.warning("polar %s failed (%s): %s", path, response.status_code, detail)
            raise PolarError(
                f"Polar answered HTTP {response.status_code}."
            )

        try:
            return response.json()
        except ValueError as exc:
            raise PolarError("Polar returned a body this app cannot read.") from exc

    def _product_id(self, period: str) -> str:
        """The product for a billing period.

        A missing yearly product falls back to the monthly one, so a deployment
        that configured one price still sells; a missing monthly one is a
        configuration fault and refuses loudly.
        """
        product_id = self._products.get(period) or self._products.get("monthly")
        if not product_id:
            raise PolarError("No Polar product is configured for this period.")
        return product_id

    async def create_checkout(
        self,
        *,
        user_id: str,
        auth_sub: str,
        period: str,
        email: str | None,
        success_url: str,
    ) -> str:
        """A hosted checkout for THIS trader, and the link to open it.

        The account travels twice — as Polar's `external_customer_id` (so the
        customer record is the trader's, and the portal works later), and in
        the checkout `metadata` (so the webhook can resolve the account even
        before Polar has a customer for it).
        """
        body: dict = {
            "products": [self._product_id(period)],
            "external_customer_id": auth_sub,
            "success_url": success_url,
            "metadata": {
                "user_id": user_id,
                "product": "saytrade",
                "billing_period": period,
            },
        }
        if email:
            body["customer_email"] = email

        data = await self._post("/v1/checkouts/", body)
        url = data.get("url")
        if not isinstance(url, str) or not url:
            raise PolarError("Polar returned no checkout URL.")
        return url

    async def create_customer_portal(
        self,
        *,
        customer_id: str | None = None,
        external_customer_id: str | None = None,
    ) -> str:
        """A temporary session for the provider's own management page.

        The link is generated per read and lives 24 hours, so it is asked for
        when the trader asks for it and never stored.
        """
        body: dict = {}
        if customer_id:
            body["customer_id"] = customer_id
        elif external_customer_id:
            body["external_customer_id"] = external_customer_id
        else:
            raise PolarError("No customer to open a portal for.")

        data = await self._post("/v1/customer-sessions/", body)
        url = data.get("customer_portal_url")
        if not isinstance(url, str) or not url:
            raise PolarError("Polar returned no portal URL.")
        return url
