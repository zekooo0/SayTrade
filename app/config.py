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

    # --- Firebase (auth) ---
    # The Firebase project id, e.g. "saytrade-app". An ID token carries
    # `iss = https://securetoken.google.com/<project>` and `aud = <project>`,
    # so the project id is both the issuer's suffix and the audience.
    firebase_project_id: str | None = None

    @property
    def firebase_issuer(self) -> str | None:
        project = (self.firebase_project_id or "").strip()
        return f"https://securetoken.google.com/{project}" if project else None

    @property
    def firebase_audience(self) -> str | None:
        project = (self.firebase_project_id or "").strip()
        return project or None

    # --- OpenAI (transcription + extraction) ---
    openai_api_key: str | None = None
    openai_transcription_model: str = "gpt-transcribe"
    openai_extraction_model: str = "gpt-6-luna"

    # --- Cloudinary (audio storage) ---
    cloudinary_cloud_name: str | None = None
    cloudinary_api_key: str | None = None
    cloudinary_api_secret: str | None = None

    # --- Monetization ---
    # The free plan holds a small number of ACTIVE trades and has a generous
    # monthly cap on AI voice notes (an abuse guard, not a product limit).
    # Pro lifts the trade limit, and a trader reaches it through the payment
    # provider below.
    free_active_trades: int = 10
    free_voice_notes_per_month: int = 100
    # None means unlimited.
    pro_active_trades: int | None = None
    pro_voice_notes_per_month: int | None = None

    # --- Polar (payments) ---
    # The merchant of record: it takes the payment, the sales tax and the
    # refund, and it owns the PRICE. No price lives in this file — a second
    # copy of a number would disagree with the one on the checkout page.
    #
    # An empty token means no checkout: POST /billing/checkout answers 503,
    # and the app goes on saying Pro is coming.
    polar_server: str = "production"  # "sandbox" rehearses, "production" sells
    polar_access_token: str | None = None
    # One product is one price. Both products grant the same plan; the billing
    # period is the only difference.
    polar_pro_monthly_product_id: str | None = None
    polar_pro_yearly_product_id: str | None = None
    # The signing secret of the webhook, set when the webhook endpoint is
    # created at Polar. Empty means the webhook refuses every call, which is
    # the safe default.
    polar_webhook_secret: str | None = None
    # Where the checkout returns the trader. Each app in the suite has its own
    # front door: the journal's is the board, the charts studio's is the studio.
    polar_success_url: str = "https://saytrade.app/trades"
    polar_chartmark_success_url: str = "https://mark.saytrade.app/studio"

    # --- ChartMark (the suite's charts instrument) ---
    # The deterministic pipeline runs in the trader's browser; the API only
    # serves its data and its one AI call.
    #
    # Twelve Data serves US: and FX: series and symbol search. A trader may
    # bring his own key (the studio sends it as a header); this one is the
    # deployment's default.
    twelve_data_api_key: str | None = None
    # FREE holds a monthly AI-brief allowance (an abuse guard, like the voice
    # notes); PRO is unlimited (None is not used here — the limit is only
    # applied to FREE plans).
    chartmark_free_ai_briefs_per_month: int = 20

    # --- Image intake limits ---
    # A screenshot of a broker screen. 8 MB covers a phone screenshot at full
    # size; the app sends a copy capped to 1280px on its long edge, so a real
    # upload lands near 50 kB. That cap is what keeps the model's token cost
    # flat: tokens follow the pixel dimensions, not the file size.
    max_image_bytes: int = 8 * 1024 * 1024
    allowed_image_mime_types: tuple[str, ...] = ("image/jpeg", "image/png", "image/webp")

    # --- Sentry ---
    # The DSN is not a secret in the way a password is — it is the address events
    # are posted to — but it stays in .env, which is gitignored, like every other
    # value here. Empty means "do not report", which is what the test suite sets.
    sentry_dsn: str = ""
    # Which deployment an event came from. Sentry's default is "production",
    # which would be a lie for a developer's laptop, so it is stated instead.
    sentry_environment: str = "development"
    # Verbose SDK logging, off unless turned on. The same switch exists on the
    # web side, and it is what makes a verification possible without a dashboard.
    sentry_debug: bool = False

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

    # How much the app says about itself. INFO prints every stage of a note and
    # every symbol decision; DEBUG adds the raw model output. See app/log.py.
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()