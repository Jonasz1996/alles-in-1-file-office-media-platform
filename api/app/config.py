from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Instellingen uit omgevingsvariabelen met prefix PLATFORM_."""

    model_config = SettingsConfigDict(env_prefix="PLATFORM_", env_file=".env", extra="ignore")

    app_name: str = "platform-api"
    environment: str = "development"
    node_name: str = "local"
    log_level: str = "info"


@lru_cache
def get_settings() -> Settings:
    return Settings()
