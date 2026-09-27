from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Single source of truth for all configuration.

    Pydantic-settings reads from environment variables and the repo-root .env.
    Optional keys (Clerk/OpenAI/Cloudinary) are None until the developer
    supplies them; the verifier/providers fail with clear errors rather than
    silently degrading when a required key is missing.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Postgres ---
    database_url: str = "postgresql+asyncpg://saytrade:saytrade@127.0.0.1:5432/saytrade"

    # --- Clerk (auth) ---
    # e.g. https://your-app.clerk.accounts.dev — also the JWT issuer.
    clerk_frontend_api: str | None = None

    # --- OpenAI (transcription + extraction) ---
    openai_api_key: str | None = None
    openai_transcription_model: str = "whisper-1"
    openai_extraction_model: str = "gpt-4o-mini"

    # --- Cloudinary (audio storage) ---
    cloudinary_cloud_name: str | None = None
    cloudinary_api_key: str | None = None
    cloudinary_api_secret: str | None = None

    # --- Monetization ---
    # The free plan holds a small number of ACTIVE trades and has a generous
    # monthly cap on AI voice notes (an abuse guard, not a product limit).
    # Pro lifts the trade limit. Prices are shown, not charged: there is no
    # checkout yet, so plans are switched by hand.
    free_active_trades: int = 3
    free_voice_notes_per_month: int = 100
    # None means unlimited.
    pro_active_trades: int | None = None
    pro_voice_notes_per_month: int | None = None
    pro_price_usd: int = 5
    # Where "Upgrade" sends the trader. Empty hides nothing — the UI shows the
    # price and says payments are not open yet. Set it to a Stripe/Gumroad
    # payment link, or to mailto:you@example.com, when you have one.
    upgrade_url: str = ""

    # --- Audio intake limits ---
    max_audio_bytes: int = 25 * 1024 * 1024  # 25 MB recording cap
    max_duration_ms: int = 3_600_000  # 1 hour
    allowed_audio_mime_types: tuple[str, ...] = (
        "audio/mp4",
        "audio/mpeg",
        "audio/wav",
        "audio/x-wav",
        "audio/webm",
        "audio/ogg",
        "audio/aac",
        "audio/x-m4a",
        "audio/m4a",
    )

    # --- CORS ---
    # Comma-separated origins allowed to call the API from a browser.
    cors_origins: str = "http://localhost:3000"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()