#!/usr/bin/env bash
# Configure the SayTrade API's Polar billing on FastAPI Cloud, and deploy.
#
# The suite shares ONE Polar webhook endpoint — ChartMark's
# https://mark.saytrade.app/api/webhooks/polar — which relays every verified
# event to this API's POST /billing/webhook with Polar's original headers. So
# this API verifies deliveries with ChartMark's endpoint secret, and every
# Polar value (token, product, secret) is read from ChartMark's local
# .env.local instead of being copied here.
#
# Run this AFTER:
#   `uv run fastapi cloud login` in this directory (the CLI session expires).

set -euo pipefail

CM="${CHARTMARK_ENV:-/home/zak/chartmark/.env.local}"
POLAR_TOKEN=$(grep -m1 '^POLAR_ACCESS_TOKEN=' "$CM" | cut -d= -f2-)
POLAR_SERVER=$(grep -m1 '^POLAR_SERVER=' "$CM" | cut -d= -f2-)
POLAR_MONTHLY=$(grep -m1 '^POLAR_PRO_MONTHLY_PRODUCT_ID=' "$CM" | cut -d= -f2-)
POLAR_WEBHOOK_SECRET=$(grep -m1 '^POLAR_WEBHOOK_SECRET=' "$CM" | cut -d= -f2-)

if [ -z "$POLAR_TOKEN" ] || [ -z "$POLAR_MONTHLY" ] || [ -z "$POLAR_WEBHOOK_SECRET" ]; then
  echo "Could not read the Polar values from $CM"
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
