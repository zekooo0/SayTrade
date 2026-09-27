"""Composition root. Transport stays thin; domain rules live in services.

Real provider instances (Clerk verifier, Cloudinary storage, OpenAI whisper
and extraction) are attached to app.state so the services never import SDKs.
Tests swap these app.state slots for fakes and mint Clerk-shaped tokens.
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import analytics, me, notes, trades
from app.auth.jwks import HttpJwksFetcher
from app.billing.entitlements import Entitlements
from app.config import settings
from app.db import SessionLocal
from app.domain.errors import DomainError
from app.providers.extraction import OpenAIExtractor
from app.providers.storage import CloudinaryStorage
from app.providers.transcription import OpenAIWhisper

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
    allow_methods=["GET", "POST", "OPTIONS"],
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
app.state.transcriber = OpenAIWhisper(
    api_key=settings.openai_api_key or "",
    model=settings.openai_transcription_model,
)
app.state.extractor = OpenAIExtractor(
    api_key=settings.openai_api_key or "",
    model=settings.openai_extraction_model,
)
app.state.entitlements = Entitlements.from_settings(settings)
# Background processing needs its own session per note, independent of the
# request session that created it.
app.state.session_factory = SessionLocal


@app.exception_handler(DomainError)
async def domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(notes.router)
app.include_router(trades.router)
app.include_router(me.router)
app.include_router(analytics.router)