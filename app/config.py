from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App configuration. Values come from the environment, falling back to .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Note the +asyncpg: the async driver must be named explicitly.
    database_url: str = "postgresql+asyncpg://saytrade:saytrade@localhost:5432/saytrade"


settings = Settings()
