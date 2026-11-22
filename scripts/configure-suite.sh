#!/usr/bin/env bash
# Configure the suite's ONE API on FastAPI Cloud, and deploy.
#
# The suite shares ONE Polar webhook endpoint — ChartMark's
# https://mark.saytrade.app/api/webhooks/polar — which relays every verified
# event to this API's POST /billing/webhook with Polar's original headers, so
# this API verifies deliveries with ChartMark's endpoint secret. After the
# direct endpoint is registered at Polar (see DEPLOY.md), the relay retires.
#
# Run this AFTER:
#   `uv run fastapi cloud login` in this directory (the CLI session expires).
#
# Values are read from ChartMark's local .env.local (same account, same
# product, same secret) so nothing is copied by hand.

set -euo pipefail

CM="${CHARTMARK_ENV:-/home/zak/chartmark/.env.local}"
POLAR_TOKEN=$(grep -m1 '^POLAR_ACCESS_TOKEN=' "$CM" | cut -d= -f2-)
POLAR_SERVER=$(grep -m1 '^POLAR_SERVER=' "$CM" | cut -d= -f2-)
POLAR_MONTHLY=$(grep -m1 '^POLAR_PRO_MONTHLY_PRODUCT_ID=' "$CM" | cut -d= -f2-)
POLAR_WEBHOOK_SECRET=$(grep -m1 '^POLAR_WEBHOOK_SECRET=' "$CM" | cut -d= -f2-)
TWELVE_DATA_KEY=$(grep -m1 '^TWELVE_DATA_API_KEY=' "$CM" | cut -d= -f2-)

if [ -z "$POLAR_TOKEN" ] || [ -z "$POLAR_MONTHLY" ] || [ -z "$POLAR_WEBHOOK_SECRET" ]; then
  echo "Could not read the Polar values from $CM"
  exit 1
fi

uv run fastapi cloud env set POLAR_ACCESS_TOKEN "$POLAR_TOKEN" --secret
uv run fastapi cloud env set POLAR_SERVER "$POLAR_SERVER"
uv run fastapi cloud env set POLAR_PRO_MONTHLY_PRODUCT_ID "$POLAR_MONTHLY"
uv run fastapi cloud env set POLAR_WEBHOOK_SECRET "$POLAR_WEBHOOK_SECRET" --secret

# ChartMark's studio calls this API straight from the browser, like the
# journal's web client does, so its origin joins the allowed list.
uv run fastapi cloud env set CORS_ORIGINS \
  "https://saytrade.app,https://saytrade-web.vercel.app,https://mark.saytrade.app"

# US: and FX: series come from Twelve Data; the studio may also send the
# trader's own key per request, this is the deployment default.
if [ -n "$TWELVE_DATA_KEY" ]; then
  uv run fastapi cloud env set TWELVE_DATA_API_KEY "$TWELVE_DATA_KEY" --secret
fi

# The LemonSqueezy era's keys are inert now; remove them so nobody reads a
# stale value by mistake.
uv run fastapi cloud env delete LEMONSQUEEZY_API_KEY || true
uv run fastapi cloud env delete LEMONSQUEEZY_STORE_ID || true
uv run fastapi cloud env delete LEMONSQUEEZY_VARIANT_MONTHLY || true
uv run fastapi cloud env delete LEMONSQUEEZY_VARIANT_YEARLY || true
uv run fastapi cloud env delete LEMONSQUEEZY_WEBHOOK_SECRET || true

# Setting a variable does not redeploy — this does.
uv run fastapi deploy
