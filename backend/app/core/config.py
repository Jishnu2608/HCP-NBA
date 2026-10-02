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

    # Fixed administrator. Stored hashed; created by the generator and at start-up.
    # Change both before any hosted use.
    admin_email: str = "admin@admin.com"
    admin_password: str = "<NBA_ADMIN_PASSWORD>"

    # Seeded demo accounts (cm01@nba.demo, rep01@nba.demo, ...). Set to false to seed the
    # administrator only.
    seed_demo_accounts: bool = True
    demo_password: str = "<NBA_DEMO_PASSWORD>"
    demo_email_domain: str = "nba.demo"

    # Demo mode allows the on-screen one-time code when no mail server is configured.
    demo_mode: bool = True

    # One-time codes are emailed when these are set (use an app password, never commit it).
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None

    otp_ttl_minutes: int = 10
    otp_max_attempts: int = 5
    otp_resend_seconds: int = 30
    verification_token_minutes: int = 30

    # What a newly verified account is given from the synthetic pool.
    signup_panel_patients: int = 30
    signup_panel_hcps: int = 15


@lru_cache
def get_settings() -> Settings:
    return Settings()
