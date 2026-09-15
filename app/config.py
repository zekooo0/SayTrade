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

    # --- Monetization (plan limits; config not schema, per the spec) ---
    free_ai_voice_limit: int = 5
    pro_ai_voice_limit: int = 100

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