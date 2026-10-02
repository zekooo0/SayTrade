"""Composition root. Transport stays thin; domain rules live in services.

Real provider instances (Clerk verifier, Cloudinary storage, OpenAI whisper
and extraction) are attached to app.state so the services never import SDKs.
Tests swap these app.state slots for fakes and mint Clerk-shaped tokens.
"""

import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import analytics, me, notes, portfolios, trades
from app.auth.jwks import HttpJwksFetcher
from app.billing.entitlements import Entitlements
from app.config import settings
from app.db import SessionLocal
from app.domain.errors import DomainError
from app.log import get_logger, setup as setup_logging
from app.providers.extraction import OpenAIExtractor
from app.providers.storage import CloudinaryStorage
from app.providers.summary import OpenAISummarizer
from app.providers.transcription import OpenAITranscriber

setup_logging(settings.log_level)
log = get_logger("saytrade.api")

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
app.state.jwks = (
    HttpJwksFetcher(settings.clerk_frontend_api)
    if settings.clerk_frontend_api
    else None
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
# Background processing needs its own session per note, independent of the
# request session that created it.
app.state.session_factory = SessionLocal

log.info(
    "SayTrade API ready | extraction=%s | summary=%s | transcription=%s | log=%s",
    settings.openai_extraction_model,
    settings.openai_extraction_model,
    settings.openai_transcription_model,
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