"""Composition root. Transport stays thin; domain rules live in services.

Real provider instances (Clerk verifier, Cloudinary storage, OpenAI whisper
and extraction) are attached to app.state so the services never import SDKs.
Tests swap these app.state slots for fakes and mint Clerk-shaped tokens.
"""

import time

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    analytics,
    billing,
    me,
    notes,
    portfolios,
    predictions,
    reviews,
    trades,
    waitlist,
)
from app.auth.jwks import FIREBASE_JWKS_URL, HttpJwksFetcher
from app.billing.entitlements import Entitlements
from app.config import settings
from app.db import SessionLocal
from app.domain.errors import DomainError
from app.log import get_logger, setup as setup_logging
from app.providers.extraction import OpenAIExtractor
from app.providers.polar import PolarClient
from app.providers.storage import CloudinaryStorage
from app.providers.summary import OpenAISummarizer
from app.providers.transcription import OpenAITranscriber
from app.services.billing import BillingPolicy

setup_logging(settings.log_level)
log = get_logger("saytrade.api")

# Errors and traces go to Sentry. This runs BEFORE the app is created, and that
# order is load-bearing: the FastAPI integration patches the framework while the
# SDK initialises, so an app built first is an app that reports nothing.
#
# No DSN means no reporting — which is what the test suite relies on, so it never
# posts from a test run.
if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        # The user id, and the request data around the error. Headers whose names
        # look like credentials are scrubbed by the SDK's own denylist; the
        # Authorization header never leaves in a clear form.
        send_default_pii=True,
        # Every transaction, because this app's traffic is tiny and the value is
        # in seeing the whole path a slow note took, not a sample of it. Lower
        # this to a fraction if the volume ever justifies it.
        traces_sample_rate=1.0,
        # The profiler runs on whatever transaction is active, so a slow request
        # arrives with the frame that made it slow.
        profile_session_sample_rate=1.0,
        profile_lifecycle="trace",
        # The app already logs one line per request. Sending those makes the log
        # a timeline around the error instead of a single event.
        enable_logs=True,
        # Off unless SENTRY_DEBUG is set, like the web side.
        debug=settings.sentry_debug,
    )

app = FastAPI(title="SayTrade API", version="0.2.0")

# Browser (Next.js dev server) talks to this API cross-origin; Bearer tokens
# are sent with Authorization, so credentials are not required.
CORS_ORIGINS = [
    origin.strip()
    for origin in (settings.cors_origins or "").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS or ["http://localhost:3000"],
    # Every method the client actually sends. A method missing here fails the
    # browser's preflight with "Disallowed CORS method", which the browser then
    # reports as a CORS failure with a null status - the call never arrives.
    # Every method the client sends. Listing them one by one has now broken this
    # twice: the browser's preflight gets "Disallowed CORS method", and the
    # browser then reports a CORS failure with a null status - the call never
    # arrives, and nothing reaches the server to log. First DELETE, then PATCH
    # when portfolios arrived. The origin list above is the real gate.
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=False,
)

# --- runtime wiring (tests override these app.state slots) ---
app.state.settings = settings
# Google publishes ONE key set for every Firebase project, so there is a single
# fetcher; the project is proven by the token's issuer and audience instead.
app.state.firebase_jwks = (
    HttpJwksFetcher(FIREBASE_JWKS_URL) if settings.firebase_project_id else None
)
app.state.storage = CloudinaryStorage(
    cloud_name=settings.cloudinary_cloud_name or "",
    api_key=settings.cloudinary_api_key or "",
    api_secret=settings.cloudinary_api_secret or "",
)
app.state.transcriber = OpenAITranscriber(
    api_key=settings.openai_api_key or "",
    model=settings.openai_transcription_model,
)
app.state.extractor = OpenAIExtractor(
    api_key=settings.openai_api_key or "",
    model=settings.openai_extraction_model,
)
# Writes the one-line action summary. Same model as the extraction: the line is
# short, and it is written from the changes, never from the transcript.
app.state.summarizer = OpenAISummarizer(
    api_key=settings.openai_api_key or "",
    model=settings.openai_extraction_model,
)
app.state.entitlements = Entitlements.from_settings(settings)
# Payments. The client is None until a token and a product are configured, and
# the checkout route answers 503 while it is None — so a half-configured
# deployment keeps saying "Pro is coming" instead of offering a button that
# cannot work.
app.state.polar = (
    PolarClient(
        access_token=settings.polar_access_token or "",
        server=settings.polar_server,
        products={
            "monthly": settings.polar_pro_monthly_product_id or "",
            "yearly": settings.polar_pro_yearly_product_id or "",
        },
    )
    if settings.polar_access_token
    and (settings.polar_pro_monthly_product_id or settings.polar_pro_yearly_product_id)
    else None
)
# What the webhook accepts: the signing secret, and the products this
# deployment sells — an event for anything else is ignored, so a stray
# endpoint can never write a plan here.
app.state.billing = BillingPolicy(
    webhook_secret=settings.polar_webhook_secret or "",
    product_ids=frozenset(
        p for p in (settings.polar_pro_monthly_product_id, settings.polar_pro_yearly_product_id) if p
    ),
)
# Background processing needs its own session per note, independent of the
# request session that created it.
app.state.session_factory = SessionLocal

log.info(
    "SayTrade API ready | extraction=%s | summary=%s | transcription=%s | "
    "billing=%s | log=%s",
    settings.openai_extraction_model,
    settings.openai_extraction_model,
    settings.openai_transcription_model,
    "polar" if app.state.polar else "off",
    settings.log_level,
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """One line per request, with its status and duration.

    The API is small enough that this is the cheapest way to see what the app
    did: which calls the browser made, which one 422'd, how long a note took.
    """
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    log.info(
        "%s %s -> %s (%.0f ms)",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    if request.url.query:
        log.debug("  query: %s", request.url.query)
    return response


@app.exception_handler(DomainError)
async def domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
    log.warning("refused: %s %s", type(exc).__name__, exc.message)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(notes.router)
app.include_router(trades.router)
app.include_router(me.router)
app.include_router(portfolios.router)
app.include_router(analytics.router)
app.include_router(predictions.router)
app.include_router(reviews.router)
app.include_router(billing.router)
app.include_router(waitlist.router)