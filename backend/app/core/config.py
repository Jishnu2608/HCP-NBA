from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data"
ENV_FILE = REPO_ROOT / ".env"


class MissingSecret(RuntimeError):
    def __init__(self, env_name: str) -> None:
        super().__init__(
            f"{env_name} is not set. Run `python -m app.bootstrap` from the backend folder to "
            f"create it in {ENV_FILE.name}, or set it in the environment."
        )


class Settings(BaseSettings):
    """Runtime configuration. Override any field with an NBA_-prefixed env var or .env entry.

    No secret has a default in code. Secrets live only in the environment or in the
    git-ignored .env file, which `python -m app.bootstrap` creates with random values.
    """

    model_config = SettingsConfigDict(env_prefix="NBA_", env_file=ENV_FILE, extra="ignore")

    app_name: str = "Healthcare NBA"
    environment: str = "local"

    # SQLite for the laptop demo; any SQLAlchemy URL (Postgres) for hosted deployments.
    database_url: str = f"sqlite:///{(DATA_DIR / 'nba_demo.db').as_posix()}"

    # --- Secrets: no defaults. Read them through `secret()`. ---
    jwt_secret: str | None = None
    # Password of the fixed administrator account (created by the generator and at start-up).
    admin_password: str | None = None
    # Shared password of the seeded demo accounts (cm01@nba.demo, rep01@nba.demo, ...).
    demo_password: str | None = None

    jwt_algorithm: str = "HS256"
    # Sessions: absolute lifetime and idle timeout (server-side, see auth/sessions.py).
    access_token_minutes: int = 480
    session_idle_minutes: int = 60

    # Origins allowed to send state-changing requests besides the request's own host
    # (comma-separated, e.g. "http://localhost:5173" for the Vite dev server).
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # Off only in tests; limits are per process.
    rate_limit_enabled: bool = True

    # "template" needs no key and no network; other providers are plugged in later.
    llm_provider: str = "template"

    datagen_seed: int = 20260101

    admin_email: str = "admin@admin.com"
    # Set to false to seed the administrator only.
    seed_demo_accounts: bool = True
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

    # Invitations for professional roles.
    invitation_ttl_hours: int = 72
    # Base of links in emails (set to a LAN address to open them on a phone).
    public_base_url: str = "http://localhost:8000"
    # Shown in emails as the contact for questions; falls back to the sender address.
    support_email: str | None = None

    # Default age of adulthood. Jurisdictions with a different age override it in
    # core/jurisdiction.py. Invited professionals must be adults where they live; patients
    # below it may register and are flagged.
    minor_age: int = 18

    # --- Legal documents: facts only the operator can supply. Unset values appear in the
    # Privacy Policy and Terms as "[To be confirmed]" and keep the "draft" banner on. ---
    legal_controller_name: str | None = None
    legal_controller_address: str | None = None
    legal_privacy_email: str | None = None
    legal_dpo_contact: str | None = None
    legal_eu_representative: str | None = None
    legal_governing_law: str | None = None
    legal_hosting_region: str | None = None
    # Days within which privacy requests are answered (to be confirmed by counsel).
    privacy_response_days: int | None = None

    # Drafting providers that call an external service never receive identifiable patient
    # or HCP data unless the operator turns this on (for example after a data-processing
    # agreement is in place).
    allow_external_identifiable_data: bool = False

    # What a newly verified account is given from the synthetic pool.
    signup_panel_patients: int = 30
    signup_panel_hcps: int = 15

    @property
    def is_local(self) -> bool:
        return self.environment == "local"

    @property
    def cookie_secure(self) -> bool:
        """Cookies are Secure everywhere except a local run over plain http."""
        return not self.is_local

    @property
    def show_development_codes(self) -> bool:
        """An on-screen one-time code is allowed only for a local run without a mail server."""
        return self.demo_mode and self.is_local and not self.smtp_host

    def secret(self, name: str) -> str:
        """A required secret, or a clear error naming the variable that is missing."""
        value = getattr(self, name)
        if not value:
            raise MissingSecret(f"NBA_{name.upper()}")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
