from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data"


class Settings(BaseSettings):
    """Runtime configuration. Override any field with an NBA_-prefixed env var or .env entry."""

    model_config = SettingsConfigDict(
        env_prefix="NBA_", env_file=REPO_ROOT / ".env", extra="ignore"
    )

    app_name: str = "Healthcare NBA"
    environment: str = "local"

    # SQLite for the laptop demo; any SQLAlchemy URL (Postgres) for hosted deployments.
    database_url: str = f"sqlite:///{(DATA_DIR / 'nba_demo.db').as_posix()}"

    jwt_secret: str = "<NBA_JWT_SECRET>"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 480

    # "template" needs no key and no network; other providers are plugged in later.
    llm_provider: str = "template"

    datagen_seed: int = 20260101

    # Demo conveniences: one-click persona login and the shared seeded password.
    demo_mode: bool = True
    demo_password: str = "<NBA_DEMO_PASSWORD>"


@lru_cache
def get_settings() -> Settings:
    return Settings()
