#!/usr/bin/env bash
# Configure the SayTrade API's Polar billing on FastAPI Cloud, and deploy.
#
# Run this AFTER:
#   1. `uv run fastapi cloud login` in this directory (the CLI session expires).
#   2. Creating the webhook endpoint in the Polar dashboard:
#        URL:     https://saytrade-api.fastapicloud.dev/billing/webhook
#        Format:  Raw (default)
#        Events:  subscription.created, subscription.updated, subscription.active,
#                 subscription.canceled, subscription.uncanceled, subscription.revoked
#      Copy the endpoint's secret (polar_whs_...) and paste it below.
#
# The Polar access token and product id are ChartMark's — the suite sells one
# shared plan — so they are read from ChartMark's local .env.local instead of
# being copied here.

set -euo pipefail

POLAR_WEBHOOK_SECRET=""   # <-- paste the webhook endpoint secret (polar_whs_...)

if [ -z "$POLAR_WEBHOOK_SECRET" ]; then
  echo "Paste the Polar webhook secret into POLAR_WEBHOOK_SECRET in this script first."
  exit 1
fi

CM="${CHARTMARK_ENV:-/home/zak/chartmark/.env.local}"
POLAR_TOKEN=$(grep -m1 '^POLAR_ACCESS_TOKEN=' "$CM" | cut -d= -f2-)
POLAR_SERVER=$(grep -m1 '^POLAR_SERVER=' "$CM" | cut -d= -f2-)
POLAR_MONTHLY=$(grep -m1 '^POLAR_PRO_MONTHLY_PRODUCT_ID=' "$CM" | cut -d= -f2-)

if [ -z "$POLAR_TOKEN" ] || [ -z "$POLAR_MONTHLY" ]; then
  echo "Could not read Polar values from $CM"
  exit 1
fi

uv run fastapi cloud env set POLAR_ACCESS_TOKEN "$POLAR_TOKEN" --secret
uv run fastapi cloud env set POLAR_SERVER "$POLAR_SERVER"
uv run fastapi cloud env set POLAR_PRO_MONTHLY_PRODUCT_ID "$POLAR_MONTHLY"
uv run fastapi cloud env set POLAR_WEBHOOK_SECRET "$POLAR_WEBHOOK_SECRET" --secret

# The LemonSqueezy era's keys are inert now; remove them so nobody reads a
# stale value by mistake.
uv run fastapi cloud env rm LEMONSQUEEZY_API_KEY || true
uv run fastapi cloud env rm LEMONSQUEEZY_STORE_ID || true
uv run fastapi cloud env rm LEMONSQUEEZY_VARIANT_MONTHLY || true
uv run fastapi cloud env rm LEMONSQUEEZY_VARIANT_YEARLY || true
uv run fastapi cloud env rm LEMONSQUEEZY_WEBHOOK_SECRET || true

# Setting a variable does not redeploy — this does.
uv run fastapi deploy
