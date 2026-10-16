"""LemonSqueezy boundary: the seller of record, and the only place its API is
spoken to.

Two calls are all this app needs. One makes a checkout for a trader, and one
reads a subscription back. The API is JSON:API over httpx; the media type is
pinned in the headers, as the provider requires.

No price is passed and no price is read. The variant carries the price, and the
checkout page shows it.
"""

import httpx

API_BASE = "https://api.lemonsqueezy.com/v1"
TIMEOUT_SECONDS = 15
MEDIA_TYPE = "application/vnd.api+json"


class LemonSqueezyError(RuntimeError):
    """The provider refused the call, or answered something unusable."""


class LemonSqueezyClient:
    """The provider's API, as this app uses it.

    `variants` maps a billing period ("monthly", "yearly") to the variant id in
    the provider's store. One variant is one price, and both grant the same
    plan.
    """

    def __init__(self, api_key: str, store_id: str, variants: dict[str, str]):
        self.api_key = api_key
        self.store_id = store_id
        self.variants = {period: str(v) for period, v in variants.items() if v}

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": MEDIA_TYPE,
            "Content-Type": MEDIA_TYPE,
            "Authorization": f"Bearer {self.api_key}",
        }

    def variant_for(self, period: str) -> str:
        variant = self.variants.get(period)
        if not variant:
            raise LemonSqueezyError(f"no {period} price is configured")
        return variant

    async def create_checkout(
        self,
        *,
        user_id: str,
        period: str,
        redirect_url: str,
        test_mode: bool,
        email: str | None = None,
    ) -> str:
        """Make a checkout for one trader and return its URL.

        `custom.user_id` is the app's own account id, and it comes back in every
        webhook this checkout causes. That field is the ONLY link between a
        payment and an account, so it is set here and never guessed later.

        `email` prefills the buyer's address, so the receipt reaches the trader
        and he does not type it again. It is a courtesy, NOT an identity: the
        link to the account is the id above.
        """
        checkout_data: dict[str, object] = {"custom": {"user_id": str(user_id)}}
        if email:
            checkout_data["email"] = email
        payload = {
            "data": {
                "type": "checkouts",
                "attributes": {
                    "checkout_data": checkout_data,
                    "product_options": {
                        "redirect_url": redirect_url,
                        # Only the priced variants are offered. A product's
                        # variants ALL appear on a checkout by default, and a
                        # store can carry a leftover one (a draft, an old price),
                        # so the two this app knows about are named.
                        "enabled_variants": [
                            self.variants[name]
                            for name in ("monthly", "yearly")
                            if name in self.variants
                        ],
                    },
                    "test_mode": test_mode,
                },
                "relationships": {
                    "store": {
                        "data": {"type": "stores", "id": str(self.store_id)}
                    },
                    "variant": {
                        "data": {
                            "type": "variants",
                            "id": self.variant_for(period),
                        }
                    },
                },
            }
        }
        data = await self._request("POST", "/checkouts", payload)
        url = (data.get("attributes") or {}).get("url")
        if not url:
            raise LemonSqueezyError("the checkout answer carried no url")
        return str(url)

    async def get_subscription(self, subscription_id: str) -> dict:
        """Read one subscription back, and return its attributes.

        Used for the trader's own management page: the portal link is generated
        per read and expires in 24 hours, so it is never stored.
        """
        data = await self._request("GET", f"/subscriptions/{subscription_id}")
        return data.get("attributes") or {}

    async def _request(
        self, method: str, path: str, payload: dict | None = None
    ) -> dict:
        url = f"{API_BASE}{path}"
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                response = await client.request(
                    method, url, headers=self._headers(), json=payload
                )
        except httpx.HTTPError as exc:
            raise LemonSqueezyError(f"{method} {path} did not complete: {exc}") from exc

        if response.status_code >= 400:
            raise LemonSqueezyError(
                f"{method} {path} answered {response.status_code}: {response.text[:300]}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise LemonSqueezyError(f"{method} {path} answered no JSON") from exc
        data = body.get("data")
        if not isinstance(data, dict):
            raise LemonSqueezyError(f"{method} {path} answered no data object")
        return data
